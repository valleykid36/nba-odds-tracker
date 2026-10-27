"""
nba_extra.py — more free NBA.com data, saved once a day (via nba_api).

  team_stats       pace, offensive/defensive/net rating, and what each defense allows
                   (points, threes made/attempted, 3P%)
  player_shooting  catch-and-shoot vs pull-up threes per game for every player

Each day's numbers are saved as their own snapshot, so later the model can be tested
on "what we knew that morning" without peeking at the future.

  context_lines(con, entry, day)   short lines the app shows under a pick
"""

import time
from datetime import datetime

from config import CURRENT_SEASON, ET, SEASONS, player_key, team_abbr
from db import connect


def _frame(endpoint, **kw):
    for attempt in range(3):
        try:
            return endpoint(timeout=60, **kw).get_data_frames()[0]
        except Exception as e:
            print(f"   attempt {attempt + 1} failed ({e}); retrying")
            time.sleep(8)
    raise RuntimeError("NBA.com didn't answer")


def _num(r, k):
    v = r.get(k)
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def update_extra(season=None):
    from nba_api.stats.endpoints import leaguedashptstats, leaguedashteamstats
    season = season or SEASONS[-1]
    day = datetime.now(ET).strftime("%Y-%m-%d")

    adv = _frame(leaguedashteamstats.LeagueDashTeamStats, season=season,
                 measure_type_detailed_defense="Advanced", per_mode_detailed="PerGame")
    time.sleep(1.5)
    opp = _frame(leaguedashteamstats.LeagueDashTeamStats, season=season,
                 measure_type_detailed_defense="Opponent", per_mode_detailed="PerGame")
    opp_by = {r["TEAM_ID"]: r for r in opp.to_dict("records")}
    teams = []
    for r in adv.to_dict("records"):
        o = opp_by.get(r["TEAM_ID"], {})
        teams.append((day, CURRENT_SEASON, team_abbr(r["TEAM_NAME"]), int(r.get("GP") or 0),
                      _num(r, "PACE"), _num(r, "OFF_RATING"), _num(r, "DEF_RATING"), _num(r, "NET_RATING"),
                      _num(o, "OPP_PTS"), _num(o, "OPP_FG3M"), _num(o, "OPP_FG3A"), _num(o, "OPP_FG3_PCT")))

    time.sleep(1.5)
    cs = _frame(leaguedashptstats.LeagueDashPtStats, season=season, player_or_team="Player",
                pt_measure_type="CatchShoot", per_mode_simple="PerGame")
    time.sleep(1.5)
    pu = _frame(leaguedashptstats.LeagueDashPtStats, season=season, player_or_team="Player",
                pt_measure_type="PullUpShot", per_mode_simple="PerGame")
    pu_by = {r["PLAYER_ID"]: r for r in pu.to_dict("records")}
    shooters = []
    for r in cs.to_dict("records"):
        p = pu_by.get(r["PLAYER_ID"], {})
        shooters.append((day, CURRENT_SEASON, player_key(r["PLAYER_NAME"]), r["PLAYER_NAME"],
                         r.get("TEAM_ABBREVIATION", ""), int(r.get("GP") or 0),
                         _num(r, "CATCH_SHOOT_FG3M"), _num(r, "CATCH_SHOOT_FG3A"), _num(r, "CATCH_SHOOT_FG3_PCT"),
                         _num(p, "PULL_UP_FG3M"), _num(p, "PULL_UP_FG3A"), _num(p, "PULL_UP_FG3_PCT")))

    con = connect()
    con.executemany("INSERT OR REPLACE INTO team_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", teams)
    con.executemany("INSERT OR REPLACE INTO player_shooting VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", shooters)
    con.commit()
    con.close()
    print(f"   team stats: {len(teams)} teams (pace, ratings, threes allowed); "
          f"shooting splits: {len(shooters)} players")


# ======================= for the app =======================
def _latest(con, table, day, where, args):
    return con.execute(f"SELECT * FROM {table} WHERE snapshot_date=(SELECT MAX(snapshot_date) FROM {table} "
                       f"WHERE snapshot_date<=?) AND {where}", (day, *args)).fetchone()


def _league(con, day):
    return con.execute("SELECT AVG(pace) pace, AVG(def_rtg) drtg, AVG(opp_fg3a) fg3a FROM team_stats "
                       "WHERE snapshot_date=(SELECT MAX(snapshot_date) FROM team_stats WHERE snapshot_date<=?)",
                       (day,)).fetchone()


def _rank(con, day, col, team, high_first=True):
    rows = con.execute(f"SELECT team FROM team_stats WHERE snapshot_date=(SELECT MAX(snapshot_date) "
                       f"FROM team_stats WHERE snapshot_date<=?) ORDER BY {col} {'DESC' if high_first else 'ASC'}",
                       (day,)).fetchall()
    order = [r["team"] for r in rows]
    return order.index(team) + 1 if team in order else None


def context_lines(con, e, day):
    """Extra matchup info for one pick (shown in the app, not used by the model yet)."""
    out = []
    try:
        lg = _league(con, day)
        if not lg or lg["pace"] is None:
            return out
        if e["kind"] == "prop":
            t = _latest(con, "team_stats", day, "team=?", (e.get("team"),))
            o = _latest(con, "team_stats", day, "team=?", (e.get("opp"),))
            if t and o and t["pace"] and o["pace"]:
                pace = (t["pace"] + o["pace"]) / 2
                out.append(f"game pace ~{pace:.1f} (league {lg['pace']:.1f}): "
                           + ("faster than average, more chances" if pace > lg["pace"] + 1 else
                              "slower than average, fewer chances" if pace < lg["pace"] - 1 else "about average"))
            if o and o["def_rtg"]:
                out.append(f"{e['opp']} defense: {o['def_rtg']:.1f} rating "
                           f"(#{_rank(con, day, 'def_rtg', e['opp'], high_first=False)} of 30)")
            if e.get("market_key") == "player_threes":
                if o and o["opp_fg3a"]:
                    out.append(f"{e['opp']} allows {o['opp_fg3a']:.1f} 3PA / {o['opp_fg3m']:.1f} 3PM per game "
                               f"(league {lg['fg3a']:.1f} 3PA)")
                s = _latest(con, "player_shooting", day, "player_key=?", (e.get("who"),))
                if s and s["cs_fg3a"] is not None:
                    pct = lambda v: f"{v:.0%}" if v is not None else "?"
                    out.append(f"catch-and-shoot {s['cs_fg3a']:.1f} 3PA at {pct(s['cs_fg3_pct'])}, "
                               f"pull-up {s['pu_fg3a'] or 0:.1f} 3PA at {pct(s['pu_fg3_pct'])}")
        else:
            h = _latest(con, "team_stats", day, "team=?", (e.get("home"),))
            a = _latest(con, "team_stats", day, "team=?", (e.get("away"),))
            if h and a and h["pace"] and a["pace"]:
                out.append(f"pace {e['away']} {a['pace']:.1f}, {e['home']} {h['pace']:.1f} (league {lg['pace']:.1f})")
                out.append(f"net rating {e['away']} {a['net_rtg']:+.1f}, {e['home']} {h['net_rtg']:+.1f}")
                out.append(f"defense {e['away']} {a['def_rtg']:.1f}, {e['home']} {h['def_rtg']:.1f}")
    except Exception as ex:      # extra info must never break the app
        out.append(f"(context unavailable: {ex})")
    return out


if __name__ == "__main__":
    update_extra()
