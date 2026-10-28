"""
budget.py — spend Odds API credits only where they're likely to find a good pick.

The free plan gives MONTHLY_CREDITS (500) that reset on the 1st. This module:
  * works out how many credits you can spend today and still last until the 1st
  * scores today's games with FREE data (injury news, back-to-backs) — props are most
    often mispriced right after a regular is ruled out, so those games come first
  * plans which games get props within today's budget, reusing anything pulled recently
"""

import calendar
from datetime import date, datetime

from config import (CREDIT_RESERVE, ET, MIN_MINUTES, MONTHLY_CREDITS, PULL_ALT_MARKETS, PULL_GAME_LINES,
                    PULL_PROP_MARKETS, team_abbr)
from db import get_meta

OUT_STATUSES = {"out", "doubtful", "suspension"}


def credits_left(con):
    """Last known balance. A new month means the plan has reset to MONTHLY_CREDITS."""
    month = datetime.now(ET).strftime("%Y-%m")
    left = get_meta(con, "credits_left")
    if left is None:
        return None
    if get_meta(con, "credits_month") not in (None, month):
        return MONTHLY_CREDITS
    return int(float(left))


def days_until_reset(today=None):
    today = today or datetime.now(ET).date()
    last = calendar.monthrange(today.year, today.month)[1]
    return last - today.day + 1          # counting today


def reset_date(today=None):
    today = today or datetime.now(ET).date()
    return date(today.year + (today.month == 12), today.month % 12 + 1, 1)


def daily_budget(con, today=None):
    """Credits you can spend today and still have some every day until the reset."""
    left = credits_left(con)
    if left is None:
        return None
    spendable = max(0, left - CREDIT_RESERVE)
    days = days_until_reset(today)
    # Weight today double: tonight's slate is known, future slates aren't.
    return min(spendable, round(2 * spendable / (days + 1)))


def team_b2b(con, team, game_date):
    r = con.execute("SELECT MAX(game_date) FROM player_games WHERE team=? AND game_date<?",
                    (team, game_date)).fetchone()
    if not r or not r[0]:
        return False
    return (date.fromisoformat(game_date) - date.fromisoformat(r[0])).days == 1


def out_regulars(con, team, game_date):
    """Regulars (20+ min recently) listed OUT/doubtful today. Free: ESPN + box scores."""
    inj = con.execute(
        "SELECT player_key, player, status FROM injuries WHERE team=? AND snapshot_date="
        "(SELECT MAX(snapshot_date) FROM injuries WHERE snapshot_date<=?)", (team, game_date)).fetchall()
    out = {r["player_key"]: r["player"] for r in inj if (r["status"] or "").lower() in OUT_STATUSES}
    if not out:
        return []
    last5 = [r[0] for r in con.execute(
        "SELECT DISTINCT game_id, game_date FROM player_games WHERE team=? AND game_date<? "
        "ORDER BY game_date DESC LIMIT 5", (team, game_date))]
    if not last5:
        return []
    q = ",".join("?" * len(last5))
    rows = con.execute(
        f"SELECT player_key, AVG(min) m FROM player_games WHERE team=? AND game_id IN ({q}) "
        f"AND min>=? GROUP BY player_key", (team, *last5, MIN_MINUTES)).fetchall()
    return [(out[r["player_key"]], round(r["m"])) for r in rows
            if r["player_key"] in out and r["m"] >= 20]


def score_game(con, event, game_date):
    """Free 'is this game worth credits?' score with plain-English reasons."""
    home, away = team_abbr(event["home_team"]), team_abbr(event["away_team"])
    score, reasons = 1.0, []
    for team in (away, home):
        for name, mins in out_regulars(con, team, game_date):
            score += mins / 10            # a 35-minute starter out = +3.5
            reasons.append(f"{team}: {name} out ({mins} min)")
        if team_b2b(con, team, game_date):
            score += 1
            reasons.append(f"{team} on a back-to-back")
    return score, reasons


def plan(con, events, game_date, budget=None, markets=None):
    """
    events: [(tip, event), ...] from ingest.todays_events().
    Returns dict: budget, lines_cost, games=[{event, tip, score, reasons, cost, chosen}], total.
    Games are chosen in score order until the budget runs out.
    """
    from ingest import fresh_prop_markets, lines_are_fresh
    markets = markets or (PULL_PROP_MARKETS + PULL_ALT_MARKETS)
    budget = daily_budget(con) if budget is None else budget
    lines_cost = 3 if PULL_GAME_LINES and not lines_are_fresh(con, game_date) else 0
    room = (budget if budget is not None else 10 ** 6) - lines_cost
    games = []
    for tip, ev in events:
        score, reasons = score_game(con, ev, game_date)
        cost = len([m for m in markets if m not in fresh_prop_markets(con, ev["id"])])
        games.append(dict(event=ev, tip=tip, score=score, reasons=reasons, cost=cost, chosen=False))
    for g in sorted(games, key=lambda g: g["score"], reverse=True):
        if g["cost"] <= room:
            g["chosen"] = True
            room -= g["cost"]
    total = lines_cost + sum(g["cost"] for g in games if g["chosen"])
    return dict(budget=budget, lines_cost=lines_cost, games=games, total=total)
