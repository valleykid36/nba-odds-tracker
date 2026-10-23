"""
picks.py — turns model predictions into a short list of good bets.

How a line becomes a pick:
  1. win% = trust x model + (1 - trust) x market   (market = sportsbooks, vig removed)
     "trust" is learned per market from every graded line: if the model has been
     sharper than the books on threes, it gets more say on threes; if not, less.
  2. EV = win% x payout at the best PA price - lose%.  Only positive-EV picks survive.
  3. Red flags (role change against the pick, early-season data, expensive price,
     back-to-back for an Over, player questionable) knock a pick down a tier.
  4. One pick per player and one side pick (ML or spread) per game, so the list
     isn't the same bet three times.

Tiers: BEST (+5% EV, no flags, top MAX_BEST_PICKS only) · CONSIDER (smaller EV or one flag) · AVOID (model is
probably wrong). Every tier is graded so you can see which ones actually make money.
"""

from collections import defaultdict

from config import (BEST_EV, BEST_MIN_GAMES, BEST_MIN_PRICE, CONSIDER_EV, MAX_BEST_PICKS,
                    MAX_PICKS_PER_PLAYER,
                    MODEL_TRUST, PROP_MARKETS, ROLE_CHANGE_FLAG, TRUST_PRIOR_LINES, payout)

GAME_LABELS = {"h2h": "ML", "spreads": "SPREAD", "totals": "TOTAL"}


def ev(win, price):
    return win * payout(price) - (1 - win)


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


# ======================= props =======================
def classify_prop(p, trust, min_ev=CONSIDER_EV):
    over = p["p_over"] > p["market_over"]
    side = "Over" if over else "Under"
    price = p["best_over_price"] if over else p["best_under_price"]
    book = p["best_over_book"] if over else p["best_under_book"]
    if price is None:
        return None
    model = p["p_over"] if over else 1 - p["p_over"]
    market = p["market_over"] if over else 1 - p["market_over"]
    t = trust_for(trust, p["market"])
    win = t * model + (1 - t) * market
    value = ev(win, price)
    if value < min_ev:
        return None

    label = PROP_MARKETS.get(p["market"], {"label": p["market"]})["label"]
    why = []
    if p["mean"] is not None and p["proj_min"] is not None:
        why.append(f"projects {p['mean']:.1f} {label} in {p['proj_min']:.0f} min (line {p['line']:g})")
    opp_f = p["opp_factor"] or 1.0
    if opp_f >= 1.04:
        why.append(f"{p['opp']} gives up a lot of this")
    elif opp_f <= 0.96:
        why.append(f"{p['opp']} limits this")
    if p["injury_boost"] and abs(p["injury_boost"]) >= 0.1:
        mates = (p["notes"] or "").split("teammates out:")[-1].strip() if "teammates out:" in (p["notes"] or "") else ""
        why.append(f"teammates out (+{p['injury_boost']:.1f})" + (f": {mates}" if mates else ""))

    warn = []
    role = p["role_change"]
    role_against = role is not None and ((not over and role >= ROLE_CHANGE_FLAG)
                                         or (over and role <= -ROLE_CHANGE_FLAG))
    if role is not None and abs(role) >= ROLE_CHANGE_FLAG and not role_against:
        why.append(f"production {'up' if role > 0 else 'down'} {abs(role):.0%} vs last season")
    if role_against:
        warn.append(f"production {role:+.0%} vs last season, opposite to this pick")
    games = p["cur_games"] or 0
    if games < BEST_MIN_GAMES:
        warn.append(f"{games} game{'s' * (games != 1)} this season; leans on last season")
    if "self:" in (p["notes"] or ""):
        warn.append(p["notes"].split(";")[0].replace("self:", "listed").strip())
    if price < BEST_MIN_PRICE:
        warn.append(f"expensive ({price:+d})")
    if over and p["b2b"]:
        warn.append("2nd night of a back-to-back: minutes risk")
    if (p["books"] or 0) < 2:
        warn.append("only one book has this line; market % is shaky")

    edge = model - market
    if role_against or (not over and games < BEST_MIN_GAMES and edge >= 0.07):
        tier = "AVOID"
    elif value >= BEST_EV and not warn:
        tier = "BEST"
    elif value >= BEST_EV and len(warn) == 1 or value < BEST_EV and not warn:
        tier = "CONSIDER"
    else:
        tier = "AVOID"
    return dict(kind="prop", tier=tier, side=side, name=p["player"] or p["player_key"], who=p["player_key"],
                game=p["event_id"], matchup=f"{p['team']} vs {p['opp']}",
                pick=f"{side} {p['line']:g} {label}", market_key=p["market"],
                price=price, book=book, model=model, market=market, win=win, ev=value, edge=edge,
                trust=t, why=why, warn=warn,
                key=(p["game_date"], p["player_key"], p["market"], p["line"]))


# ======================= game lines =======================
def classify_games(rows, trust, min_ev=CONSIDER_EV):
    """Best side per game+market. Returns (entries, {(event, market, side): entry or None})."""
    by = defaultdict(list)
    for r in rows:
        by[(r["event_id"], r["market"])].append(r)
    entries, chosen = [], {}
    for (ev_id, market), sides in by.items():
        r = max(sides, key=lambda s: s["model_prob"] - s["market_prob"])
        for s in sides:
            chosen[(ev_id, market, s["side"])] = None
        t = trust_for(trust, market)
        win = t * r["model_prob"] + (1 - t) * r["market_prob"]
        value = ev(win, r["best_price"])
        if value < min_ev:
            continue
        warn = []
        if (r["early_games"] or 0) < BEST_MIN_GAMES:
            warn.append("early season: ratings still lean on last season")
        if r["best_price"] < BEST_MIN_PRICE:
            warn.append(f"expensive ({r['best_price']:+d})")
        if (r["books"] or 0) < 2:
            warn.append("only one book has this line; market % is shaky")
        tier = ("BEST" if value >= BEST_EV and not warn else
                "CONSIDER" if value >= BEST_EV or not warn else "AVOID")
        team = r["home"] if r["side"] == "home" else r["away"]
        pt = r["point"]
        pick = (f"{team} ML" if market == "h2h" or pt is None and market == "spreads" else
                f"{team} {pt:+g}" if market == "spreads" else
                f"{r['side']} {pt:g}" if pt is not None else f"{r['side']} total")
        why = []
        if r["pred_margin"] is not None and r["pred_total"] is not None:
            fav = r["home"] if r["pred_margin"] >= 0 else r["away"]
            why.append(f"model: {fav} by {abs(r['pred_margin']):.1f}, total {r['pred_total']:.0f}")
        if r["rest_note"]:
            why.append(r["rest_note"])
        e = dict(kind="game", tier=tier, side=r["side"], name=f"{r['away']} @ {r['home']}",
                 who=None, game=ev_id, matchup=GAME_LABELS[market], pick=pick, market_key=market,
                 price=r["best_price"], book=r["best_book"], model=r["model_prob"],
                 market=r["market_prob"], win=win, ev=value, edge=r["model_prob"] - r["market_prob"],
                 trust=t, why=why, warn=warn, key=(r["game_date"], ev_id, market, r["side"]))
        entries.append(e)
        chosen[(ev_id, market, r["side"])] = e
    return entries, chosen


# ======================= the board =======================
def dedupe(entries):
    """Highest-EV first: one pick per player, one ML-or-spread per game."""
    used_player, used_side, keep = defaultdict(int), set(), []
    for e in sorted(entries, key=lambda e: (e["tier"] != "AVOID", e["ev"]), reverse=True):
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
    best = [e for e in keep if e["tier"] == "BEST"]
    for e in best[MAX_BEST_PICKS:]:          # already sorted by EV
        e["tier"] = "CONSIDER"
        e["warn"].append(f"outside today's top {MAX_BEST_PICKS}")
    return keep


def build_board(con, game_date, save=None):
    """Classify every line for game_date and return (entries, prop lines checked, games).
    Today (or later): tiers/EV are saved so they can be graded.
    Past days: the tiers saved at pick time are shown as-is (re-tiering would rewrite
    your record), with each pick's result attached."""
    from datetime import datetime
    from config import ET
    if save is None:
        save = game_date >= datetime.now(ET).strftime("%Y-%m-%d")
    trust = learned_trust(con)
    preds = con.execute("SELECT * FROM predictions WHERE game_date=? AND market_over IS NOT NULL",
                        (game_date,)).fetchall()
    grows = con.execute("SELECT * FROM game_predictions WHERE game_date=?", (game_date,)).fetchall()

    min_ev = CONSIDER_EV if save else -9     # past days: rebuild every saved pick
    prop_entries = []
    for p in preds:
        c = classify_prop(p, trust, min_ev)
        if c:
            prop_entries.append(c)
    game_entries, game_choice = classify_games(grows, trust, min_ev)
    board = dedupe(prop_entries + game_entries) if save else prop_entries + game_entries
    n_games = len({r["event_id"] for r in grows})
    prop_row = {(p["game_date"], p["player_key"], p["market"], p["line"]): p for p in preds}
    game_row = {(s["game_date"], s["event_id"], s["market"], s["side"]): s for s in grows}

    if save:
        by_key = {e["key"]: e for e in prop_entries}
        for k in prop_row:
            e = by_key.get(k)
            con.execute("UPDATE predictions SET tier=?, pick_side=?, p_blend=?, ev=? WHERE game_date=? "
                        "AND player_key=? AND market=? AND line=?",
                        (e["tier"] if e else None, e["side"] if e and e["tier"] else None,
                         round(e["win"], 4) if e else None, round(e["ev"], 4) if e else None, *k))
        for s in grows:
            e = game_choice.get((s["event_id"], s["market"], s["side"]))
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
    return sorted(board, key=lambda e: e["ev"], reverse=True), len(preds), n_games


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
