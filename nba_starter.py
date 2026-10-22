"""
NBA starter script — Project API LLM, step 1.

What it does:
  1. Gets today's NBA games from balldontlie (free tier: Games endpoint).
  2. Gets moneyline odds from The Odds API (free tier: 500 credits/month).
  3. Converts American odds to implied win probability and removes the vig,
     so you can later compare the market's number to your own model's number.

Setup:
  pip install requests python-dotenv
  Create a file named .env next to this script (see .env.example) with your keys.
  NEVER commit .env to GitHub — add it to .gitignore.

Run:
  python nba_starter.py
"""

import os
from datetime import date

import requests
from dotenv import load_dotenv

load_dotenv()
BDL_KEY = os.getenv("BALLDONTLIE_API_KEY")
ODDS_KEY = os.getenv("ODDS_API_KEY")


# ---------- 1. Today's games (balldontlie, free) ----------
def get_todays_games():
    today = date.today().isoformat()
    resp = requests.get(
        "https://api.balldontlie.io/v1/games",
        headers={"Authorization": BDL_KEY},  # no "Bearer" prefix
        params={"dates[]": today},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["data"]


# ---------- 2. Moneyline odds (The Odds API) ----------
def get_nba_moneylines():
    # Cost = markets x regions = 1 x 1 = 1 credit per call. Free plan = 500/month.
    resp = requests.get(
        "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/",
        params={
            "apiKey": ODDS_KEY,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "american",
        },
        timeout=15,
    )
    resp.raise_for_status()
    print(f"[Odds API] credits left this month: {resp.headers.get('x-requests-remaining')}")
    return resp.json()


# ---------- 3. Odds math ----------
def implied_prob(american):
    """-150 -> 0.600, +130 -> 0.435"""
    if american < 0:
        return -american / (-american + 100)
    return 100 / (american + 100)


def no_vig(p_home, p_away):
    """The two implied probs add up to >100% (that's the book's cut). Normalize them."""
    total = p_home + p_away
    return p_home / total, p_away / total


def main():
    if not BDL_KEY or not ODDS_KEY:
        raise SystemExit("Missing keys. Put BALLDONTLIE_API_KEY and ODDS_API_KEY in a .env file.")

    print("=== Today's games (balldontlie) ===")
    for g in get_todays_games():
        print(f"{g['visitor_team']['full_name']} @ {g['home_team']['full_name']}  ({g['status']})")

    print("\n=== Market win probabilities (The Odds API) ===")
    for event in get_nba_moneylines():
        home, away = event["home_team"], event["away_team"]
        if not event["bookmakers"]:
            continue
        book = event["bookmakers"][0]  # first listed book; later, compare several
        prices = {o["name"]: o["price"] for o in book["markets"][0]["outcomes"]}
        if home not in prices or away not in prices:
            continue
        ph, pa = no_vig(implied_prob(prices[home]), implied_prob(prices[away]))
        print(f"{away} ({prices[away]:+d}) @ {home} ({prices[home]:+d})  "
              f"-> fair: {home} {ph:.1%} / {away} {pa:.1%}  [{book['title']}]")


if __name__ == "__main__":
    main()
