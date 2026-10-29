"""
hot.py — who's hot, who's streaking, and the chance a player reaches any milestone (free).

For every player on a team playing today:
  - season so far: games, averages, how many games he reached each milestone (e.g. 15+ PTS in 4/4)
  - current streak at each milestone, and his floor (lowest game this season)
  - heat: last 5 games vs his normal level (🔥 +15% or more, ❄ -15% or worse)
  - chance to reach each milestone tonight (PTS 10/15/20/25/30/35, 3PM 1-5, REB, AST, PRA)

How the chance is worked out (tested on the 2023-24 to 2025-26 seasons, ~23,000 games per stat):
  expected = (1 - w) x normal level + w x last-5 average
             normal level = this season's average, with last season counted as a few extra games
             early on (so 2 hot games don't look like a new player)
  chance   = negative binomial around that number (fits basketball counts well)
  w (how much recent form counts) was fitted: PTS 0.30, 3PM 0.20, REB 0.20, AST 0.30, PRA 0.40.
  Hot streaks DO carry over, but only about a third of the way: a player 25% above his average
  over 5 games scores ~+1.7 next game, not +25%. Results when the model said 80-90%: 85-89% hit.

Every day the best milestone legs are saved to hot_legs and graded, so you can see whether the
model keeps its calibration this season (Record tab).

    python hot.py              today's hot board, season-long streaks, safest legs
    python hot.py 2026-10-28   another day
"""

import math
import sys
from collections import defaultdict
from datetime import datetime

from config import ET, implied, payout, player_key

# ---------- fitted on 2023-24 .. 2025-26 (see docstring) ----------
PARAMS = {
    "pts":  dict(w_recent=0.30, prior_games=6,  r=8),
    "fg3m": dict(w_recent=0.20, prior_games=15, r=12),
    "reb":  dict(w_recent=0.20, prior_games=10, r=12),
    "ast":  dict(w_recent=0.30, prior_games=6,  r=12),
    "pra":  dict(w_recent=0.40, prior_games=6,  r=12),
}
LADDER = {
    "pts":  (10, 15, 20, 25, 30, 35),
    "fg3m": (1, 2, 3, 4, 5),
    "reb":  (4, 6, 8, 10, 12, 15),
    "ast":  (3, 4, 6, 8, 10, 12),
    "pra":  (15, 20, 25, 30, 35, 40, 45),
}
LABEL = {"pts": "PTS", "fg3m": "3PM", "reb": "REB", "ast": "AST", "pra": "PRA"}
# Odds API markets that price "X or more" for each stat (regular Over lines count too: Over 19.5 = 20+)
MARKETS = {
    "pts":  ("player_points", "player_points_alternate"),
    "fg3m": ("player_threes", "player_threes_alternate"),
    "reb":  ("player_rebounds", "player_rebounds_alternate"),
    "ast":  ("player_assists", "player_assists_alternate"),
    "pra":  ("player_points_rebounds_assists", "player_points_rebounds_assists_alternate"),
}
HOT, COLD = 0.15, -0.15        # last 5 vs normal level
SAVE_MIN_P = 0.70              # milestone legs this likely (or priced) are saved and graded daily
OUT_STATUSES = ("out", "doubtful", "suspension")


# ---------- math ----------
def nb_at_least(t, mean, r):
    """P(X >= t) for a negative binomial with this mean and shape r."""
    if t <= 0:
        return 1.0
    if mean <= 0:
        return 0.0
    p = r / (r + mean)
    log_p, log_q = math.log(p), math.log(1 - p)
    below = sum(math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * log_p + k * log_q)
                for k in range(t))
    return max(0.0, min(1.0, 1 - below))


def expected(stat, season_vals, prior_avg):
    """Expected value tonight from this season's games (oldest first, 10+ min) and last season."""
    prm, n = PARAMS[stat], len(season_vals)
    if not n and prior_avg is None:
        return None
    season = sum(season_vals) / n if n else 0.0
    base = season if prior_avg is None else (season * n + prior_avg * prm["prior_games"]) / (n + prm["prior_games"])
    if n >= 3:
        l5 = season_vals[-5:]
        return (1 - prm["w_recent"]) * base + prm["w_recent"] * sum(l5) / len(l5), base
    return base, base


def american(dec):
    return round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))


def decimal(price):
    return 1 + payout(price)


# ---------- who plays today ----------
def teams_playing(con, day):
    """{team: (opp, is_home)} from any source that knows today's schedule."""
    games = set()
    for q in ("SELECT home, away FROM live_games WHERE game_date=?",
              "SELECT home, away FROM espn_lines WHERE game_date=?",
              "SELECT home, away FROM game_predictions WHERE game_date=?"):
        try:
            games |= {(r[0], r[1]) for r in con.execute(q, (day,))}
        except Exception:
            pass
    out = {}
    for h, a in games:
        out[h], out[a] = (a, True), (h, False)
    if not out:                                   # past days: the box scores know who played
        for r in con.execute("SELECT DISTINCT team, opp, home FROM player_games WHERE game_date=?", (day,)):
            out[r[0]] = (r[1], bool(r[2]))
    return out


def backfill(con, verbose=True):
    """One time: milestone calls for every earlier day this season (using only games before each
    day), graded right away, so the calibration check has data from day one."""
    today = datetime.now(ET).date().isoformat()
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT game_date FROM player_games WHERE season=? AND game_date<? ORDER BY game_date",
        (_season_of(today), today))]
    for d in days:
        save_legs(con, d, legs(con, d))
    n = grade_legs(con)
    if verbose:
        print(f"   milestone model: backfilled {len(days)} days this season, {n} calls graded")


def injured(con, day):
    """{player_key: status} from the latest injury list on or before day."""
    snap = con.execute("SELECT MAX(snapshot_date) FROM injuries WHERE snapshot_date<=?", (day,)).fetchone()[0]
    if not snap:
        return {}
    return {r[0]: r[1] for r in con.execute(
        "SELECT player_key, status FROM injuries WHERE snapshot_date=?", (snap,))}


# ---------- the board ----------
def _season_of(day):
    d = datetime.fromisoformat(day)
    return d.year if d.month >= 8 else d.year - 1


def board(con, day=None, teams=None):
    """List of player cards for teams playing on day (or the given teams)."""
    day = day or datetime.now(ET).date().isoformat()
    season = _season_of(day)
    playing = teams_playing(con, day) if teams is None else teams
    if not playing:
        return []
    inj = injured(con, day)
    q = ",".join("?" * len(playing))
    rows = con.execute(
        f"SELECT player_key, player_name, team, game_date, min, pts, fg3m, reb, ast FROM player_games "
        f"WHERE season=? AND game_date<? AND team IN ({q}) ORDER BY game_date",
        (season, day, *playing)).fetchall()
    by_player, last_team_games = defaultdict(list), defaultdict(set)
    for r in rows:
        by_player[(r["player_key"], r["team"])].append(r)
        last_team_games[r["team"]].add(r["game_date"])
    recent = {t: sorted(d)[-3:] for t, d in last_team_games.items()}
    prior = {}
    for r in con.execute(
            "SELECT player_key, COUNT(*) n, AVG(pts) pts, AVG(fg3m) fg3m, AVG(reb) reb, AVG(ast) ast, "
            "AVG(pts+reb+ast) pra FROM player_games WHERE season=? AND min>=10 GROUP BY player_key",
            (season - 1,)):
        if r["n"] >= 15:
            prior[r["player_key"]] = dict(r)

    cards = []
    for (key, team), games in by_player.items():
        played = [g for g in games if (g["min"] or 0) > 0]
        if not played or not any(g["game_date"] in recent[team] for g in played):
            continue                                   # not in the rotation lately (or traded away)
        real = [g for g in played if (g["min"] or 0) >= 10]
        mins = [g["min"] for g in played]
        if sum(mins[-3:]) / len(mins[-3:]) < 12:
            continue                                   # deep bench: milestones aren't worth showing
        opp, home = playing[team]
        status = inj.get(key, "")
        card = dict(key=key, name=played[-1]["player_name"], team=team, opp=opp, home=home,
                    games=len(played), min_avg=sum(mins) / len(mins), min_l3=sum(mins[-3:]) / len(mins[-3:]),
                    status=status, out=status.lower() in OUT_STATUSES, stats={})
        for stat in PARAMS:
            def val(g):
                return g["pts"] + g["reb"] + g["ast"] if stat == "pra" else g[stat]
            all_vals = [val(g) for g in played]          # every game he played (streaks count these)
            fit_vals = [val(g) for g in real]            # 10+ minute games (what the model was fitted on)
            pr = prior.get(key, {}).get(stat)
            ex = expected(stat, fit_vals, pr)
            if ex is None:
                continue
            mean, normal = ex
            l5 = all_vals[-5:]
            heat = (sum(l5) / len(l5) / normal - 1) if normal > 0.5 and len(all_vals) >= 3 else 0.0
            ladder = []
            for t in LADDER[stat]:
                hits = sum(v >= t for v in all_vals)
                streak = 0
                for v in reversed(all_vals):
                    if v < t:
                        break
                    streak += 1
                ladder.append(dict(t=t, hits=hits, n=len(all_vals), streak=streak,
                                   l10=sum(v >= t for v in all_vals[-10:]), n10=len(all_vals[-10:]),
                                   p=nb_at_least(t, mean, PARAMS[stat]["r"])))
            card["stats"][stat] = dict(
                mean=mean, normal=normal, season=sum(all_vals) / len(all_vals), l5=sum(l5) / len(l5),
                last=all_vals[::-1][:10], floor=min(all_vals), heat=heat, prior=pr, ladder=ladder)
        if card["stats"]:
            h = card["stats"].get("pts", {}).get("heat", 0)
            card["heat"] = h
            card["tag"] = "🔥 HOT" if h >= HOT else "❄ COLD" if h <= COLD else ""
            cards.append(card)
    cards.sort(key=lambda c: c["stats"].get("pts", {}).get("mean", 0), reverse=True)
    return cards


def streak_text(card, stat, step):
    """'15+ PTS in all 4 games' / '15+ PTS in 9 of 10' style summary for one rung."""
    n, hits = step["n"], step["hits"]
    if hits == n:
        return f"{step['t']}+ {LABEL[stat]} in all {n} games"
    return f"{step['t']}+ {LABEL[stat]} in {hits} of {n}" + (f" (last {step['streak']} straight)" if step["streak"] >= 3 else "")


def season_long(cards, stat="pts", min_games=3):
    """Players who reached a milestone in EVERY game this season: [(card, highest such milestone)]."""
    out = []
    for c in cards:
        s = c["stats"].get(stat)
        if not s or c["games"] < min_games or c["out"]:
            continue
        perfect = [x for x in s["ladder"] if x["hits"] == x["n"]]
        if perfect:
            out.append((c, perfect[-1]))
    return sorted(out, key=lambda x: (x[1]["t"], x[0]["stats"][stat]["floor"]), reverse=True)


# ---------- prices for milestones (from pulled regular + alternate lines) ----------
def milestone_prices(con, day):
    """{(player_key, stat, t): (best price, book, books)} for 'Over t-0.5' lines pulled for day."""
    stat_of = {m: s for s, ms in MARKETS.items() for m in ms}
    q = ",".join("?" * len(stat_of))
    best = {}
    for r in con.execute(
            f"SELECT p.player_key, p.market, p.line, p.price, p.book FROM prop_snapshots p JOIN ("
            f"  SELECT event_id, market, MAX(snapshot_utc) s FROM prop_snapshots WHERE game_date=? "
            f"  GROUP BY event_id, market) last ON p.event_id=last.event_id AND p.market=last.market "
            f"  AND p.snapshot_utc=last.s WHERE p.side='Over' AND p.market IN ({q})", (day, *stat_of)):
        t = r["line"] + 0.5
        if t != int(t):
            continue
        k = (r["player_key"], stat_of[r["market"]], int(t))
        price, book, books = best.get(k, (None, None, set()))
        books = books | {r["book"]}
        if price is None or r["price"] > price:
            price, book = r["price"], r["book"]
        best[k] = (price, book, books)
    return {k: (p, b, len(bs)) for k, (p, b, bs) in best.items()}


def legs(con, day, cards=None, min_p=SAVE_MIN_P):
    """Milestone legs worth considering: the highest rung at min_p+ per player+stat, plus every
    rung that has a price. Each leg: dict(player_key, name, team, opp, stat, t, p, price, book, ...)."""
    cards = board(con, day) if cards is None else cards
    prices = milestone_prices(con, day)
    out = []
    for c in cards:
        if c["out"]:
            continue
        for stat, s in c["stats"].items():
            safe = [x for x in s["ladder"] if x["p"] >= min_p]
            keep = {safe[-1]["t"]} if safe else set()
            keep |= {x["t"] for x in s["ladder"] if (c["key"], stat, x["t"]) in prices and x["p"] >= 0.5}
            for x in s["ladder"]:
                if x["t"] not in keep:
                    continue
                price, book, books = prices.get((c["key"], stat, x["t"]), (None, None, 0))
                out.append(dict(kind="milestone", player_key=c["key"], name=c["name"], team=c["team"],
                                opp=c["opp"], stat=stat, t=x["t"], p=x["p"], price=price, book=book,
                                books=books, hits=x["hits"], n=x["n"], streak=x["streak"],
                                status=c["status"], label=f"{c['name']} {x['t']}+ {LABEL[stat]}"))
    return out


def save_legs(con, day, leg_list):
    """Keep the model's milestone calls for grading (first call of the day wins; prices update)."""
    for l in leg_list:
        con.execute(
            "INSERT INTO hot_legs (game_date, player_key, player, team, opp, stat, t, p, price, book, "
            "hits, n, created_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now')) "
            "ON CONFLICT(game_date, player_key, stat, t) DO UPDATE SET price=excluded.price, "
            "book=excluded.book WHERE hot_legs.result IS NULL AND excluded.price IS NOT NULL",
            (day, l["player_key"], l["name"], l["team"], l["opp"], l["stat"], l["t"], round(l["p"], 4),
             l["price"], l["book"], l["hits"], l["n"]))
    con.commit()


def grade_legs(con):
    """Grade saved milestone legs from box scores. DNP = void. Returns legs graded."""
    done = 0
    for l in con.execute("SELECT * FROM hot_legs WHERE result IS NULL").fetchall():
        res = leg_result(con, l["game_date"], l["player_key"], l["team"], l["stat"], l["t"])
        if res:
            con.execute("UPDATE hot_legs SET result=?, actual=? WHERE game_date=? AND player_key=? "
                        "AND stat=? AND t=?", (*res, l["game_date"], l["player_key"], l["stat"], l["t"]))
            done += 1
    con.commit()
    return done


def leg_result(con, day, key, team, stat, t=None, line=None, side="Over"):
    """('W'|'L'|'DNP', actual) once the team's game is over and in the box scores, else None."""
    from nba_live import game_over
    if not game_over(con, day, team):
        return None                     # still being played: NBA.com's game log can be partial
    if not con.execute("SELECT 1 FROM player_games WHERE game_date=? AND team=? LIMIT 1", (day, team)).fetchone():
        return None
    r = con.execute("SELECT pts, fg3m, reb, ast, min FROM player_games WHERE game_date=? AND player_key=?",
                    (day, key)).fetchone()
    if not r or not (r["min"] or 0):
        return "DNP", None
    v = r["pts"] + r["reb"] + r["ast"] if stat == "pra" else r[stat]
    if t is not None:
        return ("W" if v >= t else "L"), v
    won = v > line if side == "Over" else v < line
    return ("W" if won else "L"), v


def season_correction(con, min_legs=300):
    """How honest the model has been this season on likely legs (60%+): actual hits / predicted.
    1.0 = spot on; 0.96 = 4% optimistic. Parlays scale leg chances by this (capped 0.85-1.05)."""
    r = con.execute("SELECT COUNT(*), SUM(result='W'), SUM(p) FROM hot_legs WHERE result IN ('W','L') "
                    "AND p>=0.6 AND game_date>=?", (f"{_season_of(datetime.now(ET).date().isoformat())}-08-01",)).fetchone()
    if not r[0] or r[0] < min_legs:
        return 1.0
    return max(0.85, min(1.05, r[1] / r[2]))


def calibration(con):
    """[(bucket label, legs, model %, actual %)] for graded milestone legs this season."""
    out = []
    for lo in (0.5, 0.6, 0.7, 0.8, 0.9):
        r = con.execute("SELECT COUNT(*), AVG(p), AVG(result='W') FROM hot_legs WHERE result IN ('W','L') "
                        "AND p>=? AND p<?", (lo, lo + 0.1 + (1e-9 if lo == 0.9 else 0))).fetchone()
        if r[0]:
            out.append((f"{lo:.0%}–{lo + 0.1:.0%}", r[0], r[1], r[2]))
    return out


if __name__ == "__main__":
    from db import connect
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(ET).date().isoformat()
    con = connect()
    cards = board(con, day)
    if not cards:
        print(f"No games found for {day}. Run 'python daily.py update' first (free).")
        sys.exit()
    print(f"=== {day}: {len(cards)} rotation players on {len({c['team'] for c in cards})} teams ===\n")
    print("🔥 HOT (last 5 games vs normal, points)")
    for c in sorted([c for c in cards if c["tag"] == "🔥 HOT" and not c["out"]], key=lambda c: -c["heat"])[:12]:
        s = c["stats"]["pts"]
        print(f"   {c['name']:<24}{c['team']} vs {c['opp']}  last 5 {s['l5']:.1f} vs normal {s['normal']:.1f} "
              f"({c['heat']:+.0%})  last: {', '.join(map(str, s['last'][:5]))}")
    print("\n❄ COLD")
    for c in sorted([c for c in cards if c["tag"] == "❄ COLD" and not c["out"]], key=lambda c: c["heat"])[:6]:
        s = c["stats"]["pts"]
        print(f"   {c['name']:<24}{c['team']}  last 5 {s['l5']:.1f} vs normal {s['normal']:.1f} ({c['heat']:+.0%})")
    for stat in ("pts", "fg3m", "reb", "ast"):
        rows = season_long(cards, stat)
        if rows:
            print(f"\nEVERY GAME THIS SEASON — {LABEL[stat]}")
            for c, step in rows[:12]:
                print(f"   {c['name']:<24}{c['team']}  {streak_text(c, stat, step):<28} floor "
                      f"{c['stats'][stat]['floor']}   tonight {step['p']:.0%}")
    lg = sorted(legs(con, day, cards), key=lambda l: -l["p"])
    print("\nSAFEST MILESTONE LEGS (chance tonight)")
    for l in lg[:20]:
        pr = f"{l['price']:+d} {l['book']}" if l["price"] is not None else "no price pulled"
        print(f"   {l['p']:.0%}  {l['label']:<34} ({l['hits']}/{l['n']} this season)  {pr}")
    con.close()
