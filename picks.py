"""
picks.py — turns model predictions into a short list of plays most likely to HIT.

How a line becomes a pick (PICK_MODE = "confidence", the default):
  1. win% = trust x model + (1 - trust) x market   (market = sportsbooks, vig removed)
     "trust" is learned per market from every graded line.
  2. The model AND the sportsbooks must both make this side the favorite. If they point
     opposite ways, there is no pick (that is how a +900 underdog used to sneak in).
  3. Factors are checked one by one and shown as a checklist:
       props: model projection, books agree, recent form (last 10 games vs this line),
              minutes, matchup, role/teammates out
       games: model, books agree, recent form, rest, home court, plus (only when there is
              news) injuries to regulars and line movement since the open (ESPN, free)
  4. TOP  = 60%+ to hit, most factors agree, no red flags, and not overpriced.
     LEAN = 55%+ to hit and at least half the factors agree.
     AVOID = the numbers look good but something says the model is wrong
             (role changed against it, or model and books far apart).
  5. Ranked by chance to hit. One pick per player and one side pick per game.

PICK_MODE = "value" in config.py brings back the old EV ranking.
Every tier is graded so you can see which ones actually hit.
"""

import math
from collections import defaultdict

from config import (BEST_EV, BEST_MIN_GAMES, CONF_LEAN, CONF_TOP, CONSIDER_EV,
                    HIT_RATE_GAMES, HIT_RATE_NEEDED, INJURY_EDGE_MIN, INJURY_FLAG_MIN,
                    INJURY_TOTAL_MIN, LEAN_MIN_EV, LINE_MOVE, LINE_MOVE_FLAG, LEAN_SIGNAL_SHARE, MAX_BEST_PICKS,
                    MAX_DISAGREE, MAX_PICKS_PER_PLAYER, MIN_MINUTES, MIN_MINUTES_OVER,
                    MODEL_TRUST, PICK_MODE, PROP_MARKETS, ROLE_CHANGE_FLAG, ROLE_FLAG_MIN_GAMES, TOP_MIN_EV,
                    TOP_SIGNAL_SHARE, TRUST_PRIOR_LINES, payout)

GAME_LABELS = {"h2h": "ML", "spreads": "SPREAD", "totals": "TOTAL"}


def ev(win, price):
    return win * payout(price) - (1 - win)


def break_even(price):
    return 1 / (1 + payout(price))


def needed(n, share):
    return math.ceil(n * share - 1e-9)


# ======================= learning how much to trust the model =======================
def _fit(rows):
    """rows: [(model_p, market_p, outcome)]. Best blend weight + Brier scores."""
    n = len(rows)
    if not n:
        return None

    def brier(w):
        return sum((w * a + (1 - w) * b - y) ** 2 for a, b, y in rows) / n
    grid = [i / 20 for i in range(21)]
    best_w = min(grid, key=brier)
    trust = (n * best_w + TRUST_PRIOR_LINES * MODEL_TRUST) / (n + TRUST_PRIOR_LINES)
    return dict(trust=trust, n=n, model=brier(1.0), market=brier(0.0), blend=brier(trust))


def learned_trust(con):
    """{market: {trust, n, model, market, blend}} from graded lines (props and game lines)."""
    out = {}
    by = defaultdict(list)
    for r in con.execute("SELECT market, p_over, market_over, result FROM predictions "
                         "WHERE result IN ('Over','Under') AND market_over IS NOT NULL"):
        by[r["market"]].append((r["p_over"], r["market_over"], 1 if r["result"] == "Over" else 0))
    for r in con.execute("SELECT market, model_prob, market_prob, result FROM game_predictions "
                         "WHERE result IN ('W','L')"):
        by[r["market"]].append((r["model_prob"], r["market_prob"], 1 if r["result"] == "W" else 0))
    for market, rows in by.items():
        out[market] = _fit(rows)
    return out


def trust_for(trust, market):
    t = trust.get(market)
    return t["trust"] if t else MODEL_TRUST


# ======================= recent form =======================
class Form:
    """Recent games for players and teams, using only games before the pick date."""

    def __init__(self, con, game_date):
        self.con, self.date = con, game_date
        self.players, self.teams = {}, None
        self._out, self._moves = {}, {}

    def out(self, team):
        """[(player, minutes)] regulars listed out/doubtful for this game."""
        if team not in self._out:
            from budget import out_regulars
            self._out[team] = out_regulars(self.con, team, self.date)
        return self._out[team]

    def moves(self, home, away):
        if (home, away) not in self._moves:
            try:
                from clv import movement
                self._moves[(home, away)] = movement(self.con, self.date, home, away)
            except Exception:
                self._moves[(home, away)] = {}
        return self._moves[(home, away)]

    def player_games(self, key):
        if key not in self.players:
            self.players[key] = self.con.execute(
                "SELECT game_date, opp, min, pts, reb, ast, stl, blk, fg3m, fg3a FROM player_games "
                "WHERE player_key=? AND game_date<? AND min>=? ORDER BY game_date DESC LIMIT ?",
                (key, self.date, MIN_MINUTES, HIT_RATE_GAMES)).fetchall()
        return self.players[key]

    def prop_hits(self, key, stats, line, over):
        """(hits, games, [values newest first]) for this line over the last N games."""
        vals = [sum(g[s] for s in stats) for g in self.player_games(key)]
        hits = sum(1 for v in vals if (v > line if over else v < line))
        return hits, len(vals), vals

    def team_games(self, team):
        """Last N games for a team: [(won, margin, total)] newest first."""
        if self.teams is None:
            from game_model import team_results
            self.teams = defaultdict(list)
            for _, _, home, away, hp, ap in team_results(self.con, self.date):
                self.teams[home].append((hp > ap, hp - ap, hp + ap))
                self.teams[away].append((ap > hp, ap - hp, hp + ap))
        return self.teams[team][::-1][:HIT_RATE_GAMES]


def signal(ok, text):
    return {"ok": bool(ok), "text": text}


def grade_tier(win, signals, warn, value, blind_spot):
    """Tier from chance to hit, factors agreeing, and red flags. None = not shown."""
    n_ok, n = sum(s["ok"] for s in signals), len(signals)
    if win < CONF_LEAN:
        return None
    if blind_spot:
        return "AVOID"
    if win >= CONF_TOP and n_ok >= needed(n, TOP_SIGNAL_SHARE) and not warn and value >= TOP_MIN_EV:
        return "BEST"
    if n_ok >= needed(n, LEAN_SIGNAL_SHARE) and len(warn) <= 1 and value >= LEAN_MIN_EV:
        return "CONSIDER"
    return None


# ======================= props =======================
def classify_prop(p, trust, form, side=None):
    """side=None: choose the side the model and books both favor (or skip).
    side='Over'/'Under': rebuild a saved pick as-is."""
    if p["market_over"] is None:
        return None
    if side is None:
        model_over, books_over = p["p_over"] >= 0.5, p["market_over"] >= 0.5
        if PICK_MODE == "value":
            side = "Over" if p["p_over"] > p["market_over"] else "Under"
        elif model_over != books_over:
            return None                       # model and books disagree on the side: no pick
        else:
            side = "Over" if model_over else "Under"
    over = side == "Over"
    price = p["best_over_price"] if over else p["best_under_price"]
    book = p["best_over_book"] if over else p["best_under_book"]
    if price is None:
        return None
    model = p["p_over"] if over else 1 - p["p_over"]
    market = p["market_over"] if over else 1 - p["market_over"]
    t = trust_for(trust, p["market"])
    win = t * model + (1 - t) * market
    value = ev(win, price)
    cfg = PROP_MARKETS.get(p["market"], {"label": p["market"], "stats": ["fg3m"]})
    label, line = cfg["label"], p["line"]

    # ---- the factor checklist ----
    signals = []
    if p["mean"] is not None:
        signals.append(signal(model >= 0.58, f"model projects {p['mean']:.1f} {label} vs line {line:g} "
                                             f"({model:.0%} to hit)"))
    signals.append(signal(market >= 0.5, f"sportsbooks also favor it ({market:.0%})"))
    hits, games, vals = form.prop_hits(p["player_key"], cfg["stats"], line, over)
    if games >= 5:
        recent = ", ".join(f"{v:g}" for v in vals[:5])
        signals.append(signal(hits / games >= HIT_RATE_NEEDED,
                              f"{side} {line:g} hit in {hits} of last {games} games (last 5: {recent})"))
    else:
        signals.append(signal(False, f"only {games} recent games to check form"))
    pm = p["proj_min"] or 0
    if over:
        signals.append(signal(pm >= MIN_MINUTES_OVER and not p["b2b"],
                              f"projected {pm:.0f} min" + (", 2nd night of a back-to-back" if p["b2b"] else "")))
    else:
        signals.append(signal(p["b2b"] or pm < 20, f"projected {pm:.0f} min" +
                              (", 2nd night of a back-to-back" if p["b2b"] else "")))
    opp_f = p["opp_factor"] or 1.0
    signals.append(signal(opp_f >= 1.02 if over else opp_f <= 0.98,
                          f"{p['opp']} " + ("allows more" if opp_f >= 1.02 else "allows less" if opp_f <= 0.98
                                            else "is average at allowing") + f" {label} ({opp_f:.2f}x)"))
    role, boost = p["role_change"], p["injury_boost"] or 0
    mates = (p["notes"] or "").split("teammates out:")[-1].strip() if "teammates out:" in (p["notes"] or "") else ""
    if over:
        ok = (role is not None and role >= 0.10) or boost >= 0.3
        txt = (f"teammates out (+{boost:.1f} {label})" + (f": {mates}" if mates else "") if boost >= 0.3 else
               f"role {'up' if (role or 0) > 0 else 'down'} {abs(role or 0):.0%} vs last season" if role is not None
               else "no role change or teammates out")
    else:
        ok = role is not None and role <= -0.10 and boost < 0.3
        txt = (f"role down {abs(role):.0%} vs last season" if role is not None and role < 0 else
               f"role up {role:.0%} vs last season" if role is not None else "no role change")
        if boost >= 0.3:
            txt += f"; teammates out add {boost:.1f}"
    signals.append(signal(ok, txt))

    # ---- red flags ----
    warn = []
    cur = p["cur_games"] or 0
    role_against = (role is not None and cur >= ROLE_FLAG_MIN_GAMES
                    and ((not over and role >= ROLE_CHANGE_FLAG) or (over and role <= -ROLE_CHANGE_FLAG)))
    if role_against:
        warn.append(f"production {role:+.0%} vs last season, opposite to this pick")
    disagree = abs(model - market)
    if disagree > MAX_DISAGREE:
        warn.append(f"model ({model:.0%}) and books ({market:.0%}) far apart: likely something the model misses")
    if cur < BEST_MIN_GAMES:
        warn.append(f"{cur} game{'s' * (cur != 1)} this season; leans on last season")
    if "self:" in (p["notes"] or ""):
        warn.append(p["notes"].split(";")[0].replace("self:", "listed").strip())
    if over and p["b2b"]:
        warn.append("2nd night of a back-to-back: minutes risk")
    if (p["books"] or 0) < 2:
        warn.append("only one book has this line; books % is shaky")
    if value < TOP_MIN_EV:
        warn.append(f"overpriced: {price:+d} needs {break_even(price):.0%} to break even")

    if PICK_MODE == "value":
        tier = _value_tier(value, warn, role_against, over, cur, model - market)
    else:
        tier = grade_tier(win, signals, warn, value, role_against)
    return dict(kind="prop", tier=tier, side=side, name=p["player"] or p["player_key"], who=p["player_key"],
                team=p["team"], opp=p["opp"], stats=cfg["stats"], line=line,
                game=p["event_id"], matchup=f"{p['team']} vs {p['opp']}",
                pick=f"{side} {line:g} {label}", market_key=p["market"],
                price=price, book=book, model=model, market=market, win=win, ev=value, edge=model - market,
                trust=t, signals=signals, n_ok=sum(s["ok"] for s in signals), why=[s["text"] for s in signals if s["ok"]],
                warn=warn, key=(p["game_date"], p["player_key"], p["market"], p["line"]))


def _value_tier(value, warn, role_against, over, games, edge):
    """The old EV rules (PICK_MODE = 'value')."""
    if value < CONSIDER_EV:
        return None
    if role_against or (not over and games < BEST_MIN_GAMES and edge >= 0.07):
        return "AVOID"
    w = [x for x in warn if not x.startswith("model (")]
    if value >= BEST_EV and not w:
        return "BEST"
    if value >= BEST_EV and len(w) == 1 or value < BEST_EV and not w:
        return "CONSIDER"
    return "AVOID"


# ======================= game lines =======================
def classify_game_side(r, other, trust, form):
    market = r["market"]
    t = trust_for(trust, market)
    win = t * r["model_prob"] + (1 - t) * r["market_prob"]
    value = ev(win, r["best_price"])
    team = r["home"] if r["side"] == "home" else r["away"] if r["side"] == "away" else None
    opp = (r["away"] if r["side"] == "home" else r["home"]) if team else None
    pt = r["point"]

    signals = [signal(r["model_prob"] >= 0.55, f"model gives it {r['model_prob']:.0%}"
                      + (f" (model: {r['home'] if r['pred_margin'] >= 0 else r['away']} by "
                         f"{abs(r['pred_margin']):.1f}, total {r['pred_total']:.0f})"
                         if r["pred_margin"] is not None else "")),
               signal(r["market_prob"] >= 0.5, f"sportsbooks also favor it ({r['market_prob']:.0%})")]
    if market == "totals":
        games = form.team_games(r["home"]) + form.team_games(r["away"])
        over = r["side"] == "Over"
        hits = sum(1 for _, _, tot in games if (tot > pt if over else tot < pt))
        if games:
            avg = sum(tot for *_, tot in games) / len(games)
            signals.append(signal(hits / len(games) >= HIT_RATE_NEEDED,
                                  f"{r['side']} {pt:g} hit in {hits} of both teams' last {len(games)} games "
                                  f"(avg total {avg:.0f})"))
    else:
        mine, theirs = form.team_games(team), form.team_games(opp)
        if mine:
            w = sum(1 for won, *_ in mine if won)
            if market == "h2h":
                tw = sum(1 for won, *_ in theirs if won)
                signals.append(signal(w / len(mine) >= HIT_RATE_NEEDED and w >= tw,
                                      f"{team} {w}-{len(mine) - w} last {len(mine)} ({opp} {tw}-{len(theirs) - tw})"))
            else:
                avg = sum(m for _, m, _ in mine) / len(mine)
                covers = sum(1 for _, m, _ in mine if m + pt > 0)
                signals.append(signal(covers / len(mine) >= HIT_RATE_NEEDED,
                                      f"{team} would have covered {pt:+g} in {covers} of last {len(mine)} "
                                      f"(avg margin {avg:+.1f})"))
    note = r["rest_note"] or ""
    if team:
        rested = opp in note and team not in note
        signals.append(signal(rested, f"{opp} on a back-to-back" if rested else
                              (note or "no rest edge")))
        signals.append(signal(r["side"] == "home", f"{team} {'at home' if r['side'] == 'home' else 'on the road'}"))
    else:
        tired = bool(note)
        signals.append(signal(tired and r["side"] == "Under", note or "no back-to-backs"))

    warn = []

    # ---- injuries (the game model doesn't see them; the books do) ----
    def who(lst):
        return ", ".join(f"{n} ({m})" for n, m in lst[:3]) + (" …" if len(lst) > 3 else "")
    h_out, a_out = form.out(r["home"]), form.out(r["away"])
    if team:
        mine, theirs = (h_out, a_out) if r["side"] == "home" else (a_out, h_out)
        m_min, t_min = sum(m for _, m in mine), sum(m for _, m in theirs)
        if m_min or t_min:
            signals.append(signal(t_min - m_min >= INJURY_EDGE_MIN,
                                  "injuries: " + "; ".join(x for x in (
                                      f"{opp} without {who(theirs)}" if theirs else "",
                                      f"{team} without {who(mine)}" if mine else "") if x)
                                  + f" (regular minutes out: {team} {m_min}, {opp} {t_min})"))
        if m_min - t_min >= INJURY_FLAG_MIN:
            warn.append(f"{team} missing {m_min - t_min} more regular minutes than {opp}; "
                        "the game model doesn't adjust for injuries")
    else:
        both = sum(m for _, m in h_out + a_out)
        if both:
            under = r["side"] == "Under"
            signals.append(signal(under and both >= INJURY_TOTAL_MIN,
                                  f"injuries: {both} regular min out across both teams"
                                  + (" (leans Under)" if both >= INJURY_TOTAL_MIN else "")))
            if not under and both >= INJURY_TOTAL_MIN * 1.5:
                warn.append(f"{both} regular minutes out across both teams; Over is fighting that")

    # ---- line movement since the open (ESPN / DraftKings, free) ----
    mv = form.moves(r["home"], r["away"]).get((market, r["side"]))
    if mv is not None and abs(mv) >= LINE_MOVE[market] / 2:
        unit = f"{abs(mv):.0%}" if market == "h2h" else f"{abs(mv):g} pts"
        toward = mv > 0
        signals.append(signal(toward and mv >= LINE_MOVE[market],
                              f"line moved {unit} {'toward' if toward else 'against'} this side since the open"))
        if -mv >= LINE_MOVE_FLAG[market]:
            warn.append(f"market moved {unit} against this side since the open (sharp money?)")

    disagree = abs(r["model_prob"] - r["market_prob"])
    if disagree > MAX_DISAGREE:
        warn.append(f"model ({r['model_prob']:.0%}) and books ({r['market_prob']:.0%}) far apart: "
                    "likely something the model misses")
    if (r["early_games"] or 0) < BEST_MIN_GAMES:
        warn.append("early season: team ratings still lean on last season")
    if (r["books"] or 0) < 2:
        warn.append("only one book has this line; books % is shaky")
    if value < TOP_MIN_EV:
        warn.append(f"overpriced: {r['best_price']:+d} needs {break_even(r['best_price']):.0%} to break even")

    if PICK_MODE == "value":
        tier = _value_tier(value, [w for w in warn if not w.startswith("overpriced")], False, True, 99, 0)
    else:
        tier = grade_tier(win, signals, warn, value, disagree > MAX_DISAGREE)
    pick = (f"{team} ML" if market == "h2h" or pt is None and market == "spreads" else
            f"{team} {pt:+g}" if market == "spreads" else
            f"{r['side']} {pt:g}" if pt is not None else f"{r['side']} total")
    return dict(kind="game", tier=tier, side=r["side"], name=f"{r['away']} @ {r['home']}",
                home=r["home"], away=r["away"], point=pt,
                who=None, game=r["event_id"], matchup=GAME_LABELS[market], pick=pick, market_key=market,
                price=r["best_price"], book=r["best_book"], model=r["model_prob"],
                market=r["market_prob"], win=win, ev=value, edge=r["model_prob"] - r["market_prob"],
                trust=t, signals=signals, n_ok=sum(s["ok"] for s in signals),
                why=[s["text"] for s in signals if s["ok"]], warn=warn,
                key=(r["game_date"], r["event_id"], market, r["side"]))


def classify_games(rows, trust, form, saved_only=False):
    """One side per game+market. Returns (entries, {(event, market, side): entry or None})."""
    by = defaultdict(list)
    for r in rows:
        by[(r["event_id"], r["market"])].append(r)
    entries, chosen = [], {}
    for (ev_id, market), sides in by.items():
        for s in sides:
            chosen[(ev_id, market, s["side"])] = None
        if saved_only:
            picks = [s for s in sides if s["tier"]]
        elif PICK_MODE == "value":
            picks = [max(sides, key=lambda s: s["model_prob"] - s["market_prob"])]
        else:   # the side both the model and the books favor, if they agree
            picks = [s for s in sides if s["model_prob"] >= 0.5 and s["market_prob"] >= 0.5]
        for r in picks:
            other = next((s for s in sides if s is not r), None)
            e = classify_game_side(r, other, trust, form)
            if e["tier"] or saved_only:
                entries.append(e)
                chosen[(ev_id, market, r["side"])] = e
    return entries, chosen


# ======================= the board =======================
def rank_key(e):
    return e["ev"] if PICK_MODE == "value" else (e["win"], e["n_ok"])


def dedupe(entries):
    """Best first: one pick per player, one ML-or-spread per game."""
    used_player, used_side, keep = defaultdict(int), set(), []
    for e in sorted(entries, key=lambda e: (e["tier"] != "AVOID", e["tier"] == "BEST", rank_key(e)),
                    reverse=True):
        if e["tier"] == "AVOID":
            keep.append(e)
            continue
        if e["kind"] == "prop":
            if used_player[e["who"]] >= MAX_PICKS_PER_PLAYER:
                e["tier"] = None
                continue
            used_player[e["who"]] += 1
        elif e["market_key"] in ("h2h", "spreads"):
            if e["game"] in used_side:
                e["tier"] = None
                continue
            used_side.add(e["game"])
        keep.append(e)
    best = sorted([e for e in keep if e["tier"] == "BEST"], key=rank_key, reverse=True)
    for e in best[MAX_BEST_PICKS:]:
        e["tier"] = "CONSIDER"
        e["warn"].append(f"outside today's top {MAX_BEST_PICKS}")
    return keep


def build_board(con, game_date, save=None):
    """Classify every line for game_date and return (entries, prop lines checked, games).
    Today (or later): tiers are saved so they can be graded.
    Past days: the tiers saved at pick time are shown as-is (re-tiering would rewrite
    your record), with each pick's result attached."""
    from datetime import datetime
    from config import ET
    if save is None:
        save = game_date >= datetime.now(ET).strftime("%Y-%m-%d")
    trust = learned_trust(con)
    form = Form(con, game_date)
    preds = con.execute("SELECT * FROM predictions WHERE game_date=? AND market_over IS NOT NULL",
                        (game_date,)).fetchall()
    grows = con.execute("SELECT * FROM game_predictions WHERE game_date=?", (game_date,)).fetchall()

    prop_entries = []
    for p in preds:
        if save:
            c = classify_prop(p, trust, form)
        elif p["tier"] and p["pick_side"]:
            c = classify_prop(p, trust, form, side=p["pick_side"])
        else:
            c = None
        if c and (c["tier"] or not save):
            prop_entries.append(c)
    game_entries, game_choice = classify_games(grows, trust, form, saved_only=not save)
    board = dedupe(prop_entries + game_entries) if save else prop_entries + game_entries
    n_games = len({r["event_id"] for r in grows})
    prop_row = {(p["game_date"], p["player_key"], p["market"], p["line"]): p for p in preds}
    game_row = {(s["game_date"], s["event_id"], s["market"], s["side"]): s for s in grows}

    if save:
        by_key = {e["key"]: e for e in prop_entries if e["tier"]}
        for k in prop_row:
            e = by_key.get(k)
            con.execute("UPDATE predictions SET tier=?, pick_side=?, p_blend=?, ev=? WHERE game_date=? "
                        "AND player_key=? AND market=? AND line=?",
                        (e["tier"] if e else None, e["side"] if e else None,
                         round(e["win"], 4) if e else None, round(e["ev"], 4) if e else None, *k))
        for s in grows:
            e = game_choice.get((s["event_id"], s["market"], s["side"]))
            e = e if e and e["tier"] else None
            con.execute("UPDATE game_predictions SET tier=?, p_blend=?, ev=? WHERE game_date=? AND "
                        "event_id=? AND market=? AND side=?",
                        (e["tier"] if e else None, round(e["win"], 4) if e else None,
                         round(e["ev"], 4) if e else None,
                         s["game_date"], s["event_id"], s["market"], s["side"]))
        con.commit()

    for e in board:
        row = (prop_row if e["kind"] == "prop" else game_row)[e["key"]]
        if not save:
            e["tier"] = row["tier"]
            if row["ev"] is not None:
                e["ev"] = row["ev"]
            if row["p_blend"] is not None:
                e["win"] = row["p_blend"]
        if e["kind"] == "prop":
            e["result"] = ("DNP" if row["result"] == "DNP" else
                           ("W" if row["result"] == e["side"] else "L") if row["result"] else "")
        else:
            e["result"] = row["result"] or ""
    board = [e for e in board if e["tier"]]
    return sorted(board, key=rank_key, reverse=True), len(preds), n_games


# ======================= record =======================
def records(con, since=None):
    """Graded record per tier: {tier: dict(w, l, units, roi)} at the best price at pick time."""
    extra = " AND game_date>=?" if since else ""
    args = (since,) if since else ()
    props = con.execute("SELECT * FROM predictions WHERE result IN ('Over','Under') AND tier IS NOT NULL "
                        "AND pick_side IS NOT NULL" + extra, args).fetchall()
    games = con.execute("SELECT * FROM game_predictions WHERE result IN ('W','L') AND tier IS NOT NULL"
                        + extra, args).fetchall()
    out = {}
    for tier in ("BEST", "CONSIDER", "AVOID"):
        bets = []
        for r in props:
            if r["tier"] == tier:
                over = r["pick_side"] == "Over"
                price = r["best_over_price"] if over else r["best_under_price"]
                if price is not None:
                    bets.append(payout(price) if (r["result"] == "Over") == over else -1.0)
        for r in games:
            if r["tier"] == tier:
                bets.append(payout(r["best_price"]) if r["result"] == "W" else -1.0)
        w = sum(1 for b in bets if b > 0)
        out[tier] = dict(w=w, l=len(bets) - w, units=sum(bets),
                         roi=(sum(bets) / len(bets)) if bets else None)
    return out


def market_label(market):
    return PROP_MARKETS.get(market, {}).get("label") or GAME_LABELS.get(market, market)
