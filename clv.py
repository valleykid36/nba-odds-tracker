"""
clv.py — closing-line value (CLV) for game-line picks, free from ESPN (no credits).

Closing line value asks: did the market move toward your pick after you made it?
If you took 76ers -2.5 and they closed -4, you beat the close by 1.5 points. Over a
season, beating the close is the most reliable early sign of a real edge, long before
win-loss records stop being noise.

ESPN shows DraftKings' opening and closing lines for every game (spread, spread price,
moneyline, total). This file saves them and scores each saved game pick:

  clv_pts   points gained vs the close (spreads/totals; + = better than the close)
  clv_ev    expected profit per unit if the CLOSING line is the true price
            (+0.03 = your price was worth +3% vs the close)

  update_clv(con)       fetch closes for recent pick days, score the picks (run by daily.py update)
  clv_summary(con)      per tier: picks scored, average clv_ev, share that beat the close

Player props aren't on ESPN; prop CLV would need Odds API credits (not done yet).

Check it by hand:
    python clv.py              scores the last week of picks and prints the summary
    python clv.py 2026-10-26   shows ESPN's open/close lines for one day
"""

import math
import sys
from datetime import datetime, timedelta, timezone

import requests

from config import BROWSER_HEADERS, ET, MARGIN_SD, TOTAL_SD, implied, payout, team_abbr
from db import connect

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
CORE_ODDS = ("https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{}"
             "/competitions/{}/odds")
PREFERRED_BOOK = "100"     # ESPN's provider id for DraftKings
LOOK_BACK_DAYS = 7         # pick days older than this without a close are left alone

# Spread/total win chance changes about this much per point near the line (from the
# same bell curves game_model.py uses): 1 / (sqrt(2*pi) * SD).
PROB_PER_POINT = {"spreads": 1 / (math.sqrt(2 * math.pi) * MARGIN_SD),   # ~3.2% per point
                  "totals": 1 / (math.sqrt(2 * math.pi) * TOTAL_SD)}     # ~2.2% per point


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(v):
    """'+2.5' / '-110' / 'EVEN' / 'PK' -> float, or None."""
    if v is None:
        return None
    s = str(v).strip().upper()
    if s in ("EVEN", "EV"):
        return 100.0
    if s in ("PK", "PICK"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def _american(block, *path):
    for k in path:
        block = (block or {}).get(k)
    if isinstance(block, dict):
        block = block.get("american")
    return _num(block)


# ---------------- fetch ----------------
def espn_lines(day):
    """day 'YYYY-MM-DD' -> rows for every started/finished game:
    (game_date, espn_id, home, away, book, kind, market, side, point, price)."""
    sb = requests.get(SCOREBOARD, params={"dates": day.replace("-", "")},
                      headers=BROWSER_HEADERS, timeout=20).json()
    rows = []
    for ev in sb.get("events", []):
        comp = ev["competitions"][0]
        state = comp.get("status", ev.get("status", {})).get("type", {}).get("state")
        if state not in ("in", "post"):
            continue                       # no closing line until the game starts
        teams = {c["homeAway"]: team_abbr(c["team"].get("displayName", "")) for c in comp["competitors"]}
        try:
            items = requests.get(CORE_ODDS.format(ev["id"], comp["id"]), headers=BROWSER_HEADERS,
                                 timeout=20).json().get("items", [])
        except (requests.RequestException, ValueError):
            continue
        if not items:
            continue
        item = next((i for i in items if str(i.get("provider", {}).get("id")) == PREFERRED_BOOK), items[0])
        book = item.get("provider", {}).get("name", "ESPN")
        base = (day, ev["id"], teams["home"], teams["away"], book)
        for kind in ("open", "close"):
            for side in ("home", "away"):
                t = item.get(f"{side}TeamOdds", {}).get(kind, {})
                ml = _american(t, "moneyLine")
                if ml is not None:
                    rows.append((*base, kind, "h2h", side, None, ml))
                pt, pr = _american(t, "pointSpread"), _american(t, "spread")
                if pt is not None and pr is not None:
                    rows.append((*base, kind, "spreads", side, pt, pr))
            tot = item.get(kind, {})
            total = _american(tot, "total")
            for side, key in (("Over", "over"), ("Under", "under")):
                pr = _american(tot, key)
                if total is not None and pr is not None:
                    rows.append((*base, kind, "totals", side, total, pr))
    return rows


def save_lines(con, rows):
    snap = now_utc()
    con.executemany("INSERT OR REPLACE INTO espn_lines VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(*r, snap) for r in rows])
    con.commit()


# ---------------- score ----------------
def clv_for(pick, close):
    """pick: game_predictions row. close: {(market, side): (point, price)} for its game.
    -> (close_point, close_price, clv_pts, clv_ev) or None."""
    mk, side, taken = pick["market"], pick["side"], pick["best_price"]
    other = {"home": "away", "away": "home", "Over": "Under", "Under": "Over"}[side]
    mine, theirs = close.get((mk, side)), close.get((mk, other))
    if not mine or not theirs or taken is None:
        return None
    (c_pt, c_pr), (_, o_pr) = mine, theirs
    fair = implied(c_pr) / (implied(c_pr) + implied(o_pr))        # no-vig close probability
    pts = None
    if mk == "spreads" and pick["point"] is not None and c_pt is not None:
        pts = pick["point"] - c_pt                                 # took +3.5, closed +2.5 -> +1
    elif mk == "totals" and pick["point"] is not None and c_pt is not None:
        pts = (c_pt - pick["point"]) if side == "Over" else (pick["point"] - c_pt)
    if pts:
        fair = min(0.99, max(0.01, fair + pts * PROB_PER_POINT[mk]))
    ev = fair * (payout(taken) + 1) - 1
    return c_pt, c_pr, (round(pts, 1) if pts is not None else None), round(ev, 4)


def score_day(con, day):
    """Fill CLV for every saved game pick on `day` whose game has an ESPN close. Returns count."""
    closes = {}
    for r in con.execute("SELECT * FROM espn_lines WHERE game_date=? AND kind='close'", (day,)):
        closes.setdefault((r["home"], r["away"]), {})[(r["market"], r["side"])] = (r["point"], r["price"])
    done = 0
    for p in con.execute("SELECT * FROM game_predictions WHERE game_date=? AND tier IS NOT NULL "
                         "AND clv_ev IS NULL", (day,)).fetchall():
        res = clv_for(p, closes.get((p["home"], p["away"]), {}))
        if res:
            con.execute("UPDATE game_predictions SET close_point=?, close_price=?, clv_pts=?, clv_ev=? "
                        "WHERE game_date=? AND event_id=? AND market=? AND side=?",
                        (*res, p["game_date"], p["event_id"], p["market"], p["side"]))
            done += 1
    con.commit()
    return done


def update_clv(con=None, verbose=True):
    """Free. Fetch ESPN closes for recent days that have unscored picks, then score them."""
    own = con is None
    con = con or connect()
    today = datetime.now(ET).date()
    since = (today - timedelta(days=LOOK_BACK_DAYS)).isoformat()
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT game_date FROM game_predictions WHERE tier IS NOT NULL AND clv_ev IS NULL "
        "AND game_date BETWEEN ? AND ? ORDER BY game_date", (since, today.isoformat()))]
    scored = 0
    for day in days:
        try:
            save_lines(con, espn_lines(day))
        except (requests.RequestException, ValueError) as e:
            if verbose:
                print(f"   closing lines for {day}: ESPN unavailable ({e})")
            continue
        scored += score_day(con, day)
    if verbose and days:
        print(f"   closing lines (ESPN, free): {scored} pick(s) scored for CLV")
    if own:
        con.close()
    return scored


def clv_summary(con, since=None):
    """{tier: dict(n, avg_ev, beat)} over game picks with a CLV score."""
    extra, args = (" AND game_date>=?", (since,)) if since else ("", ())
    out = {}
    for tier in ("BEST", "CONSIDER", "AVOID"):
        evs = [r[0] for r in con.execute(
            "SELECT clv_ev FROM game_predictions WHERE tier=? AND clv_ev IS NOT NULL" + extra,
            (tier, *args))]
        out[tier] = dict(n=len(evs), avg_ev=(sum(evs) / len(evs)) if evs else None,
                         beat=(sum(1 for e in evs if e > 0) / len(evs)) if evs else None)
    return out


def describe(s):
    """One line for the scoreboard / app card."""
    if not s["n"]:
        return "CLV: no scored picks yet"
    return f"CLV: {s['avg_ev']:+.1%} avg vs close · beat close {s['beat']:.0%} of {s['n']}"


if __name__ == "__main__":
    if len(sys.argv) > 1:
        day = sys.argv[1]
        rows = espn_lines(day)
        print(f"{len(rows)} lines for {day}")
        for r in sorted(rows, key=lambda r: (r[3], r[6], r[5], r[7])):
            pt = "" if r[8] is None else f"{r[8]:+g}" if r[6] == "spreads" else f"{r[8]:g}"
            print(f"  {r[3]} @ {r[2]}  {r[5]:<5} {r[6]:<7} {r[7]:<5} {pt:>6} {r[9]:+.0f}  ({r[4]})")
    else:
        con = connect()
        update_clv(con)
        for tier, s in clv_summary(con).items():
            print(f"   {tier:<9} {describe(s)}")
        con.close()
