# Fallback base image for networks where Debian mirrors are unreachable (e.g. some
# corporate/sandboxed CI). It is Ubuntu 24.04 with the *official* CPython 3.11 from the
# pinned python:3.11-slim image copied in (glibc-compatible). Build it, then:
#   docker build -f docker/Dockerfile --build-arg BASE_IMAGE=luma-base-ubuntu:24.04 .
FROM python:3.11.16-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b AS py
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends libssl3 libffi8 libsqlite3-0 libbz2-1.0 liblzma5 libreadline8 zlib1g libncursesw6 libuuid1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=py /usr/local /usr/local
RUN ldconfig && python3.11 -c "import ssl, sqlite3, ctypes, lzma, bz2; print('python ok')"
ENV LANG=C.UTF-8 PATH=/usr/local/bin:$PATH
