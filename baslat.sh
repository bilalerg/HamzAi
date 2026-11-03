#!/usr/bin/env bash
# Profaix — macOS / Linux için tek komutla lokal başlatma
# İlk çalıştırmada sanal ortam kurar ve paketleri yükler (internet gerekir).
# Demo veriyi sıfırlamak için:  ./baslat.sh --sifirla
set -e
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "[1/2] Sanal ortam oluşturuluyor..."
  python3 -m venv .venv
  echo "[2/2] Paketler yükleniyor, birkaç dakika sürebilir..."
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python yerel_baslat.py "$@"
