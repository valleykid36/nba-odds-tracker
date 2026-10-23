"""
game_model.py — moneyline, spread, and total predictions for every game.

Built entirely from the box scores already in nba.db (no extra downloads):
  * Elo ratings (with margin of victory and off-season regression) -> predicted margin
  * Each team's recent points scored and allowed -> predicted total
  * Rest: a team on the 2nd night of a back-to-back loses REST_B2B_POINTS of margin
  * Margin and total turned into probabilities with a normal distribution,
    then compared to the sportsbooks' vig-free prices.
"""

import math
from collections import defaultdict, deque
from datetime import date as _date

from config import (CURRENT_SEASON, ELO_HOME, ELO_K, ELO_PER_POINT, ELO_REVERT,
                    MARGIN_SD, REST_B2B_POINTS, TEAM_WINDOW, TOTAL_SD, implied, team_abbr)


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ---------------- team game results from box scores ----------------
def team_results(con, before_date=None):
    """One row per game: date, season, home, away, home_pts, away_pts (in date order)."""
    q = ("SELECT game_id, game_date, season, team, home, SUM(pts) pts FROM player_games "
         + ("WHERE game_date<? " if before_date else "") + "GROUP BY game_id, team ORDER BY game_date")
    rows = con.execute(q, (before_date,) if before_date else ()).fetchall()
    games = defaultdict(dict)
    for r in rows:
        g = games[r["game_id"]]
        g["date"], g["season"] = r["game_date"], r["season"]
        g["home" if r["home"] else "away"] = (r["team"], r["pts"])
    out = [(g["date"], g["season"], g["home"][0], g["away"][0], g["home"][1], g["away"][1])
           for g in games.values() if "home" in g and "away" in g]
    return sorted(out)


class TeamModel:
    """Replays results in order; can predict any matchup at the current point in time."""

    def __init__(self):
        self.elo = defaultdict(lambda: 1500.0)
        self.season = None
        self.scored = defaultdict(lambda: deque(maxlen=TEAM_WINDOW))
        self.allowed = defaultdict(lambda: deque(maxlen=TEAM_WINDOW))
        self.games_this_season = defaultdict(int)
        self.league = deque(maxlen=400)
        self.last_date = {}

    def b2b(self, team, game_date):
        last = self.last_date.get(team)
        return bool(last and game_date and
                    (_date.fromisoformat(game_date) - _date.fromisoformat(last)).days == 1)

    def new_season(self, season):
        if self.season is not None and season != self.season:
            for t in self.elo:
                self.elo[t] = self.elo[t] * (1 - ELO_REVERT) + 1500 * ELO_REVERT
            self.games_this_season.clear()
        self.season = season

    def predict(self, home, away, game_date=None, rest=True):
        """Predicted home margin and total. With a date, back-to-backs adjust the margin."""
        diff = self.elo[home] + ELO_HOME - self.elo[away]
        margin = diff / ELO_PER_POINT
        if rest and game_date:
            margin += REST_B2B_POINTS * (self.b2b(away, game_date) - self.b2b(home, game_date))
        lg = sum(self.league) / len(self.league) if self.league else 112.0

        def avg(d, team):
            return sum(d[team]) / len(d[team]) if d[team] else lg
        home_pts = (avg(self.scored, home) + avg(self.allowed, away)) / 2
        away_pts = (avg(self.scored, away) + avg(self.allowed, home)) / 2
        return margin, home_pts + away_pts

    def update(self, season, home, away, hp, ap, game_date=None):
        self.new_season(season)
        if game_date:
            self.last_date[home] = self.last_date[away] = game_date
        p = 1 / (1 + 10 ** (-(self.elo[home] + ELO_HOME - self.elo[away]) / 400))
        won = 1 if hp > ap else 0
        diff = (self.elo[home] + ELO_HOME - self.elo[away]) * (1 if won else -1)
        mult = math.log(abs(hp - ap) + 1) * (2.2 / (diff * 0.001 + 2.2))
        shift = ELO_K * mult * (won - p)
        self.elo[home] += shift
        self.elo[away] -= shift
        self.scored[home].append(hp); self.allowed[home].append(ap)
        self.scored[away].append(ap); self.allowed[away].append(hp)
        self.league.extend([hp, ap])
        self.games_this_season[home] += 1
        self.games_this_season[away] += 1


def build_model(con, before_date):
    m = TeamModel()
    for date, season, home, away, hp, ap in team_results(con, before_date):
        m.update(season, home, away, hp, ap, date)
    m.new_season(CURRENT_SEASON)
    return m


# ---------------- live predictions ----------------
def predict_games(con, game_date):
    rows = con.execute(
        "SELECT g.* FROM game_lines g JOIN (SELECT event_id, MAX(snapshot_utc) s FROM game_lines "
        "WHERE game_date=? GROUP BY event_id) last ON g.event_id=last.event_id AND g.snapshot_utc=last.s",
        (game_date,)).fetchall()
    if not rows:
        return []
    model = build_model(con, game_date)
    events = defaultdict(list)
    for r in rows:
        events[r["event_id"]].append(r)

    out = []
    for ev, lines in events.items():
        home, away = team_abbr(lines[0]["home_team"]), team_abbr(lines[0]["away_team"])
        margin, total = model.predict(home, away, game_date)
        tired = [t for t in (away, home) if model.b2b(t, game_date)]
        rest_note = (" and ".join(tired) + " on a back-to-back") if tired else ""
        early = min(model.games_this_season[home], model.games_this_season[away])
        for market in ("h2h", "spreads", "totals"):
            ml = [r for r in lines if r["market"] == market]
            if not ml:
                continue
            # spreads/totals: use the most common number across books
            if market != "h2h":
                key = (lambda r: abs(r["point"])) if market == "spreads" else (lambda r: r["point"])
                counts = defaultdict(int)
                for r in ml:
                    counts[key(r)] += 1
                common = max(counts, key=counts.get)
                ml = [r for r in ml if key(r) == common]
            by_book, best = defaultdict(dict), {}
            for r in ml:
                by_book[r["book"]][r["side"]] = r
                if r["side"] not in best or r["price"] > best[r["side"]]["price"]:
                    best[r["side"]] = r
            sides = ("home", "away") if market != "totals" else ("Over", "Under")
            fair = [implied(b[sides[0]]["price"]) /
                    (implied(b[sides[0]]["price"]) + implied(b[sides[1]]["price"]))
                    for b in by_book.values() if sides[0] in b and sides[1] in b]
            if not fair or sides[0] not in best or sides[1] not in best:
                continue
            mkt0 = sum(fair) / len(fair)
            if market == "h2h":
                p0 = phi(margin / MARGIN_SD)
            elif market == "spreads":
                home_point = best["home"]["point"]
                p0 = phi((margin + home_point) / MARGIN_SD)
            else:
                p0 = phi((total - best["Over"]["point"]) / TOTAL_SD)
            for side, p, mk in ((sides[0], p0, mkt0), (sides[1], 1 - p0, 1 - mkt0)):
                b = best[side]
                rec = {
                    "game_date": game_date, "event_id": ev, "home": home, "away": away,
                    "market": market, "side": side, "point": b["point"],
                    "model_prob": round(p, 4), "market_prob": round(mk, 4),
                    "best_price": b["price"], "best_book": b["book"], "books": len(fair),
                    "pred_margin": round(margin, 1), "pred_total": round(total, 1),
                    "early_games": early, "created_utc": b["snapshot_utc"],
                    "rest_note": rest_note,
                }
                con.execute("INSERT OR REPLACE INTO game_predictions (" + ",".join(rec) +
                            ") VALUES (" + ",".join("?" * len(rec)) + ")", list(rec.values()))
                out.append(rec)
    con.commit()
    return out


def grade_games(con, today):
    pending = con.execute("SELECT * FROM game_predictions WHERE result IS NULL AND game_date<?",
                          (today,)).fetchall()
    results = {(d, h): (hp, ap) for d, _, h, _, hp, ap in team_results(con)}
    n = 0
    for p in pending:
        score = results.get((p["game_date"], p["home"]))
        if not score:
            continue
        hp, ap = score
        margin, total = hp - ap, hp + ap
        if p["market"] == "h2h":
            won = (margin > 0) == (p["side"] == "home")
            res = "W" if won else "L"
        elif p["market"] == "spreads":
            cover = margin + p["point"] if p["side"] == "home" else -margin + p["point"]
            res = "P" if cover == 0 else ("W" if cover > 0 else "L")
        else:
            diff = total - p["point"]
            res = "P" if diff == 0 else ("W" if (diff > 0) == (p["side"] == "Over") else "L")
        con.execute("UPDATE game_predictions SET result=?, final=? WHERE game_date=? AND event_id=? "
                    "AND market=? AND side=?", (res, f"{p['away']} {ap} @ {p['home']} {hp}",
                                                p["game_date"], p["event_id"], p["market"], p["side"]))
        n += 1
    con.commit()
    return n


def backtest_games(con, from_season=CURRENT_SEASON - 2):
    m = TeamModel()
    briers, no_rest, correct, total_err, base_err, n = [], [], 0, 0.0, 0.0, 0
    for date, season, home, away, hp, ap in team_results(con):
        m.new_season(season)
        if season >= from_season and m.games_this_season[home] >= 3 and m.games_this_season[away] >= 3:
            margin, total = m.predict(home, away, date)
            p = phi(margin / MARGIN_SD)
            won = 1 if hp > ap else 0
            briers.append((p - won) ** 2)
            no_rest.append((phi(m.predict(home, away, date, rest=False)[0] / MARGIN_SD) - won) ** 2)
            correct += (p >= 0.5) == bool(won)
            lg = sum(m.league) / len(m.league) * 2
            total_err += abs(total - (hp + ap))
            base_err += abs(lg - (hp + ap))
            n += 1
        m.update(season, home, away, hp, ap, date)
    if not n:
        print("Not enough games to backtest.")
        return
    print(f"\n=== Game model backtest: {n:,} games, seasons {from_season}+ ===")
    print(f"Winner picked correctly: {correct / n:.1%}   Brier: {sum(briers) / n:.4f} (coin flip 0.2500)")
    print(f"Rest/back-to-back data: Brier {sum(briers) / n:.4f} with it vs {sum(no_rest) / n:.4f} "
          f"without ({'helps' if sum(briers) < sum(no_rest) else 'does not help; set REST_B2B_POINTS = 0'})")
    print(f"Total points, avg miss:  {total_err / n:.1f} pts  (league-average guess misses by {base_err / n:.1f})")
