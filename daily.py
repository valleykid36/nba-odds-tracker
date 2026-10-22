"""
daily.py — run it whenever you want. It catches up on everything you missed.

    python daily.py

Every time, for free (no Odds API credits):
  1. First run only: downloads past seasons and imports your old CSV files.
  2. Refreshes box scores for every game played since you last ran it.
  3. Grades every prediction from the days you missed and updates your scoreboard.
  4. Shows what you missed: biggest three-point nights since your last run.
  5. Refreshes today's injury report.
  6. Lists today's games and asks if you're betting today.
     - No  -> done. 0 credits spent.
     - Yes -> you pick the games; pulls game odds (1 credit) + threes lines
              (1 credit per game) and shows the model vs. the market.

What CAN'T be caught up: odds and injuries for days you skipped. The free Odds API
plan has no history, so those days simply have no lines. Box scores and grading
always catch up fully.

Other commands (optional):
    python daily.py picks [YYYY-MM-DD]    show the picks board again (free)
    python daily.py backtest              test the threes model on past seasons
    python daily.py status                what's in the database
"""

import sys
from datetime import datetime

from config import CURRENT_SEASON, ET, SEASONS, payout
from db import connect, get_meta, set_meta


def today():
    return datetime.now(ET).strftime("%Y-%m-%d")


def ask(prompt):
    try:
        return input(prompt).strip().lower()
    except EOFError:  # e.g. run by Task Scheduler with no keyboard
        return ""


# ---------------- the main routine ----------------
def run():
    con = connect()
    last_run = get_meta(con, "last_run")
    if last_run:
        days = (datetime.fromisoformat(today()) - datetime.fromisoformat(last_run)).days
        print(f"Welcome back. Last run: {last_run} ({days} day{'s' * (days != 1)} ago).\n")
    else:
        print("First run: setting everything up.\n")

    # 1) First-time setup: past seasons + old CSVs
    have = {r[0] for r in con.execute("SELECT DISTINCT season FROM player_games")}
    missing = [s for s in SEASONS if int(s[:4]) not in have and int(s[:4]) != CURRENT_SEASON]
    if not get_meta(con, "csv_imported"):
        from ingest import import_csvs
        print("Importing your existing CSV files...")
        import_csvs()
        set_meta(con, "csv_imported", today())
    if missing:
        print("Downloading past seasons (one time, a few minutes):", ", ".join(missing))

    # 2) Box scores: missing past seasons + the current season (picks up every missed game)
    from ingest import update_injuries, update_player_games
    before = con.execute("SELECT COUNT(DISTINCT game_id) FROM player_games WHERE season=?",
                         (CURRENT_SEASON,)).fetchone()[0]
    print("Updating box scores...")
    update_player_games(missing + SEASONS[-1:])
    after = con.execute("SELECT COUNT(DISTINCT game_id) FROM player_games WHERE season=?",
                        (CURRENT_SEASON,)).fetchone()[0]
    print(f"   {after - before} new games added this season ({after} total)\n")

    # 3) Grade everything pending
    grade(con)

    # 4) What you missed
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
    from ingest import todays_events
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
    pick = ask("Which games? (e.g. 1,3 or 'all') — 1 credit each, plus 1 for game odds: ")
    chosen = events if pick == "all" else [
        e for i, e in enumerate(events, 1) if str(i) in pick.replace(" ", "").split(",")]
    if not chosen:
        print("No games picked. No credits spent.")
        return

    from ingest import update_odds, update_props
    from threes_model import predict_props
    print("\nGame odds...")
    update_odds()
    for tip, ev in chosen:
        print(f"Threes lines: {ev['away_team']} @ {ev['home_team']}")
        update_props(ev)
    predict_props(con, today())
    show_report(con, today())
    con.close()


# ---------------- grading ----------------
def grade(con):
    pending = con.execute(
        "SELECT * FROM predictions WHERE actual IS NULL AND game_date<?", (today(),)).fetchall()
    done, days = 0, set()
    for p in pending:
        key = (p["game_date"], p["player_key"], p["market"], p["line"])
        row = con.execute("SELECT fg3m FROM player_games WHERE player_key=? AND game_date=?",
                          (p["player_key"], p["game_date"])).fetchone()
        if row:
            result = "Over" if row["fg3m"] > p["line"] else "Under"
            con.execute("UPDATE predictions SET actual=?, result=? WHERE game_date=? AND "
                        "player_key=? AND market=? AND line=?", (row["fg3m"], result, *key))
        elif con.execute("SELECT 1 FROM player_games WHERE team=? AND game_date=? LIMIT 1",
                         (p["team"], p["game_date"])).fetchone():
            con.execute("UPDATE predictions SET actual=-1, result='DNP' WHERE game_date=? AND "
                        "player_key=? AND market=? AND line=?", key)
        else:
            continue  # box score not in yet
        done += 1
        days.add(p["game_date"])
    con.commit()
    if done:
        print(f"Graded {done} predictions from {len(days)} day(s): {', '.join(sorted(days))}")
    scoreboard(con)


def scoreboard(con):
    rows = con.execute("SELECT * FROM predictions WHERE result IN ('Over','Under') "
                       "AND market_over IS NOT NULL ORDER BY game_date").fetchall()
    if not rows:
        print("Scoreboard: nothing graded yet. It fills in after your first betting day.")
        return
    n = len(rows)
    hits = [1 if r["result"] == "Over" else 0 for r in rows]
    b_model = sum((r["p_over"] - y) ** 2 for r, y in zip(rows, hits)) / n
    b_mkt = sum((r["market_over"] - y) ** 2 for r, y in zip(rows, hits)) / n
    days = len({r["game_date"] for r in rows})
    print(f"\n=== Scoreboard: {n} graded lines over {days} betting day(s) ===")
    print(f"Brier  model {b_model:.4f}  vs  market {b_mkt:.4f}   (lower wins; the market is the bar)")

    print("Record by tier (1 unit each at the best PA price):")
    for tier in ("BEST", "CONSIDER", "AVOID"):
        bets = []
        for r, y in zip(rows, hits):
            if r["tier"] != tier or not r["pick_side"]:
                continue
            over = r["pick_side"] == "Over"
            price = r["best_over_price"] if over else r["best_under_price"]
            if price is None:
                continue
            won = (y == 1) == over
            bets.append(payout(price) if won else -1.0)
        if bets:
            wins = sum(1 for b in bets if b > 0)
            print(f"   {tier:<9} {wins}-{len(bets) - wins}   {sum(bets):+.2f} units   "
                  f"({sum(bets) / len(bets):+.1%} ROI)")
        else:
            print(f"   {tier:<9} no graded picks yet")
    print("   (AVOID = what you'd get betting the model's stale picks. Small samples are noise until ~200.)")


# ---------------- what you missed ----------------
def missed(con, since):
    games = con.execute("SELECT COUNT(DISTINCT game_id) FROM player_games WHERE game_date>=? "
                        "AND game_date<?", (since, today())).fetchone()[0]
    if not games:
        return
    print(f"\n=== Since {since}: {games} games played ===")
    top = con.execute(
        "SELECT player_name, team, opp, game_date, fg3m, fg3a FROM player_games "
        "WHERE game_date>=? AND game_date<? ORDER BY fg3m DESC, fg3a ASC LIMIT 8",
        (since, today())).fetchall()
    print("Biggest three-point nights:")
    for r in top:
        print(f"   {r['fg3m']}-{r['fg3a']}  {r['player_name']} ({r['team']} vs {r['opp']}, {r['game_date']})")


# ---------------- picks board ----------------
def classify(p):
    """Decide BEST / CONSIDER / AVOID for one prediction. Returns (tier, side, price, book, edge, why, warn)."""
    from config import (BEST_EDGE, BEST_MIN_GAMES, BEST_MIN_PRICE, CONSIDER_EDGE,
                        ROLE_CHANGE_FLAG)
    gap = p["p_over"] - p["market_over"]
    over = gap > 0
    side = "Over" if over else "Under"
    price = p["best_over_price"] if over else p["best_under_price"]
    book = p["best_over_book"] if over else p["best_under_book"]
    edge = abs(gap)
    if edge < CONSIDER_EDGE or price is None:
        return None

    why, warn = [], []
    why.append(f"{p['exp_3pa']:.1f} 3PA x {p['pct']:.0%} = {p['mean']:.1f} makes")
    if p["opp_factor"] >= 1.04:
        why.append("opp allows lots of 3s")
    elif p["opp_factor"] <= 0.96:
        why.append("opp limits 3s")
    if p["injury_boost"]:
        why.append(f"teammates out (+{p['injury_boost']:.1f} 3PA)")

    role = p["role_change"]
    role_against = role is not None and (
        (not over and role >= ROLE_CHANGE_FLAG) or (over and role <= -ROLE_CHANGE_FLAG))
    if role is not None and abs(role) >= ROLE_CHANGE_FLAG and not role_against:
        why.append(f"role {'up' if role > 0 else 'down'} {abs(role):.0%}")
    if role_against:
        warn.append(f"3PA rate {role:+.0%} vs last season, opposite to this pick")
    games = p["cur_games"] or 0
    if games < BEST_MIN_GAMES:
        warn.append(f"only {games} game{'s' * (games != 1)} this season; relies on last season")
    if "self:" in (p["notes"] or ""):
        warn.append(p["notes"].split(";")[0].replace("self:", "status:").strip())
    if price < BEST_MIN_PRICE:
        warn.append(f"expensive ({price:+d})")

    if role_against or (not over and games < BEST_MIN_GAMES and edge >= BEST_EDGE):
        tier = "AVOID"   # big Under lean on stale data = the blind spot we saw
    elif edge >= BEST_EDGE and not warn:
        tier = "BEST"
    else:
        tier = "CONSIDER"
    return tier, side, price, book, edge, why, warn


def show_report(con, game_date):
    preds = con.execute("SELECT * FROM predictions WHERE game_date=? AND market_over IS NOT NULL",
                        (game_date,)).fetchall()
    if not preds:
        print(f"No predictions for {game_date}. Run daily.py and answer 'y' to pull lines.")
        return

    board = {"BEST": [], "CONSIDER": [], "AVOID": []}
    for p in preds:
        c = classify(p)
        tier, side = (c[0], c[1]) if c else (None, None)
        con.execute("UPDATE predictions SET tier=?, pick_side=? WHERE game_date=? AND player_key=? "
                    "AND market=? AND line=?",
                    (tier, side, p["game_date"], p["player_key"], p["market"], p["line"]))
        if c:
            board[c[0]].append((p, *c[1:]))
    con.commit()

    w = 96
    print("\n" + "=" * w)
    print(f" THREES PICKS BOARD — {game_date}   ({len(preds)} lines checked)".center(w))
    print("=" * w)
    labels = {
        "BEST": "BEST  — model edge 7%+, fair price, no red flags",
        "CONSIDER": "CONSIDER — smaller edge or one warning",
        "AVOID": "AVOID — model disagrees, but its data is likely stale",
    }
    for tier in ("BEST", "CONSIDER", "AVOID"):
        rows = sorted(board[tier], key=lambda r: r[4], reverse=True)
        print(f"\n{labels[tier]}")
        if not rows:
            print("   (none)")
            continue
        print(f"   {'PLAYER':<24}{'PICK':<13}{'PRICE':<20}{'MODEL':>7}{'MARKET':>8}{'EDGE':>7}")
        for p, side, price, book, edge, why, warn in rows:
            model = p["p_over"] if side == "Over" else 1 - p["p_over"]
            market = p["market_over"] if side == "Over" else 1 - p["market_over"]
            print(f"   {p['player'][:23]:<24}{side + ' ' + str(p['line']):<13}"
                  f"{f'{price:+d} {book}'[:19]:<20}{model:>7.0%}{market:>8.0%}{edge:>7.0%}")
            print(f"      {p['team']} vs {p['opp']}: " + ", ".join(why))
            for x in warn:
                print(f"      ! {x}")
    print("\n" + "-" * w)
    print(" Edge = model % minus market %. All picks are logged and graded automatically;")
    print(" the scoreboard tracks BEST and CONSIDER separately so you'll learn which tier to trust.")
    print("-" * w)


# ---------------- optional commands ----------------
def cmd_report():
    con = connect()
    show_report(con, sys.argv[2] if len(sys.argv) > 2 else today())


def cmd_backtest():
    from threes_model import backtest
    backtest(connect())


def cmd_status():
    con = connect()
    for t in ("player_games", "odds_snapshots", "prop_snapshots", "injuries", "predictions"):
        print(f"{t:<16} {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>9,} rows")
    r = con.execute("SELECT MIN(game_date), MAX(game_date) FROM player_games").fetchone()
    print(f"\nBox scores: {r[0]} to {r[1]}")
    print(f"Last run:   {get_meta(con, 'last_run', 'never')}")


COMMANDS = {"report": cmd_report, "picks": cmd_report, "backtest": cmd_backtest,
            "status": cmd_status}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in COMMANDS:
        COMMANDS[sys.argv[1]]()
    elif len(sys.argv) > 1:
        print(__doc__)
    else:
        run()
