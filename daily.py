"""
daily.py — run it whenever you want. It catches up on everything you missed.

    python daily.py

Every time, for free (no Odds API credits):
  1. First run only: downloads past seasons and imports your old CSV files.
  2. Refreshes box scores for every game played since you last ran it.
  3. Grades every prediction (props AND game lines) from the days you missed.
  4. Shows what you missed: the biggest scoring and three-point nights.
  5. Refreshes today's injury report.
  6. Lists today's games and asks if you're betting today.
     - No  -> done. 0 credits spent.
     - Yes -> you pick the games, it shows the credit cost, then pulls game lines
              + player props and prints the picks board.

Choose which prop types to pull (and save credits) in config.py -> PULL_PROP_MARKETS.

What CAN'T be caught up: odds and injuries for days you skipped (the free Odds API
plan has no history). Box scores and grading always catch up fully.

Other commands (optional):
    python daily.py picks [YYYY-MM-DD]    show the picks board again (free)
    python daily.py backtest              test the models on past seasons (free)
    python daily.py status                what's in the database
"""

import sys
from datetime import datetime

from config import (BEST_EDGE, BEST_MIN_GAMES, BEST_MIN_PRICE, CONSIDER_EDGE, CURRENT_SEASON,
                    ET, PROP_MARKETS, PULL_GAME_LINES, PULL_PROP_MARKETS, ROLE_CHANGE_FLAG,
                    SEASONS, payout)
from db import connect, get_meta, set_meta


def today():
    return datetime.now(ET).strftime("%Y-%m-%d")


def ask(prompt):
    try:
        return input(prompt).strip().lower()
    except EOFError:  # e.g. run by Task Scheduler with no keyboard
        return ""


# ======================= the main routine =======================
def run():
    con = connect()
    last_run = get_meta(con, "last_run")
    if last_run:
        days = (datetime.fromisoformat(today()) - datetime.fromisoformat(last_run)).days
        print(f"Welcome back. Last run: {last_run} ({days} day{'s' * (days != 1)} ago).\n")
    else:
        print("First run: setting everything up.\n")

    # 1) First-time setup
    have = {r[0] for r in con.execute("SELECT DISTINCT season FROM player_games")}
    missing = [s for s in SEASONS if int(s[:4]) not in have and int(s[:4]) != CURRENT_SEASON]
    if not get_meta(con, "csv_imported"):
        from ingest import import_csvs
        print("Importing your existing CSV files...")
        import_csvs()
        set_meta(con, "csv_imported", today())
    if missing:
        print("Downloading past seasons (one time, a few minutes):", ", ".join(missing))

    # 2) Box scores (catches every missed game)
    from ingest import todays_events, update_injuries, update_player_games
    count = "SELECT COUNT(DISTINCT game_id) FROM player_games WHERE season=?"
    before = con.execute(count, (CURRENT_SEASON,)).fetchone()[0]
    print("Updating box scores...")
    update_player_games(missing + SEASONS[-1:])
    after = con.execute(count, (CURRENT_SEASON,)).fetchone()[0]
    print(f"   {after - before} new games added this season ({after} total)\n")

    # 3) Grade everything pending, 4) what you missed
    grade(con)
    if last_run:
        missed(con, last_run)

    # 5) Injuries (free)
    print("\nUpdating injury report...")
    try:
        update_injuries()
    except Exception as e:
        print(f"   couldn't reach ESPN ({e}); continuing without today's injuries")
    set_meta(con, "last_run", today())

    # 6) Betting today?
    events = todays_events()
    if not events:
        print("\nNo more games today. You're all caught up.")
        return
    print(f"\nToday's games ({len(events)}):")
    for i, (tip, ev) in enumerate(events, 1):
        print(f"  {i}. {tip:%I:%M %p}  {ev['away_team']} @ {ev['home_team']}")
    if ask("\nBetting today? (y/N): ") not in ("y", "yes"):
        print("No credits spent. You're all caught up.")
        return

    labels = ", ".join(PROP_MARKETS[m]["label"] for m in PULL_PROP_MARKETS)
    pick = ask(f"Which games for player props ({labels})? e.g. 1,3 or 'all' or 'none': ")
    chosen = events if pick == "all" else [] if pick in ("none", "") else [
        e for i, e in enumerate(events, 1) if str(i) in pick.replace(" ", "").split(",")]
    cost = (3 if PULL_GAME_LINES else 0) + len(chosen) * len(PULL_PROP_MARKETS)
    left = get_meta(con, "credits_left", "unknown")
    print(f"\nThis will use up to {cost} credits "
          f"({'3 for game lines on all games + ' if PULL_GAME_LINES else ''}"
          f"{len(PULL_PROP_MARKETS)} per game for props). Credits left: {left}")
    if cost == 0 or ask("Go? (Y/n): ") in ("n", "no"):
        print("Cancelled. No credits spent.")
        return

    from game_model import predict_games
    from ingest import update_odds, update_props
    from props_model import predict_props
    if PULL_GAME_LINES:
        print("\nGame lines (moneyline, spread, total)...")
        update_odds()
    for tip, ev in chosen:
        print(f"Player props: {ev['away_team']} @ {ev['home_team']}")
        update_props(ev, PULL_PROP_MARKETS)
    predict_games(con, today())
    predict_props(con, today())
    show_board(con, today())
    con.close()


# ======================= grading =======================
def grade(con):
    from game_model import grade_games
    pending = con.execute("SELECT * FROM predictions WHERE actual IS NULL AND game_date<?",
                          (today(),)).fetchall()
    done, days = 0, set()
    for p in pending:
        key = (p["game_date"], p["player_key"], p["market"], p["line"])
        stats = PROP_MARKETS.get(p["market"], {"stats": ["fg3m"]})["stats"]
        row = con.execute(f"SELECT {'+'.join(stats)} v FROM player_games WHERE player_key=? "
                          "AND game_date=?", (p["player_key"], p["game_date"])).fetchone()
        if row:
            result = "Over" if row["v"] > p["line"] else "Under"
            con.execute("UPDATE predictions SET actual=?, result=? WHERE game_date=? AND "
                        "player_key=? AND market=? AND line=?", (row["v"], result, *key))
        elif con.execute("SELECT 1 FROM player_games WHERE team=? AND game_date=? LIMIT 1",
                         (p["team"], p["game_date"])).fetchone():
            con.execute("UPDATE predictions SET actual=-1, result='DNP' WHERE game_date=? AND "
                        "player_key=? AND market=? AND line=?", key)
        else:
            continue
        done += 1
        days.add(p["game_date"])
    con.commit()
    games_done = grade_games(con, today())
    if done or games_done:
        print(f"Graded {done} prop lines and {games_done} game lines"
              + (f" from {', '.join(sorted(days))}" if days else ""))
    scoreboard(con)


def scoreboard(con):
    props = con.execute("SELECT * FROM predictions WHERE result IN ('Over','Under') "
                        "AND market_over IS NOT NULL").fetchall()
    games = con.execute("SELECT * FROM game_predictions WHERE result IN ('W','L','P') "
                        "AND tier IS NOT NULL").fetchall()
    if not props and not games:
        print("Scoreboard: nothing graded yet. It fills in after your first betting day.")
        return
    print(f"\n=== Scoreboard ===")
    if props:
        n = len(props)
        y = [1 if r["result"] == "Over" else 0 for r in props]
        bm = sum((r["p_over"] - v) ** 2 for r, v in zip(props, y)) / n
        bk = sum((r["market_over"] - v) ** 2 for r, v in zip(props, y)) / n
        print(f"Props accuracy ({n} lines): Brier model {bm:.4f} vs market {bk:.4f} (lower wins)")

    print("Record of picks, 1 unit each at the best PA price:")
    for tier in ("BEST", "CONSIDER", "AVOID"):
        bets = []
        for r in props:
            if r["tier"] == tier and r["pick_side"]:
                over = r["pick_side"] == "Over"
                price = r["best_over_price"] if over else r["best_under_price"]
                if price is not None:
                    won = (r["result"] == "Over") == over
                    bets.append(payout(price) if won else -1.0)
        for r in games:
            if r["tier"] == tier and r["result"] != "P":
                bets.append(payout(r["best_price"]) if r["result"] == "W" else -1.0)
        if bets:
            w = sum(1 for b in bets if b > 0)
            print(f"   {tier:<9} {w}-{len(bets) - w}   {sum(bets):+.2f} units  ({sum(bets) / len(bets):+.1%} ROI)")
        else:
            print(f"   {tier:<9} no graded picks yet")
    print("   (Small samples are noise; judge each tier after ~200 picks.)")


# ======================= what you missed =======================
def missed(con, since):
    games = con.execute("SELECT COUNT(DISTINCT game_id) FROM player_games WHERE game_date>=? "
                        "AND game_date<?", (since, today())).fetchone()[0]
    if not games:
        return
    print(f"\n=== Since {since}: {games} games played ===")
    for title, order in (("Top scoring nights", "pts DESC"), ("Top three-point nights", "fg3m DESC, fg3a")):
        print(title + ":")
        for r in con.execute(f"SELECT player_name, team, opp, game_date, pts, fg3m, fg3a FROM player_games "
                             f"WHERE game_date>=? AND game_date<? ORDER BY {order} LIMIT 5",
                             (since, today())):
            stat = f"{r['pts']} pts" if order.startswith("pts") else f"{r['fg3m']}-{r['fg3a']} 3PT"
            print(f"   {stat:<10} {r['player_name']} ({r['team']} vs {r['opp']}, {r['game_date']})")


# ======================= picks board =======================
def classify_prop(p):
    gap = p["p_over"] - p["market_over"]
    over = gap > 0
    side = "Over" if over else "Under"
    price = p["best_over_price"] if over else p["best_under_price"]
    book = p["best_over_book"] if over else p["best_under_book"]
    edge = abs(gap)
    if edge < CONSIDER_EDGE or price is None:
        return None
    label = PROP_MARKETS.get(p["market"], {"label": p["market"]})["label"]
    why = [f"proj {p['mean']:.1f} {label} in {p['proj_min']:.0f} min"]
    if p["opp_factor"] >= 1.04:
        why.append("opponent gives up a lot of this")
    elif p["opp_factor"] <= 0.96:
        why.append("opponent limits this")
    if p["injury_boost"] and p["injury_boost"] >= 0.1:
        why.append(f"teammates out (+{p['injury_boost']:.1f})")
    warn = []
    role = p["role_change"]
    role_against = role is not None and ((not over and role >= ROLE_CHANGE_FLAG)
                                         or (over and role <= -ROLE_CHANGE_FLAG))
    if role is not None and abs(role) >= ROLE_CHANGE_FLAG and not role_against:
        why.append(f"production {'up' if role > 0 else 'down'} {abs(role):.0%} this season")
    if role_against:
        warn.append(f"production {role:+.0%} vs last season, opposite to this pick")
    games = p["cur_games"] or 0
    if games < BEST_MIN_GAMES:
        warn.append(f"{games} game{'s' * (games != 1)} this season; leans on last season")
    if "self:" in (p["notes"] or ""):
        warn.append(p["notes"].split(";")[0].replace("self:", "status:").strip())
    if price < BEST_MIN_PRICE:
        warn.append(f"expensive ({price:+d})")
    if role_against or (not over and games < BEST_MIN_GAMES and edge >= BEST_EDGE):
        tier = "AVOID"
    elif edge >= BEST_EDGE and not warn:
        tier = "BEST"
    else:
        tier = "CONSIDER"
    pick = f"{side} {p['line']:g} {label}"
    model = p["p_over"] if over else 1 - p["p_over"]
    market = p["market_over"] if over else 1 - p["market_over"]
    return dict(tier=tier, side=side, name=p["player"], matchup=f"{p['team']} vs {p['opp']}",
                pick=pick, price=price, book=book, model=model, market=market, edge=edge,
                why=why, warn=warn)


def classify_games(rows):
    """For each game+market keep the side the model likes more.
    Returns (board entries, [(tier or None, row) for every row])."""
    from collections import defaultdict
    by = defaultdict(list)
    for r in rows:
        by[(r["event_id"], r["market"])].append(r)
    entries, tiers = [], []
    for (_, market), sides in by.items():
        r = max(sides, key=lambda s: s["model_prob"] - s["market_prob"])
        edge = r["model_prob"] - r["market_prob"]
        tier = None
        if edge >= CONSIDER_EDGE:
            warn = []
            if r["early_games"] < BEST_MIN_GAMES:
                warn.append("early season: ratings still lean on last season")
            if r["best_price"] < BEST_MIN_PRICE:
                warn.append(f"expensive ({r['best_price']:+d})")
            tier = "BEST" if edge >= BEST_EDGE and not warn else "CONSIDER"
            team = r["home"] if r["side"] == "home" else r["away"]
            pick = (f"{team} ML" if market == "h2h" else f"{team} {r['point']:+g}"
                    if market == "spreads" else f"{r['side']} {r['point']:g} total")
            fav = r["home"] if r["pred_margin"] >= 0 else r["away"]
            entries.append(dict(tier=tier, name=f"{r['away']} @ {r['home']}", matchup="game",
                                pick=pick, price=r["best_price"], book=r["best_book"],
                                model=r["model_prob"], market=r["market_prob"], edge=edge,
                                why=[f"model: {fav} by {abs(r['pred_margin']):.1f}, "
                                     f"total {r['pred_total']:.0f}"], warn=warn))
        tiers.extend((tier if s is r else None, s) for s in sides)
    return entries, tiers


def show_board(con, game_date):
    preds = con.execute("SELECT * FROM predictions WHERE game_date=? AND market_over IS NOT NULL",
                        (game_date,)).fetchall()
    grows = con.execute("SELECT * FROM game_predictions WHERE game_date=?", (game_date,)).fetchall()
    if not preds and not grows:
        print(f"No picks for {game_date}. Run daily.py and answer 'y' to pull lines.")
        return

    entries = []
    for p in preds:
        c = classify_prop(p)
        con.execute("UPDATE predictions SET tier=?, pick_side=? WHERE game_date=? AND player_key=? "
                    "AND market=? AND line=?", (c["tier"] if c else None, c["side"] if c else None,
                                                p["game_date"], p["player_key"], p["market"], p["line"]))
        if c:
            entries.append(c)
    game_entries, game_tiers = classify_games(grows)
    for tier, s in game_tiers:
        con.execute("UPDATE game_predictions SET tier=? WHERE game_date=? AND event_id=? AND "
                    "market=? AND side=?", (tier, s["game_date"], s["event_id"], s["market"], s["side"]))
    con.commit()

    w = 100
    print("\n" + "=" * w)
    print(f"PICKS BOARD — {game_date}   ({len(grows) // 2} game lines, {len(preds)} prop lines checked)".center(w))
    print("=" * w)

    def table(title, rows):
        print(f"\n{title}")
        if not rows:
            print("   (none)")
            return
        print(f"   {'PLAYER / GAME':<24}{'PICK':<20}{'BEST PRICE':<21}{'MODEL':>6}{'MKT':>6}{'EDGE':>6}")
        for e in sorted(rows, key=lambda e: e["edge"], reverse=True):
            price = f"{e['price']:+d} {e['book']}"[:20]
            print(f"   {e['name'][:23]:<24}{e['pick'][:19]:<20}{price:<21}"
                  f"{e['model']:>6.0%}{e['market']:>6.0%}{e['edge']:>6.0%}")
            print(f"      {'' if e['matchup'] == 'game' else e['matchup'] + ': '}" + ", ".join(e["why"]))
            for x in e["warn"]:
                print(f"      ! {x}")

    table("GAME LINES — BEST", [e for e in game_entries if e["tier"] == "BEST"])
    table("GAME LINES — CONSIDER", [e for e in game_entries if e["tier"] == "CONSIDER"])
    table("PLAYER PROPS — BEST  (edge 7%+, fair price, no red flags)",
          [e for e in entries if e["tier"] == "BEST"])
    table("PLAYER PROPS — CONSIDER  (smaller edge or one warning)",
          [e for e in entries if e["tier"] == "CONSIDER"])
    table("PLAYER PROPS — AVOID  (model disagrees, but its data is likely stale)",
          [e for e in entries if e["tier"] == "AVOID"])
    print("\n" + "-" * w)
    print(" MODEL / MKT = chance this pick wins (model vs. sportsbooks, vig removed). EDGE = difference.")
    print(" Every pick is logged and graded automatically; the scoreboard shows each tier's record.")
    print("-" * w)


# ======================= optional commands =======================
def cmd_picks():
    show_board(connect(), sys.argv[2] if len(sys.argv) > 2 else today())


def cmd_backtest():
    from game_model import backtest_games
    from props_model import backtest
    con = connect()
    print("Running backtests (can take a minute)...\n")
    backtest(con)
    backtest_games(con)


def cmd_status():
    con = connect()
    for t in ("player_games", "game_lines", "prop_snapshots", "injuries", "predictions",
              "game_predictions"):
        print(f"{t:<17} {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>9,} rows")
    r = con.execute("SELECT MIN(game_date), MAX(game_date) FROM player_games").fetchone()
    print(f"\nBox scores:   {r[0]} to {r[1]}")
    print(f"Last run:     {get_meta(con, 'last_run', 'never')}")
    print(f"Credits left: {get_meta(con, 'credits_left', 'unknown')}")


COMMANDS = {"picks": cmd_picks, "report": cmd_picks, "backtest": cmd_backtest, "status": cmd_status}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in COMMANDS:
        COMMANDS[sys.argv[1]]()
    elif len(sys.argv) > 1:
        print(__doc__)
    else:
        run()
