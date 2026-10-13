"""Part 5/6: the firmware's belief versus the truth, one line per 6 hours for 3 days."""
from sim import Simulator

sim = Simulator(seed=1)
print("  day  hour  state   soc_fw%  soc_true%  flags")
for step in range(3 * 4):
    sim.step(6 * 3600)
    snap, dbg = sim.snapshot(), sim.debug()
    print(f"{step * 6 // 24 + 1:5d} {dbg['sim_hour']:5.1f}  {snap['state']:7} {snap['soc_pct']:7.1f}"
          f" {dbg['true_soc_pct']:10.1f}  {snap['errors'] or '-'}")
