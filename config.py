"""
config.py — settings and lookups shared by the whole pipeline.
Change numbers here to tune the model; everything else reads from this file.
"""

import unicodedata
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DB_FILE = "nba.db"

# NBA.com's CDN (Akamai) returns 403 to requests that don't look like a browser.
# A custom User-Agent or an Origin header on a plain GET is enough to get blocked.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nba.com/",
}

# ---------- Seasons ----------
# NBA.com labels. The LAST one is treated as the current season.
SEASONS = ["2022-23", "2023-24", "2024-25", "2025-26", "2026-27"]
CURRENT_SEASON = 2026          # start year of the current season
# Next season: add "2027-28" to SEASONS and set CURRENT_SEASON = 2027.

# ---------- Sportsbooks licensed in Pennsylvania (The Odds API keys) ----------
PA_BOOKS = "draftkings,fanduel,betmgm,betrivers,betparx,espnbet,williamhill_us,fanatics"

# ---------- Credit budget (The Odds API free plan) ----------
# Cost = markets x regions. Our 8 books count as 1 region, so:
#   game lines (moneyline + spread + total, ALL games) = 3 credits
#   player props = 1 credit per market per game
# Free calls (0 credits): the list of today's games, and the credit balance check.
MONTHLY_CREDITS = 500     # free plan; resets on the 1st of every month
CREDIT_RESERVE = 15       # never plan to spend below this
PROP_REFRESH_HOURS = 4    # a game's props pulled within this many hours are reused, not re-bought
LINES_REFRESH_HOURS = 3   # same for game lines

# ---------- Free NBA.com data ----------
LIVE_REFRESH_SECONDS = 120   # the app refreshes live scores this often while games are on (free)

# ---------- Which picks to pull (each prop market = 1 credit per game) ----------
# Fewer markets = more games per month. Threes and points are where the model is
# strongest; add others back once the Record tab shows they earn their credits.
PULL_PROP_MARKETS = ["player_threes", "player_points"]
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

# ---------- Game model settings ----------
ELO_K = 20
ELO_HOME = 70           # Elo points for home court
ELO_REVERT = 0.25       # pull ratings 25% toward average between seasons
ELO_PER_POINT = 28      # ~28 Elo points = 1 point of margin
MARGIN_SD = 12.5        # how much final margins vary around the prediction
TOTAL_SD = 18.0         # how much final totals vary around the prediction
TEAM_WINDOW = 15        # recent games used for team scoring/allowing
REST_B2B_POINTS = 1.5   # a team on the 2nd night of a back-to-back loses ~this much margin

# ---------- Picks: how "good" is decided ----------
# The sportsbooks are usually right. A big gap between model and market is more often a
# model blind spot than free money, so the win chance used for picks is a blend:
#     win% = trust x model + (1 - trust) x market
# "trust" starts at MODEL_TRUST and is re-learned per market from your graded lines
# (every line the model checked counts, not just picks), so it improves as data grows.
MODEL_TRUST = 0.5
TRUST_PRIOR_LINES = 300  # graded lines needed before the learned trust outweighs the start
# A pick is judged by expected value at the best PA price: EV = win% x payout - lose%.
BEST_EV = 0.05          # GOOD pick: +5% EV or better, no red flags
CONSIDER_EV = 0.025     # LEAN: +2.5% EV, or a GOOD pick with one red flag (hidden by default)
BEST_MIN_PRICE = -160   # pricier than this is a red flag
BEST_MIN_GAMES = 2      # fewer games this season than this is a red flag
MAX_PICKS_PER_PLAYER = 1  # Over points + Over PRA on the same player is one bet, not two
MAX_BEST_PICKS = 5      # only the top few by EV stay BEST; the rest drop to CONSIDER
ROLE_CHANGE_FLAG = 0.20 # stat rate per minute up/down 20%+ vs last season = role changed

# ---------- Pick mode ----------
# "confidence": rank by the chance the pick HITS, backed by several factors agreeing.
# "value":      the old way, rank by EV (favors long shots like a +900 moneyline).
PICK_MODE = "confidence"
# A pick is only considered if BOTH the model and the sportsbooks make it the likelier side.
CONF_TOP = 0.60          # TOP play: 60%+ chance to hit (blended model + books)
CONF_LEAN = 0.55         # LEAN: 55%+
TOP_SIGNAL_SHARE = 0.6   # TOP play: at least 60% of its factors agree (4 of 6 for props, 3 of 5 for games)
LEAN_SIGNAL_SHARE = 0.5  # LEAN: at least half
TOP_MIN_EV = 0.0         # TOP plays can't be overpriced: the hit chance must justify the price
LEAN_MIN_EV = -0.03      # LEAN plays may be a touch overpriced, not -1100 favorites that lose money even when they win
LINE_MOVE = {"spreads": 1.0, "totals": 1.5, "h2h": 0.03}   # open -> now move that counts as real
LINE_MOVE_FLAG = {"spreads": 2.0, "totals": 3.0, "h2h": 0.06}  # this much AGAINST a pick = red flag
INJURY_EDGE_MIN = 20     # game picks: regular minutes missing, one team vs the other, that counts
INJURY_FLAG_MIN = 45     # this many more minutes missing on YOUR team = red flag (model can't see it)
INJURY_TOTAL_MIN = 50    # both teams together missing this many minutes leans Under
MAX_DISAGREE = 0.18      # model and books more than 18 points apart = probably a model blind spot
HIT_RATE_GAMES = 10      # "recent form" looks at the player's last 10 games (10+ minutes)
HIT_RATE_NEEDED = 0.6    # ...and wants this side to have hit in 6 of them
MIN_MINUTES_OVER = 24    # an Over needs a player projected for real minutes

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
