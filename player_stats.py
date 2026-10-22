"""
player_stats.py — Project API LLM: download player box-score history.

Uses the free `nba_api` package, which reads the same stats NBA.com shows.
One request per season returns EVERY player's line for EVERY game:
points, rebounds, assists, minutes, threes made/attempted, and more.

Saves to player_games.csv. Re-run anytime to refresh the current season.

Setup:  pip install nba_api      (also installs pandas)
Run:    python player_stats.py
"""

import os
import time

import pandas as pd
from nba_api.stats.endpoints import leaguegamelog

# Season labels in NBA.com format. Add or remove seasons as you like.
SEASONS = ["2022-23", "2023-24", "2024-25", "2025-26", "2026-27"]
CSV_FILE = "player_games.csv"

KEEP = [
    "SEASON_ID", "GAME_DATE", "GAME_ID", "PLAYER_ID", "PLAYER_NAME",
    "TEAM_ABBREVIATION", "MATCHUP", "WL", "MIN",
    "PTS", "REB", "AST", "STL", "BLK", "TOV",
    "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA",
]


def fetch(season):
    for attempt in range(3):
        try:
            log = leaguegamelog.LeagueGameLog(
                season=season,
                season_type_all_star="Regular Season",
                player_or_team_abbreviation="P",
                timeout=60,
            )
            return log.get_data_frames()[0]
        except Exception as e:  # NBA.com is sometimes slow; retry
            print(f"   {season}: attempt {attempt + 1} failed ({e}); retrying in 10s")
            time.sleep(10)
    raise SystemExit(f"Couldn't download {season}. Try again in a few minutes.")


def main():
    frames = []
    for season in SEASONS:
        print(f"Downloading {season}...")
        df = fetch(season)
        print(f"   {len(df):,} player-games")
        frames.append(df)
        time.sleep(2)  # be polite to NBA.com

    all_games = pd.concat(frames, ignore_index=True)
    all_games = all_games[[c for c in KEEP if c in all_games.columns]]
    all_games["GAME_DATE"] = pd.to_datetime(all_games["GAME_DATE"]).dt.strftime("%Y-%m-%d")
    all_games = all_games.sort_values(["GAME_DATE", "GAME_ID"])
    all_games.to_csv(CSV_FILE, index=False)
    print(f"\nSaved {len(all_games):,} rows to {CSV_FILE}")

    # Quick look: top 3-point shooters this season (min 2 games).
    latest = all_games[all_games["SEASON_ID"].astype(str).str.endswith(SEASONS[-1][:4])]
    if latest.empty:
        latest = all_games[all_games["SEASON_ID"] == all_games["SEASON_ID"].max()]
    top = (latest.groupby("PLAYER_NAME")
           .agg(games=("FG3M", "size"), threes_pg=("FG3M", "mean"), attempts_pg=("FG3A", "mean"))
           .query("games >= 2")
           .sort_values("threes_pg", ascending=False)
           .head(15))
    print("\nTop 3-point makers per game, most recent season:")
    print(top.round(1).to_string())


if __name__ == "__main__":
    main()
