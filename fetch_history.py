"""
fetch_history.py — Project API LLM, step 3: download historical NBA results.

Downloads every game from the seasons below using balldontlie's FREE Games
endpoint and saves them to games.csv. Free tier = 5 requests/minute, so this
script waits between requests. ~10 seasons takes about 25-30 minutes; let it run.

Safe to stop and restart: seasons already in games.csv are skipped
(except the current season, which is refreshed every time to pick up new results).

Run:  python fetch_history.py
"""

import csv
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()
BDL_KEY = os.getenv("BALLDONTLIE_API_KEY")
CSV_FILE = "games.csv"

FIRST_SEASON = 2015          # 2015 = the 2015-16 season
CURRENT_SEASON = 2026        # 2026 = the 2026-27 season (in progress)
SECONDS_BETWEEN_CALLS = 13   # keeps us under 5 requests/minute

FIELDS = [
    "game_id", "date", "season", "postseason", "status",
    "home_team", "away_team", "home_score", "away_score",
]


def fetch_season(season):
    games, cursor, page = [], None, 0
    while True:
        params = {"seasons[]": season, "per_page": 100}
        if cursor:
            params["cursor"] = cursor
        for attempt in range(5):
            resp = requests.get("https://api.balldontlie.io/v1/games",
                                headers={"Authorization": BDL_KEY},
                                params=params, timeout=30)
            if resp.status_code == 429:          # rate limited: wait and retry
                print("   rate limited, waiting 60s...")
                time.sleep(60)
                continue
            resp.raise_for_status()
            break
        else:
            raise SystemExit("Too many rate-limit retries. Try again in a few minutes.")

        data = resp.json()
        page += 1
        for g in data["data"]:
            games.append({
                "game_id": g["id"],
                "date": g["date"][:10],
                "season": g["season"],
                "postseason": g["postseason"],
                "status": g["status"],
                "home_team": g["home_team"]["full_name"],
                "away_team": g["visitor_team"]["full_name"],
                "home_score": g["home_team_score"],
                "away_score": g["visitor_team_score"],
            })
        print(f"   season {season}: page {page}, {len(games)} games so far")
        cursor = data.get("meta", {}).get("next_cursor")
        time.sleep(SECONDS_BETWEEN_CALLS)
        if not cursor:
            return games


def main():
    if not BDL_KEY:
        raise SystemExit("Missing BALLDONTLIE_API_KEY in .env")

    existing = []
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, newline="") as f:
            existing = list(csv.DictReader(f))
    done = {int(r["season"]) for r in existing} - {CURRENT_SEASON}
    # Drop current-season rows; they get refreshed below.
    rows = [r for r in existing if int(r["season"]) != CURRENT_SEASON]

    for season in range(FIRST_SEASON, CURRENT_SEASON + 1):
        if season in done:
            print(f"season {season}: already saved, skipping")
            continue
        print(f"season {season}: downloading...")
        rows.extend(fetch_season(season))
        # Save after every season so progress isn't lost if you stop it.
        rows.sort(key=lambda r: (r["date"], int(r["game_id"])))
        with open(CSV_FILE, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)

    print(f"\nDone. {len(rows)} games saved to {CSV_FILE}")


if __name__ == "__main__":
    main()
