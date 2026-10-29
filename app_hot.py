"""
app_hot.py — the "Hot players" and "Parlays" tabs of the desktop app (see app.py).

Hot players   every rotation player playing that day, sortable by heat. Pick a stat at the top.
              Click a player for his last 10 games and his milestone ladder (season X/N, streak,
              chance tonight, price if pulled).
Parlays       the day's suggested parlays with each leg, chance to hit, payout and result,
              plus the parlay record and how well the milestone model is calibrated.
"""

import json
import tkinter as tk
from tkinter import ttk

STAT_NAMES = {"PTS": "pts", "3PM": "fg3m", "REB": "reb", "AST": "ast", "PRA": "pra"}
HOT_COLOR, COLD_COLOR = "#ff8a3d", "#6fb7ff"


def _text(app, parent, C, FONT):
    t = tk.Text(parent, bg=C["panel2"], fg=C["text"], relief="flat", wrap="word", font=(FONT, 10),
                padx=14, pady=12, highlightthickness=0, cursor="arrow")
    t.tag_configure("h", font=(FONT, 13, "bold"))
    t.tag_configure("sub", foreground=C["muted"])
    t.tag_configure("label", foreground=C["muted"], font=(FONT, 9, "bold"))
    t.tag_configure("good", foreground=C["best"])
    t.tag_configure("warn", foreground=C["consider"])
    t.tag_configure("bad", foreground=C["avoid"])
    t.tag_configure("hot", foreground=HOT_COLOR)
    t.tag_configure("cold", foreground=COLD_COLOR)
    mono = ("Consolas", 10) if FONT == "Segoe UI" else ("Menlo", 10) if FONT == "Helvetica Neue" else ("DejaVu Sans Mono", 9)
    for name, color in (("mono", C["text"]), ("monogood", C["best"]), ("monowarn", C["consider"]),
                        ("monosub", C["muted"]), ("monolabel", C["muted"])):
        t.tag_configure(name, font=mono, foreground=color)
    return t


def _write(t, parts):
    t.configure(state="normal")
    t.delete("1.0", "end")
    for text, tag in parts:
        t.insert("end", text, tag)
    t.configure(state="disabled")


# ======================= Hot players =======================
def build_hot_tab(app, C, FONT):
    f = ttk.Frame(app.nb, style="Panel.TFrame", padding=12)
    app.nb.add(f, text="Hot players")
    bar = ttk.Frame(f, style="Panel.TFrame")
    bar.pack(fill="x", pady=(0, 8))
    app.hot_lbl = ttk.Label(bar, style="Panel.TLabel", font=(FONT, 11, "bold"))
    app.hot_lbl.pack(side="left")
    app.hot_stat = tk.StringVar(value="PTS")
    app.hot_only = tk.BooleanVar(value=False)
    app.hot_perfect = tk.BooleanVar(value=False)
    ttk.Checkbutton(bar, text="Every game this season", variable=app.hot_perfect,
                    style="Panel.TCheckbutton", command=lambda: fill_hot_tree(app)).pack(side="right")
    ttk.Checkbutton(bar, text="🔥 only", variable=app.hot_only, style="Panel.TCheckbutton",
                    command=lambda: fill_hot_tree(app)).pack(side="right", padx=12)
    box = ttk.Combobox(bar, textvariable=app.hot_stat, values=list(STAT_NAMES), width=6, state="readonly")
    box.pack(side="right")
    box.bind("<<ComboboxSelected>>", lambda _: fill_hot_tree(app))
    ttk.Label(bar, text="Stat", style="PanelMuted.TLabel").pack(side="right", padx=(0, 6))

    pane = ttk.Frame(f, style="Panel.TFrame")
    pane.pack(fill="both", expand=True)
    wrap, app.hot_tree = app._tree(pane, [
        ("who", "PLAYER", 160, "w"), ("team", "TEAM", 96, "w"), ("heat", "HEAT", 72, "center"),
        ("l5", "LAST 5", 64, "e"), ("normal", "NORMAL", 70, "e"), ("floor", "FLOOR", 56, "e"),
        ("streak", "SEASON STREAK", 140, "w"), ("best", "SAFEST TONIGHT", 120, "w"),
        ("last", "LAST GAMES", 120, "w")])
    wrap.pack(side="left", fill="both", expand=True)
    app.hot_tree.tag_configure("hot", foreground=HOT_COLOR)
    app.hot_tree.tag_configure("cold", foreground=COLD_COLOR)
    for col, key in (("heat", lambda c, s: -s["heat"]), ("l5", lambda c, s: -s["l5"]),
                     ("normal", lambda c, s: -s["normal"]), ("floor", lambda c, s: -s["floor"]),
                     ("who", lambda c, s: c["name"])):
        app.hot_tree.heading(col, command=lambda k=key: _sort_hot(app, k))
    app.hot_tree.bind("<<TreeviewSelect>>", lambda _: show_hot_detail(app))

    side = ttk.Frame(pane, style="Panel.TFrame", width=430)
    side.pack(side="right", fill="y", padx=(12, 0))
    side.pack_propagate(False)
    app.hot_detail = _text(app, side, C, FONT)
    app.hot_detail.pack(fill="both", expand=True)
    app.hot_cards, app.hot_rows, app.hot_sort = [], [], None


def _sort_hot(app, key):
    app.hot_sort = key
    fill_hot_tree(app)


def load_hot(con, day):
    """(cards, prices, error) for the Hot players tab. Runs on the background thread."""
    from hot import board, milestone_prices
    try:
        return board(con, day), milestone_prices(con, day), None
    except Exception as e:
        return [], {}, e


def fill_hot(app, cards, prices, err=None):
    app.hot_cards, app.hot_prices = cards, prices
    if err:
        app.hot_tree.delete(*app.hot_tree.get_children())
        app.hot_lbl.configure(text=f"Couldn't build the hot board: {err}")
        return
    fill_hot_tree(app)


def fill_hot_tree(app):
    from hot import LABEL
    stat = STAT_NAMES[app.hot_stat.get()]
    t = app.hot_tree
    t.delete(*t.get_children())
    rows = []
    for c in app.hot_cards:
        s = c["stats"].get(stat)
        if not s:
            continue
        if app.hot_only.get() and s["heat"] < 0.15:
            continue
        perfect = [x for x in s["ladder"] if x["hits"] == x["n"]]
        if app.hot_perfect.get() and not perfect:
            continue
        rows.append((c, s, perfect))
    key = app.hot_sort or (lambda c, s: -s["mean"])
    rows.sort(key=lambda r: key(r[0], r[1]))
    app.hot_rows = rows
    for i, (c, s, perfect) in enumerate(rows):
        lab = LABEL[stat]
        if perfect:
            streak = f"{perfect[-1]['t']}+ in all {perfect[-1]['n']}"
        else:
            good = [x for x in s["ladder"] if x["hits"] / x["n"] >= 0.6]
            near = good[-1] if good else max(s["ladder"], key=lambda x: (x["hits"], -x["t"]))
            streak = f"{near['t']}+ in {near['hits']} of {near['n']}" if near["hits"] else "—"
        safe = [x for x in s["ladder"] if x["p"] >= 0.75]
        best = f"{safe[-1]['t']}+ {lab}  {safe[-1]['p']:.0%}" if safe else "—"
        heat = s["heat"]
        heat_txt = ("🔥 " if heat >= 0.15 else "❄ " if heat <= -0.15 else "") + (f"{heat:+.0%}" if c["games"] >= 3 else "new")
        tag = "muted" if c["out"] else "hot" if heat >= 0.15 else "cold" if heat <= -0.15 else ""
        name = c["name"] + (f"  ({c['status']})" if c["status"] else "")
        t.insert("", "end", iid=str(i), tags=(tag,), values=(
            name, f"{c['team']} {'vs' if c['home'] else '@'} {c['opp']}", heat_txt, f"{s['l5']:.1f}",
            f"{s['normal']:.1f}", s["floor"], streak, best, ", ".join(map(str, s["last"][:8]))))
    n_hot = sum(1 for c in app.hot_cards if c.get("heat", 0) >= 0.15 and not c["out"])
    app.hot_lbl.configure(text=(f"{len(rows)} players · {n_hot} 🔥 hot on points · click a column to sort"
                                if app.hot_cards else "No games found for this day. Click Update (free)."))
    if rows:
        t.selection_set("0")
    else:
        _write(app.hot_detail, [("Nothing to show.", "sub")])


def show_hot_detail(app):
    from hot import LABEL
    sel = app.hot_tree.selection()
    if not sel or not app.hot_rows:
        return
    c, s, _ = app.hot_rows[int(sel[0])]
    stat = STAT_NAMES[app.hot_stat.get()]
    lab = LABEL[stat]
    heat = s["heat"]
    parts = [(c["name"] + "\n", "h"),
             (f"{c['team']} {'vs' if c['home'] else '@'} {c['opp']} · {c['games']} games · "
              f"{c['min_avg']:.0f} min (last 3: {c['min_l3']:.0f})\n", "sub")]
    if c["status"]:
        parts.append((f"Injury report: {c['status']}\n", "bad" if c["out"] else "warn"))
    parts += [("\n" + lab + "\n", "label"),
              (f"Season {s['season']:.1f} · last 5 {s['l5']:.1f} · normal {s['normal']:.1f}"
               + (f" · last season {s['prior']:.1f}" if s["prior"] is not None else "") + "\n", ""),
              (("🔥 HOT " if heat >= 0.15 else "❄ COLD " if heat <= -0.15 else "Steady ") + f"{heat:+.0%} vs normal\n",
               "hot" if heat >= 0.15 else "cold" if heat <= -0.15 else "sub"),
              (f"Tonight's expected {lab}: {s['mean']:.1f} (recent form counted, not chased)\n", "sub"),
              ("\nLAST GAMES (newest first)\n", "label"),
              (", ".join(map(str, s["last"])) + "\n", ""),
              ("\nMILESTONES\n", "label"),
              (f"{'':<8}{'SEASON':>6}{'STREAK':>7}{'TONIGHT':>8}\n", "monolabel")]
    for x in s["ladder"]:
        price = app.hot_prices.get((c["key"], stat, x["t"]))
        pr = f"  {price[0]:+d}" if price else ""
        tag = "monogood" if x["p"] >= 0.8 else "monowarn" if x["p"] >= 0.6 else "monosub"
        parts.append((f"{str(x['t']) + '+ ' + lab:<8}{str(x['hits']) + '/' + str(x['n']):>6}{x['streak']:>7}"
                      f"{x['p']:>8.0%}{pr}\n", tag))
    parts.append(("\nTonight's chance comes from the model tested on three past seasons "
                  "(when it said 80–90%, 85–89% hit). Streaks are shown for context; the model "
                  "already counts recent form at its tested weight.", "sub"))
    _write(app.hot_detail, parts)


# ======================= Parlays =======================
def build_parlay_tab(app, C, FONT):
    f = ttk.Frame(app.nb, style="Panel.TFrame", padding=12)
    app.nb.add(f, text="Parlays")
    app.parlay_lbl = ttk.Label(f, style="Panel.TLabel", font=(FONT, 11, "bold"))
    app.parlay_lbl.pack(anchor="w", pady=(0, 8))
    pane = ttk.Frame(f, style="Panel.TFrame")
    pane.pack(fill="both", expand=True)
    wrap, app.parlay_tree = app._tree(pane, [
        ("pick", "PARLAY / LEG", 300, "w"), ("win", "CHANCE", 70, "e"), ("price", "PAYS", 80, "e"),
        ("book", "BOOK", 90, "w"), ("ev", "EV", 70, "e"), ("why", "WHY", 220, "w"),
        ("res", "RESULT", 80, "center")])
    wrap.pack(side="left", fill="both", expand=True)
    app.parlay_tree.tag_configure("head", font=(FONT, 10, "bold"))
    app.parlay_tree.tag_configure("W", foreground=C["win"])
    app.parlay_tree.tag_configure("L", foreground=C["loss"])

    side = ttk.Frame(pane, style="Panel.TFrame", width=330)
    side.pack(side="right", fill="y", padx=(12, 0))
    side.pack_propagate(False)
    app.parlay_side = _text(app, side, C, FONT)
    app.parlay_side.pack(fill="both", expand=True)
    ttk.Button(side, text="Copy selected parlay", command=lambda: _copy_parlay(app)).pack(fill="x", pady=(8, 0))
    app.parlay_rows = {}


def load_parlays(con, day):
    """Parlays for the day with each leg's outcome, the record and calibration (background thread)."""
    from hot import calibration
    from parlay import build, leg_outcome, record
    rows = con.execute("SELECT * FROM parlays WHERE game_date=? ORDER BY priced DESC, p_hit DESC", (day,)).fetchall()
    parlays = [(r["name"], json.loads(r["legs"]), r["p_hit"], r["price"], r["ev"], r["priced"], r["result"])
               for r in rows]
    if not parlays:
        try:
            parlays = [(n, ls, p, pr, ev, priced, None) for n, ls, p, pr, ev, priced in build(con, day)]
        except Exception:
            parlays = []
    outcomes = {(i, j): leg_outcome(con, day, l) for i, x in enumerate(parlays) for j, l in enumerate(x[1])}
    return parlays, outcomes, record(con), calibration(con)


def fill_parlays(app, data):
    parlays, outcomes, rec, cal = data
    t = app.parlay_tree
    t.delete(*t.get_children())
    app.parlay_rows = {}
    for i, (name, ls, p, price, ev, priced, result) in enumerate(parlays):
        pid = f"p{i}"
        res = {"W": "✓ WON", "L": "✗ LOST", "VOID": "void"}.get(result, "")
        t.insert("", "end", iid=pid, open=True, tags=("head", result or ""), values=(
            name, f"{p:.0%}", f"{price:+d}", "", f"{ev:+.1%}" if priced else "fair", f"{len(ls)} legs", res))
        app.parlay_rows[pid] = (name, ls, p, price)
        for j, l in enumerate(ls):
            out = outcomes.get((i, j))
            lr = "" if not out else ("✓" if out[0] == "W" else "✗" if out[0] == "L" else "DNP") + \
                (f" {out[1]}" if out[1] is not None else "")
            t.insert(pid, "end", iid=f"{pid}-{j}", tags=((out[0] if out else ""),), values=(
                "     " + l["label"], f"{l['win']:.0%}", f"{l['price']:+d}" if l.get("price") is not None else "—",
                l.get("book") or "", "", l.get("why") or "", lr))
            app.parlay_rows[f"{pid}-{j}"] = app.parlay_rows[pid]
    priced_n = sum(1 for x in parlays if x[5])
    app.parlay_lbl.configure(text=(
        f"{len(parlays)} parlay{'s' * (len(parlays) != 1)} for this day ({priced_n} with real prices)"
        + ("" if priced_n else " · pull lines for real prices") if parlays else
        "No parlays yet. Click Update (free) for ideas, or pull lines for priced parlays."))

    parts = [("PARLAY RECORD\n", "label")]
    if rec["w"] + rec["l"]:
        parts.append((f"{rec['w']}-{rec['l']}  ·  {rec['units']:+.2f} units (1 unit each)\n",
                      "good" if rec["units"] >= 0 else "bad"))
        for name, (w, l, u) in sorted(rec["by_name"].items()):
            parts.append((f"   {name}: {w}-{l}, {u:+.2f}u\n", "sub"))
    else:
        parts.append(("No graded priced parlays yet.\n", "sub"))
    parts.append(("\nIS THE MILESTONE MODEL HONEST?\n", "label"))
    if cal:
        parts.append(("Said      Legs   Actually hit\n", "sub"))
        for bucket, n, said, hit in cal:
            tag = "good" if hit >= said - 0.03 else "warn" if hit >= said - 0.08 else "bad"
            parts.append((f"{bucket:<9} {n:>5}   {hit:.0%}  (said {said:.0%})\n", tag))
    else:
        parts.append(("Fills in as tonight's legs are graded.\n", "sub"))
    parts.append(("\nHOW TO READ IT\n", "label"))
    parts.append(("Chance = every leg hits (legs are from different games, so they're treated as "
                  "independent). EV = expected profit per $1 at these prices; positive is good. "
                  "Same-game legs are never combined. A DNP leg is void, as most books do.", "sub"))
    _write(app.parlay_side, parts)


def _copy_parlay(app):
    sel = app.parlay_tree.selection()
    if not sel or sel[0] not in app.parlay_rows:
        return
    name, ls, p, price = app.parlay_rows[sel[0]]
    text = f"{name} ({p:.0%}, {price:+d})\n" + "\n".join(
        f"- {l['label']}" + (f" {l['price']:+d} {l['book']}" if l.get("price") is not None else "") for l in ls)
    app.clipboard_clear()
    app.clipboard_append(text)
    app.status.configure(text="Parlay copied")
