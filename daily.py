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
    python daily.py report [YYYY-MM-DD]   re-show a day's predictions (free)
    python daily.py backtest              test the threes model on past seasons
    python daily.py status                what's in the database
"""

import sys
from datetime import datetime

from config import CURRENT_SEASON, ET, LEAN_THRESHOLD, SEASONS, payout
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

    leans = []
    for r, y in zip(rows, hits):
        gap = r["p_over"] - r["market_over"]
        over = gap > 0
        price = r["best_over_price"] if over else r["best_under_price"]
        if abs(gap) >= LEAN_THRESHOLD and price is not None:
            won = (y == 1) == over
            leans.append((won, payout(price) if won else -1.0))
    if leans:
        w = sum(1 for won, _ in leans if won)
        profit = sum(p for _, p in leans)
        print(f"Model leans, 1 unit each at best PA price: {w}-{len(leans) - w}, "
              f"{profit:+.2f} units ({profit / len(leans):+.1%} ROI)")
        if len(leans) < 200:
            print(f"   ({len(leans)} leans is still a small sample; treat as noise under ~200)")


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


# ---------------- report ----------------
def show_report(con, game_date):
    preds = con.execute("SELECT * FROM predictions WHERE game_date=? AND market_over IS NOT NULL",
                        (game_date,)).fetchall()
    if not preds:
        print(f"No predictions for {game_date}.")
        return
    preds = sorted(preds, key=lambda p: abs(p["p_over"] - p["market_over"]), reverse=True)
    print(f"\n=== Threes model v2 vs. market — {game_date} ({len(preds)} lines) ===")
    print("proj min | expected 3PA x 3P% = expected makes | opp >1 = opponent allows more threes\n")
    for p in preds:
        gap = p["p_over"] - p["market_over"]
        side = "Over" if gap > 0 else "Under"
        price = p["best_over_price"] if side == "Over" else p["best_under_price"]
        book = p["best_over_book"] if side == "Over" else p["best_under_book"]
        flag = "LEAN" if abs(gap) >= LEAN_THRESHOLD else "    "
        price_txt = f"{price:+d} {book}" if price is not None else ""
        print(f"{flag} {p['player']} ({p['team']} vs {p['opp']})  {side} {p['line']}  {price_txt}")
        print(f"     model Over {p['p_over']:.0%} vs market {p['market_over']:.0%}  |  "
              f"{p['proj_min']:.0f} min, {p['exp_3pa']:.1f} 3PA x {p['pct']:.0%} = {p['mean']:.2f}  |  "
              f"opp {p['opp_factor']:.2f}"
              + (f"  |  injuries +{p['injury_boost']:.1f} 3PA" if p["injury_boost"] else ""))
        if p["notes"]:
            print(f"     note: {p['notes']}")
    print("\nLEAN = model and market disagree by 5%+. Everything is logged and graded "
          "automatically next time you run daily.py.")


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


COMMANDS = {"report": cmd_report, "backtest": cmd_backtest, "status": cmd_status}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in COMMANDS:
        COMMANDS[sys.argv[1]]()
    elif len(sys.argv) > 1:
        print(__doc__)
    else:
        run()
