FROM python:3.12-slim
WORKDIR /app
COPY requirements-server.txt .
# gcc + libflac-dev: pyflac has no wheel for this platform and builds from
# source; libflac stays for runtime linking, gcc is removed after the build
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev libflac-dev \
    && pip install --no-cache-dir -r requirements-server.txt \
    && apt-get purge -y gcc libc6-dev && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
COPY voicepipe/ voicepipe/
COPY server/ server/
EXPOSE 10200 10300
# Phase 1: TTS-only server for HA. Phase 2: swap CMD to server.pipeline_server.
CMD ["python", "-m", "server.tts_server", "--host", "0.0.0.0"]
