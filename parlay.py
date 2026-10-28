"""
parlay.py — builds parlays from the legs most likely to hit, and tracks how they do.

Legs come from two places:
  milestone   hot.py's "X or more" calls (Maxey 20+ PTS). Priced when the alternate lines (or a
              regular Over at X-0.5) were pulled; otherwise shown with the model's fair odds.
  prop        TOP / LEAN player props from the picks board (Over or Under a line).

Each leg's chance:
  milestone with a price:  half the model, half the sportsbook (its price with ~5% vig removed)
  milestone, no price:     the model
  (the model's part is scaled by how honest it has been this season: if its 85% calls have been
   hitting 81%, legs are scaled down to match; see hot.season_correction)
  prop:                    the board's blended chance (model + books)

Rules (config.py): one leg per player, at most PARLAY_MAX_PER_GAME legs from one game (same-game
legs move together, and most books won't take them in a regular parlay), no leg below
PARLAY_MIN_LEG_P, and no leg that's badly overpriced.

Parlays suggested each day (when there are enough legs):
  Safest 2 / Safest 3       highest chance that every leg hits
  Plus money                safest combo that pays at least even money (+100)
  Hot hand                  only players on a heater (last 5 games 15%+ above normal)
  Best value                best expected profit, still 35%+ to hit
Unpriced parlays (no lines pulled) are shown for ideas but aren't counted in the record.

    python parlay.py              today's parlays
    python parlay.py 2026-10-28   another day
"""

import itertools
import json
import sys
from datetime import datetime

from config import (ET, PARLAY_MAX_LEGS, PARLAY_MAX_PER_GAME, PARLAY_MIN_LEG_EV, PARLAY_MIN_LEG_P,
                    PROP_MARKETS, implied, payout)
from hot import HOT, LABEL, american, board, decimal, leg_result, legs

POOL = 30           # best legs considered for combos (keeps the search instant)
ALT_VIG = 1.05      # alternate lines are one-sided; this roughly strips the book's cut


def _game(l):
    return frozenset((l["team"], l["opp"]))


def milestone_legs(con, day, cards):
    from hot import season_correction
    out = []
    heat = {c["key"]: c.get("heat", 0) for c in cards}
    fix = season_correction(con)
    for l in legs(con, day, cards, min_p=PARLAY_MIN_LEG_P):
        l = dict(l, p=min(0.99, l["p"] * fix))
        if l["status"].lower() in ("out", "doubtful", "questionable"):
            continue
        if l["price"] is not None:
            book_p = min(0.99, implied(l["price"]) / ALT_VIG)
            win = 0.5 * l["p"] + 0.5 * book_p
        else:
            book_p, win = None, l["p"]
        out.append(dict(l, win=win, book_p=book_p, heat=heat.get(l["player_key"], 0),
                        why=f"{l['hits']}/{l['n']} this season" + (f", {l['streak']} straight" if l["streak"] >= 3 else "")))
    return out


def prop_legs(con, day, cards):
    from picks import build_board
    heat = {c["key"]: c.get("heat", 0) for c in cards}
    opp = {c["key"]: c["opp"] for c in cards}
    entries, _, _ = build_board(con, day, save=False)
    out = []
    for e in entries:
        if e["kind"] != "prop" or e["tier"] not in ("BEST", "CONSIDER") or e["win"] < PARLAY_MIN_LEG_P:
            continue
        out.append(dict(kind="prop", player_key=e["who"], name=e["name"], team=e["team"],
                        opp=e.get("opp") or opp.get(e["who"], ""), stats=e["stats"], line=e["line"],
                        side=e["side"], p=e["model"], win=e["win"], book_p=e["market"], price=e["price"],
                        book=e["book"], heat=heat.get(e["who"], 0), label=f"{e['name']} {e['pick']}",
                        why=f"{'TOP' if e['tier'] == 'BEST' else 'LEAN'} pick, {e['n_ok']}/{len(e['signals'])} factors"))
    return out


def _combo(ls):
    p, dec = 1.0, 1.0
    for l in ls:
        p *= l["win"]
        dec *= decimal(l["price"]) if l["price"] is not None else 1 / l["win"]
    return p, dec, p * dec - 1


def _ok(ls):
    players = [l["player_key"] for l in ls]
    if len(set(players)) < len(players):
        return False
    per_game = {}
    for l in ls:
        per_game[_game(l)] = per_game.get(_game(l), 0) + 1
    return max(per_game.values()) <= PARLAY_MAX_PER_GAME


def build(con, day=None):
    """[(name, legs, p_hit, price, ev, priced)] — the day's suggested parlays."""
    day = day or datetime.now(ET).date().isoformat()
    cards = board(con, day)
    pool = milestone_legs(con, day, cards) + prop_legs(con, day, cards)
    pool = [l for l in pool if l["win"] >= PARLAY_MIN_LEG_P]
    priced = [l for l in pool if l["price"] is not None
              and l["win"] * decimal(l["price"]) - 1 >= PARLAY_MIN_LEG_EV]
    # one leg per player+stat: keep the likeliest (a 15+ and a 20+ on the same player are one bet)
    def dedupe(ls):
        best = {}
        for l in sorted(ls, key=lambda l: -l["win"]):
            k = (l["player_key"], l.get("stat") or tuple(l.get("stats", ())))
            best.setdefault(k, l)
        return sorted(best.values(), key=lambda l: -l["win"])[:POOL]

    out = []

    def pick(name, ls, sizes, key, cond=lambda p, dec: True, is_priced=True):
        best = None
        for n in sizes:
            for c in itertools.combinations(ls, n):
                if not _ok(c):
                    continue
                p, dec, ev = _combo(c)
                if not cond(p, dec):
                    continue
                k = key(p, dec, ev)
                if best is None or k > best[0]:
                    best = (k, c, p, dec, ev)
        if best:
            _, c, p, dec, ev = best
            out.append((name, list(c), p, american(dec), ev, is_priced))

    sizes = range(2, PARLAY_MAX_LEGS + 1)
    if len(priced) >= 2:
        legs_ = dedupe(priced)
        pick("Safest 2-leg", legs_, (2,), lambda p, d, e: p)
        pick("Safest 3-leg", legs_, (3,), lambda p, d, e: p)
        pick("Plus money", legs_, sizes, lambda p, d, e: p, cond=lambda p, d: d >= 2.0)
        hot_legs = [l for l in legs_ if l["heat"] >= HOT]
        pick("Hot hand", hot_legs, sizes, lambda p, d, e: p, cond=lambda p, d: d >= 1.8)
        pick("Best value", legs_, sizes, lambda p, d, e: e, cond=lambda p, d: p >= 0.35)
    else:
        legs_ = dedupe([l for l in pool if l["kind"] == "milestone"])
        pick("Model's safest 3 (no prices pulled)", legs_, (3,), lambda p, d, e: p, is_priced=False)
        hot_legs = [l for l in legs_ if l["heat"] >= HOT]
        pick("Hot hand (no prices pulled)", hot_legs, (3,), lambda p, d, e: p, is_priced=False)
    # drop exact repeats (the same legs can win two categories)
    seen, unique = set(), []
    for x in out:
        sig = frozenset(l["label"] for l in x[1])
        if sig not in seen:
            seen.add(sig)
            unique.append(x)
    return unique


def save(con, day, parlays):
    keep = ("kind", "player_key", "name", "team", "opp", "stat", "t", "stats", "line", "side",
            "p", "win", "price", "book", "label", "why")
    for name, ls, p, price, ev, is_priced in parlays:
        con.execute(
            "INSERT INTO parlays (game_date, name, legs, p_hit, price, ev, priced, created_utc) "
            "VALUES (?,?,?,?,?,?,?,datetime('now')) ON CONFLICT(game_date, name) DO UPDATE SET "
            "legs=excluded.legs, p_hit=excluded.p_hit, price=excluded.price, ev=excluded.ev, "
            "priced=excluded.priced, created_utc=excluded.created_utc WHERE parlays.result IS NULL "
            "AND parlays.legs_graded IS NULL",
            (day, name, json.dumps([{k: l.get(k) for k in keep} for l in ls]), round(p, 4), price,
             round(ev, 4), int(is_priced)))
    con.commit()


def leg_outcome(con, day, l):
    if l["kind"] == "milestone":
        return leg_result(con, day, l["player_key"], l["team"], l["stat"], t=l["t"])
    stats = l["stats"]
    stat = "pra" if sorted(stats) == ["ast", "pts", "reb"] else stats[0] if len(stats) == 1 else None
    if stat:
        return leg_result(con, day, l["player_key"], l["team"], stat, line=l["line"], side=l["side"])
    r = con.execute(f"SELECT {'+'.join(stats)} v, min FROM player_games WHERE game_date=? AND player_key=?",
                    (day, l["player_key"])).fetchone()
    if not con.execute("SELECT 1 FROM player_games WHERE game_date=? AND team=? LIMIT 1", (day, l["team"])).fetchone():
        return None
    if not r or not r["min"]:
        return "DNP", None
    return ("W" if (r["v"] > l["line"]) == (l["side"] == "Over") else "L"), r["v"]


def grade(con):
    """Grade every open parlay. A DNP leg is void (dropped), as most books do."""
    done = 0
    for row in con.execute("SELECT * FROM parlays WHERE result IS NULL").fetchall():
        ls = json.loads(row["legs"])
        res = [leg_outcome(con, row["game_date"], l) for l in ls]
        graded = [r for r in res if r]
        won = sum(1 for r in graded if r[0] == "W")
        if any(r[0] == "L" for r in graded):
            result = "L"
        elif len(graded) == len(ls):
            result = "W" if won else "VOID"
        else:
            result = None
        con.execute("UPDATE parlays SET result=?, legs_won=?, legs_graded=? WHERE game_date=? AND name=?",
                    (result, won, len(graded), row["game_date"], row["name"]))
        done += result is not None
    con.commit()
    return done


def record(con):
    """{'w', 'l', 'units', 'by_name': {name: (w, l, units)}} for priced parlays."""
    out = dict(w=0, l=0, units=0.0, by_name={})
    for r in con.execute("SELECT name, result, price FROM parlays WHERE priced=1 AND result IN ('W','L')"):
        u = payout(r["price"]) if r["result"] == "W" else -1.0
        key = r["name"]
        w, l, units = out["by_name"].get(key, (0, 0, 0.0))
        out["by_name"][key] = (w + (r["result"] == "W"), l + (r["result"] == "L"), units + u)
        out["w"] += r["result"] == "W"
        out["l"] += r["result"] == "L"
        out["units"] += u
    return out


def refresh(con, day=None, verbose=True):
    """Free: save today's milestone calls and parlays (prices from whatever was pulled), grade old ones."""
    from hot import grade_legs, save_legs
    from hot import backfill
    day = day or datetime.now(ET).date().isoformat()
    if not con.execute("SELECT 1 FROM hot_legs LIMIT 1").fetchone():
        backfill(con, verbose)
    graded = grade_legs(con) + grade(con)
    cards = board(con, day)
    if cards:
        save_legs(con, day, legs(con, day, cards))
        parlays = build(con, day)
        save(con, day, parlays)
        if verbose:
            priced = sum(1 for p in parlays if p[5])
            print(f"   hot players + parlays: {len(cards)} players, {len(parlays)} parlays "
                  f"({priced} priced)" + (f"; {graded} legs/parlays graded" if graded else ""))
    elif verbose and graded:
        print(f"   {graded} milestone legs/parlays graded")


if __name__ == "__main__":
    from db import connect
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(ET).date().isoformat()
    con = connect()
    ps = build(con, day)
    if not ps:
        print("No parlays: not enough legs yet. Run 'python daily.py update' (free) or pull lines.")
    for name, ls, p, price, ev, is_priced in ps:
        print(f"\n{name}:  {p:.0%} to hit · pays {price:+d}" + (f" · EV {ev:+.1%}" if is_priced else " (fair odds)"))
        for l in ls:
            pr = f"{l['price']:+d} {l['book']}" if l["price"] is not None else "no price"
            print(f"   {l['win']:.0%}  {l['label']:<34} {pr:<18} {l['why']}")
    con.close()
