"""
ingest.py — everything that pulls data INTO nba.db.

  update_player_games(seasons)  NBA.com box scores (free, via nba_api)
  update_injuries()             official NBA injury report + ESPN (free)
  update_odds()                 moneyline/spread/total, all games (3 credits; skipped if fresh)
  todays_events()               today's games (free, also refreshes the credit balance)
  check_credits()               credit balance (free)
  update_props(event, markets)  player props for one game (1 credit per market; fresh markets skipped)
  import_csvs()                 one-time import of your existing CSV files
"""

import csv
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from config import (ET, LINES_REFRESH_HOURS, PA_BOOKS, PROP_REFRESH_HOURS, SEASONS, implied,
                    player_key, team_abbr)
from db import connect, set_meta

load_dotenv()
ODDS_KEY = os.getenv("ODDS_API_KEY")
ODDS_BASE = "https://api.the-odds-api.com/v4/sports/basketball_nba"
ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"


def remember_credits(resp):
    """Every Odds API response carries the balance in its headers; save it."""
    left = resp.headers.get("x-requests-remaining")
    if left is not None:
        con = connect()
        set_meta(con, "credits_left", int(float(left)))
        set_meta(con, "credits_month", datetime.now(ET).strftime("%Y-%m"))
        last = resp.headers.get("x-requests-last")
        if last is not None:
            set_meta(con, "credits_last_call", int(float(last)))
        con.close()
    return left


def check_credits():
    """Free: the sports list costs 0 credits and its headers show the balance."""
    resp = requests.get("https://api.the-odds-api.com/v4/sports", params={"apiKey": ODDS_KEY},
                        timeout=15)
    resp.raise_for_status()
    return remember_credits(resp)


def fresh_since(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def lines_are_fresh(con, game_date):
    r = con.execute("SELECT MAX(snapshot_utc) FROM game_lines WHERE game_date=?", (game_date,)).fetchone()
    return bool(r[0]) and r[0] >= fresh_since(LINES_REFRESH_HOURS)


def fresh_prop_markets(con, event_id):
    """Prop markets for this game already pulled recently (re-pulling them would waste credits)."""
    return {r[0] for r in con.execute(
        "SELECT DISTINCT market FROM prop_snapshots WHERE event_id=? AND snapshot_utc>=?",
        (event_id, fresh_since(PROP_REFRESH_HOURS)))}


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
        try:                       # skip games still being played: their rows are partial
            from nba_live import unfinished_game_ids
            skip = unfinished_game_ids(con)
        except Exception:
            skip = set()
        if skip:
            df = df[~df["GAME_ID"].astype(str).isin(skip)]
            con.execute(f"DELETE FROM player_games WHERE game_id IN ({','.join('?' * len(skip))})",
                        tuple(skip))      # partial rows saved by an earlier run come back at the final
        save_box_rows(con, [box_row(r) for r in df.to_dict("records")])
        print(f"   {season}: {len(df):,} player-games saved")
        time.sleep(2)
    con.close()


# ---------------- Injuries (official NBA report + ESPN, both free) ----------------
def espn_injuries():
    resp = requests.get(ESPN_INJURIES, timeout=20)
    resp.raise_for_status()
    rows = []
    for t in resp.json().get("injuries", []):
        team = team_abbr(t.get("displayName", ""))
        for inj in t.get("injuries", []):
            name = inj.get("athlete", {}).get("displayName", "")
            if name:
                rows.append(dict(team=team, player=name, player_key=player_key(name),
                                 status=inj.get("status", ""), comment=inj.get("shortComment", ""),
                                 return_date=(inj.get("details") or {}).get("returnDate", "")))
    return rows


def update_injuries():
    """Today's injury list. The NBA's official report wins for tonight's games (it's what
    teams must file); ESPN fills in everyone else plus return dates. Every pull is also
    kept in injury_history."""
    from nba_injury import fetch_official
    snap, today = now_utc(), datetime.now(ET).strftime("%Y-%m-%d")
    con = connect()
    merged, history = {}, []

    try:
        espn = espn_injuries()
        for r in espn:
            merged[(r["team"], r["player_key"])] = dict(r, source="ESPN")
            history.append((snap, snap, "ESPN", today, "", r["team"], r["player"], r["player_key"],
                            r["status"], r["comment"]))
        print(f"   ESPN injuries: {len(espn)} players listed")
    except Exception as e:
        print(f"   couldn't reach ESPN ({e}); keeping its last list")
        for r in con.execute("SELECT * FROM injuries WHERE snapshot_date=(SELECT MAX(snapshot_date) FROM "
                             "injuries) AND COALESCE(source, 'ESPN')='ESPN'"):
            merged[(r["team"], r["player_key"])] = dict(
                team=r["team"], player=r["player"], player_key=r["player_key"], status=r["status"],
                comment=r["comment"], return_date=r["return_date"], source="ESPN")

    try:
        official, label, missing = fetch_official()
    except Exception as e:
        official, label, missing = [], None, []
        print(f"   couldn't read the official NBA report ({e}); using ESPN only")
    if label:
        upcoming = sorted({r["game_date"] for r in official if r["game_date"] >= today})
        night = upcoming[0] if upcoming else None
        tonight = [r for r in official if r["game_date"] == night]
        for r in tonight:
            old = merged.get((r["team"], r["player_key"]), {})
            merged[(r["team"], r["player_key"])] = dict(
                team=r["team"], player=r["player"], player_key=r["player_key"], status=r["status"],
                comment=r["reason"], return_date=old.get("return_date", ""), source="NBA")
        seen = con.execute("SELECT 1 FROM injury_history WHERE source='NBA' AND report=? LIMIT 1",
                           (label,)).fetchone()
        if not seen:
            history += [(label, snap, "NBA", r["game_date"], r["matchup"], r["team"], r["player"],
                         r["player_key"], r["status"], r["reason"]) for r in official]
        print(f"   official NBA report ({label}): {len(tonight)} players for {night or 'no upcoming games'}"
              + (f"; not yet submitted: {', '.join(t for t in missing if t)}" if missing else ""))
        set_meta(con, "nba_injury_report", label)
    elif not official:
        print("   official NBA report: none posted in the last day (normal on off days)")

    if merged:
        con.execute("DELETE FROM injuries WHERE snapshot_date = ?", (today,))  # keep latest per day
        con.executemany(
            "INSERT INTO injuries (snapshot_utc, snapshot_date, team, player, player_key, status, comment, "
            "return_date, source) VALUES (?,?,?,?,?,?,?,?,?)",
            [(snap, today, r["team"], r["player"], r["player_key"], r["status"], r["comment"],
              r["return_date"], r["source"]) for r in merged.values()])
    con.executemany("INSERT INTO injury_history VALUES (?,?,?,?,?,?,?,?,?,?)", history)
    con.commit()
    con.close()
    if not merged:
        raise RuntimeError("no injury source reachable")
    print(f"   injuries: {len(merged)} players on today's list")


# ---------------- Game lines: moneyline, spread, total (The Odds API) ----------------
def update_odds(force=False):
    """All of today's games, 3 markets = 3 credits. Skipped if pulled in the last few hours."""
    today = datetime.now(ET).strftime("%Y-%m-%d")
    if not force:
        con = connect()
        fresh = lines_are_fresh(con, today)
        con.close()
        if fresh:
            print(f"   game lines: already pulled in the last {LINES_REFRESH_HOURS}h, reusing (0 credits)")
            return 0
    resp = requests.get(f"{ODDS_BASE}/odds/", timeout=15, params={
        "apiKey": ODDS_KEY, "bookmakers": PA_BOOKS, "markets": "h2h,spreads,totals",
        "oddsFormat": "american"})
    resp.raise_for_status()
    snap, ml_rows, line_rows = now_utc(), [], []
    for ev in resp.json():
        tip = to_et(ev["commence_time"])
        home, away = ev["home_team"], ev["away_team"]
        when = (tip.strftime("%Y-%m-%d %H:%M"), tip.strftime("%Y-%m-%d"))
        for book in ev["bookmakers"]:
            for mkt in book["markets"]:
                for o in mkt["outcomes"]:
                    side = ("home" if o["name"] == home else "away" if o["name"] == away else o["name"])
                    line_rows.append((snap, ev["id"], *when, home, away, book["title"], mkt["key"],
                                      side, o.get("point"), o["price"]))
                if mkt["key"] == "h2h":
                    p = {o["name"]: o["price"] for o in mkt["outcomes"]}
                    if home in p and away in p:
                        pa, ph = implied(p[away]), implied(p[home])
                        ml_rows.append((snap, ev["id"], *when, away, home, book["title"], p[away],
                                        p[home], round(pa / (pa + ph), 4), round(ph / (pa + ph), 4)))
    con = connect()
    con.executemany("INSERT INTO odds_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", ml_rows)
    con.executemany("INSERT INTO game_lines VALUES (?,?,?,?,?,?,?,?,?,?,?)", line_rows)
    con.commit()
    con.close()
    print(f"   game lines: {len(line_rows)} prices saved  [credits left: {remember_credits(resp)}]")
    return int(float(resp.headers.get("x-requests-last", 3)))


# ---------------- Player props (The Odds API) ----------------
def todays_events():
    resp = requests.get(f"{ODDS_BASE}/events", params={"apiKey": ODDS_KEY}, timeout=15)  # free
    resp.raise_for_status()
    remember_credits(resp)
    today = datetime.now(ET).date()
    out = [(to_et(e["commence_time"]), e) for e in resp.json()]
    return sorted([x for x in out if x[0].date() == today and x[0] > datetime.now(ET)],
                  key=lambda x: x[0])


def update_props(event, markets=("player_threes",), force=False):
    """One game, all requested prop markets in one call. Costs 1 credit per market returned.
    Markets already pulled for this game in the last few hours are skipped. Returns credits used."""
    if not force:
        con = connect()
        have = fresh_prop_markets(con, event["id"])
        con.close()
        skipped = [m for m in markets if m in have]
        markets = [m for m in markets if m not in have]
        if skipped:
            print(f"   props: reusing {len(skipped)} market(s) pulled in the last {PROP_REFRESH_HOURS}h")
        if not markets:
            return 0
    resp = requests.get(f"{ODDS_BASE}/events/{event['id']}/odds", timeout=20, params={
        "apiKey": ODDS_KEY, "bookmakers": PA_BOOKS, "markets": ",".join(markets),
        "oddsFormat": "american"})
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
    print(f"   props: {len(rows)} prices saved  [credits left: {remember_credits(resp)}]")
    return int(float(resp.headers.get("x-requests-last", len(markets))))


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
