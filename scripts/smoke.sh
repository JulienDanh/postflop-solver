#!/usr/bin/env bash
# Fast correctness smoke test (~6s): exercises the real solver (PostFlopGame)
# including the CFR solve loop, compression, isomorphism, and terminal eval.
# Run this after any change to the solver, utility, sliceop, or game modules.
#
# For broader coverage, use:
#   cargo test --lib -- --skip bunching --skip test_all_hands --skip set_bunching
# (~17s, 26 tests)
#
# For the full suite (including 133M hand evaluation):
#   cargo test --lib
# (~6 min)
set -e
cargo test --lib --quiet -- --test-threads=4 \
    game::tests::node_locking \
    game::tests::always_win \
    game::tests::one_raise_all_range \
    game::tests::isomorphism_monotone
