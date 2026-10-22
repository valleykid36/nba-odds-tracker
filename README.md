# NBA Odds Tracker

A season-long NBA data pipeline and player-prop model. It collects box scores, injury reports, and odds from Pennsylvania-licensed sportsbooks into a local SQLite database, projects player three-pointers made with a matchup- and injury-aware model, and grades every prediction against real results and the betting market.

## Features

- **Data warehouse (SQLite):** multi-season player box scores, game-line and player-prop odds snapshots, daily injury reports, predictions, and graded results
- **Odds collection:** moneylines and player threes props from DraftKings, FanDuel, BetMGM, BetRivers, betPARX, and theScore Bet via [The Odds API](https://the-odds-api.com/), converted to vig-free probabilities
- **Threes model v2:** projected minutes × 3PA rate × shrunk 3P%, weighted toward the current season, adjusted for opponent threes allowed and for teammates newly ruled out, with a negative binomial distribution
- **Backtesting:** day-by-day replay of past seasons using only prior information; reports Brier score and calibration vs. a baseline model
- **Grading:** scores each prediction against the box score and against the market's closing probability; tracks a flat-stake record for model "leans"
- **Game model:** Elo ratings from 10+ seasons of results with self-grading
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

Each run (free): refreshes box scores for every missed game, grades all pending predictions, shows the biggest three-point nights you missed, and updates the injury report. It then asks whether you're betting today; only if you say yes does it spend Odds API credits (1 for game odds + 1 per game you choose).

```bash
python daily.py report [date]   # re-show a day's predictions
python daily.py backtest        # grade the model on past seasons
python daily.py status          # database summary
```

## Roadmap

- [x] Odds collection, vig removal, PA sportsbook filter, line shopping
- [x] Elo game model with backtest
- [x] SQLite data warehouse + daily pipeline
- [x] Threes model v2 with matchups, injuries, and backtesting
- [x] Automatic grading vs. results and market
- [ ] Rest days / back-to-backs and pace in the model
- [ ] Points, rebounds, assists props
- [ ] LLM parsing of injury and lineup news
- [ ] FastAPI service + play-money odds website, deployed

## Disclaimer

For educational and research purposes. Bet responsibly and only with sportsbooks licensed in your state.
