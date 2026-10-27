"""
nba_live.py — live scores and box scores from NBA.com's free scoreboard (no credits).

  update_live()        today's games: score, quarter, clock, and every player's stats so far.
                       Finished games are copied into player_games right away, so picks
                       grade minutes after the final buzzer instead of the next morning.
  live_for(con, day)   what the app shows: {player_key: stats}, {(home, away): game}

These are the same JSON files NBA.com's own scoreboard uses; they are public and free.
"""

import math
import re
from datetime import datetime, timedelta, timezone

import requests

from config import BROWSER_HEADERS, CURRENT_SEASON, ET, player_key
from db import connect

SCOREBOARD = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
BOXSCORE = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{}.json"
SCHEDULED, LIVE, FINAL = 1, 2, 3
_source = {"name": None}     # which source answered last: "cdn" or "stats"


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_minutes(v):
    """'PT34M12.00S' -> 34.2"""
    m = re.match(r"PT(?:(\d+)M)?(?:([\d.]+)S)?", v or "")
    if not m:
        return 0.0
    return float(m.group(1) or 0) + float(m.group(2) or 0) / 60


def clock(v):
    """'PT05M12.00S' -> '5:12'"""
    m = re.match(r"PT(\d+)M([\d.]+)S", v or "")
    return f"{int(m.group(1))}:{int(float(m.group(2))):02d}" if m else ""


def get(url):
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


# ---------- Fallback: stats.nba.com via nba_api (same source as the box scores that already work) ----------

def _basketball_day():
    """Today's NBA date in ET. Before 6 AM it's still last night's slate (late West Coast games)."""
    return (datetime.now(ET) - timedelta(hours=6)).date().isoformat()


def _stats_scoreboard():
    from nba_api.stats.endpoints import scoreboardv3
    raw = scoreboardv3.ScoreboardV3(game_date=_basketball_day(), timeout=30).get_dict()
    sb = raw.get("scoreboard", {})
    sb.setdefault("gameDate", _basketball_day())
    return sb                          # same field names as the CDN scoreboard


def _mmss_to_iso(v):
    """'34:12' -> 'PT34M12.00S' so the rest of this file reads both sources the same way."""
    m = re.match(r"(\d+):(\d+)", str(v or ""))
    return f"PT{int(m.group(1))}M{int(m.group(2))}.00S" if m else ""


def _stats_box(game, status):
    """BoxScoreTraditionalV3, reshaped to look like the CDN box score."""
    from nba_api.stats.endpoints import boxscoretraditionalv3
    raw = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game["gameId"], timeout=30).get_dict()
    box = raw.get("boxScoreTraditional", {})
    totals_match = True
    for side in ("homeTeam", "awayTeam"):
        pts = 0
        for p in box.get(side, {}).get("players", []):
            s = p.setdefault("statistics", {})
            s["minutes"] = _mmss_to_iso(s.get("minutes"))
            p["played"] = "1" if iso_minutes(s["minutes"]) > 0 else "0"
            p["name"] = f"{p.get('firstName', '')} {p.get('familyName', '')}".strip()
            pts += int(s.get("points", 0) or 0)
        if pts != int(game[side].get("score", 0) or 0):
            totals_match = False
    # stats.nba.com has no "final" flag on box scores. Only call it final once the players'
    # points add up to the final score, so a half-updated box score never gets graded.
    box["gameStatus"] = FINAL if status == FINAL and totals_match else LIVE
    return box


def fetch_scoreboard(verbose=True):
    try:
        sb = get(SCOREBOARD).get("scoreboard", {})
        _source["name"] = "cdn"
        return sb
    except requests.RequestException as e:
        if verbose:
            print(f"   cdn.nba.com scoreboard failed ({e}); using stats.nba.com instead")
        _source["name"] = "stats"
        return _stats_scoreboard()


def fetch_box(game, status):
    if _source["name"] == "cdn":
        try:
            return get(BOXSCORE.format(game["gameId"])).get("game", {})
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 403:
                raise                  # 404 = not posted yet; handled by the caller
    return _stats_box(game, status)


def update_live(verbose=True):
    """Refresh today's scoreboard and the box score of every started game.
    Returns (games, finished games newly saved to player_games)."""
    sb = fetch_scoreboard(verbose)
    day, games = sb.get("gameDate", ""), sb.get("games", [])
    con = connect()
    snap, newly_final = now_utc(), 0
    for g in games:
        gid, status = g["gameId"], int(g.get("gameStatus", 1))
        home, away = g["homeTeam"], g["awayTeam"]
        prev = con.execute("SELECT status FROM live_games WHERE game_id=?", (gid,)).fetchone()
        was_final = prev is not None and prev["status"] == FINAL
        def save_game(st=status, text=(g.get("gameStatusText") or "").strip()):
            con.execute("INSERT OR REPLACE INTO live_games VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                gid, day, g.get("gameTimeUTC", ""), home["teamTricode"], away["teamTricode"],
                home.get("score", 0), away.get("score", 0), st, text,
                g.get("period", 0), clock(g.get("gameClock")), snap))
        if status == SCHEDULED or (status == FINAL and was_final):
            save_game()                    # nothing to fetch (not started, or already saved)
            continue
        try:
            box = fetch_box(g, status)
        except Exception as e:
            if verbose:
                print(f"   {away['teamTricode']} @ {home['teamTricode']}: box score not up yet ({e})")
            if status != FINAL:
                save_game()                # score/clock still useful; a Final waits for its box score
            continue
        rows, finals = [], []
        for side, opp_side, is_home in (("homeTeam", "awayTeam", 1), ("awayTeam", "homeTeam", 0)):
            team, opp = box[side]["teamTricode"], box[opp_side]["teamTricode"]
            for p in box[side].get("players", []):
                s = p.get("statistics", {})
                name = p.get("name") or f"{p.get('firstName', '')} {p.get('familyName', '')}".strip()
                played = 1 if str(p.get("played", "0")) == "1" else 0
                mins = iso_minutes(s.get("minutes"))
                vals = [int(s.get(k, 0) or 0) for k in (
                    "points", "reboundsTotal", "assists", "steals", "blocks",
                    "threePointersMade", "threePointersAttempted")]
                rows.append((gid, p["personId"], day, name, player_key(name), team, played, mins,
                             *vals, snap))
                if played and gid.startswith("002"):   # regular season, same as NBA.com game logs
                    st = lambda k: int(s.get(k, 0) or 0)
                    finals.append((
                        gid, day, CURRENT_SEASON, p["personId"], name, player_key(name), team, opp,
                        is_home, mins, st("points"), st("reboundsTotal"), st("assists"), st("steals"),
                        st("blocks"), st("turnovers"), st("fieldGoalsMade"), st("fieldGoalsAttempted"),
                        st("threePointersMade"), st("threePointersAttempted"), st("freeThrowsMade"),
                        st("freeThrowsAttempted"), st("reboundsOffensive"), st("reboundsDefensive")))
        con.executemany("INSERT OR REPLACE INTO live_box VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        box_final = int(box.get("gameStatus", status)) == FINAL
        if box_final and finals:
            con.executemany("INSERT OR REPLACE INTO player_games VALUES (" + ",".join("?" * 24) + ")",
                            finals)
            newly_final += 1
        if status == FINAL and not box_final:
            save_game(LIVE, "Final (stats finishing)")   # box score not final yet: grade next refresh
        else:
            save_game()
    con.commit()
    con.close()
    if verbose:
        live = sum(1 for g in games if int(g.get("gameStatus", 1)) == LIVE)
        done = sum(1 for g in games if int(g.get("gameStatus", 1)) == FINAL)
        print(f"   live scores ({day}, via {_source['name']}): {len(games)} games, {live} in progress, {done} final"
              + (f"; {newly_final} newly final, saved for grading" if newly_final else ""))
    return games, newly_final


def live_for(con, game_date):
    """({player_key: row}, {(home, away): game row}) for one day, from the last refresh."""
    players = {r["player_key"]: r for r in con.execute(
        "SELECT b.*, g.status, g.status_text FROM live_box b JOIN live_games g USING (game_id) "
        "WHERE b.game_date=?", (game_date,))}
    games = {(r["home"], r["away"]): r for r in con.execute(
        "SELECT * FROM live_games WHERE game_date=?", (game_date,))}
    return players, games


def games_active(con, game_date):
    """True while any of the day's games is in progress or about to start."""
    now = datetime.now(timezone.utc)
    for r in con.execute("SELECT status, tip_utc FROM live_games WHERE game_date=?", (game_date,)):
        if r["status"] == LIVE:
            return True
        if r["status"] == SCHEDULED and r["tip_utc"]:
            tip = datetime.fromisoformat(r["tip_utc"].replace("Z", "+00:00"))
            if 0 <= (now - tip).total_seconds() < 6 * 3600:
                return True
    return False


def _game_for(games, team):
    return next((g for (h, a), g in games.items() if team in (h, a)), None)


def live_text(e, players, games):
    """(short text for the RESULT column, longer line for the details panel) or None."""
    if e["kind"] == "prop":
        g = _game_for(games, e.get("team"))
        if not g or g["status"] == SCHEDULED:
            return None
        row = players.get(e["who"])
        line, over = e["line"], e["side"] == "Over"
        if not row or not row["played"]:
            if g["status"] == FINAL:
                return "DNP", "didn't play"
            return f"— · {g['status_text']}", f"hasn't checked in yet ({g['status_text']})"
        v = sum(row[s] or 0 for s in e["stats"])
        winning = v > line if over else v < line
        if g["status"] == FINAL:
            return ("✓ W" if winning else "✗ L"), f"final: {v:g} ({e['pick']}) in {row['min']:.0f} min"
        need = max(0, math.floor(line) + 1 - v) if over else None
        long = (f"{v:g} so far in {row['min']:.0f} min ({g['status_text']})"
                + (f" — needs {need} more" if over and need else " — already over" if over else
                   (f" — can add {math.floor(line) - v:g} more and still hit" if math.floor(line) > v
                    else " — one more and it loses") if winning else " — already over the line"))
        return f"{'↑' if winning else '↓'} {v:g}/{line:g} · {g['status_text']}", long
    g = games.get((e.get("home"), e.get("away")))
    if not g or g["status"] == SCHEDULED:
        return None
    hp, ap = g["home_pts"] or 0, g["away_pts"] or 0
    margin = hp - ap if e["side"] == "home" else ap - hp
    mk, pt = e["market_key"], e.get("point")
    if mk == "h2h":
        winning = margin > 0
    elif mk == "spreads":
        winning = margin + (pt or 0) > 0
    else:
        winning = (hp + ap > pt) if e["side"] == "Over" else (hp + ap < pt)
    score = f"{g['away']} {ap}-{hp} {g['home']}"
    if g["status"] == FINAL:
        return None                         # graded from the box score; the result shows instead
    extra = f" (total {hp + ap})" if mk == "totals" else ""
    return (f"{'↑' if winning else '↓'} {ap}-{hp} · {g['status_text']}",
            f"{score}{extra}, {g['status_text']}: currently {'winning' if winning else 'losing'}")


if __name__ == "__main__":
    update_live()
