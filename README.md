# NBA Odds Tracker

A Python pipeline that collects live NBA moneyline odds from Pennsylvania-licensed sportsbooks, converts them into vig-free win probabilities, and builds a timestamped odds history for modeling and backtesting.

This is the first stage of a larger project: a deployed prediction API that compares a statistical model's win probabilities to the betting market to identify value.

## Features

- Pulls moneylines from DraftKings, FanDuel, BetMGM, BetRivers, betPARX, and theScore Bet via [The Odds API](https://the-odds-api.com/)
- Converts American odds to implied probability and removes the sportsbook margin (vig)
- Computes a cross-book consensus probability and finds the best available price for each side (line shopping)
- Appends every snapshot to a CSV, building a history of opening, intraday, and closing lines
- Pulls schedule data from the [balldontlie](https://www.balldontlie.io/) NBA API
- API keys kept out of source code via environment variables

## Tech

Python · REST APIs · requests · python-dotenv · CSV data pipeline

## Setup

```bash
git clone https://github.com/<your-username>/nba-odds-tracker.git
cd nba-odds-tracker
pip install -r requirements.txt
cp .env.example .env   # Windows: copy .env.example .env
# add your API keys to .env
```

## Usage

```bash
python odds_logger.py    # log PA sportsbook odds + show today's market
python nba_starter.py    # today's games + market probabilities
```

## Roadmap

- [x] Odds collection and vig-free probability conversion
- [x] PA-licensed sportsbook filter and line shopping
- [ ] Scheduled runs (Task Scheduler / GitHub Actions)
- [ ] Join odds history with final scores
- [ ] Baseline model (Elo / logistic regression) and backtest vs. closing line
- [ ] FastAPI service + Docker deployment
- [ ] LLM-based injury-news parsing

## Disclaimer

For educational and research purposes. Bet responsibly and only with sportsbooks licensed in your state.
