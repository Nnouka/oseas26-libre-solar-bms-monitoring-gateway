# Docker-only workflow

The only thing installed on your machine is **Docker Desktop**. No Python, no uv, no mosquitto, no WSL tooling.
Everything below runs in Linux containers; your repo folder is shared into them, so you edit files with any Windows editor.

## 1. Open a dev shell

```bash
docker compose -f docker-compose.dev.yml run --rm dev bash
```

You are now in `/work` (your repo) inside Linux with Python 3.12, uv, git and the mosquitto CLI tools.
Leave with `exit`; the container disappears, your files stay.

## 2. Do the tutorial exercises

Every command in the tutorial that starts with `uv ...` or `python ...` runs in this shell, from the service folder:

```bash
cd services/bms-simulator
uv sync                 # first time (packages go to a volume, not your repo)
uv run pytest -q
uv run uvicorn main:app --port 8000
```

One-shot form, without opening a shell:

```bash
docker compose -f docker-compose.dev.yml run --rm -w /work/services/bms-simulator dev uv run pytest -q
```

## 3. Watch MQTT while the stack runs

Terminal A: `docker compose up --build -d`
Terminal B:

```bash
docker compose -f docker-compose.dev.yml run --rm dev mosquitto_sub -h mosquitto -t 'bms/#' -v
```

Port mapping to your browser still works: simulator `http://localhost:8001/health`, gateway `http://localhost:8002/health`.

## 4. Clean up (leaves nothing behind)

```bash
docker compose --profile ha down -v --rmi all --remove-orphans
docker compose -f docker-compose.dev.yml down -v --rmi all
```

## Notes

- **Line endings:** commit with LF. Add a `.gitattributes` containing `* text=auto eol=lf`, otherwise bash scripts break in Linux.
- **Git:** run `git` on Windows as before; the container shares the same `.git`, but your SSH key is not inside it, so don't push from the container.
- **Slow Windows bind mounts:** keep the repo inside the WSL2 filesystem if tests feel slow.
- **native_sim:** needs a 32-bit Linux build toolchain. Treat it as a separate container job (stretch), not part of the dev shell.
- **No Docker on the machine?** Use GitHub Codespaces: the same compose files work there, and "run on a machine other than mine" holds literally.
