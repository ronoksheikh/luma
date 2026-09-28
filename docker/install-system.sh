#!/usr/bin/env bash
# System packages for the Luma Studio runtime image (Debian bookworm or Ubuntu 24.04).
# Package versions come from the pinned base image's distribution snapshot.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# Optional extra CA (corporate proxies): docker build --secret id=extra_ca,src=/path/ca.crt
if [ -f /run/secrets/extra_ca ]; then
  cp /run/secrets/extra_ca /usr/local/share/ca-certificates/luma-extra-ca.crt
fi

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl git unzip xz-utils sudo procps util-linux iptables tini \
  build-essential pkg-config \
  ffmpeg \
  fontconfig fonts-dejavu-core fonts-noto-core fonts-noto-cjk fonts-noto-color-emoji \
  libegl1 libgl1 libgles2 libfontconfig1 libfreetype6 libglib2.0-0 \
  libcairo2 libsndfile1 \
  less nano jq
update-ca-certificates || true
rm -rf /var/lib/apt/lists/*

# Inter — official release from rsms/inter, verified by SHA-256
INTER_VERSION="${INTER_VERSION:-4.1}"
INTER_SHA256="${INTER_SHA256:-9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e}"
tmp="$(mktemp -d)"
curl -fsSL "https://github.com/rsms/inter/releases/download/v${INTER_VERSION}/Inter-${INTER_VERSION}.zip" -o "$tmp/inter.zip"
echo "${INTER_SHA256}  $tmp/inter.zip" | sha256sum -c -
unzip -q "$tmp/inter.zip" -d "$tmp/inter"
mkdir -p /usr/share/fonts/truetype/inter
cp "$tmp"/inter/extras/ttf/Inter-*.ttf "$tmp"/inter/extras/ttf/InterDisplay-*.ttf /usr/share/fonts/truetype/inter/
cp "$tmp"/inter/InterVariable*.ttf /usr/share/fonts/truetype/inter/ 2>/dev/null || true
rm -rf "$tmp"
fc-cache -f >/dev/null

# python3.11 must exist (python:3.11-slim ships it in /usr/local)
command -v python3.11 >/dev/null || { echo "python3.11 not found in the base image" >&2; exit 1; }
