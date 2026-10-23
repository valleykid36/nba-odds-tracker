# NBA Odds Tracker

A season-long NBA data pipeline and prediction system. It collects box scores, injury reports, and odds from Pennsylvania-licensed sportsbooks into a local SQLite database, projects player props (points, rebounds, assists, threes, PRA) and game lines (moneyline, spread, total) with matchup- and injury-aware models, ranks picks into tiers, and grades every prediction against real results and the betting market.

## Features

- **Data warehouse (SQLite):** multi-season player box scores, game-line and player-prop odds snapshots, daily injury reports, predictions, and graded results
- **Odds collection:** moneylines, spreads, totals, and player props from DraftKings, FanDuel, BetMGM, BetRivers, betPARX, and theScore Bet via [The Odds API](https://the-odds-api.com/), converted to vig-free probabilities
- **Player prop model:** projected minutes × per-minute rates weighted toward the current season, adjusted for what the opponent allows and for teammates newly ruled out; threes use attempts × shrunk 3P%; negative binomial probabilities for every market
- **Game model:** margin-of-victory Elo for moneyline and spread, back-to-back rest adjustment (backtest reports whether it helps), recent scoring/allowing for totals
- **Good picks only:** each line's win chance blends the model with the market, weighted by how sharp the model has actually been in that market (re-learned from every graded line). Picks are ranked by expected value at the best PA price, capped at the top 5, one per player, with red flags (role changes, early-season data, back-to-backs, expensive prices, one-book lines)
- **Credit budget:** spreads the free plan's 500 monthly credits (reset on the 1st) across the month, spends them first on games with injury news (found with free data), and never re-buys lines pulled in the last few hours
- **Desktop app:** tkinter window with picks, record, injuries and a pull-lines planner
- **Backtesting:** day-by-day replay of past seasons using only prior information; Brier score and calibration per market
- **Grading:** every pick scored against the box score and the market; record and ROI tracked per tier
- API keys kept out of source code via environment variables

## Tech

Python · SQLite · REST APIs · pandas · nba_api · requests · tkinter

## Setup

```bash
git clone https://github.com/valleykid36/nba-odds-tracker.git
cd nba-odds-tracker
pip install -r requirements.txt
cp .env.example .env   # Windows: copy .env.example .env, then add your API keys
python daily.py             # first run downloads past seasons and sets up the database
python daily.py backtest    # test the threes model on history
```

## Usage

**Desktop app** (double-click `NBA Picks.bat` on Windows or `NBA Picks.command` on Mac, or run):

```bash
python app.py
```

1. **Update (free)** refreshes box scores, grades past picks and pulls the injury report.
2. **Pull today's lines…** shows today's credit budget and pre-ticks the games worth spending on (a regular ruled out = props most likely mispriced). Untick or tick games and watch the cost update.
3. The **Picks** tab shows only BEST picks; tick *Show LEAN* / *Show AVOID* for the rest. Click a pick for why the model likes it and its red flags. ◀ ▶ browse past days with results.
4. The **Record** tab shows each tier's record and how sharp the model is vs the books per market.

**Command line** still works the same way:

```bash
python daily.py                     # catch up, then plan and pull within budget
python daily.py picks [date] [all]  # show good picks again (add "all" for every tier)
python daily.py credits             # credit balance and today's budget (free)
python daily.py backtest            # grade the models on past seasons
python daily.py status              # database summary
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
- [x] Market-blended win chance with learned trust, EV ranking, credit budget planner
- [x] Desktop GUI (tkinter)
- [ ] Pace in the model; closing-line value (CLV) tracking
- [ ] LLM parsing of injury and lineup news
- [ ] FastAPI service + play-money odds website, deployed

## Disclaimer

For educational and research purposes. Bet responsibly and only with sportsbooks licensed in your state.
