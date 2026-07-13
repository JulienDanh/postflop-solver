"""Demo of the friendly Python API.

Build first:  maturin develop --features python
Run:          python examples/python_demo.py
"""
from postflop_solver import Solver

s = Solver(
    oop_range="66+,A8s+,A5s-A4s,AJo+,K9s+,KQo,QTs+,JTs,96s+,85s+,75s+,65s,54s",
    ip_range="QQ-22,AQs-A2s,ATo+,K5s+,KJo+,Q8s+,J8s+,T7s+,96s+,86s+,75s+,64s+,53s+",
    board="Td9d6hQc",
    starting_pot=200,
    effective_stack=900,
    bet_sizes="60%, e, a",
    raise_sizes="2.5x",
    river_donk_sizes="50%",
)

mem, _ = s.memory_usage()
print(f"Memory: {mem / 1e9:.2f} GB")

s.allocate_memory()
s.solve(iterations=1000, verbose=True)
print(f"Exploitability: {s.exploitability():.2f}")

print(f"Average equity (OOP): {100 * s.average_equity('oop'):.1f}%")
print(f"Average EV (OOP): {s.average_ev('oop'):.1f}")

strat = s.strategy()
print(f"Available actions: {s.available_actions()}")
first_hand = list(strat.keys())[0]
print(f"Strategy for {first_hand}: {strat[first_hand]}")

s.play("Bet(120)")
print(f"After cbet: player={s.current_player()}, actions={s.available_actions()}")
s.back_to_root()
