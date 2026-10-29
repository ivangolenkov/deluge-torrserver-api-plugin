FROM debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3 AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-libtorrent ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-runtime.txt constraints.txt /opt/requirements/
RUN python3 -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/pip install -r /opt/requirements/requirements-runtime.txt \
       setuptools==75.8.0 wheel==0.45.1
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1

FROM runtime AS build
WORKDIR /source
COPY setup.py ./
COPY deluge_stream ./deluge_stream
RUN python setup.py bdist_egg --dist-dir /artifact

FROM runtime AS product
COPY --from=build /artifact/*.egg /opt/plugin/
COPY docker/entrypoint.py /opt/stream-entrypoint.py
EXPOSE 8112 8090 58846 6881 6881/udp
ENTRYPOINT ["/opt/venv/bin/python", "/opt/stream-entrypoint.py"]
CMD ["core"]
