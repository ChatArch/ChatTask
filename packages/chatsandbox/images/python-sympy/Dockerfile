FROM docker.io/library/python:3.12-slim@sha256:05a994d2344eb364cc186910f4fc8786a38215f39f00d0acc25bff33a08f4177

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /opt/chatsandbox-examples
COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.lock
COPY workload.py ./workload.py
# OpenSandbox injects execd and starts its own bounded command. No daemon,
# registry credential, Sandbox Key or model credential belongs in this image.
CMD ["/bin/sh", "-c", "sleep 7200"]
