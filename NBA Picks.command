#!/bin/bash
# Double-click on a Mac to open NBA Picks. First time? Run ./setup_mac.sh once.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up (one time)..."
  ./setup_mac.sh
  exit
fi
git pull --quiet --ff-only 2>/dev/null || true   # grab the latest code if online
.venv/bin/python -m pip install --quiet -r requirements.txt 2>/dev/null || true   # new packages, if any
exec .venv/bin/python app.py
