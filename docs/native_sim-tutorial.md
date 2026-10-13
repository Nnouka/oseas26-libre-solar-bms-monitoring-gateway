# Run the Libre Solar BMS firmware with Zephyr `native_sim`

This tutorial walks through building the Libre Solar BMS firmware as a program for your own computer,
running it, talking to it with ThingSet commands, and watching the live data stream it produces. It
assumes Windows with WSL2 Ubuntu, or a Linux machine. All commands are for the Ubuntu terminal.

> **Verified.** The commands and outputs below were run on 2026-10-13 on Ubuntu 24.04 (x86-64) with
> `bms-firmware` revision `779d201`, Zephyr 4.4.2 and ThingSet SDK `8b32f7d`. If you build a newer
> revision, small differences are normal.

> **Important:** `native_sim` runs the **real firmware application** on your computer, but it replaces
> the BQ76952 battery-monitor chip with an emulator that is only a block of empty memory. The firmware
> boots and runs its state machine, configuration storage and ThingSet interface, but there are **no
> cells**: every voltage, current and temperature reads zero. Use it to learn the real message format,
> field names, defaults and behaviour. Do not use it as proof that battery protection works, and do
> not expect realistic battery data from it.
>
> It is separate from this project's Python/MQTT BMS simulator (`services/bms-simulator`). The
> findings from this exercise are written up in [firmware-findings.md](firmware-findings.md), and the
> simulator is meant to mimic what you see here.

## What you will have at the end

- The Libre Solar firmware and its Zephyr dependencies checked out in a West workspace.
- A `native_sim` build of the firmware running in a terminal.
- A session in the ThingSet shell where you read `Meas`, `Conf` and `Input` and change a setting.
- The live `#mLive` data stream the firmware sends once per second, captured in a second terminal.

## Why use WSL2/Linux here?

Zephyr's `native_sim` is a POSIX host simulator and is meant for Linux or macOS. On Windows, use
WSL2 Ubuntu. Run the commands in the **Ubuntu/WSL terminal**, not PowerShell, Git Bash or the Docker
Compose terminal. Keep the Zephyr source tree inside the WSL Linux filesystem (for example `~/work`),
not under `/mnt/c`, to avoid slow builds and permission problems.

You do not need the project's Docker Compose stack for this tutorial.

## 1. Open Ubuntu in WSL2

Open the **Ubuntu** app. From PowerShell you can check that it is WSL2:

```powershell
wsl --list --verbose
```

The Ubuntu distribution should show version `2`. If WSL is not installed, follow
[Install WSL](https://learn.microsoft.com/windows/wsl/install), then launch Ubuntu from the Start menu.

In the Ubuntu terminal, confirm the environment:

```bash
uname -a
```

The output should identify Linux. If it says `MINGW` you are in Git Bash; open Ubuntu instead.

## 2. Install Linux build prerequisites

```bash
sudo apt update
sudo apt install --no-install-recommends \
  git cmake ninja-build gperf ccache dfu-util device-tree-compiler wget \
  python3-dev python3-pip python3-setuptools python3-tk python3-venv \
  xz-utils file make gcc g++ gcc-multilib g++-multilib libsdl2-dev
```

`gcc-multilib` and `g++-multilib` matter: Zephyr's default `native_sim` is a **32-bit** program, and
without these packages the build cannot link it. (If you cannot install them, see "64-bit build" in
the troubleshooting section.)

Check the main tools:

```bash
git --version
cmake --version
ninja --version
gcc --version
python3 --version
```

## 3. Create a Python virtual environment and install West

```bash
mkdir -p ~/work
cd ~/work
python3 -m venv ~/work/zephyr-venv
source ~/work/zephyr-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install west
west --version
```

In every new Ubuntu terminal, activate the environment again:

```bash
source ~/work/zephyr-venv/bin/activate
```

## 4. Create the Libre Solar West workspace

```bash
cd ~/work
west init -m https://github.com/LibreSolar/bms-firmware west-workspace
cd ~/work/west-workspace/bms-firmware
west update
```

`west update` downloads Zephyr and several modules (several GB; let it finish). The workspace looks
like this:

```text
~/work/west-workspace/
├── .west/                  # West workspace metadata
├── bms-firmware/           # Libre Solar application and manifest
├── zephyr/                 # Zephyr RTOS source
├── thingset-zephyr-sdk/    # ThingSet SDK
└── modules/                # Zephyr and vendor modules
```

Install the Python packages that the checked-out Zephyr needs:

```bash
west packages pip --install
```

If you do not have the Zephyr SDK installed, tell Zephyr to use your normal compiler. Add this to
your `~/.bashrc` or run it in every new terminal:

```bash
export ZEPHYR_TOOLCHAIN_VARIANT=host
```

## 5. Check the workspace

```bash
cd ~/work/west-workspace/bms-firmware
west topdir
west list
```

`west topdir` prints `~/work/west-workspace`. `west list` includes `bms-firmware`, `zephyr` and
`thingset-zephyr-sdk`.

## 6. Build and run the firmware

> **Build from the `app` folder.** The repository root is a Zephyr *module*, not the application.
> Running `west build` there fails with `Unknown CMake command "zephyr_include_directories"`. The
> firmware's own CI also starts with `cd app`.

```bash
cd ~/work/west-workspace/bms-firmware/app
west build -b native_sim -p auto
```

The first build takes a few minutes (about 350 build steps). Then run it:

```bash
./build/zephyr/zephyr.exe
```

(`west build -t run` builds and runs in one go.) Stop it with **Ctrl+C**. Startup looks like this
(colours and some repeated lines removed):

```text
WARNING: Using a test - not safe - entropy source
uart_1 connected to pseudotty: /dev/pts/1
Warning: Bluetooth device missing.
[00:00:00.000,000] <inf> emul: Registering 1 emulator(s) for i2c@100
[00:00:00.000,000] <inf> i2c_emul_ctlr: Register emulator 'bq76952@8' at I2C addr 08
[00:00:00.010,000] <err> can_native_linux: Cannot open vcan0 (-97)
*** Booting Zephyr OS build 6d51db239667 ***
[00:00:00.010,000] <inf> thingset_sdk: ThingSet Node ID (EUI-64): 9C232EA9CABFB9A2
[00:00:00.010,000] <inf> thingset_storage_eeprom: EEPROM empty, keeping default values for data objects
[00:00:00.010,000] <err> bt_hci_core: HCI driver is not ready
[00:00:00.010,000] <inf> bms_main: Hardware: Libre Solar BMS Simulator (v0.1)
[00:00:00.010,000] <err> thingset_can: CAN device not ready
[00:00:00.510,000] <inf> bms_ic_bq769x2: Detected 0 cells
[00:00:00.570,000] <inf> bms_ic_bq769x2: Activated BMS IC 0x0
[00:00:01.630,000] <inf> bms: OFF -> DIS (error flags: 0x00000000)
[00:00:02.130,000] <inf> bms: DIS -> NORMAL (error flags: 0x00000000)
```

How to read it:

- **Errors about `vcan0`, `bt_hci_core`, `thingset_can` and `Bluetooth` are expected.** There is no
  virtual CAN interface and no Bluetooth adapter. The firmware carries on.
- `uart_1 connected to pseudotty: /dev/pts/1` is the ThingSet **serial port**. Note the path; step 8
  uses it. Your number may differ.
- `Detected 0 cells` is the emulated chip with no cells.
- `OFF -> DIS -> NORMAL` is the firmware's real state machine starting up.

## 7. Talk to the firmware with ThingSet

The firmware's shell runs in the same terminal (prompt `uart:~$`; log lines are interleaved with
what you type). Switch it into ThingSet mode:

```text
uart:~$ select thingset
```

Now type ThingSet requests. `?` reads, `=` writes, `!` runs a function.

```text
?
```

lists the groups and the live subset:

```text
:85 {"Device":null,"Conf":null,"Meas":null,"Input":null,"pNodeID":"9C232EA9CABFB9A2","pNodeName":"Libre Solar BMS","Networking":null,"_Reporting":null,"mLive":["Meas/rPackVoltage_V","Meas/rStackVoltage_V","Meas/rPackCurrent_A","Meas/rCellTemps_degC","Meas/rICTemp_degC","Meas/rMOSFETTemp_degC","Meas/rSOC_pct","Meas/rErrorFlags","Meas/rBmsState","Meas/rCellVoltages_V","Meas/rCellAvgVoltage_V","Meas/rCellMinVoltage_V","Meas/rCellMaxVoltage_V","Meas/rBalancingStatus"]}
```

`:85` is the response status ("Content"). Next, the measurements:

```text
?Meas
```

```text
:85 {"rPackVoltage_V":0.00,"rStackVoltage_V":0.00,"rPackCurrent_A":0.00,"rCellTemps_degC":[-273.1,0.0],"rICTemp_degC":-273.1,"rMOSFETTemp_degC":-273.1,"rSOC_pct":0.0,"rErrorFlags":0,"rBmsState":3,"rCellVoltages_V":[0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000],"rCellAvgVoltage_V":null,"rCellMinVoltage_V":10.000,"rCellMaxVoltage_V":0.000,"rBalancingStatus":0}
```

All zeros, as expected with no cells. Things worth noticing: the cell array has **16** entries,
temperatures read `-273.1` (0 K, no sensor), the average is `null` (0 / 0), the minimum is `10.000`
(the driver's starting value), and `rBmsState` is `3` (NORMAL).

The configuration, with the LiFePO4 defaults:

```text
?Conf
```

```text
:85 {"sNominalCapacity_Ah":50.0,"sShortCircuitLimit_A":80.0,"sShortCircuitDelay_us":195,"sDisOvercurrent_A":52.0,"sDisOvercurrentDelay_ms":320,"sChgOvercurrent_A":52.0,"sChgOvercurrentDelay_ms":320,"sDisMaxTemp_degC":45.0,"sDisMinTemp_degC":-20.0,"sChgMaxTemp_degC":45.0,"sChgMinTemp_degC":0.0,"sTempLimitHysteresis_degC":5.0,"sCellOvervoltage_V":3.8,"sCellOvervoltageReset_V":3.4,"sCellOvervoltageDelay_ms":1999,"sCellUndervoltage_V":2.5,"sCellUndervoltageReset_V":3.1,"sCellUndervoltageDelay_ms":1999,"sBalTargetVoltageDiff_V":0.010,"sBalMinVoltage_V":3.3,"sBalIdleDelay_s":1800,"sBalIdleCurrent_A":0.1,"xPresetNMC":["fCapacity_Ah"],"xPresetLFP":["fCapacity_Ah"],"xPresetLTO":["fCapacity_Ah"],"sOcvPoints_V":[3.392,3.314,3.309,3.308,3.304,3.296,3.283,3.275,3.271,3.268,3.265,3.264,3.262,3.252,3.240,3.226,3.213,3.190,3.177,3.132,2.833],"sSocPoints_pct":[100.0,95.0,90.0,85.0,80.0,75.0,70.0,65.0,60.0,55.0,50.0,45.0,40.0,35.0,30.0,25.0,20.0,15.0,10.0,5.0,0.0]}
```

Now change something. The Zephyr shell eats plain double quotes, so **escape them with a backslash**:

```text
=Input {\"wChgEnable\":false}
```

```text
:84
[00:00:04.630,000] <inf> bms: NORMAL -> DIS (error flags: 0x00000000)
```

`:84` means "Changed". The state machine reacted at once: charging is now disabled, so the state went
from NORMAL to DIS. Confirm and put it back:

```text
?Meas/rBmsState
=Input {\"wChgEnable\":true}
```

`rBmsState` reads `2` (DIS) in between and returns to `3` (NORMAL) afterwards.

> **If you forget the backslashes** (`=Input {"wChgEnable":false}`) you get `:A0`, which is the
> ThingSet "bad request" error, with no explanation.

You can also write configuration, for example `=Conf {\"sNominalCapacity_Ah\":100}`; it is saved to
the emulated EEPROM (`EEPROM save: ...`). Be aware that on `native_sim` the emulated chip may start
failing reads after a reconfiguration (`Failed to read data from BMS IC: -5`); restart the program
(its EEPROM is not kept between runs) if that bothers you.

## 8. Watch the live data stream

The ThingSet **serial port** is a separate channel from the shell. Leave the firmware running and
open a **second Ubuntu terminal**. Use the pseudo-terminal path printed at startup (here
`/dev/pts/1`):

```bash
stty -F /dev/pts/1 raw -echo
cat /dev/pts/1
```

One line arrives every second, with a CRC at the end:

```text
#mLive {"Meas":{"rPackVoltage_V":0.00,"rStackVoltage_V":0.00,"rPackCurrent_A":0.00,"rCellTemps_degC":[-273.1,0.0],"rICTemp_degC":-273.1,"rMOSFETTemp_degC":-273.1,"rSOC_pct":0.0,"rErrorFlags":0,"rBmsState":3,"rCellVoltages_V":[0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000],"rCellAvgVoltage_V":null,"rCellMinVoltage_V":10.000,"rCellMaxVoltage_V":0.000,"rBalancingStatus":0}} 437123CA#
```

This line is exactly what a real BMS C1 sends on its UART (at 115200 baud) and what the Cicada relay
would have to forward. Its parts: `#` statement marker, `mLive` the subset name, the JSON payload, a
space, the CRC-32 in upper-case hex, and a closing `#`.

Check the CRC yourself. The rule: CRC-32 over the text from the first `#` up to, not including, the
space before the CRC.

```python
import zlib

line = '#mLive {...paste the JSON here...} 437123CA#'
body, crc = line[:-1].rsplit(" ", 1)
print(crc, f"{zlib.crc32(body.encode()):08X}")   # the two values must match
```

Press **Ctrl+C** in both terminals when you are done.

## 9. What to look for, and what not to expect

Do **not** assume that:

- Cell voltages, current or temperatures come from any battery model. They are all zero.
- The BQ76952's registers or protections are emulated. The chip is empty memory.
- Over/under-voltage, over-current, temperature, balancing or MOSFET behaviour has been checked.
- The firmware publishes MQTT. It does not: it speaks ThingSet over serial, CAN and Bluetooth only.
- Bluetooth or CAN work. They need a Bluetooth adapter or a virtual CAN interface
  (`sudo ip link add dev vcan0 type vcan && sudo ip link set up vcan0`) and are not needed here.

Do take from it:

- The exact field names, units, decimals and the `#mLive` line format.
- The default configuration and the state machine.
- That the line carries no timestamp and no device id (the gateway has to add both).

See [firmware-findings.md](firmware-findings.md) for the full write-up.

## Troubleshooting

### `Unknown CMake command "zephyr_include_directories"`

You ran `west build` in the repository root. Change to the application folder first:

```bash
cd ~/work/west-workspace/bms-firmware/app
west build -b native_sim -p auto
```

### `west: command not found`

```bash
source ~/work/zephyr-venv/bin/activate
west --version
```

### The build says no toolchain was found

```bash
export ZEPHYR_TOOLCHAIN_VARIANT=host
```

### The link step fails with 32-bit errors

Install `gcc-multilib` and `g++-multilib` (step 2), then rebuild with `-p auto`.

### 64-bit build (no multilib)

Zephyr's 64-bit variant does not pick up the app's `native_sim` files by itself, so pass them
explicitly:

```bash
cd ~/work/west-workspace/bms-firmware/app
west build -b native_sim/native/64 -d build64 -p -- \
  -DEXTRA_CONF_FILE=boards/native_sim.conf \
  -DEXTRA_DTC_OVERLAY_FILE=boards/native_sim.overlay
./build64/zephyr/zephyr.exe
```

Without those two options the build fails in `bms_soc.c` (`'struct bms_ic_data' has no member named
'current'`) and `button.c`, because the emulated chip and its devicetree are missing.

### `:A0` when writing a value

Escape the double quotes with backslashes (step 7).

### `west build` says the board is unknown

```bash
cd ~/work/west-workspace/bms-firmware
west update
west boards | grep native_sim
```

### The process exits immediately or the log is unclear

Separate build from run and keep the first error above the final `FATAL ERROR`:

```bash
cd ~/work/west-workspace/bms-firmware/app
west build -p -b native_sim
./build/zephyr/zephyr.exe --help
./build/zephyr/zephyr.exe
```

`-p` asks West for a pristine build; it does not delete your source repositories.

### I want to use Docker

Keep the project's `docker-compose.yml` for the gateway services. The firmware build needs West, the
Zephyr sources and a native 32-bit compiler; containerise it separately if you want to, mounting the
West workspace into a development container. For a first run, WSL2 Ubuntu is the simplest place to
read compiler errors.

## Sources

- [Libre Solar BMS simulator documentation](https://libre.solar/bms-firmware/src/dev/simulator.html)
- [Libre Solar West workspace setup](https://libre.solar/bms-firmware/src/dev/workspace_setup.html)
- [Zephyr Getting Started Guide](https://docs.zephyrproject.org/latest/develop/getting_started/index.html)
- [Zephyr `native_sim` documentation](https://docs.zephyrproject.org/latest/boards/native/native_sim/doc/index.html)
- [ThingSet protocol](https://thingset.io)
