"""
config.py — settings and lookups shared by the whole pipeline.
Change numbers here to tune the model; everything else reads from this file.
"""

import unicodedata
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DB_FILE = "nba.db"

# ---------- Seasons ----------
# NBA.com labels. The LAST one is treated as the current season.
SEASONS = ["2022-23", "2023-24", "2024-25", "2025-26", "2026-27"]
CURRENT_SEASON = 2026          # start year of the current season
# Next season: add "2027-28" to SEASONS and set CURRENT_SEASON = 2027.

# ---------- Sportsbooks licensed in Pennsylvania (The Odds API keys) ----------
PA_BOOKS = "draftkings,fanduel,betmgm,betrivers,betparx,espnbet,williamhill_us,fanatics"

# ---------- Which picks to pull (each prop market = 1 credit per game) ----------
# Remove markets you don't care about to save credits.
PULL_PROP_MARKETS = [
    "player_points", "player_rebounds", "player_assists",
    "player_threes", "player_points_rebounds_assists",
]
PULL_GAME_LINES = True   # moneyline + spread + total for ALL of today's games = 3 credits

# Each prop market: which box-score stats it adds up, and its "randomness" (NB r; lower = streakier).
PROP_MARKETS = {
    "player_points":   {"label": "PTS", "stats": ["pts"], "r": 9.0},
    "player_rebounds": {"label": "REB", "stats": ["reb"], "r": 12.0},
    "player_assists":  {"label": "AST", "stats": ["ast"], "r": 10.0},
    "player_threes":   {"label": "3PM", "stats": ["fg3m"], "r": 12.0},
    "player_points_rebounds_assists": {"label": "PRA", "stats": ["pts", "reb", "ast"], "r": 10.0},
    "player_points_rebounds": {"label": "P+R", "stats": ["pts", "reb"], "r": 10.0},
    "player_points_assists":  {"label": "P+A", "stats": ["pts", "ast"], "r": 10.0},
    "player_rebounds_assists": {"label": "R+A", "stats": ["reb", "ast"], "r": 11.0},
    "player_blocks":   {"label": "BLK", "stats": ["blk"], "r": 6.0},
    "player_steals":   {"label": "STL", "stats": ["stl"], "r": 8.0},
}

# ---------- Player model settings ----------
MIN_MINUTES = 10        # ignore games where the player played less than this
PRIOR_MINUTES = 250     # how much last season's per-minute rates count (minutes of evidence)
PRIOR_GAMES_MIN = 3     # how much last season's minutes count (in games of evidence)
PCT_PRIOR_ATTEMPTS = 200  # threes only: pull 3P% toward league average by this many attempts
LEAGUE_3P_PCT = 0.36
OPP_WINDOW = 15         # opponent's last N games used for "allowed" stats
OPP_WEIGHT = 0.5        # 0 = ignore opponent, 1 = fully trust the opponent ratio
INJURY_SHARE = 0.8      # share of an injured teammate's production that gets redistributed
NB_DISPERSION = 12.0    # threes "r" (kept for the threes backtest)
LEAN_THRESHOLD = 0.05   # a "lean" = model and market disagree by at least 5%

# ---------- Game model settings ----------
ELO_K = 20
ELO_HOME = 70           # Elo points for home court
ELO_REVERT = 0.25       # pull ratings 25% toward average between seasons
ELO_PER_POINT = 28      # ~28 Elo points = 1 point of margin
MARGIN_SD = 12.5        # how much final margins vary around the prediction
TOTAL_SD = 18.0         # how much final totals vary around the prediction
TEAM_WINDOW = 15        # recent games used for team scoring/allowing

# ---------- Picks board tiers ----------
BEST_EDGE = 0.07        # BEST: model beats market by 7%+ ...
BEST_MIN_PRICE = -160   # ... at a price no worse than -160 ...
BEST_MIN_GAMES = 2      # ... with at least this many games this season
CONSIDER_EDGE = 0.04    # CONSIDER: 4%+ edge that doesn't meet every BEST rule
ROLE_CHANGE_FLAG = 0.20 # stat rate per minute up/down 20%+ vs last season = role changed

# ---------- Names ----------
TEAM_ABBR = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "LA Clippers": "LAC", "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP", "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR", "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}


def team_abbr(full_name):
    return TEAM_ABBR.get(full_name, full_name)


def player_key(name):
    """'Nikola Jokić' -> 'nikola jokic'; drops Jr./III so names match across sources."""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    n = n.replace(".", "").replace("'", "").replace("-", " ")
    for suffix in (" jr", " sr", " iii", " ii", " iv"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return " ".join(n.split())


def implied(american):
    return -american / (-american + 100) if american < 0 else 100 / (american + 100)


def payout(american):
    """Profit on a 1-unit winning bet."""
    return american / 100 if american > 0 else 100 / -american
