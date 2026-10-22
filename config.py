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

# ---------- Threes model settings ----------
MIN_MINUTES = 10        # ignore games where the player played less than this
PRIOR_MINUTES = 250     # how much last season's 3PA rate counts (in minutes of evidence)
PRIOR_GAMES_MIN = 3     # how much last season's minutes count (in games of evidence)
PCT_PRIOR_ATTEMPTS = 200  # pull 3P% toward league average by this many attempts
LEAGUE_3P_PCT = 0.36
OPP_WINDOW = 15         # opponent's last N games used for "threes allowed"
OPP_WEIGHT = 0.5        # 0 = ignore opponent, 1 = fully trust the opponent ratio
INJURY_SHARE = 0.8      # share of an injured teammate's 3PA that gets redistributed
NB_DISPERSION = 12.0    # negative binomial "r"; lower = more random games. Tune with backtest.
LEAN_THRESHOLD = 0.05   # a "lean" = model and market disagree by at least 5%

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
