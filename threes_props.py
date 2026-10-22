"""
threes_props.py — Project API LLM: player 3-pointers-made props vs. history.

For today's games:
  1. Gets the game list from The Odds API (FREE, no credits).
  2. Gets "player threes made" lines from PA sportsbooks
     (COSTS 1 credit per game; you'll be asked before it spends them).
  3. For each line (e.g. Anthony Edwards Over 2.5 threes), estimates the chance
     from the player's recent games and compares it to the sportsbook's price.
  4. Logs every line to props_history.csv so you can grade them later.

Needs: player_games.csv (run player_stats.py first) and ODDS_API_KEY in .env.
Run:   python threes_props.py
"""

import csv
import math
import os
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()
ODDS_KEY = os.getenv("ODDS_API_KEY")
ET = ZoneInfo("America/New_York")
BASE = "https://api.the-odds-api.com/v4/sports/basketball_nba"
PA_BOOKS = "draftkings,fanduel,betmgm,betrivers,betparx,espnbet,williamhill_us,fanatics"

RECENT_GAMES = 20     # how many recent games to base each player's estimate on
MIN_GAMES = 8         # skip players with less history than this
MIN_MINUTES = 10      # ignore games where the player barely played

HISTORY_FIELDS = [
    "snapshot_utc", "game_id", "tipoff_et", "matchup", "player", "book", "side",
    "line", "price", "market_fair", "model_prob", "recent_avg", "hit_rate_last10",
]


# ---------- helpers ----------
def norm(name):
    """'Nikola Jokić' -> 'nikola jokic'; also drops Jr./III so names match across sources."""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    n = n.replace(".", "").replace("'", "")
    for suffix in (" jr", " sr", " iii", " ii", " iv"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return n.strip()


def implied(american):
    return -american / (-american + 100) if american < 0 else 100 / (american + 100)


def poisson_at_least(k, mean):
    """Chance of making at least k threes if makes follow a Poisson distribution."""
    return 1 - sum(math.exp(-mean) * mean ** i / math.factorial(i) for i in range(k))


def parse_minutes(m):
    try:
        return float(str(m).split(":")[0])
    except ValueError:
        return 0.0


# ---------- player history ----------
def load_history():
    if not os.path.exists("player_games.csv"):
        raise SystemExit("player_games.csv not found. Run: python player_stats.py")
    games = defaultdict(list)
    with open("player_games.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if parse_minutes(r["MIN"]) >= MIN_MINUTES:
                games[norm(r["PLAYER_NAME"])].append((r["GAME_DATE"], int(float(r["FG3M"]))))
    for k in games:
        games[k].sort()
    return games


def player_profile(history, player):
    g = history.get(norm(player), [])
    if len(g) < MIN_GAMES:
        return None
    recent = [m for _, m in g[-RECENT_GAMES:]]
    last10 = [m for _, m in g[-10:]]
    return {"recent": recent, "last10": last10, "avg": sum(recent) / len(recent)}


# ---------- odds ----------
def todays_events():
    resp = requests.get(f"{BASE}/events", params={"apiKey": ODDS_KEY}, timeout=15)  # free
    resp.raise_for_status()
    today = datetime.now(ET).date()
    out = []
    for ev in resp.json():
        tip = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")).astimezone(ET)
        if tip.date() == today and tip > datetime.now(ET):
            out.append((tip, ev))
    return sorted(out, key=lambda x: x[0])


def threes_lines(event_id):
    resp = requests.get(
        f"{BASE}/events/{event_id}/odds",
        params={"apiKey": ODDS_KEY, "bookmakers": PA_BOOKS,
                "markets": "player_threes", "oddsFormat": "american"},
        timeout=15,
    )
    resp.raise_for_status()
    print(f"   [credits left: {resp.headers.get('x-requests-remaining')}]")
    return resp.json()


def main():
    if not ODDS_KEY:
        raise SystemExit("Missing ODDS_API_KEY in .env")
    history = load_history()
    events = todays_events()
    if not events:
        print("No upcoming NBA games left today.")
        return

    print("Today's games:")
    for i, (tip, ev) in enumerate(events, 1):
        print(f"  {i}. {tip:%I:%M %p}  {ev['away_team']} @ {ev['home_team']}")
    pick = input(f"\nCheck which games? (e.g. 1,2 or 'all'; costs 1 credit per game): ").strip()
    if pick.lower() == "all":
        chosen = events
    else:
        idx = {int(x) for x in pick.replace(" ", "").split(",") if x.isdigit()}
        chosen = [e for i, e in enumerate(events, 1) if i in idx]

    snapshot = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log_rows, results = [], []

    for tip, ev in chosen:
        matchup = f"{ev['away_team']} @ {ev['home_team']}"
        print(f"\nFetching threes lines: {matchup}")
        data = threes_lines(ev["id"])

        # Group: (player, line) -> {"Over": [(price, book)], "Under": [...]}
        lines = defaultdict(lambda: {"Over": [], "Under": []})
        for book in data.get("bookmakers", []):
            for mkt in book["markets"]:
                for o in mkt["outcomes"]:
                    if o["name"] in ("Over", "Under") and "point" in o:
                        lines[(o["description"], o["point"])][o["name"]].append(
                            (o["price"], book["title"]))

        for (player, line), sides in lines.items():
            prof = player_profile(history, player)
            need = math.floor(line) + 1                     # Over 2.5 -> need 3+
            p_over = poisson_at_least(need, prof["avg"]) if prof else None
            hit10 = (sum(m >= need for m in prof["last10"]) / len(prof["last10"])) if prof else None

            # Market's fair Over probability: average across books that post BOTH sides.
            fair = []
            by_book = defaultdict(dict)
            for side in ("Over", "Under"):
                for price, b in sides[side]:
                    by_book[b][side] = price
            for b, s in by_book.items():
                if "Over" in s and "Under" in s:
                    po, pu = implied(s["Over"]), implied(s["Under"])
                    fair.append(po / (po + pu))
            market_over = sum(fair) / len(fair) if fair else None

            for side in ("Over", "Under"):
                for price, b in sides[side]:
                    mf = (market_over if side == "Over" else 1 - market_over) if market_over else None
                    mp = (p_over if side == "Over" else 1 - p_over) if p_over is not None else None
                    log_rows.append({
                        "snapshot_utc": snapshot, "game_id": ev["id"],
                        "tipoff_et": tip.strftime("%Y-%m-%d %H:%M"), "matchup": matchup,
                        "player": player, "book": b, "side": side, "line": line, "price": price,
                        "market_fair": round(mf, 4) if mf else "",
                        "model_prob": round(mp, 4) if mp is not None else "",
                        "recent_avg": round(prof["avg"], 2) if prof else "",
                        "hit_rate_last10": round(hit10, 2) if hit10 is not None else "",
                    })

            if prof and market_over is not None:
                best_over = max(sides["Over"], default=None)
                best_under = max(sides["Under"], default=None)
                gap = p_over - market_over
                results.append((abs(gap), player, line, prof, hit10, p_over, market_over,
                                gap, best_over, best_under, matchup))

    new_file = not os.path.exists("props_history.csv")
    with open("props_history.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(log_rows)
    print(f"\nSaved {len(log_rows)} prop prices to props_history.csv")

    print("\n=== Threes made: model vs. market (biggest disagreements first) ===")
    print("Model = Poisson estimate from the player's last "
          f"{RECENT_GAMES} games (10+ min).\n")
    for _, player, line, prof, hit10, p_over, mkt, gap, bo, bu, matchup in sorted(results, reverse=True)[:20]:
        lean = "Over" if gap > 0 else "Under"
        best = bo if lean == "Over" else bu
        best_txt = f"{best[0]:+d} ({best[1]})" if best else "n/a"
        print(f"{player} — {line} threes   [{matchup}]")
        print(f"   avg {prof['avg']:.1f}/game, hit {lean.lower()} {line} in "
              f"{hit10 if lean == 'Over' else 1 - hit10:.0%} of last 10")
        print(f"   model Over {p_over:.0%} vs market Over {mkt:.0%}  ->  "
              f"leans {lean} by {abs(gap):.0%}, best price {best_txt}\n")

    print("Reminder: this model doesn't know about injuries, minutes changes, matchups,")
    print("or new roles. A big gap often means the book knows something it doesn't.")


if __name__ == "__main__":
    main()
