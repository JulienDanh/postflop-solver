"""User-friendly wrapper around the raw PyO3 bindings.

Usage:
    from postflop_solver import Solver

    s = Solver(
        oop_range="66+,A8s+,...",
        ip_range="QQ-22,...",
        board="Td9d6h",           # flop only, or flop+turn, or flop+turn+river
        starting_pot=200,
        effective_stack=900,
        bet_sizes="60%, e, a",    # same for both players, all streets
        raise_sizes="2.5x",       # same for both players, all streets
    )
    s.solve(iterations=1000)
    s.strategy()        # -> {hand: {action: freq}}
    s.equity()          # -> {hand: equity}
    s.expected_values() # -> {hand: ev}
"""
from __future__ import annotations

from typing import Optional

from ._core import PostFlopGame as _Game, compute_average_py as _avg

_OOP = 0
_IP = 1


class Solver:
    """High-level solver interface.

    Wraps the raw PyO3 bindings with:
    - Dict-based strategy/equity/EV (hand -> value) instead of flat arrays
    - Action strings ("Bet(120)") instead of indices for play()
    - Auto cache_normalized_weights() after every play/back_to_root
    - Bet sizes as a single string applied to all streets/players
    - Average equity/EV as properties
    - Node locking with dict input: {hand: {action: freq}}
    """

    def __init__(
        self,
        oop_range: str,
        ip_range: str,
        board: str,
        starting_pot: int,
        effective_stack: int,
        bet_sizes: str = "",
        raise_sizes: str = "",
        flop_bet_sizes: Optional[str] = None,
        turn_bet_sizes: Optional[str] = None,
        river_bet_sizes: Optional[str] = None,
        flop_raise_sizes: Optional[str] = None,
        turn_raise_sizes: Optional[str] = None,
        river_raise_sizes: Optional[str] = None,
        turn_donk_sizes: Optional[str] = None,
        river_donk_sizes: Optional[str] = None,
        rake_rate: float = 0.0,
        rake_cap: float = 0.0,
        add_allin_threshold: float = 1.5,
        force_allin_threshold: float = 0.15,
        merging_threshold: float = 0.1,
    ):
        """Create a new solver instance.

        Args:
            oop_range: OOP player range in PioSOLVER-style string (e.g. "66+,A8s+,AKo").
            ip_range: IP player range string.
            board: Board cards as a single string. 3 cards = flop only,
                4 cards = flop+turn, 5 cards = flop+turn+river (e.g. "Td9d6h", "Td9d6hQc").
            starting_pot: Pot size in chips at the start of postflop play.
            effective_stack: Effective stack in chips (from the start of postflop).
            bet_sizes: Bet size string applied to all streets and both players
                (e.g. "60%, e, a" = 60% pot, geometric, all-in).
                Override per-street with flop_bet_sizes/turn_bet_sizes/river_bet_sizes.
            raise_sizes: Raise size string applied to all streets and both players
                (e.g. "2.5x" = 2.5x previous bet).
                Override per-street with flop_raise_sizes/turn_raise_sizes/river_raise_sizes.
            flop_bet_sizes: Override bet sizes for flop only.
            turn_bet_sizes: Override bet sizes for turn only.
            river_bet_sizes: Override bet sizes for river only.
            flop_raise_sizes: Override raise sizes for flop only.
            turn_raise_sizes: Override raise sizes for turn only.
            river_raise_sizes: Override raise sizes for river only.
            turn_donk_sizes: Donk bet sizes for turn (None = use default).
            river_donk_sizes: Donk bet sizes for river (None = use default).
            rake_rate: Rake as fraction of pot (0.0 to 1.0).
            rake_cap: Maximum rake in chips.
            add_allin_threshold: Add all-in if max bet <= this * pot (0.0 = disable).
            force_allin_threshold: Force all-in if SPR after call <= this (0.0 = disable).
            merging_threshold: Merge bet actions with close values (0.0 = disable).

        Example:
            >>> s = Solver(
            ...     oop_range="66+,A8s+",
            ...     ip_range="QQ-22,ATo+",
            ...     board="Td9d6hQc",
            ...     starting_pot=200,
            ...     effective_stack=900,
            ...     bet_sizes="60%, e, a",
            ...     raise_sizes="2.5x",
            ... )
        """
        cards = [board[i:i + 2] for i in range(0, len(board), 2)]
        if len(cards) < 3 or len(cards) > 5:
            raise ValueError(f"Board must be 3-5 cards (6-10 chars), got '{board}'")
        flop = "".join(cards[:3])
        turn = cards[3] if len(cards) > 3 else None
        river = cards[4] if len(cards) > 4 else None
        initial_state = {3: "flop", 4: "turn", 5: "river"}[len(cards)]

        def street(s, rs, fs, frs):
            bs = fs if fs is not None else s
            rs2 = frs if frs is not None else rs
            return [(bs, rs2), (bs, rs2)]

        flop_b = street(bet_sizes, raise_sizes, flop_bet_sizes, flop_raise_sizes)
        turn_b = street(bet_sizes, raise_sizes, turn_bet_sizes, turn_raise_sizes)
        river_b = street(bet_sizes, raise_sizes, river_bet_sizes, river_raise_sizes)

        self._g = _Game(
            oop_range=oop_range,
            ip_range=ip_range,
            flop=flop,
            turn=turn,
            river=river,
            initial_state=initial_state,
            starting_pot=starting_pot,
            effective_stack=effective_stack,
            flop_bet_sizes=flop_b,
            turn_bet_sizes=turn_b,
            river_bet_sizes=river_b,
            turn_donk_sizes=turn_donk_sizes,
            river_donk_sizes=river_donk_sizes,
            rake_rate=rake_rate,
            rake_cap=rake_cap,
            add_allin_threshold=add_allin_threshold,
            force_allin_threshold=force_allin_threshold,
            merging_threshold=merging_threshold,
        )
        self._cache_dirty = True

    # -- solving -------------------------------------------------------

    def allocate_memory(self, compress: bool = False) -> None:
        """Allocate memory for the game tree. Must be called before solve().

        Args:
            compress: If True, use 16-bit integer compression (halves memory,
                slight precision loss). If False, use 32-bit floats.
        """
        self._g.allocate_memory(compress)

    def solve(
        self,
        iterations: int = 1000,
        target_exploitability: Optional[float] = None,
        verbose: bool = False,
    ) -> float:
        """Run Discounted CFR until convergence or max iterations.

        Args:
            iterations: Maximum number of CFR iterations.
            target_exploitability: Stop early if exploitability drops below this.
                Defaults to 0.5% of the starting pot.
            verbose: Print iteration progress to stdout.

        Returns:
            Final exploitability value (in chips).
        """
        if target_exploitability is None:
            target_exploitability = self._g.starting_pot() * 0.005
        return self._g.solve(iterations, target_exploitability, verbose)

    def solve_step(self, iteration: int) -> None:
        """Run a single CFR iteration. Use for manual solve loops.

        Args:
            iteration: Current iteration number (0-indexed).
        """
        self._g.solve_step(iteration)

    def finalize(self) -> None:
        """Compute and cache expected values after solving. Called automatically by solve()."""
        self._g.finalize()

    def exploitability(self) -> float:
        """Compute current exploitability (how far from Nash equilibrium).

        Returns:
            Exploitability in chips. Lower is better; <1% of pot is typical for a solved game.
        """
        return self._g.exploitability()

    @property
    def is_solved(self) -> bool:
        """True if the game has been solved."""
        return self._g.exploitability() > 0

    # -- navigation ----------------------------------------------------

    def play(self, action) -> None:
        """Play an action from the current node, advancing to the next node.

        Args:
            action: Action string (e.g. "Bet(120)", "Check", "Fold") or
                integer index from available_actions().

        Raises:
            ValueError: If the action string doesn't match any available action.
        """
        if isinstance(action, str):
            actions = self.available_actions()
            idx = _match_action(action, actions)
            if idx is None:
                raise ValueError(f"Action '{action}' not found in {actions}")
            action = idx
        self._g.play(action)
        self._cache_dirty = True

    def back_to_root(self) -> None:
        """Navigate back to the root node, resetting all state."""
        self._g.back_to_root()
        self._cache_dirty = True

    def available_actions(self) -> list[str]:
        """Return the list of available actions at the current node.

        Returns:
            List of action strings like ["Check", "Bet(120)", "AllIn(900)"].
        """
        return self._g.available_actions()

    def current_player(self) -> str:
        """Return which player acts at the current node.

        Returns:
            "oop" or "ip".
        """
        p = self._g.current_player()
        return "oop" if p == _OOP else "ip"

    def current_board(self) -> str:
        """Return the current board as a string (e.g. "Td9d6hQc")."""
        return "".join(self._g.current_board())

    def is_chance_node(self) -> bool:
        """True if the current node is a chance node (card deal)."""
        return self._g.is_chance_node()

    def is_terminal_node(self) -> bool:
        """True if the current node is terminal (hand ended)."""
        return self._g.is_terminal_node()

    def possible_cards(self) -> list[str]:
        """Return the list of possible cards to deal at a chance node.

        Returns:
            List of card strings like ["2c", "2d", ...].
        """
        mask = self._g.possible_cards()
        return [_int_to_card_str(i) for i in range(52) if mask & (1 << i)]

    def history(self) -> list[int]:
        """Return the action history from root to current node.

        Returns:
            List of action indices played so far.
        """
        return self._g.history()

    # -- queries (auto-cache) -----------------------------------------

    def _ensure_cache(self) -> None:
        if self._cache_dirty:
            self._g.cache_normalized_weights()
            self._cache_dirty = False

    def strategy(self, player: Optional[str] = None) -> dict[str, dict[str, float]]:
        """Return the strategy at the current node.

        Args:
            player: "oop", "ip", or None for current player.

        Returns:
            Dict mapping each hand string to a dict of {action: frequency}.
            Example: {"AhKh": {"Check": 0.3, "Bet(120)": 0.7, ...}, ...}
        """
        self._ensure_cache()
        p = _player_idx(player, self)
        hands = self._g.private_cards(p)
        actions = self.available_actions()
        raw = self._g.strategy()
        n = len(hands)
        na = len(actions)
        return {
            hand: {actions[a]: raw[a * n + h_idx] for a in range(na)}
            for h_idx, hand in enumerate(hands)
        }

    def equity(self, player: Optional[str] = None) -> dict[str, float]:
        """Return equity (0-1) for each hand at the current node.

        Args:
            player: "oop", "ip", or None for current player.

        Returns:
            Dict mapping hand string to equity. Example: {"AhKh": 0.73, ...}
        """
        self._ensure_cache()
        p = _player_idx(player, self)
        hands = self._g.private_cards(p)
        vals = self._g.equity(p)
        return dict(zip(hands, vals))

    def expected_values(self, player: Optional[str] = None) -> dict[str, float]:
        """Return expected value (in chips) for each hand at the current node.

        Args:
            player: "oop", "ip", or None for current player.

        Returns:
            Dict mapping hand string to EV. Example: {"AhKh": 45.2, ...}
        """
        self._ensure_cache()
        p = _player_idx(player, self)
        hands = self._g.private_cards(p)
        vals = self._g.expected_values(p)
        return dict(zip(hands, vals))

    def average_equity(self, player: Optional[str] = None) -> float:
        """Return range-weighted average equity.

        Args:
            player: "oop", "ip", or None for current player.

        Returns:
            Average equity (0-1).
        """
        self._ensure_cache()
        p = _player_idx(player, self)
        return _avg(self._g.equity(p), self._g.normalized_weights(p))

    def average_ev(self, player: Optional[str] = None) -> float:
        """Return range-weighted average expected value.

        Args:
            player: "oop", "ip", or None for current player.

        Returns:
            Average EV in chips.
        """
        self._ensure_cache()
        p = _player_idx(player, self)
        return _avg(self._g.expected_values(p), self._g.normalized_weights(p))

    def range_percentages(self, player: Optional[str] = None) -> dict[str, float]:
        """Return each hand's percentage of the range at the current node.

        Args:
            player: "oop", "ip", or None for current player.

        Returns:
            Dict mapping hand string to percentage (0-100).
        """
        self._ensure_cache()
        p = _player_idx(player, self)
        hands = self._g.private_cards(p)
        w = self._g.normalized_weights(p)
        total = sum(w)
        return {h: (wt / total * 100.0 if total > 0 else 0.0) for h, wt in zip(hands, w)}

    def private_cards(self, player: Optional[str] = None) -> list[str]:
        """Return the list of private hands for a player.

        Args:
            player: "oop", "ip", or None for current player.

        Returns:
            List of hand strings like ["5c4c", "Ac4c", ...].
        """
        p = _player_idx(player, self)
        return self._g.private_cards(p)

    def num_hands(self, player: Optional[str] = None) -> int:
        """Return the number of private hands for a player.

        Args:
            player: "oop", "ip", or None for current player.
        """
        p = _player_idx(player, self)
        return self._g.num_private_hands(p)

    # -- node locking --------------------------------------------------

    def lock_strategy(self, strategy: dict[str, dict[str, float]]) -> None:
        """Lock the current node's strategy.

        Args:
            strategy: {hand: {action: freq}} where freq > 0 locks the hand.
                      Hands not in the dict are left unlocked.
                      Actions not in the inner dict get 0.0.
        """
        p = self._g.current_player()
        hands = self._g.private_cards(p)
        actions = self.available_actions()
        n = len(hands)
        na = len(actions)
        hand_idx = {h: i for i, h in enumerate(hands)}

        raw = [0.0] * (na * n)
        for hand, freqs in strategy.items():
            if hand not in hand_idx:
                raise ValueError(f"Hand '{hand}' not in player's range")
            h_idx = hand_idx[hand]
            for action_str, freq in freqs.items():
                a_idx = _match_action(action_str, actions)
                if a_idx is None:
                    raise ValueError(f"Action '{action_str}' not found in {actions}")
                raw[a_idx * n + h_idx] = freq

        self._g.lock_current_strategy(raw)

    def unlock_strategy(self) -> None:
        """Remove the node lock from the current node.

        Must navigate to the locked node (same path used for locking) after
        allocate_memory() before calling this.
        """
        self._g.unlock_current_strategy()

    # -- save/load -----------------------------------------------------

    def save(self, path: str, memo: str = "", compression_level: Optional[int] = None) -> None:
        """Save the solved game to a file.

        Args:
            path: File path to save to.
            memo: Optional memo string saved with the data.
            compression_level: zstd compression level (1-22), or None for no
                compression. Requires the `zstd` Rust feature.
        """
        self._g.save(path, memo, compression_level)

    @staticmethod
    def load(path: str, max_memory: Optional[int] = None) -> "Solver":
        """Load a previously saved game from a file.

        Args:
            path: File path to load from.
            max_memory: Maximum memory allowed (bytes), or None for no limit.

        Returns:
            A Solver instance loaded from the file.
        """
        raw = _Game.load(path, max_memory)
        s = Solver.__new__(Solver)
        s._g = raw
        s._cache_dirty = True
        return s

    # -- misc ----------------------------------------------------------

    def memory_usage(self) -> tuple[int, int]:
        """Return estimated memory usage.

        Returns:
            (uncompressed_bytes, compressed_bytes)
        """
        return self._g.memory_usage()

    @property
    def starting_pot(self) -> int:
        """Starting pot size in chips."""
        return self._g.starting_pot()

    def __repr__(self) -> str:
        try:
            board = self.current_board()
        except Exception:
            board = "??"
        return f"<Solver board={board} player={self.current_player()} actions={self.available_actions()}>"


# -- helpers -----------------------------------------------------------

def _int_to_card_str(idx: int) -> str:
    ranks = "23456789TJQKA"
    suits = "cdhs"
    return f"{ranks[idx // 4]}{suits[idx % 4]}"


def _match_action(query: str, actions: list[str]) -> Optional[int]:
    q = query.strip().lower()
    for i, a in enumerate(actions):
        if a.lower() == q:
            return i
    for i, a in enumerate(actions):
        if q in a.lower():
            return i
    return None


def _player_idx(player: Optional[str], solver: "Solver") -> int:
    if player is None:
        return solver._g.current_player()
    p = player.strip().lower()
    if p in ("oop", "0"):
        return _OOP
    if p in ("ip", "1"):
        return _IP
    raise ValueError(f"Player must be 'oop' or 'ip', got '{player}'")
