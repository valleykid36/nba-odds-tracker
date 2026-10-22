"""
props_model.py — player prop model for every market in config.PROP_MARKETS
(points, rebounds, assists, threes, PRA, and combos).

For each stat:
    expected = projected minutes x per-minute rate x opponent factor + injury boost
  * Per-minute rates and minutes weight THIS season, with past seasons as a prior,
    so trades and bigger roles show up after a few games.
  * Opponent factor: does tonight's opponent allow more or less of this stat than average?
  * Injury boost: when a regular teammate is newly OUT, his production is
    redistributed to the remaining players by their share.
  * Threes use attempts x shooting % (shooting % pulled toward league average).
  * Combos (PRA etc.) add up each stat's projection.
  * P(Over) comes from a negative binomial (handles streaky nights better than Poisson).

backtest() grades every market on past seasons using only information available
before each game.
"""

import math
from collections import defaultdict, deque

from config import (CURRENT_SEASON, INJURY_SHARE, LEAGUE_3P_PCT, MIN_MINUTES,
                    OPP_WEIGHT, OPP_WINDOW, PCT_PRIOR_ATTEMPTS, PRIOR_GAMES_MIN,
                    PRIOR_MINUTES, PROP_MARKETS, implied, team_abbr)

OUT_STATUSES = {"out", "doubtful", "suspension"}
STATS = ["min", "pts", "reb", "ast", "fg3m", "fg3a", "blk", "stl"]


# ---------------- math ----------------
def prob_at_least(k, mean, r):
    """P(X >= k) for a negative binomial with this mean and dispersion r."""
    if mean <= 0:
        return 0.0 if k > 0 else 1.0
    p = r / (r + mean)
    total = 0.0
    for i in range(k):
        total += math.exp(math.lgamma(i + r) - math.lgamma(i + 1) - math.lgamma(r)
                          + r * math.log(p) + i * math.log(1 - p))
    return max(0.0, min(1.0, 1 - total))


def prob_over(line, mean, r):
    return prob_at_least(math.floor(line) + 1, mean, r)


def empty():
    d = {s: 0.0 for s in STATS}
    d["games"] = 0
    return d


# ---------------- core projection ----------------
def project(cur, prior, recent_min, stats, opp_factors=None, boosts=None):
    """
    cur / prior: dicts of season totals (min, pts, reb, ...; games) for this season / past 2 seasons.
    recent_min:  this season's last 5 minute totals.
    Returns {"proj_min", "mean", "parts": {stat: mean}} or None.
    """
    opp_factors = opp_factors or {}
    boosts = boosts or {}
    if cur["games"] + prior["games"] < 5 or (cur["min"] + prior["min"]) <= 0:
        return None

    prior_avg = prior["min"] / prior["games"] if prior["games"] else None
    if recent_min and prior_avg:
        proj_min = (sum(recent_min) + PRIOR_GAMES_MIN * prior_avg) / (len(recent_min) + PRIOR_GAMES_MIN)
    elif recent_min:
        proj_min = sum(recent_min) / len(recent_min)
    else:
        proj_min = prior_avg

    def rate(s):
        if prior["min"] > 0:
            pr = prior[s] / prior["min"]
            return (cur[s] + pr * PRIOR_MINUTES) / (cur["min"] + PRIOR_MINUTES)
        return cur[s] / cur["min"]

    parts = {}
    for s in stats:
        f = opp_factors.get(s, 1.0)
        if s == "fg3m":   # threes: attempts x shrunk shooting %
            att = cur["fg3a"] + prior["fg3a"]
            pct = (cur["fg3m"] + prior["fg3m"] + LEAGUE_3P_PCT * PCT_PRIOR_ATTEMPTS) / (att + PCT_PRIOR_ATTEMPTS)
            exp_3pa = rate("fg3a") * proj_min * opp_factors.get("fg3a", 1.0) + boosts.get("fg3a", 0.0)
            parts[s] = exp_3pa * pct
        else:
            parts[s] = rate(s) * proj_min * f + boosts.get(s, 0.0)
    return {"proj_min": proj_min, "mean": sum(parts.values()), "parts": parts}


def factor(allowed, league_avg):
    if not allowed or not league_avg:
        return 1.0
    return 1 + OPP_WEIGHT * ((sum(allowed) / len(allowed)) / league_avg - 1)


def needed_stats(stats):
    return sorted(set(stats) | ({"fg3a"} if "fg3m" in stats else set()))


# ---------------- live inputs from the database ----------------
def player_inputs(con, key, season, before_date):
    rows = con.execute(
        "SELECT season, team, " + ",".join(STATS) + " FROM player_games WHERE player_key=? "
        "AND game_date<? AND season>=? AND min>=? ORDER BY game_date",
        (key, before_date, season - 2, MIN_MINUTES)).fetchall()
    cur, prior, recent = empty(), empty(), []
    for r in rows:
        d = cur if r["season"] == season else prior
        for s in STATS:
            d[s] += r[s]
        d["games"] += 1
        if r["season"] == season:
            recent.append(r["min"])
    return cur, prior, recent[-5:], (rows[-1]["team"] if rows else None)


def opp_factors(con, opp, before_date, cache):
    """Per-stat factor: what opponents produce against `opp` vs. league average."""
    if opp in cache:
        return cache[opp]
    cols = [s for s in STATS if s != "min"]
    team_games = con.execute(
        "SELECT game_id, game_date, team, " + ",".join(f"SUM({s}) {s}" for s in cols) +
        " FROM player_games WHERE game_date<? AND game_date>=date(?, '-120 days') "
        "GROUP BY game_id, team", (before_date, before_date)).fetchall()
    league = {s: [r[s] for r in team_games] for s in cols}
    league_avg = {s: (sum(v) / len(v) if v else None) for s, v in league.items()}
    by_game = defaultdict(dict)
    for r in team_games:
        by_game[r["game_id"]][r["team"]] = r
    opp_games = sorted([g for g, teams in by_game.items() if opp in teams],
                       key=lambda g: by_game[g][opp]["game_date"], reverse=True)[:OPP_WINDOW]
    out = {}
    for s in cols:
        allowed = [r[s] for g in opp_games for t, r in by_game[g].items() if t != opp]
        out[s] = factor(allowed, league_avg[s])
    cache[opp] = out
    return out


def injury_boosts(con, key, team, game_date, my_parts, stats, cache):
    """Per-stat boost from teammates who played recently but are OUT tonight."""
    if team not in cache:
        inj = con.execute(
            "SELECT player_key, status FROM injuries WHERE team=? AND snapshot_date="
            "(SELECT MAX(snapshot_date) FROM injuries WHERE snapshot_date<=?)",
            (team, game_date)).fetchall()
        out = {r["player_key"] for r in inj if r["status"].lower() in OUT_STATUSES}
        last3 = [r[0] for r in con.execute(
            "SELECT DISTINCT game_id, game_date FROM player_games WHERE team=? AND game_date<? "
            "ORDER BY game_date DESC LIMIT 3", (team, game_date))]
        played = []
        if last3 and out:
            q = ",".join("?" * len(last3))
            played = con.execute(
                f"SELECT player_key, player_name, COUNT(*) n, " +
                ",".join(f"SUM({s}) {s}" for s in STATS if s != "min") +
                f" FROM player_games WHERE team=? AND game_id IN ({q}) AND min>=? GROUP BY player_key",
                (team, *last3, MIN_MINUTES)).fetchall()
        cache[team] = (out, played)
    out, played = cache[team]
    if not out or not played:
        return {}, []

    boosts, names = {}, set()
    for s in needed_stats(stats):
        vacated = active = 0.0
        for r in played:
            per_game = r[s] / r["n"]
            if r["player_key"] in out:
                vacated += per_game
                if per_game > 0:
                    names.add(r["player_name"])
            elif r["player_key"] != key:
                active += per_game
        mine = my_parts.get(s, 0.0)
        if vacated and (active + mine) > 0:
            boosts[s] = INJURY_SHARE * vacated * mine / (active + mine)
    return boosts, sorted(names)


def own_status(con, key, game_date):
    r = con.execute(
        "SELECT status FROM injuries WHERE player_key=? AND snapshot_date="
        "(SELECT MAX(snapshot_date) FROM injuries WHERE snapshot_date<=?)", (key, game_date)).fetchone()
    return r["status"] if r else ""


def market_summary(prices):
    by_book, best = defaultdict(dict), {"Over": None, "Under": None}
    for book, side, price in prices:
        by_book[book][side] = price
        if best[side] is None or price > best[side][0]:
            best[side] = (price, book)
    fair = []
    for s in by_book.values():
        if "Over" in s and "Under" in s:
            po, pu = implied(s["Over"]), implied(s["Under"])
            fair.append(po / (po + pu))
    return (sum(fair) / len(fair) if fair else None), len(fair), best


# ---------------- live predictions ----------------
def predict_props(con, game_date, season=CURRENT_SEASON):
    """Predict every prop line logged for game_date (latest snapshot per game+market)."""
    rows = con.execute(
        "SELECT p.* FROM prop_snapshots p JOIN ("
        "  SELECT event_id, market, MAX(snapshot_utc) s FROM prop_snapshots "
        "  WHERE game_date=? GROUP BY event_id, market) last "
        "ON p.event_id=last.event_id AND p.market=last.market AND p.snapshot_utc=last.s",
        (game_date,)).fetchall()
    grouped, meta = defaultdict(list), {}
    for r in rows:
        if r["market"] not in PROP_MARKETS:
            continue
        k = (r["player_key"], r["market"], r["line"])
        grouped[k].append((r["book"], r["side"], r["price"]))
        meta[k] = r

    players, opp_cache, inj_cache, out = {}, {}, {}, []
    for (key, market, line), prices in grouped.items():
        m, cfg = meta[(key, market, line)], PROP_MARKETS[market]
        if key not in players:
            players[key] = player_inputs(con, key, season, game_date)
        cur, prior, recent, team = players[key]
        home, away = team_abbr(m["home_team"]), team_abbr(m["away_team"])
        if team not in (home, away):
            continue
        opp = away if team == home else home
        status = own_status(con, key, game_date)
        if status.lower() in OUT_STATUSES:
            continue

        stats = cfg["stats"]
        of = opp_factors(con, opp, game_date, opp_cache)
        base = project(cur, prior, recent, stats, of)
        if not base:
            continue
        my_parts = dict(base["parts"])
        if "fg3m" in stats:   # injury share for threes is based on attempts
            my_parts["fg3a"] = base["parts"]["fg3m"] / max(LEAGUE_3P_PCT, 0.01)
        boosts, missing = injury_boosts(con, key, team, game_date, my_parts, stats, inj_cache)
        proj = project(cur, prior, recent, stats, of, boosts)

        p_over = prob_over(line, proj["mean"], cfg["r"])
        mkt_over, n_books, best = market_summary(prices)

        # Role change: per-game production this season vs. past seasons (per-game, so it
        # catches both more minutes and a bigger share of the offense).
        role = None
        if cur["games"] >= 3 and prior["games"] >= 10:   # need a few games before trusting it
            src = ["fg3a"] if stats == ["fg3m"] else stats
            c = sum(cur[s] for s in src) / cur["games"]
            p = sum(prior[s] for s in src) / prior["games"]
            role = c / p - 1 if p > 0 else None

        notes = []
        if status:
            notes.append(f"self: {status}")
        if missing:
            notes.append("teammates out: " + ", ".join(missing))
        boost_total = proj["mean"] - base["mean"]   # how much injuries added to the projection
        opp_avg = sum(of.get("fg3a" if s == "fg3m" else s, 1.0) for s in stats) / len(stats)
        rec = {
            "game_date": game_date, "event_id": m["event_id"], "player": m["player"],
            "player_key": key, "team": team, "opp": opp, "market": market, "line": line,
            "proj_min": round(proj["proj_min"], 1), "exp_3pa": None, "pct": None,
            "mean": round(proj["mean"], 2), "opp_factor": round(opp_avg, 3),
            "injury_boost": round(boost_total, 2), "p_over": round(p_over, 4),
            "market_over": round(mkt_over, 4) if mkt_over else None, "books": n_books,
            "best_over_price": best["Over"][0] if best["Over"] else None,
            "best_over_book": best["Over"][1] if best["Over"] else None,
            "best_under_price": best["Under"][0] if best["Under"] else None,
            "best_under_book": best["Under"][1] if best["Under"] else None,
            "notes": "; ".join(notes), "created_utc": m["snapshot_utc"],
            "cur_games": cur["games"], "role_change": round(role, 3) if role is not None else None,
        }
        con.execute("INSERT OR REPLACE INTO predictions (" + ",".join(rec) + ") VALUES (" +
                    ",".join("?" * len(rec)) + ")", list(rec.values()))
        out.append(rec)
    con.commit()
    return out


# ---------------- backtest ----------------
BACKTEST_MARKETS = ["player_points", "player_rebounds", "player_assists",
                    "player_threes", "player_points_rebounds_assists"]


def backtest(con, from_season=CURRENT_SEASON - 2):
    """Replay history day by day; predict each player-game from earlier games only."""
    rows = con.execute("SELECT game_id, game_date, season, player_key, team, opp, " +
                       ",".join(STATS) + " FROM player_games ORDER BY game_date, game_id").fetchall()
    if not rows:
        print("No player data yet. Run: python daily.py")
        return
    cols = [s for s in STATS if s != "min"]
    totals = defaultdict(empty)                          # (player, season)
    recent = defaultdict(lambda: deque(maxlen=5))        # (player, season) minutes
    last20 = defaultdict(lambda: deque(maxlen=20))       # player -> dict of stats
    allowed = defaultdict(lambda: {s: deque(maxlen=OPP_WINDOW) for s in cols})
    league = {s: deque(maxlen=600) for s in cols}
    graded = defaultdict(list)                           # market -> (p_v2, p_v1, hit)

    i = 0
    while i < len(rows):
        date, day = rows[i]["game_date"], []
        while i < len(rows) and rows[i]["game_date"] == date:
            day.append(rows[i])
            i += 1
        lavg = {s: (sum(league[s]) / len(league[s]) if league[s] else None) for s in cols}

        for r in day:
            if r["season"] < from_season or r["min"] < MIN_MINUTES or len(last20[r["player_key"]]) < 8:
                continue
            p, s = r["player_key"], r["season"]
            prior = empty()
            for ps in (s - 1, s - 2):
                for k in prior:
                    prior[k] += totals[(p, ps)][k]
            of = {st: factor(list(allowed[r["opp"]][st]), lavg[st]) for st in cols}
            for market in BACKTEST_MARKETS:
                cfg = PROP_MARKETS[market]
                proj = project(totals[(p, s)], prior, list(recent[(p, s)]), cfg["stats"], of)
                if not proj:
                    continue
                line = math.floor(proj["mean"]) + 0.5
                actual = sum(r[st] for st in cfg["stats"])
                v1 = sum(sum(g[st] for st in cfg["stats"]) for g in last20[p]) / len(last20[p])
                p_v1 = 1 - sum(math.exp(-v1) * v1 ** k / math.factorial(k)
                               for k in range(math.floor(line) + 1)) if v1 < 60 else 0.5
                graded[market].append((prob_over(line, proj["mean"], cfg["r"]), p_v1,
                                       1 if actual > line else 0))

        day_team, opp_of = defaultdict(lambda: defaultdict(float)), {}
        for r in day:
            for st in cols:
                day_team[(r["game_id"], r["team"])][st] += r[st]
            opp_of[(r["game_id"], r["team"])] = r["opp"]
            if r["min"] >= MIN_MINUTES:
                t = totals[(r["player_key"], r["season"])]
                for st in STATS:
                    t[st] += r[st]
                t["games"] += 1
                recent[(r["player_key"], r["season"])].append(r["min"])
                last20[r["player_key"]].append({st: r[st] for st in cols})
        for k, vals in day_team.items():
            for st in cols:
                allowed[opp_of[k]][st].append(vals[st])
                league[st].append(vals[st])

    print(f"=== Props backtest, seasons {from_season}+ (lower Brier = better) ===")
    print(f"{'MARKET':<8}{'GAMES':>9}{'MODEL':>9}{'OLD':>9}{'BASE':>9}{'CALIB ERR':>11}")
    for market in BACKTEST_MARKETS:
        g = graded[market]
        if not g:
            continue
        n = len(g)
        b2 = sum((a - y) ** 2 for a, _, y in g) / n
        b1 = sum((b - y) ** 2 for _, b, y in g) / n
        base = sum(y for *_, y in g) / n
        b0 = sum((base - y) ** 2 for *_, y in g) / n
        buckets = defaultdict(list)
        for a, _, y in g:
            buckets[min(int(a * 10), 9)].append((a, y))
        calib = sum(abs(sum(a for a, _ in v) / len(v) - sum(y for _, y in v) / len(v)) * len(v)
                    for v in buckets.values()) / n
        print(f"{PROP_MARKETS[market]['label']:<8}{n:>9,}{b2:>9.4f}{b1:>9.4f}{b0:>9.4f}{calib:>10.1%}")
    print("\nMODEL = this model, OLD = last-20-games average, BASE = always guess the average.")
    print("CALIB ERR = how far its % are from reality on average (under ~2% is good).")
