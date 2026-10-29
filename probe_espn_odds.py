"""probe_espn_odds.py — one-time check of ESPN's free closing lines.

    python probe_espn_odds.py            last night's first game (Oct 26)
    python probe_espn_odds.py 20261027   or any date YYYYMMDD

Prints every open/close field for one finished game as 'path = value'.
Nothing is saved; no credits used.
"""
import sys

import requests

DAY = sys.argv[1] if len(sys.argv) > 1 else "20261026"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"}
SKIP = ("displayValue", "fraction", "decimal", "value", "$ref", "links", "alternateDisplayValue")


def flat(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in SKIP:
                yield from flat(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from flat(v, f"{path}[{i}]")
    else:
        yield path, obj


sb = requests.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
                  params={"dates": DAY}, headers=H, timeout=20).json()
ev = sb["events"][0]
comp = ev["competitions"][0]
teams = {c["homeAway"]: c["team"]["abbreviation"] for c in comp["competitors"]}
print(f"{len(sb['events'])} games on {DAY}. First: {ev['name']}  (away {teams['away']}, home {teams['home']})")

url = (f"https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{ev['id']}"
       f"/competitions/{comp['id']}/odds")
items = requests.get(url, headers=H, timeout=20).json().get("items", [])
print("Providers:", ", ".join(f"{i.get('provider', {}).get('name')} (id {i.get('provider', {}).get('id')})"
                              for i in items))
first = items[0]
for path, v in flat(first):
    if any(w in path for w in ("open", "close", "overUnder", "spread", "details", "provider.name")) \
            and "current" not in path:
        print(f"  {path} = {v}")
