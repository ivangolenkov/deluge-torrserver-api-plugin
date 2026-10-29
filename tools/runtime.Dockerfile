FROM debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-libtorrent ca-certificates make \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
COPY requirements-dev.txt requirements-runtime.txt constraints.txt /opt/requirements/
RUN python3 -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/pip install -r /opt/requirements/requirements-dev.txt \
       -r /opt/requirements/requirements-runtime.txt
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["make", "check", "PYTHON=python", "VENV=/opt/venv"]
