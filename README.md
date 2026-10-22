# NBA Odds Tracker

A season-long NBA data pipeline and prediction system. It collects box scores, injury reports, and odds from Pennsylvania-licensed sportsbooks into a local SQLite database, projects player props (points, rebounds, assists, threes, PRA) and game lines (moneyline, spread, total) with matchup- and injury-aware models, ranks picks into tiers, and grades every prediction against real results and the betting market.

## Features

- **Data warehouse (SQLite):** multi-season player box scores, game-line and player-prop odds snapshots, daily injury reports, predictions, and graded results
- **Odds collection:** moneylines, spreads, totals, and player props from DraftKings, FanDuel, BetMGM, BetRivers, betPARX, and theScore Bet via [The Odds API](https://the-odds-api.com/), converted to vig-free probabilities
- **Player prop model:** projected minutes × per-minute rates weighted toward the current season, adjusted for what the opponent allows and for teammates newly ruled out; threes use attempts × shrunk 3P%; negative binomial probabilities for every market
- **Game model:** margin-of-victory Elo for moneyline and spread, recent scoring/allowing for totals
- **Picks board:** BEST / CONSIDER / AVOID tiers with reasons and automatic red flags (role changes, early-season data, injury status, expensive prices)
- **Backtesting:** day-by-day replay of past seasons using only prior information; Brier score and calibration per market
- **Grading:** every pick scored against the box score and the market; record and ROI tracked per tier
- API keys kept out of source code via environment variables

## Tech

Python · SQLite · REST APIs · pandas · nba_api · requests

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

Run it whenever you like; it catches up on everything since the last run.

```bash
python daily.py
```

Each run (free): refreshes box scores for every missed game, grades all pending predictions, shows the biggest scoring and three-point nights you missed, and updates the injury report. It then asks whether you're betting today and shows the credit cost before spending anything: 3 credits for game lines on every game, plus 1 per prop market per game you pick (choose markets in `config.py`).

```bash
python daily.py report [date]   # re-show a day's predictions
python daily.py backtest        # grade the model on past seasons
python daily.py status          # database summary
```

## Roadmap

- [x] Odds collection, vig removal, PA sportsbook filter, line shopping
- [x] Elo game model with backtest
- [x] SQLite data warehouse + daily pipeline
- [x] Player prop model (PTS, REB, AST, 3PM, PRA) with matchups, injuries, and backtesting
- [x] Game-line model (moneyline, spread, total)
- [x] Tiered picks board
- [x] Automatic grading vs. results and market
- [ ] Rest days / back-to-backs and pace in the model
- [ ] LLM parsing of injury and lineup news
- [ ] FastAPI service + play-money odds website, deployed

## Disclaimer

For educational and research purposes. Bet responsibly and only with sportsbooks licensed in your state.
