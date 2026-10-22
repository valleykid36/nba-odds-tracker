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
