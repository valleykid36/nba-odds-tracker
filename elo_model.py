"""
elo_model.py — Project API LLM, step 4: your first prediction model.

Elo is a rating system (originally for chess): every team has a number,
the winner takes points from the loser, and the gap between two ratings
gives a win probability. It's simple, explainable, and a standard baseline.

What this script does:
  1. Replays every game in games.csv in date order, updating team ratings.
  2. TESTS the model honestly: before each game, it records the prediction
     it would have made, then checks it against the real result.
     Reports accuracy and Brier score (lower = better; 0.25 = coin flip).
  3. Shows current team ratings (top/bottom).
  4. For today's games, compares Elo's win probability to the market's
     (from your odds_history.csv) and shows the gap.

Run:  python elo_model.py
Needs: games.csv (from fetch_history.py). odds_history.csv is optional.
"""

import csv
import math
import os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

# ---- Model settings (tweak these later and see if the Brier score improves) ----
K = 20               # how fast ratings react to results
HOME_ADV = 70        # Elo points for playing at home
START = 1500         # rating for a new team
REVERT = 0.25        # between seasons, pull ratings 25% back toward average
TEST_FROM_SEASON = 2017   # give the model 2 seasons to "warm up" before grading it

ET = ZoneInfo("America/New_York")

# balldontlie and The Odds API spell a few teams differently.
NAME_FIX = {"LA Clippers": "Los Angeles Clippers"}


def fix(name):
    return NAME_FIX.get(name, name)


def win_prob(r_home, r_away):
    return 1 / (1 + 10 ** (-(r_home + HOME_ADV - r_away) / 400))


def mov_multiplier(margin, elo_diff):
    # Bigger wins move ratings more, but less so when a heavy favorite wins big.
    return math.log(abs(margin) + 1) * (2.2 / (elo_diff * 0.001 + 2.2))


def load_games():
    if not os.path.exists("games.csv"):
        raise SystemExit("games.csv not found. Run: python fetch_history.py")
    with open("games.csv", newline="") as f:
        games = list(csv.DictReader(f))
    games.sort(key=lambda g: (g["date"], int(g["game_id"])))
    return games


def run_elo(games):
    ratings = defaultdict(lambda: START)
    season = None
    graded = []  # (predicted home win prob, actual 1/0)

    for g in games:
        s = int(g["season"])
        if season is not None and s != season:
            for t in ratings:  # off-season regression to the mean
                ratings[t] = ratings[t] * (1 - REVERT) + START * REVERT
        season = s

        if g["status"] != "Final":
            continue
        hs, as_ = int(g["home_score"]), int(g["away_score"])
        if hs == as_:
            continue
        home, away = fix(g["home_team"]), fix(g["away_team"])
        p = win_prob(ratings[home], ratings[away])
        home_won = 1 if hs > as_ else 0

        if s >= TEST_FROM_SEASON:
            graded.append((p, home_won))

        winner_diff = (ratings[home] + HOME_ADV - ratings[away]) * (1 if home_won else -1)
        shift = K * mov_multiplier(hs - as_, winner_diff) * (home_won - p)
        ratings[home] += shift
        ratings[away] -= shift

    return ratings, graded


def report_accuracy(graded):
    n = len(graded)
    if not n:
        print("Not enough games to grade yet.")
        return
    correct = sum((p >= 0.5) == bool(y) for p, y in graded)
    brier = sum((p - y) ** 2 for p, y in graded) / n
    home_rate = sum(y for _, y in graded) / n
    brier_home = sum((home_rate - y) ** 2 for _, y in graded) / n

    print(f"=== Model test: {n:,} games since {TEST_FROM_SEASON} ===")
    print(f"Picked the winner:     {correct / n:.1%}")
    print(f"Brier score (Elo):     {brier:.4f}   <- lower is better")
    print(f"Brier score (always home team {home_rate:.0%}): {brier_home:.4f}")
    print(f"Brier score (coin flip):       0.2500")

    # Calibration: when Elo says ~60%, does that team win ~60% of the time?
    print("\nCalibration (predicted vs. actual home win rate):")
    buckets = defaultdict(list)
    for p, y in graded:
        buckets[min(int(p * 10), 9)].append((p, y))
    for b in sorted(buckets):
        items = buckets[b]
        avg_p = sum(p for p, _ in items) / len(items)
        actual = sum(y for _, y in items) / len(items)
        print(f"   predicted {avg_p:5.1%}  ->  actual {actual:5.1%}   ({len(items)} games)")


def show_ratings(ratings, games):
    latest_season = max(int(g["season"]) for g in games)
    active = set()
    for g in games:
        if int(g["season"]) == latest_season:
            active |= {fix(g["home_team"]), fix(g["away_team"])}
    ranked = sorted(((ratings[t], t) for t in active), reverse=True)
    print("\n=== Current Elo ratings ===")
    for i, (r, t) in enumerate(ranked, 1):
        print(f"{i:2}. {t:<26} {r:6.0f}")


def compare_to_market(ratings):
    if not os.path.exists("odds_history.csv"):
        print("\n(No odds_history.csv yet: run odds_logger.py to compare with the market.)")
        return
    today = datetime.now(ET).strftime("%Y-%m-%d")
    with open("odds_history.csv", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["tipoff_et"].startswith(today)]
    if not rows:
        print("\n(No odds logged for today's games yet: run odds_logger.py first.)")
        return

    # Use the most recent snapshot for each game, averaged across books.
    latest = {}
    for r in rows:
        if r["snapshot_utc"] > latest.get(r["game_id"], ""):
            latest[r["game_id"]] = r["snapshot_utc"]
    games = defaultdict(list)
    for r in rows:
        if r["snapshot_utc"] == latest[r["game_id"]]:
            games[r["game_id"]].append(r)

    print(f"\n=== Today ({today}): Elo vs. the market ===")
    print("Positive gap = Elo likes that team MORE than the sportsbooks do.\n")
    for book_rows in sorted(games.values(), key=lambda rs: rs[0]["tipoff_et"]):
        r0 = book_rows[0]
        home, away = r0["home_team"], r0["away_team"]
        market = sum(float(r["home_fair"]) for r in book_rows) / len(book_rows)
        if home not in ratings or away not in ratings:
            print(f"{away} @ {home}: team name not found in games.csv, skipping")
            continue
        elo = win_prob(ratings[home], ratings[away])
        gap = elo - market
        side, g = (home, gap) if gap >= 0 else (away, -gap)
        print(f"{r0['tipoff_et'][11:]}  {away} @ {home}")
        print(f"   Elo:    {home} {elo:.1%}")
        print(f"   Market: {home} {market:.1%}   ({len(book_rows)} books)")
        print(f"   Gap:    {g:.1%} toward {side}\n")

    print("Reminder: Elo doesn't know about injuries, rest, or trades. The market does.")
    print("Treat gaps as questions to investigate, not bets to place.")


def main():
    games = load_games()
    ratings, graded = run_elo(games)
    report_accuracy(graded)
    show_ratings(ratings, games)
    compare_to_market(ratings)


if __name__ == "__main__":
    main()
