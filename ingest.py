"""
ingest.py — everything that pulls data INTO nba.db.

  update_player_games(seasons)  NBA.com box scores (free, via nba_api)
  update_injuries()             ESPN injury report (free)
  update_odds()                 game moneylines from PA books (1 Odds API credit)
  todays_events()               today's games (free)
  update_props(event)           player threes lines for one game (1 credit)
  import_csvs()                 one-time import of your existing CSV files
"""

import csv
import os
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from config import ET, PA_BOOKS, SEASONS, implied, player_key, team_abbr
from db import connect

load_dotenv()
ODDS_KEY = os.getenv("ODDS_API_KEY")
ODDS_BASE = "https://api.the-odds-api.com/v4/sports/basketball_nba"
ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_et(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)


def minutes(value):
    """NBA.com gives minutes as 34 or '34:12'."""
    try:
        s = str(value)
        if ":" in s:
            m, sec = s.split(":")[:2]
            return float(m) + float(sec) / 60
        return float(s)
    except ValueError:
        return 0.0


def num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def parse_matchup(matchup):
    """'CLE @ PHI' -> (CLE, PHI, home=0);  'PHI vs. CLE' -> (PHI, CLE, home=1)."""
    if " @ " in matchup:
        team, opp = matchup.split(" @ ")
        return team.strip(), opp.strip(), 0
    team, opp = matchup.replace("vs.", "vs").split(" vs ")
    return team.strip(), opp.strip(), 1


def box_row(r):
    """Turn one NBA.com game-log row (dict) into a player_games row."""
    team, opp, home = parse_matchup(r["MATCHUP"])
    return (
        str(r["GAME_ID"]), str(r["GAME_DATE"])[:10], int(str(r["SEASON_ID"])[-4:]),
        num(r["PLAYER_ID"]), r["PLAYER_NAME"], player_key(r["PLAYER_NAME"]),
        team, opp, home, minutes(r["MIN"]),
        num(r.get("PTS")), num(r.get("REB")), num(r.get("AST")), num(r.get("STL")),
        num(r.get("BLK")), num(r.get("TOV")), num(r.get("FGM")), num(r.get("FGA")),
        num(r.get("FG3M")), num(r.get("FG3A")), num(r.get("FTM")), num(r.get("FTA")),
        num(r.get("OREB")), num(r.get("DREB")),
    )


def save_box_rows(con, rows):
    con.executemany(
        "INSERT OR REPLACE INTO player_games VALUES (" + ",".join("?" * 24) + ")", rows)
    con.commit()


# ---------------- Player box scores (NBA.com) ----------------
def update_player_games(seasons=None):
    from nba_api.stats.endpoints import leaguegamelog  # imported here so other steps work without it

    seasons = seasons or SEASONS[-1:]
    con = connect()
    for season in seasons:
        for attempt in range(3):
            try:
                df = leaguegamelog.LeagueGameLog(
                    season=season, season_type_all_star="Regular Season",
                    player_or_team_abbreviation="P", timeout=60,
                ).get_data_frames()[0]
                break
            except Exception as e:
                print(f"   {season}: attempt {attempt + 1} failed ({e}); retrying")
                time.sleep(10)
        else:
            print(f"   {season}: giving up for now; try again later")
            continue
        df["GAME_DATE"] = df["GAME_DATE"].astype(str).str[:10]
        save_box_rows(con, [box_row(r) for r in df.to_dict("records")])
        print(f"   {season}: {len(df):,} player-games saved")
        time.sleep(2)
    con.close()


# ---------------- Injuries (ESPN) ----------------
def update_injuries():
    resp = requests.get(ESPN_INJURIES, timeout=20)
    resp.raise_for_status()
    snap = now_utc()
    today = datetime.now(ET).strftime("%Y-%m-%d")
    rows = []
    for t in resp.json().get("injuries", []):
        team = team_abbr(t.get("displayName", ""))
        for inj in t.get("injuries", []):
            name = inj.get("athlete", {}).get("displayName", "")
            if not name:
                continue
            rows.append((snap, today, team, name, player_key(name), inj.get("status", ""),
                         inj.get("shortComment", ""),
                         (inj.get("details") or {}).get("returnDate", "")))
    con = connect()
    con.execute("DELETE FROM injuries WHERE snapshot_date = ?", (today,))  # keep latest per day
    con.executemany("INSERT INTO injuries VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    print(f"   injuries: {len(rows)} players listed")


# ---------------- Game moneylines (The Odds API) ----------------
def update_odds():
    resp = requests.get(f"{ODDS_BASE}/odds/", timeout=15, params={
        "apiKey": ODDS_KEY, "bookmakers": PA_BOOKS, "markets": "h2h", "oddsFormat": "american"})
    resp.raise_for_status()
    snap, rows = now_utc(), []
    for ev in resp.json():
        tip = to_et(ev["commence_time"])
        home, away = ev["home_team"], ev["away_team"]
        for book in ev["bookmakers"]:
            p = {o["name"]: o["price"] for o in book["markets"][0]["outcomes"]}
            if home in p and away in p:
                pa, ph = implied(p[away]), implied(p[home])
                rows.append((snap, ev["id"], tip.strftime("%Y-%m-%d %H:%M"),
                             tip.strftime("%Y-%m-%d"), away, home, book["title"],
                             p[away], p[home], round(pa / (pa + ph), 4), round(ph / (pa + ph), 4)))
    con = connect()
    con.executemany("INSERT INTO odds_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    print(f"   odds: {len(rows)} prices saved  [credits left: {resp.headers.get('x-requests-remaining')}]")


# ---------------- Player props (The Odds API) ----------------
def todays_events():
    resp = requests.get(f"{ODDS_BASE}/events", params={"apiKey": ODDS_KEY}, timeout=15)  # free
    resp.raise_for_status()
    today = datetime.now(ET).date()
    out = [(to_et(e["commence_time"]), e) for e in resp.json()]
    return sorted([x for x in out if x[0].date() == today and x[0] > datetime.now(ET)],
                  key=lambda x: x[0])


def update_props(event, market="player_threes"):
    resp = requests.get(f"{ODDS_BASE}/events/{event['id']}/odds", timeout=15, params={
        "apiKey": ODDS_KEY, "bookmakers": PA_BOOKS, "markets": market, "oddsFormat": "american"})
    resp.raise_for_status()
    tip, snap, rows = to_et(event["commence_time"]), now_utc(), []
    for book in resp.json().get("bookmakers", []):
        for mkt in book["markets"]:
            for o in mkt["outcomes"]:
                if o.get("name") in ("Over", "Under") and "point" in o and o.get("description"):
                    rows.append((snap, event["id"], tip.strftime("%Y-%m-%d %H:%M"),
                                 tip.strftime("%Y-%m-%d"), event["home_team"], event["away_team"],
                                 o["description"], player_key(o["description"]), book["title"],
                                 mkt["key"], o["name"], o["point"], o["price"]))
    con = connect()
    con.executemany("INSERT INTO prop_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    print(f"   props: {len(rows)} prices saved  [credits left: {resp.headers.get('x-requests-remaining')}]")
    return snap


# ---------------- One-time import of existing CSVs ----------------
def import_csvs():
    con = connect()
    if os.path.exists("player_games.csv"):
        with open("player_games.csv", newline="", encoding="utf-8") as f:
            rows = [box_row(r) for r in csv.DictReader(f)]
        save_box_rows(con, rows)
        print(f"   player_games.csv: {len(rows):,} rows imported")

    if os.path.exists("odds_history.csv"):
        with open("odds_history.csv", newline="", encoding="utf-8") as f:
            rows = [(r["snapshot_utc"], r["game_id"], r["tipoff_et"], r["tipoff_et"][:10],
                     r["away_team"], r["home_team"], r["book"], num(r["away_price"]),
                     num(r["home_price"]), float(r["away_fair"]), float(r["home_fair"]))
                    for r in csv.DictReader(f)]
        existing = {r[0] for r in con.execute("SELECT DISTINCT snapshot_utc FROM odds_snapshots")}
        rows = [r for r in rows if r[0] not in existing]
        con.executemany("INSERT INTO odds_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        print(f"   odds_history.csv: {len(rows):,} rows imported")

    if os.path.exists("props_history.csv"):
        with open("props_history.csv", newline="", encoding="utf-8") as f:
            rows = []
            for r in csv.DictReader(f):
                away, home = r["matchup"].split(" @ ")
                rows.append((r["snapshot_utc"], r["game_id"], r["tipoff_et"], r["tipoff_et"][:10],
                             home, away, r["player"], player_key(r["player"]), r["book"],
                             "player_threes", r["side"], float(r["line"]), num(r["price"])))
        existing = {r[0] for r in con.execute("SELECT DISTINCT snapshot_utc FROM prop_snapshots")}
        rows = [r for r in rows if r[0] not in existing]
        con.executemany("INSERT INTO prop_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        print(f"   props_history.csv: {len(rows):,} rows imported")
    con.commit()
    con.close()
