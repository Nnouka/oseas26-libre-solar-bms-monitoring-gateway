# Dev toolbox: Python + uv + MQTT CLI tools. Your code is bind-mounted, never copied in.
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git mosquitto-clients curl procps \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv
# venv lives in the image/volume, not in your repo: a Windows .venv can't work in Linux
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /work
CMD ["bash"]
