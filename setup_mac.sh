#!/bin/bash
# setup_mac.sh — one-time setup for NBA Picks on a Mac.
#
#   git clone https://github.com/valleykid36/nba-odds-tracker.git ~/nba-odds-tracker
#   cd ~/nba-odds-tracker && ./setup_mac.sh
#
# What it does (safe to run again any time):
#   1. Finds a Python 3.10+ that has a working Tk window toolkit (installs one with Homebrew if needed)
#   2. Makes a private environment in .venv and installs requirements.txt
#   3. Creates .env and asks for your Odds API key (stays on this Mac, never committed)
#   4. Gets the database: downloads the latest snapshot from GitHub, or builds a fresh one
#   5. Makes "NBA Picks.command" double-clickable

set -e
cd "$(dirname "$0")"
REPO="valleykid36/nba-odds-tracker"
say() { printf "\n\033[1m%s\033[0m\n" "$1"; }

# ---------- 1. Python with tkinter ----------
good_python() {   # $1 = python path; ok if 3.10+ and Tk 8.6+
  "$1" - <<'EOF' >/dev/null 2>&1
import sys, tkinter
assert sys.version_info >= (3, 10)
assert tkinter.TkVersion >= 8.6
EOF
}

say "1/5  Looking for Python with tkinter..."
PY=""
for cand in /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 /opt/homebrew/bin/python3 \
            /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
            "$(command -v python3 2>/dev/null)"; do
  if [ -n "$cand" ] && [ -x "$cand" ] && good_python "$cand"; then PY="$cand"; break; fi
done

if [ -z "$PY" ]; then
  if command -v brew >/dev/null 2>&1; then
    say "Installing Python 3.12 + Tk with Homebrew (a few minutes)..."
    brew install python@3.12 python-tk@3.12
    PY="$(brew --prefix)/bin/python3.12"
  else
    echo "No Python 3.10+ with tkinter found, and Homebrew isn't installed."
    echo "Easiest fix: install Python from https://www.python.org/downloads/macos/"
    echo "(the python.org installer includes tkinter), then run ./setup_mac.sh again."
    exit 1
  fi
fi
if ! good_python "$PY"; then
  echo "$PY is missing tkinter. Run: brew install python-tk@3.12   then ./setup_mac.sh again."
  exit 1
fi
echo "   using $PY ($("$PY" --version))"

# ---------- 2. Private environment + packages ----------
say "2/5  Installing packages into .venv..."
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
echo "   done"

# ---------- 3. API key ----------
say "3/5  API key..."
if [ -f .env ] && grep -q "^ODDS_API_KEY=" .env && ! grep -q "paste_your" .env; then
  echo "   .env already set up"
else
  cp .env.example .env
  printf "   Paste your Odds API key (from the-odds-api.com; Enter to skip for now): "
  read -r KEY
  if [ -n "$KEY" ]; then
    sed -i '' "s|^ODDS_API_KEY=.*|ODDS_API_KEY=$KEY|" .env
    echo "   saved to .env (this file never goes to GitHub)"
  else
    echo "   skipped: add it later by editing .env (free updates work without it)"
  fi
fi

# ---------- 4. Database ----------
say "4/5  Database..."
if [ -f nba.db ]; then
  echo "   nba.db already here; keeping it"
else
  URL="https://github.com/$REPO/releases/download/db-snapshot/nba.db"
  if curl -fsSL -o nba.db.part "$URL"; then
    mv nba.db.part nba.db
    echo "   downloaded the latest snapshot (box scores, odds history, picks and your record)"
  else
    rm -f nba.db.part
    echo "   no snapshot on GitHub; building a fresh database from NBA.com (a few minutes, free)..."
    .venv/bin/python -c "import daily, db; daily.catch_up(db.connect())"
  fi
fi

# ---------- 5. Launcher ----------
say "5/5  Launcher..."
chmod +x "NBA Picks.command" setup_mac.sh
xattr -d com.apple.quarantine "NBA Picks.command" 2>/dev/null || true
echo "   double-click \"NBA Picks.command\" in Finder, or run: .venv/bin/python app.py"

say "All set. Opening NBA Picks..."
open "NBA Picks.command"
