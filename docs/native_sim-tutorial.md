# Run the Libre Solar BMS firmware with Zephyr `native_sim`

This tutorial walks through setting up the Libre Solar BMS firmware simulator from a clean Linux environment, building it, starting it, and checking what it can and cannot simulate. It assumes you are using Windows with WSL2 Ubuntu, or a Linux machine. The commands below are for Ubuntu in a WSL2 terminal.

> **Important:** Zephyr's `native_sim` runs the firmware application as a program on your computer. Libre Solar documents this as a way to try ThingSet communication and basic firmware features. It does **not** emulate the BQ76952 battery-monitor chip, real cell voltages, MOSFET power paths, or a complete BMS C1 board. Treat it as a firmware/protocol experiment, not as proof that battery protection works.
>
> This is separate from the hackathon's planned Python/MQTT BMS simulator. `native_sim` helps explore the real BMS firmware; the Python/MQTT simulator will generate representative messages for the gateway and dashboards. The two can complement each other, but one does not replace the other.

## What you will have at the end

- The Libre Solar firmware and its Zephyr dependencies checked out in a West workspace.
- A `native_sim` build of the Libre Solar BMS firmware.
- The simulator process running in a terminal, with its startup output visible.
- A clear idea of the next step for trying the ThingSet interface.

## Why use WSL2/Linux here?

Zephyr's `native_sim` is a POSIX host simulator, and Libre Solar's documentation describes it for Linux or macOS. On Windows, use WSL2 Ubuntu for this tutorial. Run the commands in the **Ubuntu/WSL terminal**, not PowerShell, Git Bash, or the Docker Compose terminal. Keeping Zephyr's source tree inside the WSL Linux filesystem (for example, `~/work`) usually avoids slow builds and Linux file-permission problems caused by building under `/mnt/c`.

The current Docker Compose project is the gateway stack. You do not need to start those services to build this firmware simulator. Later, the gateway's MQTT broker can be used alongside a separate MQTT publisher; the firmware simulator is not itself an MQTT battery simulator.

## 1. Open Ubuntu in WSL2

If WSL2 and Ubuntu are already installed, open the **Ubuntu** app and continue to step 2. Check that it is WSL2 from PowerShell with:

```powershell
wsl --list --verbose
```

The Ubuntu distribution should show version `2`. If WSL is not installed, Microsoft's install instructions are here: [Install WSL](https://learn.microsoft.com/windows/wsl/install). Follow those instructions, install Ubuntu, and then launch Ubuntu from the Start menu. Do not paste the Linux commands in this tutorial into PowerShell.

In the Ubuntu terminal, confirm the environment:

```bash
uname -a
```

The output should identify Linux. If it says `MINGW`, you are in Git Bash; if it looks like a Windows PowerShell prompt, open Ubuntu instead.

## 2. Install Linux build prerequisites

Update Ubuntu and install the host tools needed for Zephyr and its native simulator:

```bash
sudo apt update
sudo apt install --no-install-recommends \
  git cmake ninja-build gperf ccache dfu-util device-tree-compiler wget \
  python3-dev python3-pip python3-setuptools python3-tk python3-venv \
  xz-utils file make gcc g++ libsdl2-dev
```

Enter your Ubuntu password if prompted. Linux does not display the password characters as you type; that is expected.

Check that the main tools are available:

```bash
git --version
cmake --version
ninja --version
gcc --version
python3 --version
```

If `apt` reports that a package cannot be found, check that `sudo apt update` completed successfully and that the package name was copied correctly.

## 3. Create a Python virtual environment and install West

West is Zephyr's workspace and build command. A virtual environment keeps its Python packages separate from Ubuntu's system Python.

```bash
mkdir -p ~/work
cd ~/work
python3 -m venv ~/work/zephyr-venv
source ~/work/zephyr-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install west
```

Whenever you open a new Ubuntu terminal to continue, activate the same environment again:

```bash
source ~/work/zephyr-venv/bin/activate
```

Verify West is available:

```bash
west --version
```

If the shell says `west: command not found`, check that the virtual environment is active. Its name, `zephyr-venv`, should appear at the start of the prompt.

## 4. Create the Libre Solar West workspace

The Libre Solar firmware repository is a West manifest repository. `west init` clones it and sets up the workspace; `west update` then fetches Zephyr and the modules declared by Libre Solar.

```bash
cd ~/work
west init -m https://github.com/LibreSolar/bms-firmware west-workspace
cd ~/work/west-workspace/bms-firmware
west update
```

`west update` downloads several repositories and can take a while. Let it finish. You should now have a layout similar to this:

```text
~/work/west-workspace/
├── .west/                  # West workspace metadata
├── bms-firmware/           # Libre Solar application and manifest
├── zephyr/                 # Zephyr RTOS source
└── modules/                # Zephyr and vendor modules
```

Install the Python packages required by the checked-out Zephyr version:

```bash
west packages pip --install
```

This command installs packages into the active Python environment. If it says `west: unknown command "packages"`, your workspace or Zephyr checkout may be from an older release. Confirm you ran `west update` from `~/work/west-workspace/bms-firmware`, then use the setup instructions for the Zephyr revision pinned by that firmware checkout: [Zephyr Getting Started](https://docs.zephyrproject.org/latest/develop/getting_started/index.html).

## 5. Check the workspace before building

Make sure you are in the firmware repository and West recognizes the workspace:

```bash
cd ~/work/west-workspace/bms-firmware
west topdir
west list
```

`west topdir` should print `~/work/west-workspace`. `west list` should include `bms-firmware` and `zephyr` (plus modules). If West says this is not a workspace, change into the firmware path above and make sure the `.west` folder exists one directory higher.

## 6. Build and run the Libre Solar simulator

Libre Solar's simulator page gives this command:

```bash
west build -b native_sim -t run
```

Run it from `~/work/west-workspace/bms-firmware` with the virtual environment active. The first build may take a few minutes. West configures and compiles the application, then runs the resulting host executable. Keep the terminal open to see its logs. Stop it with **Ctrl+C**.

If you prefer to build and run as two separate steps (useful when debugging a build failure):

```bash
west build -b native_sim
west build -t run
```

The executable is normally at:

```text
~/work/west-workspace/bms-firmware/build/zephyr/zephyr.exe
```

You can also run it directly after a successful build:

```bash
./build/zephyr/zephyr.exe
```

Use `Ctrl+C` to stop it. To inspect the executable's options, run:

```bash
./build/zephyr/zephyr.exe --help
```

## 7. What to look for, and what not to expect

Read the startup/log output first. The application is running on your host, but it has no real BMS C1 hardware attached. This simulator is useful for trying firmware-level behavior and communication paths supported by the `native_sim` build.

Do **not** assume that:

- Cell voltages are coming from a realistic electrochemical battery model.
- The BQ76952 chip or its register behavior is fully emulated.
- Over/under-voltage, over-current, temperature, balancing, or MOSFET behavior has been validated against physical hardware.
- The simulator automatically publishes data to the Docker Compose Mosquitto broker.

Libre Solar explicitly notes that the firmware simulator does not mock the whole BMS IC. For realistic gateway development, your project can separately publish representative ThingSet-shaped readings to MQTT. That Python publisher is the project-level BMS simulator described in the README; it can run against your Compose broker without pretending to be the BMS C1 hardware.

## 8. Useful follow-up: inspect the firmware's test and protocol references

- [Libre Solar simulator documentation](https://libre.solar/bms-firmware/src/dev/simulator.html) — supported purpose and the documented run command.
- [Libre Solar workspace setup](https://libre.solar/bms-firmware/src/dev/workspace_setup.html) — West workspace layout and dependency update instructions.
- [BMS C1 user manual](https://libre.solar/bms-c1/manual/) — device measurements and ThingSet examples.
- [BMS C1 hardware test report](https://github.com/LibreSolar/bms-c1/blob/main/testing/v0.3/README.md) — examples of tests performed against actual hardware and bench equipment.
- [Zephyr `native_sim` documentation](https://docs.zephyrproject.org/latest/boards/native/native_sim/doc/index.html) — host simulator details and limitations.

## Troubleshooting

### `west: command not found`

Activate the environment and retry:

```bash
source ~/work/zephyr-venv/bin/activate
west --version
```

### `west build` says the board is unknown

Confirm the manifest dependencies were fetched and the command is being run from the BMS workspace:

```bash
cd ~/work/west-workspace/bms-firmware
west update
west boards | grep native_sim
```

If the command does not list `native_sim`, check the Zephyr checkout and manifest state (`west list`) before deleting or recreating any folders.

### CMake complains about a missing program or Python package

Activate the virtual environment, install the system prerequisites in step 2, and rerun:

```bash
west packages pip --install
west build -p -b native_sim
```

`-p` asks West for a pristine build, which clears generated build files and reconfigures from scratch. It does not delete your source repositories. Avoid manually deleting the workspace while troubleshooting.

### The process exits immediately or the log is unclear

Separate build from run so you can see which stage fails:

```bash
west build -p -b native_sim
./build/zephyr/zephyr.exe --help
./build/zephyr/zephyr.exe
```

Save the full error output, including the first CMake or compiler error above the final `FATAL ERROR`; the final line is often only a summary.

### I want to use Docker

The existing `docker-compose.yml` is for the gateway's runtime services. Keep that stack focused on the broker and application services. The firmware build needs a development environment with West, Zephyr sources, a native C compiler, and the Zephyr build tools. You can containerize that separately later, mounting the West workspace into a development container. For the first successful run, WSL2 Ubuntu provides a simpler place to understand the toolchain and read compiler errors. Do not install Zephyr dependencies into the MQTT broker container.

## Sources

- [Libre Solar BMS simulator documentation](https://libre.solar/bms-firmware/src/dev/simulator.html)
- [Libre Solar West workspace setup](https://libre.solar/bms-firmware/src/dev/workspace_setup.html)
- [Zephyr Getting Started Guide](https://docs.zephyrproject.org/latest/develop/getting_started/index.html)
- [Zephyr `native_sim` documentation](https://docs.zephyrproject.org/latest/boards/native/native_sim/doc/index.html)
