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
        self._g.allocate_memory(compress)

    def solve(
        self,
        iterations: int = 1000,
        target_exploitability: Optional[float] = None,
        verbose: bool = False,
    ) -> float:
        if target_exploitability is None:
            target_exploitability = self._g.starting_pot() * 0.005
        return self._g.solve(iterations, target_exploitability, verbose)

    def solve_step(self, iteration: int) -> None:
        self._g.solve_step(iteration)

    def finalize(self) -> None:
        self._g.finalize()

    def exploitability(self) -> float:
        return self._g.exploitability()

    @property
    def is_solved(self) -> bool:
        return self._g.exploitability() > 0

    # -- navigation ----------------------------------------------------

    def play(self, action) -> None:
        if isinstance(action, str):
            actions = self.available_actions()
            idx = _match_action(action, actions)
            if idx is None:
                raise ValueError(f"Action '{action}' not found in {actions}")
            action = idx
        self._g.play(action)
        self._cache_dirty = True

    def back_to_root(self) -> None:
        self._g.back_to_root()
        self._cache_dirty = True

    def available_actions(self) -> list[str]:
        return self._g.available_actions()

    def current_player(self) -> str:
        p = self._g.current_player()
        return "oop" if p == _OOP else "ip"

    def current_board(self) -> str:
        return "".join(self._g.current_board())

    def is_chance_node(self) -> bool:
        return self._g.is_chance_node()

    def is_terminal_node(self) -> bool:
        return self._g.is_terminal_node()

    def possible_cards(self) -> list[str]:
        mask = self._g.possible_cards()
        return [
            _int_to_card_str(i) for i in range(52) if mask & (1 << i)
        ]

    def history(self) -> list[int]:
        return self._g.history()

    # -- queries (auto-cache) -----------------------------------------

    def _ensure_cache(self) -> None:
        if self._cache_dirty:
            self._g.cache_normalized_weights()
            self._cache_dirty = False

    def strategy(self, player: Optional[str] = None) -> dict[str, dict[str, float]]:
        self._ensure_cache()
        p = _player_idx(player, self)
        hands = self._g.private_cards(p)
        actions = self.available_actions()
        raw = self._g.strategy()
        n = len(hands)
        na = len(actions)
        result = {}
        for h_idx, hand in enumerate(hands):
            result[hand] = {
                actions[a]: raw[a * n + h_idx] for a in range(na)
            }
        return result

    def equity(self, player: Optional[str] = None) -> dict[str, float]:
        self._ensure_cache()
        p = _player_idx(player, self)
        hands = self._g.private_cards(p)
        vals = self._g.equity(p)
        return dict(zip(hands, vals))

    def expected_values(self, player: Optional[str] = None) -> dict[str, float]:
        self._ensure_cache()
        p = _player_idx(player, self)
        hands = self._g.private_cards(p)
        vals = self._g.expected_values(p)
        return dict(zip(hands, vals))

    def average_equity(self, player: Optional[str] = None) -> float:
        self._ensure_cache()
        p = _player_idx(player, self)
        eq = self._g.equity(p)
        w = self._g.normalized_weights(p)
        return _avg(eq, w)

    def average_ev(self, player: Optional[str] = None) -> float:
        self._ensure_cache()
        p = _player_idx(player, self)
        ev = self._g.expected_values(p)
        w = self._g.normalized_weights(p)
        return _avg(ev, w)

    def range_percentages(self, player: Optional[str] = None) -> dict[str, float]:
        self._ensure_cache()
        p = _player_idx(player, self)
        hands = self._g.private_cards(p)
        w = self._g.normalized_weights(p)
        total = sum(w)
        return {h: (wt / total * 100.0 if total > 0 else 0.0) for h, wt in zip(hands, w)}

    def private_cards(self, player: Optional[str] = None) -> list[str]:
        p = _player_idx(player, self)
        return self._g.private_cards(p)

    def num_hands(self, player: Optional[str] = None) -> int:
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
        self._g.unlock_current_strategy()

    # -- save/load -----------------------------------------------------

    def save(self, path: str, memo: str = "", compression_level: Optional[int] = None) -> None:
        self._g.save(path, memo, compression_level)

    @staticmethod
    def load(path: str, max_memory: Optional[int] = None) -> "Solver":
        raw = _Game.load(path, max_memory)
        s = Solver.__new__(Solver)
        s._g = raw
        s._cache_dirty = True
        return s

    # -- misc ----------------------------------------------------------

    def memory_usage(self) -> tuple[int, int]:
        return self._g.memory_usage()

    @property
    def starting_pot(self) -> int:
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
