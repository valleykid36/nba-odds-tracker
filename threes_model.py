"""
threes_model.py — Model v2 for player threes made.

    expected 3PA  = projected minutes x 3PA per minute x opponent factor + injury boost
    expected 3PM  = expected 3PA x shooting %
    P(Over line)  = from a negative binomial distribution around expected 3PM

What each piece handles:
  * Projected minutes & 3PA rate weight THIS season, falling back on last
    season early on, so new roles (trades, bigger usage) show up fast.
  * Shooting % is pulled toward league average until there are enough attempts.
  * Opponent factor: does tonight's opponent allow more or fewer threes than average?
  * Injury boost: when a regular teammate is newly OUT, his three-point attempts
    get redistributed to the remaining players by their share.
  * Negative binomial instead of Poisson: shooting nights are streakier than
    Poisson assumes (more 0-for nights AND more 7-make nights).

All settings live in config.py. The backtest() function grades the model on
past seasons so you can tell whether a settings change actually helps.
"""

import math
from collections import defaultdict, deque

from config import (CURRENT_SEASON, INJURY_SHARE, LEAGUE_3P_PCT, MIN_MINUTES,
                    NB_DISPERSION, OPP_WEIGHT, OPP_WINDOW, PCT_PRIOR_ATTEMPTS,
                    PRIOR_GAMES_MIN, PRIOR_MINUTES, implied, team_abbr)

OUT_STATUSES = {"out", "doubtful", "suspension"}


# ---------------- math ----------------
def prob_at_least(k, mean, r=NB_DISPERSION):
    """P(makes >= k) under a negative binomial with this mean and dispersion r."""
    if mean <= 0:
        return 0.0 if k > 0 else 1.0
    p = r / (r + mean)
    total = 0.0
    for i in range(k):
        log_pmf = (math.lgamma(i + r) - math.lgamma(i + 1) - math.lgamma(r)
                   + r * math.log(p) + i * math.log(1 - p))
        total += math.exp(log_pmf)
    return max(0.0, min(1.0, 1 - total))


def prob_over(line, mean):
    return prob_at_least(math.floor(line) + 1, mean)


# ---------------- core projection (shared by live predictions and backtest) ----------------
def project(cur, prior, opp_factor=1.0, injury_boost=0.0):
    """
    cur:   dict(min, fg3a, fg3m, games, recent_min=[last 5 game minutes]) for this season
    prior: dict(min, fg3a, fg3m, games) for the previous 1-2 seasons
    Returns dict or None if not enough data.
    """
    if cur["games"] + prior["games"] < 5:
        return None

    # 3PA per minute: this season's rate, with last season as a prior
    if prior["min"] > 0:
        prior_rate = prior["fg3a"] / prior["min"]
        rate = (cur["fg3a"] + prior_rate * PRIOR_MINUTES) / (cur["min"] + PRIOR_MINUTES)
    elif cur["min"] > 0:
        rate = cur["fg3a"] / cur["min"]
    else:
        return None

    # Minutes: recent games this season, with last season's average as a prior
    recent = cur["recent_min"]
    prior_avg = prior["min"] / prior["games"] if prior["games"] else None
    if recent and prior_avg:
        n = len(recent)
        proj_min = (sum(recent) + PRIOR_GAMES_MIN * prior_avg) / (n + PRIOR_GAMES_MIN)
    elif recent:
        proj_min = sum(recent) / len(recent)
    else:
        proj_min = prior_avg

    # 3P%: all attempts this season + prior, pulled toward league average
    att = cur["fg3a"] + prior["fg3a"]
    made = cur["fg3m"] + prior["fg3m"]
    pct = (made + LEAGUE_3P_PCT * PCT_PRIOR_ATTEMPTS) / (att + PCT_PRIOR_ATTEMPTS)

    exp_3pa = rate * proj_min * opp_factor + injury_boost
    return {"proj_min": proj_min, "exp_3pa": exp_3pa, "pct": pct, "mean": exp_3pa * pct,
            "rate": rate}


def opponent_factor(allowed_recent, league_avg):
    if not allowed_recent or not league_avg:
        return 1.0
    ratio = (sum(allowed_recent) / len(allowed_recent)) / league_avg
    return 1 + OPP_WEIGHT * (ratio - 1)


# ---------------- live: build inputs from the database ----------------
def player_inputs(con, key, season, before_date):
    rows = con.execute(
        "SELECT season, team, min, fg3a, fg3m, game_date FROM player_games "
        "WHERE player_key=? AND game_date<? AND season>=? AND min>=? ORDER BY game_date",
        (key, before_date, season - 2, MIN_MINUTES)).fetchall()
    cur = {"min": 0, "fg3a": 0, "fg3m": 0, "games": 0, "recent_min": []}
    prior = {"min": 0, "fg3a": 0, "fg3m": 0, "games": 0}
    for r in rows:
        d = cur if r["season"] == season else prior
        d["min"] += r["min"]
        d["fg3a"] += r["fg3a"]
        d["fg3m"] += r["fg3m"]
        d["games"] += 1
        if r["season"] == season:
            cur["recent_min"].append(r["min"])
    cur["recent_min"] = cur["recent_min"][-5:]
    team = rows[-1]["team"] if rows else None
    return cur, prior, team


def opp_inputs(con, opp, before_date):
    """Threes ATTEMPTED by opponents against `opp` in its last OPP_WINDOW games, and league avg."""
    team_games = con.execute(
        "SELECT game_id, team, SUM(fg3a) a FROM player_games WHERE game_date<? "
        "AND game_date>=date(?, '-120 days') GROUP BY game_id, team", (before_date, before_date)).fetchall()
    by_game = defaultdict(dict)
    for r in team_games:
        by_game[r["game_id"]][r["team"]] = r["a"]
    league = [a for g in by_game.values() for a in g.values()]
    league_avg = sum(league) / len(league) if league else None

    opp_games = con.execute(
        "SELECT DISTINCT game_id, game_date FROM player_games WHERE team=? AND game_date<? "
        "ORDER BY game_date DESC LIMIT ?", (opp, before_date, OPP_WINDOW)).fetchall()
    allowed = []
    for g in opp_games:
        others = [a for t, a in by_game.get(g["game_id"], {}).items() if t != opp]
        if others:
            allowed.append(others[0])
    return allowed, league_avg


def injury_boost(con, key, team, season, game_date, my_exp_3pa):
    """Extra 3PA for this player from teammates who are newly OUT tonight."""
    inj = con.execute(
        "SELECT player_key, status FROM injuries WHERE team=? AND snapshot_date="
        "(SELECT MAX(snapshot_date) FROM injuries WHERE snapshot_date<=?)",
        (team, game_date)).fetchall()
    out = {r["player_key"] for r in inj if r["status"].lower() in OUT_STATUSES}
    if not out:
        return 0.0, []

    last3 = [r["game_id"] for r in con.execute(
        "SELECT DISTINCT game_id, game_date FROM player_games WHERE team=? AND game_date<? "
        "ORDER BY game_date DESC LIMIT 3", (team, game_date))]
    if not last3:
        return 0.0, []
    q = ",".join("?" * len(last3))
    played = con.execute(
        f"SELECT player_key, player_name, SUM(fg3a) a, COUNT(*) n FROM player_games "
        f"WHERE team=? AND game_id IN ({q}) AND min>=? GROUP BY player_key",
        (team, *last3, MIN_MINUTES)).fetchall()

    # Newly out = played regular minutes in the last 3 games but OUT tonight.
    vacated, names, active_total = 0.0, [], 0.0
    for r in played:
        per_game = r["a"] / r["n"]
        if r["player_key"] in out:
            vacated += per_game
            names.append(r["player_name"])
        elif r["player_key"] != key:
            active_total += per_game
    if not vacated:
        return 0.0, []
    share = my_exp_3pa / (active_total + my_exp_3pa) if (active_total + my_exp_3pa) else 0
    return INJURY_SHARE * vacated * share, names


def own_status(con, key, game_date):
    r = con.execute(
        "SELECT status FROM injuries WHERE player_key=? AND snapshot_date="
        "(SELECT MAX(snapshot_date) FROM injuries WHERE snapshot_date<=?)", (key, game_date)).fetchone()
    return r["status"] if r else ""


def market_summary(prices):
    """prices: list of (book, side, price) for one player+line -> fair Over prob and best prices."""
    by_book = defaultdict(dict)
    best = {"Over": None, "Under": None}
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


def predict_props(con, game_date, market="player_threes", season=CURRENT_SEASON):
    """Predict every prop line logged for game_date (latest snapshot per game). Saves to predictions."""
    rows = con.execute(
        "SELECT p.* FROM prop_snapshots p JOIN ("
        "  SELECT event_id, MAX(snapshot_utc) s FROM prop_snapshots "
        "  WHERE game_date=? AND market=? GROUP BY event_id) last "
        "ON p.event_id=last.event_id AND p.snapshot_utc=last.s WHERE p.market=?",
        (game_date, market, market)).fetchall()

    grouped = defaultdict(list)
    meta = {}
    for r in rows:
        k = (r["player_key"], r["line"])
        grouped[k].append((r["book"], r["side"], r["price"]))
        meta[k] = r

    opp_cache, out = {}, []
    for (key, line), prices in grouped.items():
        m = meta[(key, line)]
        cur, prior, team = player_inputs(con, key, season, game_date)
        home, away = team_abbr(m["home_team"]), team_abbr(m["away_team"])
        notes = []
        if team not in (home, away):
            notes.append(f"team unknown (last seen {team or 'never'})")
            continue
        opp = away if team == home else home
        status = own_status(con, key, game_date)
        if status.lower() in OUT_STATUSES:
            continue
        if status:
            notes.append(f"self: {status}")
        if cur["games"] < 3:
            notes.append(f"only {cur['games']} games this season")

        if opp not in opp_cache:
            opp_cache[opp] = opponent_factor(*opp_inputs(con, opp, game_date))
        of = opp_cache[opp]

        base = project(cur, prior, of)
        if not base:
            continue
        boost, missing = injury_boost(con, key, team, season, game_date, base["exp_3pa"])
        if missing:
            notes.append("teammates out: " + ", ".join(missing))
        proj = project(cur, prior, of, boost)

        p_over = prob_over(line, proj["mean"])
        mkt_over, n_books, best = market_summary(prices)
        rec = {
            "game_date": game_date, "event_id": m["event_id"], "player": m["player"],
            "player_key": key, "team": team, "opp": opp, "market": market, "line": line,
            "proj_min": round(proj["proj_min"], 1), "exp_3pa": round(proj["exp_3pa"], 2),
            "pct": round(proj["pct"], 3), "mean": round(proj["mean"], 2),
            "opp_factor": round(of, 3), "injury_boost": round(boost, 2),
            "p_over": round(p_over, 4), "market_over": round(mkt_over, 4) if mkt_over else None,
            "books": n_books,
            "best_over_price": best["Over"][0] if best["Over"] else None,
            "best_over_book": best["Over"][1] if best["Over"] else None,
            "best_under_price": best["Under"][0] if best["Under"] else None,
            "best_under_book": best["Under"][1] if best["Under"] else None,
            "notes": "; ".join(notes), "created_utc": m["snapshot_utc"],
        }
        con.execute(
            "INSERT OR REPLACE INTO predictions (" + ",".join(rec) + ") VALUES (" +
            ",".join("?" * len(rec)) + ")", list(rec.values()))
        out.append(rec)
    con.commit()
    return out


# ---------------- backtest ----------------
def backtest(con, from_season=CURRENT_SEASON - 2):
    """
    Replays history day by day. For every player-game from `from_season` on, it predicts
    using ONLY earlier games, then checks the real result. Compares v2 to the old
    "last 20 games, Poisson" model on the line closest to each player's projection.
    (No injury data exists for past games, so the backtest runs without injury boosts.)
    """
    rows = con.execute(
        "SELECT game_id, game_date, season, player_key, team, opp, min, fg3a, fg3m "
        "FROM player_games ORDER BY game_date, game_id").fetchall()
    if not rows:
        print("No player data. Run: python daily.py history")
        return

    totals = defaultdict(lambda: {"min": 0, "fg3a": 0, "fg3m": 0, "games": 0})  # (player, season)
    recent_min = defaultdict(lambda: deque(maxlen=5))                            # (player, season)
    last20 = defaultdict(lambda: deque(maxlen=20))                               # player -> makes

    allowed = defaultdict(lambda: deque(maxlen=OPP_WINDOW))                      # team -> opp 3PA
    league = deque(maxlen=600)

    graded = []  # (p_v2, p_v1, hit)
    i = 0
    while i < len(rows):
        date = rows[i]["game_date"]
        day = []
        while i < len(rows) and rows[i]["game_date"] == date:
            day.append(rows[i])
            i += 1
        league_avg = sum(league) / len(league) if league else None

        # 1) predict with yesterday's knowledge
        for r in day:
            if r["season"] < from_season or r["min"] < MIN_MINUTES:
                continue
            p, s = r["player_key"], r["season"]
            cur = dict(totals[(p, s)])
            cur["recent_min"] = list(recent_min[(p, s)])
            prior = {"min": 0, "fg3a": 0, "fg3m": 0, "games": 0}
            for ps in (s - 1, s - 2):
                for k in prior:
                    prior[k] += totals[(p, ps)][k]
            of = opponent_factor(list(allowed[r["opp"]]), league_avg)
            proj = project(cur, prior, of)
            if not proj or len(last20[p]) < 8:
                continue
            line = math.floor(proj["mean"]) + 0.5          # a typical book line for this player
            v1_mean = sum(last20[p]) / len(last20[p])
            p_v1 = 1 - sum(math.exp(-v1_mean) * v1_mean ** k / math.factorial(k)
                           for k in range(math.floor(line) + 1))
            graded.append((prob_over(line, proj["mean"]), p_v1, 1 if r["fg3m"] > line else 0))

        # 2) learn from today's results
        day_team, opp_of = defaultdict(int), {}
        for r in day:
            day_team[(r["game_id"], r["team"])] += r["fg3a"]
            opp_of[(r["game_id"], r["team"])] = r["opp"]
            if r["min"] >= MIN_MINUTES:
                t = totals[(r["player_key"], r["season"])]
                t["min"] += r["min"]; t["fg3a"] += r["fg3a"]; t["fg3m"] += r["fg3m"]; t["games"] += 1
                recent_min[(r["player_key"], r["season"])].append(r["min"])
                last20[r["player_key"]].append(r["fg3m"])
        for k, att in day_team.items():
            allowed[opp_of[k]].append(att)   # the opponent ALLOWED these attempts
            league.append(att)

    if not graded:
        print("Not enough history to backtest.")
        return
    n = len(graded)
    b2 = sum((p - y) ** 2 for p, _, y in graded) / n
    b1 = sum((p - y) ** 2 for _, p, y in graded) / n
    base = sum(y for *_, y in graded) / n
    b0 = sum((base - y) ** 2 for *_, y in graded) / n
    print(f"=== Threes backtest: {n:,} player-games, seasons {from_season}+ ===")
    print(f"Brier v2 (minutes x rate x %, opponent, neg. binomial): {b2:.4f}")
    print(f"Brier v1 (last-20 makes, Poisson):                     {b1:.4f}")
    print(f"Brier 'always {base:.0%}':                                {b0:.4f}")
    print("Lower is better.\n\nCalibration of v2 (predicted Over vs. actual Over rate):")
    buckets = defaultdict(list)
    for p, _, y in graded:
        buckets[min(int(p * 10), 9)].append((p, y))
    for b in sorted(buckets):
        it = buckets[b]
        print(f"   predicted {sum(p for p, _ in it) / len(it):5.1%} -> actual "
              f"{sum(y for _, y in it) / len(it):5.1%}   ({len(it):,})")
