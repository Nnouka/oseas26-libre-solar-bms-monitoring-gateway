# Part 1: Project setup and a first test

**Goal.** A clean, reproducible Python project in `services/bms-simulator/` with `uv`, a working test runner, and a
health endpoint that is covered by a test. Everything later hangs on this.

**Time.** 45 minutes.

## Ideas

- **Virtual environment.** A private folder of installed packages for one project, so that projects do not disturb each other.
  `uv` creates and manages it for you (`.venv/`).
- **`pyproject.toml` and `uv.lock`.** The first lists what you ask for ("fastapi, paho-mqtt"). The second records the exact versions
  that were chosen, so a teammate (or a Docker build) gets the same ones. Commit both.
- **Test first.** A test is a small program that calls your code and states what must be true. When you write the test first and
  watch it fail, you know that it can fail, which means that a pass means something.
- **Python 3.12.** New enough for modern type hints, old enough that every library supports it, and what the Docker image will use.

## Steps

### 1.1 A branch for your learning work

From the repository root:

```bash
git switch main
git switch -c learn/bms-simulator
```

### 1.2 Check the tools

```bash
python --version      # any version is fine here; uv will fetch 3.12 when needed
uv --version          # if missing: https://docs.astral.sh/uv/getting-started/installation/
```

### 1.3 Clean up the service folder

Look at what is in `services/bms-simulator/`. There are two things to fix:

- `requirements.txt` was produced by `pip-chill` and lists packages that this project does not use. `uv` makes it unnecessary.
  Remove it with `git rm services/bms-simulator/requirements.txt`.
- Line endings. Windows writes CRLF, Linux and Docker expect LF, and a mix makes Git show whole files as changed. At the
  **repository root** create a file named `.gitattributes` with one line:

  ```text
  * text=auto eol=lf
  ```

  then run `git add --renormalize .` and look at `git status`. Commit these two changes together.

### 1.4 Declare the dependencies

Edit `pyproject.toml` (or use `uv add`) so that it reads like this:

```toml
[project]
name = "bms-simulator"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi[standard]>=0.115",
    "paho-mqtt>=2.1.0",
]

[dependency-groups]
dev = ["pytest>=8"]
```

Then:

```bash
uv sync
uv run python --version     # should say 3.12.x
```

`fastapi[standard]` brings the web server (`uvicorn`), `pydantic` and `httpx` (used by the test client). `paho-mqtt` is the MQTT client library, version 2.x. Note that
version 2 changed the API, so code from older blog posts may not work.

### 1.5 Where the tests live

Create the folder `tests/` and in it a file `conftest.py` (pytest reads it automatically):

```python
import sys
from pathlib import Path

# Make `import battery` work when pytest is started from the service folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

All our modules sit flat next to `main.py`. This keeps imports short (`from battery import Battery`).

### 1.6 The first test: health

Your `main.py` already has `/` and `/health`. Create `tests/test_health.py`:

```python
from fastapi.testclient import TestClient

import main


def test_health_says_ok():
    client = TestClient(main.app)
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "bms-simulator"
```

Run it:

```bash
uv run pytest -q
```

If it fails, read the message. Maybe your `/health` returns something slightly different from what the test expects. Decide which of the two should change
(the test states the contract, so usually the code) and fix it. Expected:

```text
.                                                                        [100%]
1 passed in 0.3s
```

> **Why this test matters later.** In Part 9 `/health` also reports the MQTT connection, and Docker will use it to decide whether the service is alive.
> The test will keep you honest when you change `main.py`.

## Checkpoint

- `uv run pytest -q` shows `1 passed`.
- `uv run fastapi dev main.py` starts the server and `http://localhost:8000/health` shows JSON in the browser (stop with Ctrl+C).
- `git status` shows no unexplained changes in files you did not touch.

## Commit

```bash
git add .gitattributes services/bms-simulator
git commit -m "chore(bms-simulator): uv project, tests folder, health test"
```

## Self-check questions

1. Why is `uv.lock` committed but `.venv/` is not?
2. What does `uv run pytest` do that plain `pytest` might not?
3. Why do we put the unit in every variable name (`pack_current_a`)?

<details>
<summary>Answers</summary>

1. `uv.lock` makes installs reproducible for everyone. `.venv/` is a machine-specific build product that anyone can recreate from it (and it is large).
2. It makes sure the project's own virtual environment (and so the declared versions) is used, creating it first if needed.
3. A value without a unit is a bug waiting to happen (a famous spacecraft was lost to pound-force versus newtons). The names make a wrong unit visible in the code.

</details>

Next: [Part 2: MQTT first contact](02-mqtt-first-contact.md).
