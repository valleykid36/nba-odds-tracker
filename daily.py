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
     - Yes -> it plans which games are worth credits (injury news first) inside
              today's budget, you confirm or change it, then it pulls lines and
              prints only the good picks.

Prefer a window? Run:  python app.py

Credits: 500/month on the free plan, reset on the 1st. The daily budget spreads what's
left across the days until the reset. Lines pulled in the last few hours are reused.
Choose which prop types to pull in config.py -> PULL_PROP_MARKETS.

What CAN'T be caught up: odds and injuries for days you skipped (the free Odds API
plan has no history). Box scores and grading always catch up fully.

Other commands (optional):
    python daily.py update                free catch-up only, no questions (what the scheduler runs)
    python daily.py live                  live scores + grade finished games (free)
    python daily.py injuries              refresh the injury list (official NBA report + ESPN, free)
    python daily.py picks [YYYY-MM-DD]    show the good picks again (free); add "all" for every tier
    python daily.py credits               credit balance and today's budget (free)
    python daily.py clv                   closing-line value of game picks (ESPN, free)
    python daily.py backtest              test the models on past seasons (free)
    python daily.py status                what's in the database
"""

import sys
from datetime import datetime, timedelta

from config import CURRENT_SEASON, ET, PROP_MARKETS, PULL_GAME_LINES, PULL_PROP_MARKETS, SEASONS
from db import connect, get_meta, set_meta


def today():
    return datetime.now(ET).strftime("%Y-%m-%d")


def ask(prompt):
    try:
        return input(prompt).strip().lower()
    except EOFError:  # e.g. run by Task Scheduler with no keyboard
        return ""


# ======================= the main routine =======================
def catch_up(con):
    """Steps 1-5. All free: box scores, grading, what you missed, injuries."""
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
    from ingest import update_injuries, update_player_games
    count = "SELECT COUNT(DISTINCT game_id) FROM player_games WHERE season=?"
    before = con.execute(count, (CURRENT_SEASON,)).fetchone()[0]
    print("Updating box scores...")
    update_player_games(missing + SEASONS[-1:])
    after = con.execute(count, (CURRENT_SEASON,)).fetchone()[0]
    print(f"   {after - before} new games added this season ({after} total)\n")

    # 2b) Today's live/finished games from NBA.com's scoreboard (free) so tonight's picks grade fast
    try:
        from nba_live import update_live
        update_live()
    except Exception as e:
        print(f"   couldn't reach the NBA.com scoreboard ({e}); finished games grade on the next update")

    # 3) Grade everything pending, 4) what you missed
    grade(con)
    try:
        from clv import update_clv
        update_clv(con)                      # closing lines for game picks (ESPN, free)
    except Exception as e:
        print(f"   couldn't get closing lines ({e}); will try again next update")
    if last_run:
        missed(con, last_run)

    # 5) Injuries (free)
    print("\nUpdating injury report...")
    try:
        update_injuries()
    except Exception as e:
        print(f"   couldn't get injuries ({e}); continuing without today's injuries")

    # 6) Team pace/ratings and player shooting splits from NBA.com (free, once a day)
    if get_meta(con, "team_stats_date") != today():
        print("\nUpdating team and shooting stats...")
        try:
            from nba_extra import update_extra
            update_extra()
            set_meta(con, "team_stats_date", today())
        except Exception as e:
            print(f"   couldn't get team stats ({e}); will try again next update")
    set_meta(con, "last_run", today())


def pull(con, chosen, lines=PULL_GAME_LINES, force=False):
    """Spend credits: game lines + props for the chosen events, then predict. Returns credits used."""
    from game_model import predict_games
    from ingest import update_odds, update_props
    from props_model import predict_props
    used = 0
    if lines:
        print("\nGame lines (moneyline, spread, total)...")
        used += update_odds(force=force)
    for tip, ev in chosen:
        print(f"Player props: {ev['away_team']} @ {ev['home_team']}")
        used += update_props(ev, PULL_PROP_MARKETS, force=force)
    predict_games(con, today())
    predict_props(con, today())
    print(f"\nCredits used this pull: {used}")
    return used


def print_plan(plan_):
    b = plan_["budget"]
    print(f"\nToday's budget: {b if b is not None else 'unknown (first pull)'} credits"
          f"  (game lines {plan_['lines_cost']}, props {len(PULL_PROP_MARKETS)} per game)")
    for i, g in enumerate(plan_["games"], 1):
        mark = "x" if g["chosen"] else " "
        note = "; ".join(g["reasons"]) or "no injury/rest news"
        cost = f"{g['cost']} cr" if g["cost"] else "already pulled"
        print(f"  [{mark}] {i}. {g['tip']:%I:%M %p}  {g['event']['away_team']} @ "
              f"{g['event']['home_team']}  ({cost})  {note}")
    print(f"Planned: {plan_['total']} credits")


def run():
    from budget import credits_left, days_until_reset, plan, reset_date
    from ingest import todays_events
    con = connect()
    catch_up(con)

    # 6) Betting today?
    events = todays_events()     # free, and refreshes the credit balance
    if not events:
        print("\nNo more games today. You're all caught up.")
        return
    left = credits_left(con)
    print(f"\nCredits left: {left if left is not None else 'unknown'}  "
          f"(resets {reset_date():%b} 1, {days_until_reset()} days incl. today)")
    print(f"Today's games ({len(events)}):")
    for i, (tip, ev) in enumerate(events, 1):
        print(f"  {i}. {tip:%I:%M %p}  {ev['away_team']} @ {ev['home_team']}")
    if ask("\nBetting today? (y/N): ") not in ("y", "yes"):
        print("No credits spent. You're all caught up.")
        return

    p = plan(con, events, today())
    print_plan(p)
    labels = ", ".join(PROP_MARKETS[m]["label"] for m in PULL_PROP_MARKETS)
    pick = ask(f"Press Enter for the plan, or choose games for props ({labels}) e.g. 1,3 / 'all' / 'none': ")
    if pick == "":
        chosen = [(g["tip"], g["event"]) for g in p["games"] if g["chosen"]]
    else:
        chosen = events if pick == "all" else [] if pick == "none" else [
            e for i, e in enumerate(events, 1) if str(i) in pick.replace(" ", "").split(",")]
    from ingest import fresh_prop_markets
    cost = p["lines_cost"] + sum(len([m for m in PULL_PROP_MARKETS
                                      if m not in fresh_prop_markets(con, ev["id"])])
                                 for _, ev in chosen)
    print(f"\nThis will use up to {cost} credits. Credits left: {left if left is not None else 'unknown'}")
    if left is not None and cost > left:
        print("That's more than you have. Pick fewer games.")
        return
    if cost == 0 and not chosen and not p["lines_cost"]:
        print("Everything is already pulled; showing picks.")
    elif ask("Go? (Y/n): ") in ("n", "no"):
        print("Cancelled. No credits spent.")
        return
    pull(con, chosen)
    show_board(con, today())
    con.close()


# ======================= grading =======================
def grade(con, quiet=False):
    from game_model import grade_games
    # today's games count too: finished games come in from the live scoreboard (nba_live.py)
    pending = con.execute("SELECT * FROM predictions WHERE actual IS NULL AND game_date<=?",
                          (today(),)).fetchall()
    # A game only counts once it's over. NBA.com's game logs can include games still being
    # played, so today's games wait for the live scoreboard to say Final.
    live = {(r["game_date"], t): r["status"] for r in con.execute(
        "SELECT game_date, home, away, status FROM live_games") for t in (r["home"], r["away"])}

    def finished(day, team):
        st = live.get((day, team))
        return st == 3 if st is not None else day < today()

    done, days = 0, set()
    for p in pending:
        if not finished(p["game_date"], p["team"]):
            continue
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
    tomorrow = (datetime.fromisoformat(today()) + timedelta(days=1)).strftime("%Y-%m-%d")
    games_done = grade_games(con, tomorrow, ready=finished)      # only finished games have scores, so this is safe
    if done or games_done:
        print(f"Graded {done} prop lines and {games_done} game lines"
              + (f" from {', '.join(sorted(days))}" if days else ""))
    if not quiet:
        scoreboard(con)
    return done + games_done


def scoreboard(con):
    from clv import clv_summary, describe
    from picks import learned_trust, market_label, records
    rec, clv = records(con), clv_summary(con)
    if not any(r["w"] + r["l"] for r in rec.values()):
        print("Scoreboard: nothing graded yet. It fills in after your first betting day.")
        return
    print("\n=== Scoreboard (1 unit per pick at the best PA price) ===")
    for tier, r in rec.items():
        if r["w"] + r["l"]:
            print(f"   {tier:<9} {r['w']}-{r['l']}   {r['units']:+.2f} units  ({r['roi']:+.1%} ROI)"
                  + (f"   {describe(clv[tier])} (game lines)" if clv[tier]["n"] else ""))
        else:
            print(f"   {tier:<9} no graded picks yet")
    trust = learned_trust(con)
    if trust:
        print("How sharp is the model vs the books, by market (Brier, lower wins):")
        for m, t in sorted(trust.items(), key=lambda x: -x[1]["n"]):
            better = "model" if t["model"] < t["market"] else "books"
            print(f"   {market_label(m):<7} {t['n']:>6,} lines  model {t['model']:.4f}  books "
                  f"{t['market']:.4f}  -> {better} sharper; trusting model {t['trust']:.0%}")
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
def show_board(con, game_date, show_all=False):
    from picks import build_board
    board, n_props, n_games = build_board(con, game_date)
    if not n_props and not n_games:
        print(f"No lines for {game_date}. Run daily.py (or app.py) and pull lines.")
        return
    from config import PICK_MODE
    tiers = ("BEST", "CONSIDER", "AVOID") if show_all else ("BEST",)
    names = {"BEST": "TOP" if PICK_MODE == "confidence" else "BEST", "CONSIDER": "LEAN", "AVOID": "AVOID"}
    w = 100
    print("\n" + "=" * w)
    print(f"PICKS — {game_date}   ({n_games} games, {n_props} prop lines checked)".center(w))
    print("=" * w)
    for tier in tiers:
        rows = [e for e in board if e["tier"] == tier]
        print(f"\n{names[tier]}" + ("  (60%+ to hit, most factors agree, no red flags)"
                                    if tier == "BEST" and PICK_MODE == "confidence" else
                                    "  (+5% EV or better, no red flags)" if tier == "BEST" else ""))
        if not rows:
            print("   (none today — passing is a good pick too)" if tier == "BEST" else "   (none)")
            continue
        print(f"   {'PLAYER / GAME':<24}{'PICK':<22}{'BEST PRICE':<21}{'HIT%':>6}{'BOOKS':>7}{'FACTORS':>9}  RESULT")
        for e in rows:
            price = f"{e['price']:+d} {e['book']}"[:20]
            print(f"   {e['name'][:23]:<24}{e['pick'][:21]:<22}{price:<21}"
                  f"{e['win']:>6.0%}{e['market']:>7.0%}{e['n_ok']:>6}/{len(e['signals'])}  {e['result']}")
            for s in e["signals"]:
                print(f"      {'+' if s['ok'] else '-'} {s['text']}")
            for x in e["warn"]:
                print(f"      ! {x}")
    hidden = sum(1 for e in board if e["tier"] in ("CONSIDER", "AVOID"))
    print("\n" + "-" * w)
    print(" HIT% = chance to hit (model blended with the books). BOOKS = sportsbooks' chance (vig removed).")
    print(" FACTORS = how many checks agree with the pick. Every line is graded automatically.")
    if not show_all and hidden:
        print(f" {hidden} weaker picks hidden. See them: python daily.py picks {game_date} all")
    print("-" * w)


# ======================= optional commands =======================
def cmd_picks():
    args = sys.argv[2:]
    day = next((a for a in args if a[:2] == "20"), today())
    show_board(connect(), day, show_all="all" in args)


def cmd_credits():
    from budget import credits_left, daily_budget, days_until_reset, reset_date
    from ingest import check_credits
    con = connect()
    try:
        check_credits()   # free
    except Exception as e:
        print(f"(couldn't check online: {e}; showing last known)")
    print(f"Credits left:   {credits_left(con)}")
    print(f"Resets:         {reset_date():%B} 1 ({days_until_reset()} days incl. today)")
    print(f"Today's budget: {daily_budget(con)}")


def cmd_backtest():
    from game_model import backtest_games
    from props_model import backtest
    con = connect()
    print("Running backtests (can take a minute)...\n")
    backtest(con)
    backtest_games(con)


def cmd_status():
    con = connect()
    for t in ("player_games", "game_lines", "prop_snapshots", "injuries", "injury_history",
              "predictions", "game_predictions", "live_games", "team_stats", "player_shooting"):
        print(f"{t:<17} {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>9,} rows")
    r = con.execute("SELECT MIN(game_date), MAX(game_date) FROM player_games").fetchone()
    print(f"\nBox scores:   {r[0]} to {r[1]}")
    print(f"Last run:     {get_meta(con, 'last_run', 'never')}")
    print(f"Credits left: {get_meta(con, 'credits_left', 'unknown')}")


def cmd_update():
    """Free catch-up only (box scores, live scores, grading, injuries, team stats). Never spends
    credits and never asks anything, so it is safe to run on a schedule."""
    con = connect()
    catch_up(con)
    con.close()


def cmd_live():
    from nba_live import update_live
    update_live()
    con = connect()
    grade(con)
    con.close()


def cmd_injuries():
    from ingest import update_injuries
    update_injuries()


def cmd_clv():
    from clv import clv_summary, describe, update_clv
    con = connect()
    update_clv(con)
    for tier, s in clv_summary(con).items():
        print(f"   {tier:<9} {describe(s)}")
    con.close()


COMMANDS = {"clv": cmd_clv, "update": cmd_update, "live": cmd_live, "injuries": cmd_injuries, "picks": cmd_picks, "report": cmd_picks, "backtest": cmd_backtest, "status": cmd_status,
            "credits": cmd_credits}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in COMMANDS:
        COMMANDS[sys.argv[1]]()
    elif len(sys.argv) > 1:
        print(__doc__)
    else:
        run()
