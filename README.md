# NBA Odds Tracker

A season-long NBA data pipeline and prediction system. It collects box scores, injury reports, and odds from Pennsylvania-licensed sportsbooks into a local SQLite database, projects player props (points, rebounds, assists, threes, PRA) and game lines (moneyline, spread, total) with matchup- and injury-aware models, ranks picks into tiers, and grades every prediction against real results and the betting market.

## Features

- **Data warehouse (SQLite):** multi-season player box scores, game-line and player-prop odds snapshots, daily injury reports, predictions, and graded results
- **Official NBA injury report:** reads the league's own PDF (posted every ~15 minutes on game days) and merges it with ESPN; the official status wins for tonight, ESPN covers long-term injuries and return dates. Every report pulled is kept in `injury_history`
- **Live scores, free:** NBA.com's public scoreboard and box scores show each pick's progress while games are on (e.g. `↑ 2/3.5 · Q3 5:12`), and picks grade minutes after the final buzzer, without spending any Odds API credits
- **More NBA.com data:** daily snapshots of team pace, offensive/defensive/net rating, threes allowed, and every player's catch-and-shoot vs pull-up threes, shown under each pick
- **Runs itself:** a scheduled free update five times a day (Mac launchd / Windows Task Scheduler)
- **Odds collection:** moneylines, spreads, totals, and player props from DraftKings, FanDuel, BetMGM, BetRivers, betPARX, and theScore Bet via [The Odds API](https://the-odds-api.com/), converted to vig-free probabilities
- **Player prop model:** projected minutes × per-minute rates weighted toward the current season, adjusted for what the opponent allows and for teammates newly ruled out; threes use attempts × shrunk 3P%; negative binomial probabilities for every market
- **Game model:** margin-of-victory Elo for moneyline and spread, back-to-back rest adjustment (backtest reports whether it helps), recent scoring/allowing for totals
- **Picks most likely to hit:** each line's chance to hit blends the model with the market, weighted by how sharp the model has actually been in that market (re-learned from every graded line). A pick needs the model and the sportsbooks to favor the same side, then shows a factor checklist (model projection, books, last-10-game hit rate, minutes, matchup, role/teammates out; for games: record, rest, home court). TOP plays are 60%+ to hit with most factors agreeing, no red flags, and a price that isn't overpaying. Ranked by chance to hit, top 5, one per player. (`PICK_MODE = "value"` in `config.py` switches back to EV ranking.)
- **Hot players and streaks:** for every rotation player playing that day: season hit counts at each milestone (e.g. *15+ PTS in all 4 games*), current streaks, floors, and a heat meter (last 5 games vs normal level). A milestone model gives the chance to reach any "X or more" tonight for PTS, 3PM, REB, AST and PRA; backtested on ~23,000 games per stat across three seasons (when it said 80–90%, 85–89% hit). Testing showed hot streaks carry over only about a third of the way, so recent form is weighted at its fitted value (20–40% by stat) instead of chased. Every call is saved and graded to track calibration this season
- **Parlay builder:** combines the likeliest legs (milestones priced with alternate lines, plus TOP/LEAN props) into Safest 2/3-leg, Plus-money, Hot-hand and Best-value parlays; one leg per player and per game, leg chances scaled by the model's honesty so far this season, and every parlay graded leg by leg
- **Game-pick injuries and line movement:** regulars ruled out on each side and the open→current line move (ESPN, free) as factors and red flags
- **Closing-line value (CLV):** every game pick compared with DraftKings' closing line from ESPN, free
- **Credit budget:** spreads the free plan's 500 monthly credits (reset on the 1st) across the month, spends them first on games with injury news (found with free data), and never re-buys lines pulled in the last few hours
- **Desktop app:** tkinter window with picks, hot players, parlays, scores, record, injuries and a pull-lines planner; one-click desktop/Start-menu shortcut (`make_shortcut.bat`, Ctrl+Alt+N)
- **Backtesting:** day-by-day replay of past seasons using only prior information; Brier score and calibration per market
- **Grading:** every pick scored against the box score and the market; record and ROI tracked per tier
- API keys kept out of source code via environment variables

## Tech

Python · SQLite · REST APIs · pandas · nba_api · requests · tkinter

## Setup

### Mac (one command)

Open Terminal and paste:

```bash
git clone https://github.com/valleykid36/nba-odds-tracker.git ~/nba-odds-tracker && cd ~/nba-odds-tracker && ./setup_mac.sh
```

`setup_mac.sh` finds (or installs with Homebrew) a Python with tkinter, sets up a private `.venv`, asks for your Odds API key (saved only in `.env`), downloads the latest database snapshot, and opens the app. After that, double-click **NBA Picks.command** in Finder; it pulls the latest code each time it opens.

### Windows

```bash
git clone https://github.com/valleykid36/nba-odds-tracker.git
cd nba-odds-tracker
pip install -r requirements.txt
copy .env.example .env      # then add your Odds API key
python daily.py             # first run downloads past seasons and sets up the database
```

Then double-click **NBA Picks.bat**.

### Database

`nba.db` (box scores, odds history, picks, record) is not in git because it changes daily. A snapshot is published as the `db-snapshot` GitHub release so a new machine starts with your full history instead of rebuilding from NBA.com. Use one machine for pulling lines on a given day so the two databases don't drift apart.

## Usage

**Desktop app** (double-click `NBA Picks.bat` on Windows or `NBA Picks.command` on Mac, or run):

```bash
python app.py
```

1. **Update (free)** refreshes box scores, live scores, grades picks, pulls the official NBA injury report + ESPN, and the day's team/shooting stats.
   **Live scores (free)** just refreshes scores and grades finished games; while games are on the app does this by itself every 2 minutes. The **Scores** tab lists every game with your picks in it.
2. **Pull today's lines…** shows today's credit budget and pre-ticks the games worth spending on (a regular ruled out = props most likely mispriced). Untick or tick games and watch the cost update.
3. The **Picks** tab shows only TOP picks, most likely to hit first; tick *Show LEAN* / *Show AVOID* for the rest. Click a pick for its factor checklist (✓/✗) and red flags. ◀ ▶ browse past days with results.
4. The **Record** tab shows each tier's record and how sharp the model is vs the books per market.

**Command line** still works the same way:

```bash
python daily.py                     # catch up, then plan and pull within budget
python daily.py update              # free catch-up only, no questions (what the schedule runs)
python daily.py live                # live scores + grade finished games (free)
python daily.py injuries            # official NBA injury report + ESPN (free)
python nba_injury.py                # print the newest official report as the program reads it
python daily.py picks [date] [all]  # show good picks again (add "all" for every tier)
python daily.py credits             # credit balance and today's budget (free)
python daily.py backtest            # grade the models on past seasons
python daily.py status              # database summary
```

### Automatic daily updates (free)

Turn on once per computer. It runs `daily.py update` at 10:10 AM, 1:10 PM, 5:40 PM, 7:10 PM and 11:50 PM (around when teams file injury reports, plus a late run that grades the night). A computer that's asleep runs it when it wakes. Output goes to `logs/update.log`.

```bash
./schedule_mac.sh          # Mac   (./schedule_mac.sh off to stop)
schedule_windows.bat       # Windows: double-click   (schedule_windows.bat off to stop)
```

### Credits (The Odds API free plan)

500 credits a month, reset on the 1st. Game lines for every game cost 3 credits; props cost 1 credit per market per game. Fewer markets in `PULL_PROP_MARKETS` (default: threes and points) means more games per month. The daily budget spreads what's left until the reset, and lines pulled within a few hours are reused instead of re-bought.

## Roadmap

- [x] Odds collection, vig removal, PA sportsbook filter, line shopping
- [x] Elo game model with backtest
- [x] SQLite data warehouse + daily pipeline
- [x] Player prop model (PTS, REB, AST, 3PM, PRA) with matchups, injuries, and backtesting
- [x] Game-line model (moneyline, spread, total)
- [x] Tiered picks board
- [x] Automatic grading vs. results and market
- [x] Rest days / back-to-backs
- [x] Market-blended win chance with learned trust, credit budget planner
- [x] Confidence-based picks with factor checklist (last-10 hit rate, minutes, matchup, role)
- [x] One-command Mac setup
- [x] Desktop GUI (tkinter)
- [x] Official NBA injury report, free live scores + instant grading, daily team/shooting snapshots, scheduled updates
- [x] Closing-line value (CLV) tracking; injuries and line movement in game picks
- [x] Hot-player streak tracking, backtested milestone model, parlay builder with grading
- [ ] Opponent and minutes adjustments in the milestone model; prop CLV
- [ ] Pace and shooting splits in the model (data is being collected; turn on once a backtest shows it helps)
- [ ] FastAPI service + play-money odds website, deployed

## Disclaimer

For educational and research purposes. Bet responsibly and only with sportsbooks licensed in your state.
