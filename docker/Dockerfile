# Data Receipt box services (DNS logger + Shield, dashboard, rules-v1, ml-v1, flows) for the
# Arduino Uno Q (QRB2210, linux/arm64). One image, five containers (docker-compose.yml).
# Hotspot, NetworkManager and iptables stay on the host (deploy/start_box_docker.sh).
#
# Build on an Apple-Silicon Mac (native arm64) or on the board:
#   docker buildx build --platform linux/arm64 -f docker/Dockerfile -t digin:latest --load .
FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DIGIN_DB=/home/arduino/digin/digin.db

# tcpdump: box/flows.py. iproute2: entrypoint waits for ap0 / 10.42.0.1.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tcpdump iproute2 \
 && rm -rf /var/lib/apt/lists/*

# Dependencies first (slow layer, cached); code last so a code edit rebuilds in seconds.
COPY docker/requirements.txt /tmp/requirements.txt
RUN pip install --only-binary=:all: -r /tmp/requirements.txt \
 && python -c "import dnslib, sklearn, joblib, maxminddb, numpy" \
 && python -m compileall -q /usr/local/lib/python3.13 >/dev/null || true

# Same path as on the host so every default in the code (and the systemd units) still holds.
WORKDIR /home/arduino/madhacks26
COPY . .
# Bake bytecode so a cold start skips compiling (the A53 is slow); check the model loads here,
# at build time, rather than discovering a version mismatch on the box at demo time.
RUN python -m compileall -q . \
 && python -c "import joblib; joblib.load('ml/models/model_ml-v1.joblib'); print('model ok')" \
 && chmod +x docker/entrypoint.sh

ENTRYPOINT ["/home/arduino/madhacks26/docker/entrypoint.sh"]
