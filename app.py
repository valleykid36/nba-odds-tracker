"""
app.py — desktop window for the NBA odds tracker (no command prompt needed).

    python app.py

  Update (free)   box scores, grading, injuries — same as the first half of daily.py
  Pull lines      shows today's credit budget and which games are worth it, then pulls
  Live (free)     live scores from NBA.com; picks show their progress and grade at the final
  Picks tab       only TOP picks by default; tick the boxes to see weaker ones
  Hot players     who's hot or cold, season-long streaks, chance to reach each milestone tonight
  Parlays         parlays built from the likeliest legs, their results, and the parlay record
  Scores tab      today's games, score and clock (refreshes itself while games are on)
  Record tab      how each tier has done, and how sharp the model is per market
  Injuries tab    today's injury report
  Log tab         everything the pipeline printed

Uses tkinter, which ships with Python on Windows. On a Mac with Homebrew Python:
    brew install python-tk
"""

import queue
import sys
import threading
import tkinter as tk
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from tkinter import messagebox, ttk

from config import ET, LIVE_REFRESH_SECONDS, PROP_MARKETS, PULL_GAME_LINES, PULL_PROP_MARKETS
from db import connect

# ---------------- look ----------------
C = {
    "bg": "#101418", "panel": "#181d23", "panel2": "#1f252c", "line": "#2a3139",
    "text": "#e7eaee", "muted": "#8b95a3", "accent": "#4f9dff",
    "best": "#3ecf8e", "consider": "#f2b84b", "avoid": "#f06a6a",
    "win": "#3ecf8e", "loss": "#f06a6a", "sel": "#2b3a4d",
}
if sys.platform == "darwin":
    FONT = "Helvetica Neue"
elif sys.platform.startswith("win"):
    FONT = "Segoe UI"
else:
    FONT = "DejaVu Sans"
from config import PICK_MODE
TIER_TEXT = {"BEST": "TOP" if PICK_MODE == "confidence" else "BEST", "CONSIDER": "LEAN", "AVOID": "AVOID"}


def today_str():
    return datetime.now(ET).strftime("%Y-%m-%d")


class QueueWriter:
    """Sends print() output from the worker thread to the Log tab."""

    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(("log", s))

    def flush(self):
        pass


# ======================= main window =======================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NBA Picks")
        self.geometry("1180x760")
        self.minsize(900, 560)
        self.configure(bg=C["bg"])
        self.day = datetime.now(ET).date()
        self.q = queue.Queue()
        self.busy = False
        self.board = []
        self.show_consider = tk.BooleanVar(value=False)
        self.show_avoid = tk.BooleanVar(value=False)
        self._style()
        self._build()
        self.after(100, self._poll)
        self.refresh()
        self.after(300, self.check_credits)
        self.after(2000, self.auto_live)

    # ---------- styling ----------
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=C["bg"], foreground=C["text"], font=(FONT, 10),
                    bordercolor=C["line"], lightcolor=C["panel"], darkcolor=C["panel"],
                    troughcolor=C["panel"], fieldbackground=C["panel"])
        s.configure("TFrame", background=C["bg"])
        s.configure("Panel.TFrame", background=C["panel"])
        s.configure("TLabel", background=C["bg"], foreground=C["text"])
        s.configure("Muted.TLabel", foreground=C["muted"])
        s.configure("Panel.TLabel", background=C["panel"])
        s.configure("PanelMuted.TLabel", background=C["panel"], foreground=C["muted"])
        s.configure("Title.TLabel", font=(FONT, 16, "bold"))
        s.configure("Date.TLabel", font=(FONT, 12, "bold"))
        s.configure("Big.TLabel", background=C["panel"], font=(FONT, 20, "bold"))
        s.configure("TButton", background=C["panel2"], foreground=C["text"], padding=(12, 6),
                    borderwidth=1, focusthickness=0)
        s.map("TButton", background=[("active", C["line"]), ("disabled", C["panel"])],
              foreground=[("disabled", C["muted"])])
        s.configure("Accent.TButton", background=C["accent"], foreground="#ffffff")
        s.map("Accent.TButton", background=[("active", "#3d86e0"), ("disabled", C["panel2"])])
        s.configure("Nav.TButton", padding=(8, 4))
        s.configure("TCheckbutton", background=C["bg"], foreground=C["text"])
        s.map("TCheckbutton", background=[("active", C["bg"])],
              indicatorcolor=[("selected", C["accent"]), ("!selected", C["panel2"])])
        s.configure("Panel.TCheckbutton", background=C["panel"])
        s.map("Panel.TCheckbutton", background=[("active", C["panel"])])
        s.configure("TNotebook", background=C["bg"], borderwidth=0, tabmargins=(0, 4, 0, 0))
        s.configure("TNotebook.Tab", background=C["bg"], foreground=C["muted"], padding=(16, 8),
                    borderwidth=0, font=(FONT, 10, "bold"))
        s.map("TNotebook.Tab", background=[("selected", C["panel"])],
              foreground=[("selected", C["text"])])
        s.configure("Treeview", background=C["panel"], fieldbackground=C["panel"],
                    foreground=C["text"], rowheight=30, borderwidth=0, font=(FONT, 10))
        s.map("Treeview", background=[("selected", C["sel"])], foreground=[("selected", C["text"])])
        s.configure("Treeview.Heading", background=C["panel2"], foreground=C["muted"],
                    font=(FONT, 9, "bold"), borderwidth=0, padding=(6, 6))
        s.map("Treeview.Heading", background=[("active", C["line"])])
        s.configure("Vertical.TScrollbar", background=C["line"], arrowcolor=C["muted"],
                    troughcolor=C["panel"], bordercolor=C["panel"], lightcolor=C["line"],
                    darkcolor=C["line"], borderwidth=0, gripcount=0)
        s.map("Vertical.TScrollbar", background=[("active", C["muted"])])
        s.configure("Horizontal.TProgressbar", background=C["accent"], troughcolor=C["panel"],
                    borderwidth=0, thickness=4)

    # ---------- layout ----------
    def _build(self):
        top = ttk.Frame(self, padding=(16, 12, 16, 8))
        top.pack(fill="x")
        ttk.Label(top, text="NBA Picks", style="Title.TLabel").pack(side="left")
        nav = ttk.Frame(top)
        nav.pack(side="left", padx=(24, 0))
        ttk.Button(nav, text="◀", style="Nav.TButton", width=3,
                   command=lambda: self.shift_day(-1)).pack(side="left")
        self.date_lbl = ttk.Label(nav, style="Date.TLabel", width=17, anchor="center")
        self.date_lbl.pack(side="left", padx=6)
        ttk.Button(nav, text="▶", style="Nav.TButton", width=3,
                   command=lambda: self.shift_day(1)).pack(side="left")
        ttk.Button(nav, text="Today", style="Nav.TButton",
                   command=self.go_today).pack(side="left", padx=(6, 0))

        self.pull_btn = ttk.Button(top, text="Pull today's lines…", style="Accent.TButton",
                                   command=self.open_pull)
        self.pull_btn.pack(side="right")
        self.update_btn = ttk.Button(top, text="Update (free)", command=self.run_update)
        self.update_btn.pack(side="right", padx=8)
        self.live_btn = ttk.Button(top, text="Live scores (free)", command=self.run_live)
        self.live_btn.pack(side="right")

        sub = ttk.Frame(self, padding=(16, 0, 16, 6))
        sub.pack(fill="x")
        self.credit_lbl = ttk.Label(sub, style="Muted.TLabel")
        self.credit_lbl.pack(side="left")
        self.progress = ttk.Progressbar(sub, mode="indeterminate", length=160,
                                        style="Horizontal.TProgressbar")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=16, pady=(6, 0))
        self._build_picks()
        from app_hot import build_hot_tab, build_parlay_tab
        build_hot_tab(self, C, FONT)
        build_parlay_tab(self, C, FONT)
        self._build_scores()
        self._build_record()
        self._build_injuries()
        self._build_log()

        self.status = ttk.Label(self, style="Muted.TLabel", padding=(16, 6))
        self.status.pack(fill="x")

    def _tree(self, parent, cols):
        """cols: [(id, heading, width, anchor)]"""
        wrap = ttk.Frame(parent, style="Panel.TFrame")
        tree = ttk.Treeview(wrap, columns=[c[0] for c in cols], show="headings", selectmode="browse")
        for cid, head, width, anchor in cols:
            tree.heading(cid, text=head, anchor=anchor)
            tree.column(cid, width=width, anchor=anchor, stretch=cid in ("who", "pick", "note", "why"))
        sb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for tag, color in (("BEST", C["best"]), ("CONSIDER", C["consider"]), ("AVOID", C["avoid"])):
            tree.tag_configure(tag, foreground=color)
        tree.tag_configure("muted", foreground=C["muted"])
        return wrap, tree

    def _build_picks(self):
        f = ttk.Frame(self.nb, style="Panel.TFrame", padding=12)
        self.nb.add(f, text="Picks")
        bar = ttk.Frame(f, style="Panel.TFrame")
        bar.pack(fill="x", pady=(0, 8))
        self.summary = ttk.Label(bar, style="Panel.TLabel", font=(FONT, 11, "bold"))
        self.summary.pack(side="left")
        ttk.Checkbutton(bar, text="Show AVOID", variable=self.show_avoid, style="Panel.TCheckbutton",
                        command=self.fill_picks).pack(side="right")
        ttk.Checkbutton(bar, text="Show LEAN", variable=self.show_consider, style="Panel.TCheckbutton",
                        command=self.fill_picks).pack(side="right", padx=12)

        pane = ttk.Frame(f, style="Panel.TFrame")
        pane.pack(fill="both", expand=True)
        wrap, self.picks_tree = self._tree(pane, [
            ("tier", "TIER", 64, "w"), ("pick", "PICK", 170, "w"), ("who", "PLAYER / GAME", 170, "w"),
            ("price", "PRICE", 64, "e"), ("book", "BOOK", 100, "w"), ("win", "HIT %", 64, "e"),
            ("mkt", "BOOKS %", 78, "e"), ("ev", "FACTORS", 74, "center"), ("res", "RESULT", 72, "center")])
        wrap.pack(side="left", fill="both", expand=True)
        self.picks_tree.bind("<<TreeviewSelect>>", self.show_detail)

        side = ttk.Frame(pane, style="Panel.TFrame", width=360)
        side.pack(side="right", fill="y", padx=(12, 0))
        side.pack_propagate(False)
        self.detail = tk.Text(side, bg=C["panel2"], fg=C["text"], relief="flat", wrap="word",
                              font=(FONT, 10), padx=14, pady=12, highlightthickness=0,
                              cursor="arrow")
        self.detail.pack(fill="both", expand=True)
        self.detail.tag_configure("h", font=(FONT, 13, "bold"))
        self.detail.tag_configure("sub", foreground=C["muted"])
        self.detail.tag_configure("label", foreground=C["muted"], font=(FONT, 9, "bold"))
        self.detail.tag_configure("warn", foreground=C["consider"])
        self.detail.tag_configure("good", foreground=C["best"])
        ttk.Button(side, text="Copy pick", command=self.copy_pick).pack(fill="x", pady=(8, 0))
        self._detail_text("Select a pick to see why the model likes it.", [])

    def _build_record(self):
        f = ttk.Frame(self.nb, style="Panel.TFrame", padding=12)
        self.nb.add(f, text="Record")
        cards = ttk.Frame(f, style="Panel.TFrame")
        cards.pack(fill="x", pady=(0, 12))
        self.cards = {}
        for tier in ("BEST", "CONSIDER", "AVOID"):
            c = tk.Frame(cards, bg=C["panel2"], padx=16, pady=12)
            c.pack(side="left", fill="x", expand=True, padx=(0, 10))
            tk.Label(c, text=TIER_TEXT[tier], bg=C["panel2"], fg=C[tier.lower()],
                     font=(FONT, 10, "bold")).pack(anchor="w")
            big = tk.Label(c, bg=C["panel2"], fg=C["text"], font=(FONT, 20, "bold"))
            big.pack(anchor="w")
            small = tk.Label(c, bg=C["panel2"], fg=C["muted"], font=(FONT, 10), justify="left")
            small.pack(anchor="w")
            self.cards[tier] = (big, small)
        ttk.Label(f, text="How sharp is the model vs the sportsbooks, by market",
                  style="Panel.TLabel", font=(FONT, 11, "bold")).pack(anchor="w", pady=(4, 6))
        wrap, self.trust_tree = self._tree(f, [
            ("mkt", "MARKET", 90, "w"), ("n", "GRADED LINES", 130, "e"),
            ("model", "MODEL BRIER", 120, "e"), ("books", "BOOKS BRIER", 120, "e"),
            ("sharper", "SHARPER", 100, "center"), ("trust", "TRUST IN MODEL", 140, "e"),
            ("note", "", 300, "w")])
        wrap.pack(fill="both", expand=True)
        ttk.Label(f, style="PanelMuted.TLabel", wraplength=1000, justify="left", text=(
            "Brier score: lower is better. Every line the model checks is graded, not just picks, so this "
            "learns fast. Trust starts at 50% and moves toward whatever has actually worked; markets where "
            "the books stay sharper get less model weight, so they produce fewer picks. Judge tiers after "
            "~200 picks; anything smaller is mostly luck.")).pack(anchor="w", pady=(10, 0))

    def _build_injuries(self):
        f = ttk.Frame(self.nb, style="Panel.TFrame", padding=12)
        self.nb.add(f, text="Injuries")
        self.inj_lbl = ttk.Label(f, style="PanelMuted.TLabel")
        self.inj_lbl.pack(anchor="w", pady=(0, 8))
        wrap, self.inj_tree = self._tree(f, [
            ("team", "TEAM", 70, "w"), ("who", "PLAYER", 200, "w"), ("status", "STATUS", 110, "w"),
            ("note", "NOTE", 470, "w"), ("ret", "RETURN", 100, "w"), ("src", "SOURCE", 70, "w")])
        wrap.pack(fill="both", expand=True)
        self.inj_tree.tag_configure("out", foreground=C["avoid"])
        self.inj_tree.tag_configure("q", foreground=C["consider"])
        self.inj_tree.tag_configure("ok", foreground=C["best"])

    def _build_log(self):
        f = ttk.Frame(self.nb, style="Panel.TFrame", padding=12)
        self.nb.add(f, text="Log")
        self.log_frame = f
        self.log = tk.Text(f, bg=C["panel2"], fg=C["text"], relief="flat", wrap="none",
                           font=("Consolas" if sys.platform.startswith("win") else "Menlo"
                                 if sys.platform == "darwin" else "DejaVu Sans Mono", 9),
                           padx=12, pady=10, highlightthickness=0)
        sb = ttk.Scrollbar(f, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---------- background work ----------
    def run_task(self, label, fn, done=None):
        if self.busy:
            return
        self.set_busy(True, label)

        def target():
            w = QueueWriter(self.q)
            try:
                with redirect_stdout(w), redirect_stderr(w):
                    result = fn()
                self.q.put(("done", label, result, done, None))
            except Exception as e:
                self.q.put(("log", traceback.format_exc()))
                self.q.put(("done", label, None, done, e))
        threading.Thread(target=target, daemon=True).start()

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "log":
                    self.log.insert("end", item[1])
                    self.log.see("end")
                else:
                    _, label, result, done, err = item
                    self.set_busy(False)
                    if err:
                        self.status.configure(text=f"{label} failed: {err}  (details in Log tab)")
                    else:
                        self.status.configure(text=f"{label}: done")
                        if done:
                            done(result)
                    self.refresh()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def set_busy(self, busy, label=""):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.update_btn.configure(state=state)
        self.pull_btn.configure(state=state)
        self.live_btn.configure(state=state)
        if busy:
            self.status.configure(text=f"{label}…")
            self.progress.pack(side="right")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    # ---------- actions ----------
    def check_credits(self):
        def fn():
            from ingest import ODDS_KEY, check_credits
            if ODDS_KEY:
                check_credits()          # free call
        self.run_task("Checking credits", fn)

    def run_update(self):
        def fn():
            import daily
            con = connect()
            daily.catch_up(con)
            con.close()
        self.nb.select(self.log_frame)
        self.run_task("Update", fn)

    def open_pull(self):
        def fn():
            from budget import plan
            from ingest import todays_events
            events = todays_events()       # free
            con = connect()
            p = plan(con, events, today_str())
            con.close()
            return p
        self.run_task("Finding today's games", fn, done=lambda p: PullDialog(self, p))

    def do_pull(self, chosen, lines):
        def fn():
            import daily
            from picks import build_board
            con = connect()
            used = daily.pull(con, chosen, lines=lines)
            build_board(con, today_str())
            con.close()
            return used

        def done(used):
            self.go_today()
            self.nb.select(0)
            self.status.configure(text=f"Pulled lines: {used} credits used")
        self.run_task("Pulling lines", fn, done=done)

    def shift_day(self, n):
        self.day += timedelta(days=n)
        self.refresh()

    def go_today(self):
        self.day = datetime.now(ET).date()
        self.refresh()

    def copy_pick(self):
        sel = self.picks_tree.selection()
        if not sel:
            return
        e = self.visible[int(sel[0])]
        self.clipboard_clear()
        self.clipboard_append(f"{e['name']}: {e['pick']} {e['price']:+d} ({e['book']})")
        self.status.configure(text="Copied pick to clipboard")

    # ---------- live scores (free) ----------
    def run_live(self, quiet=False):
        def fn():
            from nba_live import update_live
            import daily
            update_live(verbose=not quiet)
            con = connect()
            n = daily.grade(con, quiet=True)
            con.close()
            return n

        def done(n):
            self.status.configure(text="Live scores updated" + (f" · {n} picks graded" if n else "")
                                  + f" · {datetime.now(ET):%I:%M %p}")
        self.run_task("Live scores", fn, done=done)

    def auto_live(self):
        """While games are on, refresh live scores every LIVE_REFRESH_SECONDS (free)."""
        try:
            from nba_live import games_active
            con = connect()
            active = games_active(con, today_str())
            con.close()
            if active and not self.busy and self.day == datetime.now(ET).date():
                self.run_live(quiet=True)
        except Exception:
            self.log.insert("end", traceback.format_exc())
        self.after(LIVE_REFRESH_SECONDS * 1000, self.auto_live)

    def attach_live(self, con, day):
        from nba_extra import context_lines
        from nba_live import live_for, live_text
        players, games = live_for(con, day)
        for e in self.board:
            e["live"] = None if e["result"] else live_text(e, players, games)
            e["context"] = context_lines(con, e, day)

    def day_record(self):
        out = []
        for tier in ("BEST", "CONSIDER"):
            res = [e["result"] for e in self.board if e["tier"] == tier and e["result"] in ("W", "L")]
            if res:
                out.append(f"{TIER_TEXT[tier]} {res.count('W')}-{res.count('L')}")
        return ("   ·   this day: " + ", ".join(out)) if out else ""

    def _build_scores(self):
        f = ttk.Frame(self.nb, style="Panel.TFrame", padding=12)
        self.nb.add(f, text="Scores")
        self.scores_lbl = ttk.Label(f, style="PanelMuted.TLabel")
        self.scores_lbl.pack(anchor="w", pady=(0, 8))
        wrap, self.scores_tree = self._tree(f, [
            ("game", "GAME", 150, "w"), ("score", "SCORE", 120, "center"), ("status", "STATUS", 130, "w"),
            ("picks", "YOUR PICKS IN THIS GAME", 520, "w")])
        wrap.pack(fill="both", expand=True)
        self.scores_tree.tag_configure("live", foreground=C["consider"])
        self.scores_tree.tag_configure("final", foreground=C["muted"])

    def fill_scores(self, con, day):
        t = self.scores_tree
        t.delete(*t.get_children())
        rows = con.execute("SELECT * FROM live_games WHERE game_date=? ORDER BY tip_utc", (day,)).fetchall()
        if not rows:
            self.scores_lbl.configure(text="No scores saved for this day. Click “Live scores (free)” on a game day.")
            return
        upd = max(r["updated_utc"] for r in rows)
        upd_et = datetime.fromisoformat(upd).astimezone(ET)
        self.scores_lbl.configure(text=f"From NBA.com's scoreboard · last refreshed {upd_et:%I:%M %p} · "
                                       f"refreshes itself every {LIVE_REFRESH_SECONDS // 60} min while games are on")
        for r in rows:
            mine = []
            for e in self.board:
                if e["tier"] not in ("BEST", "CONSIDER"):
                    continue
                teams = {e.get("team"), e.get("opp")} if e["kind"] == "prop" else {e.get("home"), e.get("away")}
                if {r["home"], r["away"]} == teams:
                    res = {"W": "✓", "L": "✗", "P": "push", "DNP": "DNP"}.get(e["result"], "") or \
                        (e.get("live") or ("",))[0].split(" ")[0]
                    who = (e["name"].split()[-1] + " ") if e["kind"] == "prop" else ""
                    mine.append(f"{res} {who}{e['pick']}".strip())
            tag = "live" if r["status"] == 2 else "final" if r["status"] == 3 else ""
            score = f"{r['away_pts']} - {r['home_pts']}" if r["status"] != 1 else ""
            t.insert("", "end", tags=(tag,), values=(f"{r['away']} @ {r['home']}", score,
                                                     r["status_text"], "   ".join(mine)))

    # ---------- refresh views ----------
    def refresh(self):
        if self.busy:
            return
        from budget import credits_left, daily_budget, days_until_reset, reset_date
        from picks import build_board
        con = connect()
        day = self.day.isoformat()
        is_today = self.day == datetime.now(ET).date()
        self.date_lbl.configure(text=("Today · " if is_today else "") + self.day.strftime("%a %b %d"))
        left = credits_left(con)
        budget = daily_budget(con)
        self.credit_lbl.configure(text=(
            f"{left if left is not None else '?'} credits left · today's budget "
            f"{budget if budget is not None else '?'} · resets {reset_date():%b} 1 "
            f"({days_until_reset()}d)"))
        try:
            self.board, self.n_props, self.n_games = build_board(con, day)
            self.attach_live(con, day)
            self.fill_picks()
            self.fill_scores(con, day)
            self.fill_record(con)
            self.fill_injuries(con, day)
            from app_hot import fill_hot, fill_parlays
            fill_hot(self, con, day)
            fill_parlays(self, con, day)
        except Exception as e:
            self.log.insert("end", traceback.format_exc())
            self.status.configure(text=f"Couldn't show {day}: {e}  (details in Log tab)")
        finally:
            con.close()

    def fill_picks(self):
        t = self.picks_tree
        t.delete(*t.get_children())
        tiers = {"BEST"} | ({"CONSIDER"} if self.show_consider.get() else set()) \
            | ({"AVOID"} if self.show_avoid.get() else set())
        self.visible = [e for e in self.board if e["tier"] in tiers]
        order = {"BEST": 0, "CONSIDER": 1, "AVOID": 2}
        from picks import rank_key
        self.visible.sort(key=rank_key, reverse=True)          # most likely to hit first...
        self.visible.sort(key=lambda e: order[e["tier"]])      # ...within each tier
        for i, e in enumerate(self.visible):
            t.insert("", "end", iid=str(i), tags=(e["tier"],), values=(
                TIER_TEXT[e["tier"]], e["pick"], e["name"], f"{e['price']:+d}", e["book"],
                f"{e['win']:.0%}", f"{e['market']:.0%}", f"{e['n_ok']}/{len(e['signals'])}",
                {"W": "✓ W", "L": "✗ L", "P": "push", "DNP": "DNP"}.get(e["result"], "")
                or (e.get("live") or ("",))[0]))
        best = sum(1 for e in self.board if e["tier"] == "BEST")
        hidden = len(self.board) - len(self.visible)
        if not self.n_props and not self.n_games:
            msg = "No lines pulled for this day." + (
                "  Click “Pull today's lines…”" if self.day == datetime.now(ET).date() else "")
        else:
            msg = (f"{best} {TIER_TEXT['BEST']} pick{'s' * (best != 1)}"
                   + (" — passing today is a fine pick too" if not best else "")
                   + f"   ·   {self.n_games} games, {self.n_props} prop lines checked"
                   + (f"   ·   {hidden} weaker hidden" if hidden else "")
                   + self.day_record())
        self.summary.configure(text=msg)
        if self.visible:
            t.selection_set("0")
        else:
            self._detail_text("Nothing to show.", [])

    def show_detail(self, _=None):
        sel = self.picks_tree.selection()
        if not sel:
            return
        e = self.visible[int(sel[0])]
        parts = [
            (e["pick"] + "\n", "h"),
            (f"{e['name']}  ·  {e['matchup']}\n", "sub"),
            (f"{e['price']:+d} at {e['book']}\n\n", "sub"),
            ("CHANCE TO HIT\n", "label"),
            (f"{e['win']:.0%}  (model {e['model']:.0%}, books {e['market']:.0%}, "
             f"trusting model {e['trust']:.0%})\n", ""),
            (f"EV {e['ev']:+.1%} per $1 at this price\n\n", "good" if e["ev"] >= 0 else "warn"),
            (f"FACTORS  {e['n_ok']} of {len(e['signals'])} agree\n", "label"),
        ]
        parts += [(f"{'✓' if s['ok'] else '✗'}  {s['text']}\n", "good" if s["ok"] else "sub")
                  for s in e["signals"]]
        if e["warn"]:
            parts.append(("\nRED FLAGS\n", "label"))
            parts += [(f"!  {w}\n", "warn") for w in e["warn"]]
        if e.get("context"):
            parts.append(("\nMATCHUP DATA (NBA.com)\n", "label"))
            parts += [(f"·  {c}\n", "") for c in e["context"]]
        if e["result"]:
            parts.append((f"\nRESULT: {e['result']}\n", "good" if e["result"] == "W" else "warn"))
        elif e.get("live"):
            parts.append(("\nLIVE\n", "label"))
            parts.append((e["live"][1] + "\n", "good" if e["live"][0].startswith(("↑", "✓")) else "warn"))
        self._detail_text(None, parts)

    def _detail_text(self, plain, parts):
        d = self.detail
        d.configure(state="normal")
        d.delete("1.0", "end")
        if plain:
            d.insert("end", plain, "sub")
        for text, tag in parts:
            d.insert("end", text, tag)
        d.configure(state="disabled")

    def fill_record(self, con):
        from clv import clv_summary, describe
        from picks import learned_trust, market_label, records
        since = (datetime.now(ET).date() - timedelta(days=30)).isoformat()
        allr, recent, clv = records(con), records(con, since), clv_summary(con)
        for tier, (big, small) in self.cards.items():
            r, r30 = allr[tier], recent[tier]
            if r["w"] + r["l"]:
                big.configure(text=f"{r['w']}-{r['l']}", fg=C["win"] if r["units"] >= 0 else C["loss"])
                small.configure(text=f"{r['units']:+.2f} units · {r['roi']:+.1%} ROI\n"
                                     f"last 30 days: {r30['w']}-{r30['l']}, {r30['units']:+.2f}u\n"
                                     f"{describe(clv[tier])} (game lines)")
            else:
                big.configure(text="—", fg=C["muted"])
                small.configure(text="no graded picks yet\n")
        t = self.trust_tree
        t.delete(*t.get_children())
        trust = learned_trust(con)
        for m, x in sorted(trust.items(), key=lambda kv: -kv[1]["n"]):
            better = x["model"] < x["market"]
            note = ("model has an edge here" if better and x["n"] >= 200 else
                    "books are sharper — fewer picks from this market" if x["n"] >= 200 else
                    "too few lines to judge yet")
            pulled = " (not pulled)" if m in PROP_MARKETS and m not in PULL_PROP_MARKETS else ""
            t.insert("", "end", tags=("BEST" if better else "muted",), values=(
                market_label(m), f"{x['n']:,}", f"{x['model']:.4f}", f"{x['market']:.4f}",
                "model" if better else "books", f"{x['trust']:.0%}", note + pulled))

    def fill_injuries(self, con, day):
        t = self.inj_tree
        t.delete(*t.get_children())
        snap = con.execute("SELECT MAX(snapshot_date) FROM injuries WHERE snapshot_date<=?",
                           (day,)).fetchone()[0]
        if not snap:
            self.inj_lbl.configure(text="No injury report yet. Click Update (free).")
            return
        rows = con.execute("SELECT * FROM injuries WHERE snapshot_date=? ORDER BY team, status, player",
                           (snap,)).fetchall()
        from db import get_meta
        nba = sum(1 for r in rows if "source" in r.keys() and r["source"] == "NBA")
        official = get_meta(con, "nba_injury_report")
        self.inj_lbl.configure(text=f"Injury list for {snap} · {len(rows)} players · "
                                    + (f"{nba} from the official NBA report ({official}), rest from ESPN"
                                       if nba else "ESPN (official NBA report not posted yet)"))
        for r in rows:
            st = (r["status"] or "").lower()
            tag = "out" if st in ("out", "doubtful", "suspension") else "ok" if st == "available" else "q"
            src = r["source"] if "source" in r.keys() and r["source"] else "ESPN"
            t.insert("", "end", tags=(tag,), values=(r["team"], r["player"], r["status"],
                                                     r["comment"], r["return_date"], src))


# ======================= pull dialog =======================
class PullDialog(tk.Toplevel):
    def __init__(self, app, plan):
        super().__init__(app)
        self.app, self.plan = app, plan
        self.title("Pull today's lines")
        self.configure(bg=C["panel"])
        self.geometry("760x520")
        self.transient(app)
        from budget import credits_left
        con = connect()
        self.left = credits_left(con)
        con.close()

        f = ttk.Frame(self, style="Panel.TFrame", padding=16)
        f.pack(fill="both", expand=True)
        if not plan["games"]:
            ttk.Label(f, text="No more games today.", style="Panel.TLabel",
                      font=(FONT, 12, "bold")).pack(anchor="w")
            ttk.Button(f, text="Close", command=self.destroy).pack(anchor="e", pady=12)
            return
        labels = ", ".join(PROP_MARKETS[m]["label"] for m in PULL_PROP_MARKETS)
        ttk.Label(f, text="Which games are worth credits today?", style="Panel.TLabel",
                  font=(FONT, 13, "bold")).pack(anchor="w")
        ttk.Label(f, style="PanelMuted.TLabel", wraplength=720, justify="left", text=(
            f"Pre-ticked from free data: games with a regular ruled out come first, because props "
            f"there are most often mispriced. Props cost 1 credit per market ({labels}) per game; "
            f"anything pulled in the last few hours is reused for free.")).pack(anchor="w", pady=(4, 12))

        self.lines_var = tk.BooleanVar(value=PULL_GAME_LINES)
        lc = plan["lines_cost"]
        ttk.Checkbutton(f, variable=self.lines_var, style="Panel.TCheckbutton", command=self.update_total,
                        text=f"Game lines for every game (ML, spread, total) — "
                             f"{f'{lc} credits' if lc else 'already pulled, free'}").pack(anchor="w")
        ttk.Separator(f).pack(fill="x", pady=10)

        box = ttk.Frame(f, style="Panel.TFrame")
        box.pack(fill="both", expand=True)
        self.vars = []
        for g in sorted(plan["games"], key=lambda g: g["tip"]):
            v = tk.BooleanVar(value=g["chosen"])
            self.vars.append((v, g))
            row = ttk.Frame(box, style="Panel.TFrame")
            row.pack(fill="x", pady=3)
            ev = g["event"]
            cost = f"{g['cost']} cr" if g["cost"] else "pulled, free"
            ttk.Checkbutton(row, variable=v, style="Panel.TCheckbutton", command=self.update_total,
                            text=f"{g['tip']:%I:%M %p}   {ev['away_team']} @ {ev['home_team']}   ({cost})"
                            ).pack(anchor="w")
            why = "; ".join(g["reasons"]) or "no injury or rest news"
            ttk.Label(row, text="      " + why, style="PanelMuted.TLabel").pack(anchor="w")

        foot = ttk.Frame(f, style="Panel.TFrame")
        foot.pack(fill="x", pady=(12, 0))
        self.total_lbl = tk.Label(foot, bg=C["panel"], font=(FONT, 11, "bold"))
        self.total_lbl.pack(side="left")
        self.go_btn = ttk.Button(foot, text="Pull", width=8, style="Accent.TButton", command=self.go)
        self.go_btn.pack(side="right")
        ttk.Button(foot, text="Cancel", width=8, command=self.destroy).pack(side="right", padx=8)
        self.update_total()
        self.grab_set()

    def cost(self):
        return (self.plan["lines_cost"] if self.lines_var.get() else 0) + \
            sum(g["cost"] for v, g in self.vars if v.get())

    def update_total(self):
        c, b = self.cost(), self.plan["budget"]
        over_budget = b is not None and c > b
        over_left = self.left is not None and c > self.left
        msg = f"Total: {c} credits   ·   today's budget {b if b is not None else '?'}   ·   " \
              f"{self.left if self.left is not None else '?'} left this month"
        if over_left:
            msg += "   — more than you have"
        elif over_budget:
            msg += "   — over today's budget"
        self.total_lbl.configure(text=msg, fg=C["avoid"] if over_left else
                                 C["consider"] if over_budget else C["text"])
        self.go_btn.configure(state="disabled" if over_left else "normal")

    def go(self):
        chosen = [(g["tip"], g["event"]) for v, g in self.vars if v.get()]
        lines = self.lines_var.get()
        if not chosen and not lines:
            messagebox.showinfo("Nothing selected", "Tick at least one game or game lines.", parent=self)
            return
        self.destroy()
        self.app.do_pull(chosen, lines)


if __name__ == "__main__":
    App().mainloop()
