# AGENTS.md

Guide for AI agents working on this codebase. Read this before making changes.

## What this is

A Texas hold'em postflop poker solver library in Rust. Uses the Discounted CFR
algorithm. Two-player (OOP vs IP) postflop only — no preflop, no multi-way. The
crate is a library; `examples/` and `tests/` show how to drive it.

## Commands

```sh
# Build (default features: bincode + rayon)
cargo build

# Build with all features (adds zstd compression for save/load)
cargo build --features zstd

# Smoke test (~6s, 4 tests) — one solve + compressed + isomorphism + terminal
./scripts/smoke.sh

# Fast test subset (~17s, 26 tests) — skips slow bunching & all-hands tests
cargo test --lib -- --skip bunching --skip test_all_hands --skip set_bunching

# Full test suite (~6 min — hand evaluation iterates all 133M 7-card combos)
cargo test --lib

# Integration tests (Kuhn & Leduc poker, ~seconds)
cargo test --test kuhn --test leduc

# Run an example end-to-end
cargo run --release --example basic

# Lint (CI runs with --deny warnings)
cargo clippy -- -A clippy::needless_range_loop

# Format check (CI enforces)
cargo fmt --all --check
```

Always run **`./scripts/smoke.sh`** after touching solver, utility, sliceop, or
game modules — it's the fastest signal that the solver still works (~6s). Run
the **fast test subset** before committing. Run the full suite when touching
`hand.rs` or `bunching.rs`.

## Architecture

### Data flow: config → tree → solve → query

```
CardConfig + TreeConfig + ActionTree
        ↓
  PostFlopGame::with_config()
        ↓
  allocate_memory(compress: bool)
        ↓
  solve() / solve_step() + finalize()
        ↓
  equity() / expected_values() / strategy() / available_actions()
```

### Module map (src/)

| Module | Role |
|--------|------|
| `lib.rs` | Crate root, feature gates, module wiring |
| `interface.rs` | `Game` + `GameNode` traits — the solver's abstract interface to any game |
| `solver.rs` | Discounted CFR: `solve()`, `solve_step()`, regret matching |
| `utility.rs` | `finalize()`, `compute_exploitability()`, `compute_current_ev()`, slice ops dispatch, compression encode/decode |
| `sliceop.rs` | Hot-path slice operations (sum, fma, max, inner_product) with manual unrolling for SIMD |
| `atomic_float.rs` | `AtomicF32`/`AtomicF64` — f32/f64 stored as atomic bits |
| `mutex_like.rs` | `MutexLike<T>` — `UnsafeCell` wrapper with no locking; safety is manual |
| `card.rs` | `Card` (=u8), `CardConfig`, card pair indexing, isomorphism detection |
| `range.rs` | `Range` — 52*51/2 float array of hand weights; string parsing (PioSOLVER-style) |
| `hand.rs` | 7-card hand evaluator (bit tricks, no table lookup except final index) |
| `hand_table.rs` | Static lookup table mapping raw hand values → ranking indices |
| `bet_size.rs` | `BetSize` enum, `BetSizeOptions`/`DonkSizeOptions` parsing from strings |
| `action_tree.rs` | `Action`, `BoardState`, `TreeConfig`, `ActionTree` — abstract game tree builder |
| `bunching.rs` | `BunchingData` — handles folded-card distribution (bunching effect) |
| `file.rs` | Save/load `PostFlopGame` or `BunchingData` to/from files (bincode ± zstd) |

### game/ submodule

| File | Role |
|------|------|
| `mod.rs` | `PostFlopGame` struct + `PostFlopNode` struct definitions |
| `base.rs` | `impl Game for PostFlopGame` — trait wiring, tree building, memory allocation |
| `node.rs` | `impl GameNode for PostFlopNode` — strategy/regret/cfvalue accessors via raw pointers |
| `evaluation.rs` | Terminal node evaluation — pot math, hand comparison, bunching eval |
| `interpreter.rs` | Public query API: `play()`, `strategy()`, `equity()`, `expected_values()`, `available_actions()` |
| `serialization.rs` | bincode Encode/Decode for `PostFlopGame` (feature-gated) |
| `tests.rs` | Integration tests for the game module |

### Key abstractions

**`Game` / `GameNode` traits** (`interface.rs`): The solver is generic over any
game implementing these traits. `PostFlopGame` is the real implementation;
`tests/kuhn.rs` and `tests/leduc.rs` provide minimal test games. Many trait
methods have default implementations that `unreachable!()` — they only matter
when compression or chance nodes are involved.

**`PostFlopNode`** (`game/mod.rs:122`): A `#[repr(C)]` struct storing raw
`*mut u8` pointers into global storage vectors (`storage1/2/3`). This is the
hot data structure — every strategy/regret/cfvalue access goes through
`slice::from_raw_parts` on these pointers. The `player` field is a bitfield:
bits 0-1 are the player (0=OOP, 1=IP), bit 2 is chance flag, bit 3 is terminal,
bits 3-4 combined signal fold.

## Critical invariants

### Unsafe Rust

This codebase makes heavy use of `unsafe`. Do not add unsafe code casually, and
do not remove existing unsafe without understanding why it's there.

- **`MutexLike`** provides `Send`/`Sync` with **no actual locking**. It's safe
  only because the solver guarantees no data races by construction (each thread
  works on disjoint subtrees). If you change the parallelization in
  `utility.rs`/`solver.rs`, you must verify this still holds.
- **Raw pointer storage in `PostFlopNode`**: `storage1/2/3` point into
  `PostFlopGame`'s `storage1`/`storage2`/`storage_ip`/`storage_chance` Vecs.
  These Vecs must not be reallocated after nodes are created. Any code that
  mutates these Vecs' capacity is a use-after-free risk.
- **`MaybeUninit` transmutation**: `sliceop.rs` casts
  `&mut [MaybeUninit<f32>]` to `&mut [f32]` after writing. This is sound only
  if every element was written before the cast.

### Performance-critical patterns

- **`get_unchecked` everywhere in `sliceop.rs`**: Bounds checks are intentionally
  skipped. The caller must ensure correct lengths.
- **f32 for storage, f64 for accumulation**: Strategy/regret/cfvalue are f32.
  Summations use f64 intermediates (`sum_slices_f64_uninit`, `inner_product`).
  Do not "simplify" f64→f32 — it loses precision that the algorithm depends on.
- **Manual 8-element unrolling**: `sliceop.rs` functions unroll by 8 to help
  the compiler auto-vectorize. Don't replace with simple iterators without
  checking the generated assembly.
- **`#[inline]` on hot functions**: Many functions are `#[inline]` for a reason.
  Don't remove it without benchmarking.

### Compression

When `is_compression_enabled` is true, strategy/regret/cfvalue are stored as
i16/u16 with a per-node f32 scale factor. The `GameNode` trait has paired
methods (`strategy()` vs `strategy_compressed()`, etc.). The solver checks
`is_compression_enabled()` and dispatches accordingly. If you add a new storage
path, you must handle both compressed and uncompressed cases.

## Feature flags

- `bincode` (default): Serialization via bincode 2.0.0-rc.3. Required for
  save/load.
- `rayon` (default): Parallel `for_each_child`. Without it, everything is
  single-threaded.
- `zstd`: Optional zstd compression for save/load files.

All `#[cfg(feature = ...)]` branches must be maintained in sync.

## Testing notes

- `hand::tests::test_all_hands` iterates all C(52,7) ≈ 133M combinations (~5
  min). Skip it during iteration with `--skip test_all_hands`.
- Bunching tests (`test_bunching_independent_*`, `set_bunching_effect*`) take
  60+ seconds each. Skip with `--skip bunching --skip set_bunching`.
- `game::tests` has both compressed and uncompressed variants — run both when
  touching storage or evaluation code.
- `tests/kuhn.rs` and `tests/leduc.rs` implement minimal poker games against the
  `Game` trait — good for verifying solver correctness changes.

## Node locking

Node locking forces a player's strategy at a specific node to a fixed
distribution, then solves the rest of the tree as a best response. This is
how you model exploitative play (e.g., "IP folds 90% to cbet" → solve for
OOP's maximally exploitative strategy).

### Lifecycle

```
allocate_memory()
  → play(action) to navigate to the target node
  → lock_current_strategy(&[f32])
  → back_to_root()
  → solve() / solve_step()
```

To remove a lock, repeat the same navigation after `allocate_memory()` and call
`unlock_current_strategy()`. Locks persist across `allocate_memory()` calls —
calling `allocate_memory(false)` to reset for a new solve does **not** clear
them.

###Strategy array layout

`lock_current_strategy(strategy)` takes a slice of length
`num_actions * num_hands`, laid out **action-major, hand-minor**:

```
[a0h0, a0h1, ..., a0hN, a1h0, a1h1, ..., a1hN, ..., aMh0, ..., aMhN]
```

- `num_actions` = `game.available_actions().len()`
- `num_hands` = `game.private_cards(player).len()` where `player` is the
  current player at the locked node
- `a0` is the first action (e.g., Check or Fold), `a1` is the second, etc.

###Locking rules

- A hand is **locked** if any action's value for that hand is > 0.0.
- A hand is **unlocked** (solver can adjust freely) if all actions' values
  for that hand are ≤ 0.0.
- For locked hands, the frequencies are normalized so actions sum to 1.0.
- Negative values are treated as zero.

This means you can lock some hands while leaving others free, e.g., lock only
the bluffing range and let the solver optimize value hands.

###Reading OOP's strategy at the root

After solving, call `game.strategy()` **at the root node** (before any `play()`)
to read the cbet decision. The strategy array uses the same action-major layout:
`[check_freq * N, bet_freq * N, ...]`.

###Gotchas

- `play()` invalidates the normalized weights cache. Call
  `cache_normalized_weights()` before reading `strategy()`, `equity()`,
  `expected_values()`, or `normalized_weights()` after navigating.
- `normalized_weights()` returns **raw combo counts**, not 0-1 probabilities.
  Divide by the sum to get range percentages.
- `solve()` takes `&mut T` but `tree_config()` takes `&self` — extract the
  target exploitability into a variable before calling `solve()`.

###See also

- `examples/exploit_fold_to_cbet.rs` — full worked example
- `examples/node_locking.rs` — simpler toy example with known solutions
- `src/game/interpreter.rs:870` — `lock_current_strategy` implementation
- `src/utility.rs:628` — `apply_locking_strategy` (how locks override the
  solver's computed strategy during CFR)

- No comments unless explaining non-obvious `unsafe` or algorithmic intent.
- `#[inline]` on small hot functions.
- Match existing import ordering: stdlib, then external crates, then `crate::*`.
- `#[allow(clippy::needless_range_loop)]` is used where indexed loops are
  intentional (vectorization).
