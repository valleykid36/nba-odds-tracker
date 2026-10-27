"""
nba_injury.py — the NBA's official injury report (free, no credits).

The league posts a PDF many times a day (roughly every 15 minutes on game days):
    https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-26_05_30PM.pdf
Teams must list every player who is Out / Doubtful / Questionable / Probable for
tonight, so it is more complete and more official than news sites.

  latest_report_url()   finds the newest PDF (checks back in 15-minute steps)
  parse_report(pdf)     turns the PDF into rows: date, time, matchup, team, player, status, reason
  fetch_official()      both of the above, plus saves every report into injury_history

Check it by hand any time:
    python nba_injury.py          prints the newest report as the program reads it
"""

import io
import re
from datetime import datetime, timedelta

import requests

from config import BROWSER_HEADERS, ET, TEAM_ABBR, player_key, team_abbr

BASE = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{}.pdf"
HEADERS = {**BROWSER_HEADERS, "Accept": "application/pdf,*/*"}   # same CDN family as live scores
STATUSES = ("Out", "Doubtful", "Questionable", "Probable", "Available")
LOOK_BACK_HOURS = 30      # off days have no report; yesterday's last one is still useful

_TEAMS = sorted(set(TEAM_ABBR), key=len, reverse=True)
_HEAD = re.compile(r"^(?P<date>\d{2}/\d{2}/\d{4})\s+")
_TIME = re.compile(r"^(?P<time>\d{1,2}:\d{2}\s*\(ET\))\s+")
_MATCH = re.compile(r"^(?P<matchup>[A-Z]{2,3}\s*@\s*[A-Z]{2,3})\s+")
_PLAYER = re.compile(r"^(?P<player>.+?,.+?)\s+(?P<status>" + "|".join(STATUSES) + r")\b\s*(?P<reason>.*)$")
_SKIP = re.compile(r"^(Injury ?Report:|Page ?\d+ ?of ?\d+|Game ?Date\b)")
# pdfplumber often drops the spaces inside cells ("BrooklynNets", "NOTYETSUBMITTED"),
# so teams are matched with spaces removed and wrapped reason lines are re-attached below.
_TEAMS_NOSPACE = sorted(((t.replace(" ", ""), t) for t in set(TEAM_ABBR)), key=lambda x: len(x[0]), reverse=True)
_SUFFIX = re.compile(r"(?<=[a-z])(Jr\.?|Sr\.?|III|II|IV)$")


def report_name(t):
    """datetime (ET) -> '2026-10-26_05_30PM'"""
    return t.strftime("%Y-%m-%d_%I_%M%p")


def latest_report_url(now=None, look_back_hours=LOOK_BACK_HOURS):
    """Newest report that exists. Usually found on the first or second try."""
    now = now or datetime.now(ET)
    t = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    errors = 0
    for _ in range(int(look_back_hours * 4)):
        url = BASE.format(report_name(t))
        try:
            r = requests.get(url, headers=HEADERS, timeout=8, stream=True)   # headers only
            ok = r.status_code == 200 and "pdf" in r.headers.get("Content-Type", "pdf").lower()
            r.close()
            if ok:
                return url, t
        except requests.RequestException:
            errors += 1
            if errors >= 3:            # offline or blocked: don't spend minutes retrying
                raise
        t -= timedelta(minutes=15)
    return None, None


def _name(last_first):
    """'Jackson Jr., Jaren' -> 'Jaren Jackson Jr.'"""
    if "," not in last_first:
        return last_first.strip()
    last, first = last_first.split(",", 1)
    last = _SUFFIX.sub(r" \1", last.strip())          # 'JacksonJr.' -> 'Jackson Jr.'
    return f"{first.strip()} {last}"


def _spaced(text):
    """'Injury/Illness-RightHamstringStrain' -> 'Injury/Illness-Right Hamstring Strain'"""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)


def parse_lines(lines):
    """Text lines from the PDF -> list of row dicts. Blank date/time/matchup/team carry down.

    A reason that wraps onto two lines is printed around the player's row (part above, part
    below), so loose lines wait in `pending`: they belong to the next player if that player's
    row has no reason of its own, otherwise to the player before.
    """
    rows, cur = [], {"date": "", "time": "", "matchup": "", "team": ""}
    not_submitted, pending = [], []

    def flush_to_previous():
        if pending and rows:
            rows[-1]["reason"] = (rows[-1]["reason"] + " " + " ".join(pending)).strip()
        pending.clear()

    for raw in lines:
        line = " ".join(raw.split())
        if not line or _SKIP.match(line):
            continue
        for key, rx in (("date", _HEAD), ("time", _TIME), ("matchup", _MATCH)):
            m = rx.match(line)
            if m:
                cur[key] = m.group(key).replace(" ", "") if key == "matchup" else m.group(key)
                line = line[m.end():]
                if key == "date":
                    cur.update(time="", matchup="", team="")
        squeezed = line.replace(" ", "")
        for short, full in _TEAMS_NOSPACE:
            if squeezed.startswith(short):
                cur["team"] = team_abbr(full)
                # cut the team name off the original line, however many spaces it had
                n = 0
                for i, ch in enumerate(line):
                    if ch != " ":
                        n += 1
                    if n == len(short):
                        line = line[i + 1:].strip()
                        break
                break
        if "NOTYETSUBMITTED" in line.upper().replace(" ", ""):
            flush_to_previous()
            not_submitted.append(cur["team"])
            continue
        m = _PLAYER.match(line)
        if m and cur["team"]:
            name = _name(m.group("player"))
            reason = _spaced(m.group("reason").strip())
            if pending and not reason:
                reason = " ".join(pending)
                pending.clear()
            else:
                flush_to_previous()
            mm, dd, yy = cur["date"].split("/") if cur["date"] else ("", "", "")
            rows.append(dict(game_date=f"{yy}-{mm}-{dd}" if yy else "", game_time=cur["time"],
                             matchup=cur["matchup"], team=cur["team"], player=name,
                             player_key=player_key(name), status=m.group("status"),
                             reason=reason))
        elif line:
            pending.append(_spaced(line))     # wrapped reason text; placed when the next row arrives
    flush_to_previous()
    return rows, not_submitted


def parse_report(pdf_bytes):
    import pdfplumber
    lines = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            lines += (page.extract_text() or "").splitlines()
    return parse_lines(lines)


def fetch_official():
    """(rows, report label, teams not yet submitted). Empty rows if no report could be read."""
    url, when = latest_report_url()
    if not url:
        return [], None, []
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    rows, missing = parse_report(r.content)
    return rows, when.strftime("%Y-%m-%d %I:%M %p ET"), missing


if __name__ == "__main__":
    url, when = latest_report_url()
    if not url:
        print("No official report found in the last", LOOK_BACK_HOURS, "hours.")
    else:
        print("Newest report:", url)
        data = requests.get(url, headers=HEADERS, timeout=30).content
        rows, missing = parse_report(data)
        for x in rows:
            print(f"{x['game_date']} {x['matchup']:<8} {x['team']:<4} {x['player']:<26} "
                  f"{x['status']:<13} {x['reason']}")
        print(f"\n{len(rows)} players read." + (f"  Not yet submitted: {', '.join(missing)}" if missing else ""))
        if not rows:
            print("Couldn't read any rows; the PDF layout may have changed. ESPN stays as the backup.")
