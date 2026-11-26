#!/bin/bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "初回セットアップ中です（1〜2分かかります）..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
fi
.venv/bin/python app.py
