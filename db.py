"""
db.py — the project's database (a single file: nba.db).

SQLite ships with Python, needs no server, and you can open the file with
"DB Browser for SQLite" (free) to look at your data like a spreadsheet.
"""

import sqlite3

from config import DB_FILE

SCHEMA = """
CREATE TABLE IF NOT EXISTS player_games (
    game_id TEXT, game_date TEXT, season INTEGER,
    player_id INTEGER, player_name TEXT, player_key TEXT,
    team TEXT, opp TEXT, home INTEGER, min REAL,
    pts INTEGER, reb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER, tov INTEGER,
    fgm INTEGER, fga INTEGER, fg3m INTEGER, fg3a INTEGER, ftm INTEGER, fta INTEGER,
    oreb INTEGER, dreb INTEGER,
    PRIMARY KEY (game_id, player_id)
);
CREATE INDEX IF NOT EXISTS ix_pg_player ON player_games(player_key, game_date);
CREATE INDEX IF NOT EXISTS ix_pg_team ON player_games(team, game_date);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    snapshot_utc TEXT, event_id TEXT, tipoff_et TEXT, game_date TEXT,
    away_team TEXT, home_team TEXT, book TEXT,
    away_price INTEGER, home_price INTEGER, away_fair REAL, home_fair REAL
);

CREATE TABLE IF NOT EXISTS prop_snapshots (
    snapshot_utc TEXT, event_id TEXT, tipoff_et TEXT, game_date TEXT,
    home_team TEXT, away_team TEXT, player TEXT, player_key TEXT,
    book TEXT, market TEXT, side TEXT, line REAL, price INTEGER
);
CREATE INDEX IF NOT EXISTS ix_props ON prop_snapshots(game_date, player_key);

CREATE TABLE IF NOT EXISTS injuries (
    snapshot_utc TEXT, snapshot_date TEXT, team TEXT,
    player TEXT, player_key TEXT, status TEXT, comment TEXT, return_date TEXT
);
CREATE INDEX IF NOT EXISTS ix_inj ON injuries(snapshot_date, team);

CREATE TABLE IF NOT EXISTS predictions (
    game_date TEXT, event_id TEXT, player TEXT, player_key TEXT,
    team TEXT, opp TEXT, market TEXT, line REAL,
    proj_min REAL, exp_3pa REAL, pct REAL, mean REAL,
    opp_factor REAL, injury_boost REAL, p_over REAL,
    market_over REAL, books INTEGER,
    best_over_price INTEGER, best_over_book TEXT,
    best_under_price INTEGER, best_under_book TEXT,
    notes TEXT, created_utc TEXT,
    actual INTEGER, result TEXT,
    PRIMARY KEY (game_date, player_key, market, line)
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS game_lines (
    snapshot_utc TEXT, event_id TEXT, tipoff_et TEXT, game_date TEXT,
    home_team TEXT, away_team TEXT, book TEXT, market TEXT,
    side TEXT, point REAL, price INTEGER
);
CREATE INDEX IF NOT EXISTS ix_gl ON game_lines(game_date, event_id);

CREATE TABLE IF NOT EXISTS game_predictions (
    game_date TEXT, event_id TEXT, home TEXT, away TEXT, market TEXT, side TEXT, point REAL,
    model_prob REAL, market_prob REAL, best_price INTEGER, best_book TEXT, books INTEGER,
    pred_margin REAL, pred_total REAL, early_games INTEGER, created_utc TEXT,
    tier TEXT, result TEXT, final TEXT,
    PRIMARY KEY (game_date, event_id, market, side)
);

-- Every injury report ever pulled (official NBA + ESPN), so you can look back at
-- who was listed when. The 'injuries' table above is just the latest one per day.
CREATE TABLE IF NOT EXISTS injury_history (
    report TEXT, fetched_utc TEXT, source TEXT, game_date TEXT, matchup TEXT,
    team TEXT, player TEXT, player_key TEXT, status TEXT, reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_injh ON injury_history(game_date, team, player_key);
CREATE INDEX IF NOT EXISTS ix_injh_report ON injury_history(source, report);

-- DraftKings opening and closing lines from ESPN (free), for closing-line value.
CREATE TABLE IF NOT EXISTS espn_lines (
    game_date TEXT, espn_id TEXT, home TEXT, away TEXT, book TEXT,
    kind TEXT, market TEXT, side TEXT, point REAL, price REAL, fetched_utc TEXT,
    PRIMARY KEY (game_date, espn_id, kind, market, side)
);

-- Milestone calls from hot.py ("Maxey 20+ PTS, 84%"), graded so the model's calibration is tracked.
CREATE TABLE IF NOT EXISTS hot_legs (
    game_date TEXT, player_key TEXT, player TEXT, team TEXT, opp TEXT, stat TEXT, t INTEGER,
    p REAL, price INTEGER, book TEXT, hits INTEGER, n INTEGER, created_utc TEXT,
    result TEXT, actual INTEGER,
    PRIMARY KEY (game_date, player_key, stat, t)
);

-- Parlays suggested by parlay.py (legs stored as JSON), graded leg by leg.
CREATE TABLE IF NOT EXISTS parlays (
    game_date TEXT, name TEXT, legs TEXT, p_hit REAL, price INTEGER, ev REAL, priced INTEGER,
    created_utc TEXT, result TEXT, legs_won INTEGER, legs_graded INTEGER,
    PRIMARY KEY (game_date, name)
);

-- Live scores from NBA.com's free scoreboard (no credits).
CREATE TABLE IF NOT EXISTS live_games (
    game_id TEXT PRIMARY KEY, game_date TEXT, tip_utc TEXT, home TEXT, away TEXT,
    home_pts INTEGER, away_pts INTEGER, status INTEGER, status_text TEXT,
    period INTEGER, clock TEXT, updated_utc TEXT
);
CREATE TABLE IF NOT EXISTS live_box (
    game_id TEXT, player_id INTEGER, game_date TEXT, player_name TEXT, player_key TEXT,
    team TEXT, played INTEGER, min REAL, pts INTEGER, reb INTEGER, ast INTEGER,
    stl INTEGER, blk INTEGER, fg3m INTEGER, fg3a INTEGER, updated_utc TEXT,
    PRIMARY KEY (game_id, player_id)
);
CREATE INDEX IF NOT EXISTS ix_live_box ON live_box(game_date, player_key);

-- Daily snapshots of NBA.com team and shooting stats (one row per day, so the
-- model can later be tested using only what was known on each date).
CREATE TABLE IF NOT EXISTS team_stats (
    snapshot_date TEXT, season INTEGER, team TEXT, gp INTEGER,
    pace REAL, off_rtg REAL, def_rtg REAL, net_rtg REAL,
    opp_pts REAL, opp_fg3m REAL, opp_fg3a REAL, opp_fg3_pct REAL,
    PRIMARY KEY (snapshot_date, team)
);
CREATE TABLE IF NOT EXISTS player_shooting (
    snapshot_date TEXT, season INTEGER, player_key TEXT, player TEXT, team TEXT, gp INTEGER,
    cs_fg3m REAL, cs_fg3a REAL, cs_fg3_pct REAL,
    pu_fg3m REAL, pu_fg3a REAL, pu_fg3_pct REAL,
    PRIMARY KEY (snapshot_date, player_key)
);
"""


def get_meta(con, key, default=None):
    r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_meta(con, key, value):
    con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(value)))
    con.commit()


NEW_COLUMNS = [  # added after the first version; older databases get them automatically
    ("predictions", "cur_games", "INTEGER"),
    ("predictions", "role_change", "REAL"),
    ("predictions", "tier", "TEXT"),
    ("predictions", "pick_side", "TEXT"),
    ("predictions", "p_blend", "REAL"),        # win% of the picked side after blending with market
    ("predictions", "ev", "REAL"),             # expected value per unit at the best price
    ("predictions", "b2b", "INTEGER"),         # player's team on 2nd night of a back-to-back
    ("game_predictions", "p_blend", "REAL"),
    ("game_predictions", "ev", "REAL"),
    ("game_predictions", "rest_note", "TEXT"),
    ("game_predictions", "close_point", "REAL"),   # closing line (ESPN / DraftKings)
    ("game_predictions", "close_price", "REAL"),
    ("game_predictions", "clv_pts", "REAL"),       # points better (+) or worse than the close
    ("game_predictions", "clv_ev", "REAL"),        # value per unit if the close is the true price
    ("injuries", "source", "TEXT"),            # "NBA" (official report) or "ESPN"
]


def connect():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for table, col, kind in NEW_COLUMNS:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
    con.commit()
    return con
