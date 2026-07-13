import postflop_solver as ps

game = ps.PostFlopGame(
    oop_range="66+,A8s+,A5s-A4s,AJo+,K9s+,KQo,QTs+,JTs,96s+,85s+,75s+,65s,54s",
    ip_range="QQ-22,AQs-A2s,ATo+,K5s+,KJo+,Q8s+,J8s+,T7s+,96s+,86s+,75s+,64s+,53s+",
    flop="Td9d6h",
    turn="Qc",
    river=None,
    initial_state="turn",
    starting_pot=200,
    effective_stack=900,
    flop_bet_sizes=[("60%, e, a", "2.5x"), ("60%, e, a", "2.5x")],
    turn_bet_sizes=[("60%, e, a", "2.5x"), ("60%, e, a", "2.5x")],
    river_bet_sizes=[("60%, e, a", "2.5x"), ("60%, e, a", "2.5x")],
    river_donk_sizes="50%",
)

mem, mem_compressed = game.memory_usage()
print(f"Memory: {mem / 1e9:.2f} GB (uncompressed), {mem_compressed / 1e9:.2f} GB (compressed)")

game.allocate_memory(compress=False)
game.solve(max_iterations=1000, target_exploitability=200 * 0.005, verbose=True)
print(f"Exploitability: {game.exploitability():.2f}")

game.cache_normalized_weights()

equity = game.equity(0)
ev = game.expected_values(0)
weights = game.normalized_weights(0)
avg_eq = ps.compute_average_py(equity, weights)
avg_ev = ps.compute_average_py(ev, weights)
print(f"Average equity (OOP): {100 * avg_eq:.1f}%")
print(f"Average EV (OOP): {avg_ev:.1f}")

actions = game.available_actions()
print(f"Available actions: {actions}")

hands = game.private_cards(0)
strategy = game.strategy()
print(f"First hand: {hands[0]}, strategy: {strategy[:len(actions)]}")
print(f"Board: {game.current_board()}")
