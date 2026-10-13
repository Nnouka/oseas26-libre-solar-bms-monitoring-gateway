"""Part 4: run the bare battery for two days with both switches closed (no firmware yet)."""
from physical_battery import Battery

b = Battery(seed=1, start_hour=0.0)
print(" hour   I(A)  true_soc%  cell_min  cell_max  temp_C")
for step in range(48 * 6):                         # 10-minute steps for two days
    reading = b.step(600, chg_on=True, dis_on=True, bleed_mask=0)
    if step % 18 == 17 and step < 24 * 6:          # print every 3 hours of day one
        cells = [v for v in reading.cell_v if v > 0.5]
        print(f"{b.hour_of_day():5.1f} {b.current_a:6.1f} {100 * sum(b.soc) / b.n:9.1f}"
              f" {min(cells):9.3f} {max(cells):9.3f} {b.temp_c:7.1f}")
