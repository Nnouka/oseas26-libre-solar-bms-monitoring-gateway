# Part 0: Run the real firmware

**Goal.** See with your own eyes what the Libre Solar BMS firmware does, so that the simulator you build can copy it.
You will end with a captured `#mLive` line that becomes the target of the golden test in Part 5.

**Time.** 1.5 to 3 hours, mostly waiting for downloads and the first build.

## Ideas

- **Firmware is just a program.** Zephyr, the real-time OS the BMS uses, can compile it for your laptop as a program
  called `native_sim`. The BMS application code is the real thing. Only the hardware is faked.
- **The BQ76952 chip is the sensor.** On the real board the chip measures cell voltages, current and temperatures.
  In `native_sim` it is replaced by a block of empty memory, so every reading is zero. This is not a bug. It means you
  will see the *format* and the *behaviour* of the firmware but not realistic battery data.
- **ThingSet** is the protocol the BMS speaks. It has a request/response shell (you type `?Meas`) and a data
  stream (`#mLive`, sent unasked).

## Do this

Follow **[docs/native_sim-tutorial.md](../../../docs/native_sim-tutorial.md)** from start to finish. It was
run command by command on a clean machine, so if something differs from its expected output, stop and compare
before going on. Use WSL2 Ubuntu on Windows.

Then read **[docs/firmware-findings.md](../../../docs/firmware-findings.md)**, sections 1 to 5, once through. You do not need to memorise it. You will come back to its tables in Parts 3 and 5.

## Your answer sheet

Fill this in from your own terminal, not from the document. Writing it down is the point.

| # | Question | Where to look | Your answer |
| --- | --- | --- | --- |
| 1 | What are the 14 items in `#mLive`, in order? | the captured line | |
| 2 | How many decimals does each use? | the captured line | |
| 3 | What does the firmware print for a value that is "not a number"? | `rCellAvgVoltage_V` | |
| 4 | What do `rBmsState` 0, 1, 2, 3 mean? | firmware-findings.md | |
| 5 | Which `Conf` value says how long the pack must be idle before balancing starts? | `?Conf` | |
| 6 | What is the over-voltage trip level, and at which voltage does it clear? | `?Conf` | |
| 7 | Does the stream contain a timestamp? A device id? The number of cells? | the captured line | |
| 8 | Which two `Conf` items hold the voltage-to-charge table, and in which order are the 21 points listed? | `?Conf` | |

<details>
<summary>Answers (check yourself after filling in the table)</summary>

1. `rPackVoltage_V, rStackVoltage_V, rPackCurrent_A, rCellTemps_degC, rICTemp_degC, rMOSFETTemp_degC, rSOC_pct, rErrorFlags, rBmsState, rCellVoltages_V, rCellAvgVoltage_V, rCellMinVoltage_V, rCellMaxVoltage_V, rBalancingStatus`
2. Pack/stack voltage and current: 2. Temperatures and SoC: 1. Cell voltages, average, min and max: 3. Flags, state and balancing mask are integers.
3. `null`.
4. 0 OFF, 1 CHG (only charging allowed), 2 DIS (only discharging allowed), 3 NORMAL (both), 4 SHUTDOWN.
5. `sBalIdleDelay_s` (1800 s, thirty minutes), together with `sBalIdleCurrent_A` (0.1 A).
6. Trips at 3.80 V and clears below 3.40 V (LFP defaults).
7. No, no, no. Whoever receives the line has to add the time and the device id, and count the cells.
8. `sOcvPoints_V` and `sSocPoints_pct`, from 100 % down to 0 % in 5 % steps (so 3.392 V is 100 % and 2.833 V is 0 %). Part 4 reuses this table.

</details>

## The three things to carry into the next parts

1. **The line.** Keep the exact `#mLive` line in a text file. In Part 5 your code must print the same line, character for character, for the same (empty) chip.
2. **The defaults.** Over-voltage 3.80/3.40, under-voltage 2.50/3.10, temperature limits 45 and 0, balancing after 1800 s, 16 cell slots.
3. **What native_sim cannot tell you.** It cannot show realistic cell voltages, currents, heat or State of Charge. For those, the simulator needs a model of the physical battery. That model is Part 4.

## Checkpoint

- You can start and stop `native_sim`.
- You captured at least two `#mLive` lines and your own CRC check says they are valid.
- The answer sheet is complete.

Commit nothing yet (the firmware lives outside this repository). Go on to [Part 1](01-project-setup.md).
