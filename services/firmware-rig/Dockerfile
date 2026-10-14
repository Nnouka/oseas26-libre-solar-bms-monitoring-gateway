# Firmware rig: the REAL Libre Solar BMS firmware (Zephyr native_sim) driven by our battery model.
# Linux/amd64 only (native_sim is a 32-bit program). First build downloads several GB (west update).

# ---- stage 1: build the firmware, exactly as docs/native_sim-tutorial.md does ----
FROM python:3.12-bookworm AS firmware
RUN apt-get update && apt-get install -y --no-install-recommends \
      git cmake ninja-build gperf ccache dfu-util device-tree-compiler wget xz-utils file make \
      gcc g++ gcc-multilib g++-multilib libsdl2-dev \
 && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/zephyr-venv && /opt/zephyr-venv/bin/pip install --no-cache-dir west
ENV PATH="/opt/zephyr-venv/bin:$PATH" ZEPHYR_TOOLCHAIN_VARIANT=host
WORKDIR /work
# Pinned to the revision the rig was developed against (override with --build-arg FIRMWARE_REV=...).
ARG FIRMWARE_REV=779d20199bff2666e01371ce2f562dcda4d6bda3
# (`west init --mr` takes a branch or tag, not a commit, so the pinned commit is checked out afterwards)
RUN west init -m https://github.com/LibreSolar/bms-firmware west-workspace \
 && cd west-workspace/bms-firmware && git checkout ${FIRMWARE_REV} && west update
RUN cd west-workspace/bms-firmware && west packages pip --install
COPY rig.conf /work/rig.conf
RUN cd west-workspace/bms-firmware/app \
 && west build -b native_sim -p auto -- -DEXTRA_CONF_FILE=/work/rig.conf

# ---- stage 2: the rig ----
FROM python:3.12-slim-bookworm
# 32-bit C and gcc runtime libraries for the native_sim binary (it needs libgcc_s for pthread_exit)
RUN apt-get update && apt-get install -y --no-install-recommends libc6-i386 lib32gcc-s1 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY --from=firmware /work/west-workspace/bms-firmware/app/build/zephyr/zephyr.exe /opt/firmware/zephyr.exe
COPY *.py ./
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 FIRMWARE_BINARY=/opt/firmware/zephyr.exe
EXPOSE 8000 3333
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
