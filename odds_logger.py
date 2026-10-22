"""
odds_logger.py — Project API LLM, step 2: build your own odds history.

Every run:
  1. Pulls NBA moneylines from sportsbooks licensed in Pennsylvania (1 credit).
  2. APPENDS one row per game per sportsbook to odds_history.csv, with a timestamp.
  3. Prints today's games with the consensus fair probability (average across books)
     and the best available price for each team.

Run it a few times per game day (e.g. morning, afternoon, ~30 min before tip).
Over weeks this becomes your dataset: how lines open, how they move, and what
they close at. Later you'll join it with final scores to train and test a model.

Uses the same .env file as nba_starter.py (only ODDS_API_KEY is needed).
"""

import csv
import os
from datetime import datetime, timezone
from statistics import mean
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()
ODDS_KEY = os.getenv("ODDS_API_KEY")

# Sportsbooks licensed in Pennsylvania, by The Odds API's bookmaker key.
# Caesars (williamhill_us) and Fanatics only return data on paid Odds API plans;
# they're listed so they appear automatically if you ever upgrade.
# bet365 is legal in PA but isn't offered by The Odds API.
# Check the PGCB site now and then; licenses change.
PA_BOOKS = [
    "draftkings", "fanduel", "betmgm", "betrivers",
    "betparx", "espnbet", "williamhill_us", "fanatics",
]
CSV_FILE = "odds_history.csv"
ET = ZoneInfo("America/New_York")

FIELDS = [
    "snapshot_utc", "game_id", "tipoff_et", "away_team", "home_team",
    "book", "away_price", "home_price", "away_fair", "home_fair",
]


def implied_prob(american):
    return -american / (-american + 100) if american < 0 else 100 / (american + 100)


def no_vig(p_a, p_b):
    total = p_a + p_b
    return p_a / total, p_b / total


def fetch_odds():
    resp = requests.get(
        "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/",
        # Asking for specific books (up to 10) costs the same as 1 region: 1 credit.
        params={"apiKey": ODDS_KEY, "bookmakers": ",".join(PA_BOOKS),
                "markets": "h2h", "oddsFormat": "american"},
        timeout=15,
    )
    resp.raise_for_status()
    print(f"[Odds API] credits left this month: {resp.headers.get('x-requests-remaining')}")
    return resp.json()


def main():
    if not ODDS_KEY:
        raise SystemExit("Missing ODDS_API_KEY in .env")

    snapshot = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today_et = datetime.now(ET).date()
    rows, summary = [], []

    for ev in fetch_odds():
        home, away = ev["home_team"], ev["away_team"]
        tip = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")).astimezone(ET)
        fair_home, best = [], {home: None, away: None}

        for book in ev["bookmakers"]:
            prices = {o["name"]: o["price"] for o in book["markets"][0]["outcomes"]}
            if home not in prices or away not in prices:
                continue
            pa, ph = no_vig(implied_prob(prices[away]), implied_prob(prices[home]))
            fair_home.append(ph)
            for team in (home, away):
                if best[team] is None or prices[team] > best[team][0]:
                    best[team] = (prices[team], book["title"])
            rows.append({
                "snapshot_utc": snapshot, "game_id": ev["id"],
                "tipoff_et": tip.strftime("%Y-%m-%d %H:%M"),
                "away_team": away, "home_team": home, "book": book["title"],
                "away_price": prices[away], "home_price": prices[home],
                "away_fair": round(pa, 4), "home_fair": round(ph, 4),
            })

        if tip.date() == today_et and fair_home:
            summary.append((tip, away, home, mean(fair_home), len(fair_home), best))

    # Append to the history file (create it with a header the first time).
    new_file = not os.path.exists(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} rows to {CSV_FILE}\n")

    print(f"=== Today's games ({today_et}) — consensus across books ===")
    for tip, away, home, ph, n, best in sorted(summary):
        print(f"{tip:%I:%M %p}  {away} @ {home}")
        print(f"   market: {home} {ph:.1%} / {away} {1 - ph:.1%}   ({n} books)")
        print(f"   best price: {away} {best[away][0]:+d} ({best[away][1]}),  "
              f"{home} {best[home][0]:+d} ({best[home][1]})")


if __name__ == "__main__":
    main()