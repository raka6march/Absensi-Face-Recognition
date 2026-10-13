#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
if [[ ! -f .venv/.installed ]]; then
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/.installed
fi
.venv/bin/python setup_models.py
open http://localhost:8000
exec .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
