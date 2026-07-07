#!/usr/bin/env python3
"""
EQL Session Report
====================
A detailed, read-once breakdown of a play session: damage split six ways
(Melee / Skill / Spell / Song / Damage Shield / Pet), heal totals, kill
rate, which spell/ability is carrying your damage or healing, and how your
Stance/Invocation choices compare in practice. This is the deep-dive
companion to the live DPS/HPS meter overlay (eql_dps_meter.py), which
stays deliberately small/non-intrusive.

Usage:
    python eql_session_report.py "C:\\...\\Logs\\eqlog_Miranda_rivervale.txt"

Or launch it from the DPS meter's right-click menu -> "Open Session
Report...", which hands it the log already in use.

Log files accumulate many play sessions; each "Welcome to EverQuest
Legends!" login banner starts a new one. This report splits the log into
those sessions and shows ONE at a time -- pick which from the "Session"
dropdown at the top (defaults to the most recent).

Tabs
-----
* Overview        -- headline numbers plus a color-coded damage-split bar.
* Graphs          -- bar charts with the NAMES of your spells/abilities on
                     them: damage by ability, healing by ability, and DPS
                     per fight over time. A category filter (Melee / Skill
                     / Spell / Song / Dmg Shield / Pet) and a name search
                     narrow the charts to whatever you're comparing, so
                     "is Spell X out-damaging Spell Y" is one glance.
* Damage/Healing by Ability -- the same data as sortable tables, with the
                     same filter + search controls.
* Sessions        -- EVERY session in the log side by side: length, fights,
                     avg combat DPS, kills/hr, deaths. The best session per
                     metric gets a star, a chart compares them visually,
                     and PERSONAL RECORDS (best avg combat DPS, best
                     kills/hr, biggest hit) persist to a per-character JSON
                     across runs -- beat one and it's flagged NEW RECORD.
                     Double-click a session row to open that session.
* Stance / Invocation, Spells Cast, Passive Healing (est.), Unrecognized
  lines -- as before (see below).

What's empirical vs. exact
------------------------------
Damage/heal totals, kill counts, and per-ability breakdowns come directly
from parsed log lines -- exact, not estimated. "Avg combat DPS" divides
YOUR damage in completed fights by ACTIVE combat time (downtime between
chained pulls is capped out -- see eql_combat_tracker.ACTIVE_GAP_CAP), so
sessions of different lengths compare fairly.

Stance/Invocation comparison is empirical: it groups your *completed*
fights by whichever Stance/Invocation was active when each fight started,
and averages observed DPS/DTPS across those fights. More fights in a
bucket = a more trustworthy average.

Passive Healing (est.) tab: some effects -- notably a Bard's heal-over-time
songs -- never produce a "healed X for N hit points" log line at all.
This tab estimates candidate heal magnitudes from `spells_us.txt` itself
(EQEmu classic-era reference math, PER-TICK for HoTs -- cross-check a
manual guess, don't treat as exact).
"""

import json
import os
import re
import sys
import tkinter as tk
from datetime import datetime
from tkinter import ttk, filedialog

from eql_combat_tracker import (
    CombatTracker, YOU_LABEL, STANCES, INVOCATIONS, CATEGORY_LABELS,
    CATEGORIES,
)
from eql_spell_db import SPELL_DB, CLASS_NAMES

APP_DIR = os.path.dirname(os.path.abspath(__file__))

SESSION_MARK = "Welcome to EverQuest Legends!"
ZONE_RE = re.compile(r"You have entered (.+)\.")

# -- report palette ----------------------------------------------------------
BG = "#f4f5f7"          # window background
PANEL = "#ffffff"       # card/table background
INK = "#15202b"         # main text
SUBTLE = "#5c6773"      # captions / secondary text
GRID = "#e3e6ea"        # separators, chart gridlines
ACCENT = "#2f6fdb"      # interactive highlights
GOOD = "#2e9e5b"        # above-average bars
MUTED_BAR = "#b9c2cc"   # below-average bars
BEST_BG = "#fdf3d7"     # row highlight for best session
RECORD = "#b8860b"      # personal-record gold

CAT_COLORS = {          # one color per damage category, used everywhere
    "melee": "#4c86d8", "skill": "#9a6bdd", "spell": "#e0a63a",
    "song": "#d65c8b", "ds": "#3fae7a", "pet": "#7a8699",
}
HEAL_COLOR = "#2e9e5b"

FONT = ("Segoe UI", 9)
FONT_SMALL = ("Segoe UI", 8)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_TITLE = ("Segoe UI", 10, "bold")
FONT_MONO = ("Consolas", 10)

# personal-record thresholds -- a lucky 20-second session shouldn't set an
# unbeatable "best avg DPS" record, so records only count when the session
# has enough data behind them
RECORD_MIN_FIGHTS = 5     # for the avg-combat-DPS record
RECORD_MIN_KILLS = 10     # for the kills/hour record


def char_name_from(path):
    m = re.match(r"eqlog_([A-Za-z]+)", os.path.basename(path))
    return m.group(1) if m else None


def load_sessions(log_path):
    """Split the log into play sessions at each login banner.

    Returns a list of dicts {label, lines, ts, zone} in chronological
    order. Lines before the first banner (log started mid-session, or an
    old client version) become their own "(log start)" bucket.
    """
    try:
        with open(log_path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    chunks, cur = [], []
    for raw in data.splitlines():
        line = raw.decode("cp1252", errors="replace").rstrip("\r")
        if line.endswith(SESSION_MARK):
            if cur:
                chunks.append(cur)
            cur = [line]
        else:
            cur.append(line)
    if cur:
        chunks.append(cur)

    out = []
    for i, lines in enumerate(chunks):
        ts = lines[0][1:25] if lines[0].startswith("[") else "?"
        zone = ""
        for l in lines[:8]:
            zm = ZONE_RE.search(l)
            if zm:
                zone = zm.group(1)
                break
        prefix = "" if lines[0].endswith(SESSION_MARK) else "(log start) "
        label = f"{i + 1}:  {prefix}{ts}" + (f" — {zone}" if zone else "")
        out.append({"label": label, "lines": lines, "ts": ts, "zone": zone})
    return out


def build_tracker(log_path, lines=None):
    """Build a tracker from one session's lines (or the whole file, in
    which case the tracker's own session resets make it equal to the
    latest session). The trailing open fight is flushed so completed-fight
    stats (avg combat DPS etc.) include it."""
    tracker = CombatTracker(self_name=char_name_from(log_path))
    SPELL_DB.set_game_dir_hint(os.path.dirname(os.path.dirname(log_path)))
    if lines is None:
        try:
            with open(log_path, "rb") as f:
                data = f.read()
        except OSError:
            return tracker
        lines = [raw.decode("cp1252", errors="replace").rstrip("\r")
                 for raw in data.splitlines()]
    for line in lines:
        tracker.handle_line(line)
    tracker.force_end_fight()
    return tracker


def summarize_session(sess, log_path):
    """One session -> comparable stats dict (for the Sessions tab)."""
    tr = build_tracker(log_path, sess["lines"])
    dmg_total = (tr.melee_dmg_out + tr.skill_dmg_out + tr.spell_dmg_out +
                 tr.song_dmg_out + tr.ds_dmg_out + tr.pet_dmg_out)
    biggest = max((s["biggest"] for s in tr.abilities_dmg.values()),
                  default=0)
    return {
        "label": sess["label"], "ts": sess["ts"], "zone": sess["zone"],
        "minutes": tr.session_elapsed() / 60.0,
        "fights": tr.fights_completed,
        "avg_dps": tr.avg_combat_dps(),
        "avg_dtps": tr.avg_combat_dtps(),
        "dmg": dmg_total,
        "kills": len(tr.kills),
        "kph": tr.kills_per_hour(),
        "deaths": len(tr.deaths),
        "biggest": biggest,
        "heal": tr.heal_out_total,
    }


def _fmt_num(n):
    n = int(n)
    if n >= 1_000_000:
        return f"{n/1_000_000:,.2f}M"
    if n >= 1000:
        return f"{n:,}"
    return str(n)


def _fmt_minutes(m):
    return f"{int(m)//60}h {int(m)%60:02d}m" if m >= 60 else f"{m:.0f}m"


# ----------------------------------------------------------------------------
# Records (personal bests, persisted per character)
# ----------------------------------------------------------------------------
def records_path(char):
    return os.path.join(APP_DIR,
                        f"eql_session_report_records_{char or 'Unknown'}.json")


def load_records(char):
    try:
        with open(records_path(char), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_records(char, records):
    try:
        with open(records_path(char), "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2)
    except OSError:
        pass


def update_records(char, summaries):
    """Fold this log's session summaries into the persisted personal bests.
    Returns (records, newly_set) where newly_set is a set of record keys
    that were beaten by a session in `summaries`."""
    records = load_records(char)
    newly = set()

    def consider(key, value, sess, fmt):
        if value <= 0:
            return
        best = records.get(key, {}).get("value", 0)
        if value > best:
            records[key] = {"value": value, "when": sess["ts"],
                            "zone": sess["zone"], "display": fmt}
            newly.add(key)

    for s in summaries:
        if s["fights"] >= RECORD_MIN_FIGHTS:
            consider("best_avg_dps", round(s["avg_dps"], 2), s,
                     f"{s['avg_dps']:.1f} DPS over {s['fights']} fights")
        if s["kills"] >= RECORD_MIN_KILLS:
            consider("best_kph", round(s["kph"], 2), s,
                     f"{s['kph']:.1f} kills/hr ({s['kills']} kills)")
        consider("biggest_hit", s["biggest"], s,
                 f"{_fmt_num(s['biggest'])} damage in one hit")
    if newly:
        save_records(char, records)
    return records, newly


RECORD_TITLES = {"best_avg_dps": "Best avg combat DPS",
                 "best_kph": "Best kill rate",
                 "biggest_hit": "Biggest hit"}


# ----------------------------------------------------------------------------
# Canvas chart helpers
# ----------------------------------------------------------------------------
def draw_hbar_chart(canvas, rows, title, empty_msg):
    """Horizontal bar chart: rows = (name, value, color, note). Names on
    the left, value + note on the bar's right. Scales to canvas width."""
    canvas.delete("all")
    w = max(canvas.winfo_width(), 400)
    if not rows:
        canvas.create_text(w // 2, 60, text=empty_msg, fill=SUBTLE,
                           font=FONT)
        return
    canvas.create_text(10, 12, anchor="w", text=title, fill=INK,
                       font=FONT_TITLE)
    name_w = min(220, max(120, max(len(r[0]) for r in rows) * 7))
    x0 = name_w + 16
    x1 = w - 130
    max_v = max(r[1] for r in rows) or 1
    y = 36
    row_h = 24
    for name, value, color, note in rows:
        canvas.create_text(name_w + 8, y + 8, anchor="e", text=name,
                           fill=INK, font=FONT)
        bw = int((x1 - x0) * value / max_v)
        canvas.create_rectangle(x0, y, x0 + max(bw, 2), y + 16,
                                fill=color, outline="")
        canvas.create_text(x0 + max(bw, 2) + 6, y + 8, anchor="w",
                           text=f"{_fmt_num(value)}   {note}",
                           fill=SUBTLE, font=FONT_SMALL)
        y += row_h
    canvas.configure(scrollregion=(0, 0, w, y + 10))


def draw_vbar_chart(canvas, bars, title, avg=None, avg_label="", best=None):
    """Vertical bar chart: bars = (x_label, value, color, top_label).
    Optional dashed average line; `best` index gets a star."""
    canvas.delete("all")
    w = max(canvas.winfo_width(), 400)
    h = max(canvas.winfo_height(), 220)
    if not bars:
        canvas.create_text(w // 2, 60, text="(no data in this session)",
                           fill=SUBTLE, font=FONT)
        return
    canvas.create_text(10, 12, anchor="w", text=title, fill=INK,
                       font=FONT_TITLE)
    pad_l, pad_r, pad_t, pad_b = 24, 16, 34, 34
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
    max_v = max(v for _, v, _, _ in bars) or 1
    n = len(bars)
    slot = plot_w / n
    bar_w = max(6, min(46, int(slot * 0.66)))
    for i, (xlbl, v, color, toplbl) in enumerate(bars):
        cx = pad_l + slot * i + slot / 2
        bh = int(plot_h * v / max_v)
        x0, y0 = cx - bar_w / 2, pad_t + plot_h - bh
        canvas.create_rectangle(x0, y0, x0 + bar_w, pad_t + plot_h,
                                fill=color, outline="")
        if toplbl:
            canvas.create_text(cx, y0 - 8, text=toplbl, fill=SUBTLE,
                               font=FONT_SMALL)
        if best is not None and i == best:
            canvas.create_text(cx, y0 - 20, text="★", fill=RECORD,
                               font=("Segoe UI", 11))
        if n <= 30:
            canvas.create_text(cx, pad_t + plot_h + 10, text=xlbl,
                               fill=SUBTLE, font=FONT_SMALL)
    canvas.create_line(pad_l, pad_t + plot_h, w - pad_r, pad_t + plot_h,
                       fill=GRID)
    if avg is not None and max_v > 0:
        ay = pad_t + plot_h - int(plot_h * min(avg, max_v) / max_v)
        canvas.create_line(pad_l, ay, w - pad_r, ay, fill=SUBTLE,
                           dash=(4, 3))
        canvas.create_text(w - pad_r, ay - 8, anchor="e", text=avg_label,
                           fill=SUBTLE, font=FONT_SMALL)


# ----------------------------------------------------------------------------
# Report window
# ----------------------------------------------------------------------------
def run_report(log_path):
    root = tk.Tk()
    root.title(f"EQL Session Report -- {char_name_from(log_path) or 'Unknown'}")
    root.geometry("920x640")
    root.configure(bg=BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=BG, foreground=INK, font=FONT)
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(10, 5), font=FONT)
    style.map("TNotebook.Tab",
              background=[("selected", PANEL)],
              foreground=[("selected", ACCENT)])
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                    rowheight=22, font=FONT, borderwidth=0)
    style.configure("Treeview.Heading", font=FONT_BOLD, background="#e9ebee",
                    foreground=INK, relief="flat")
    style.configure("TCombobox", fieldbackground=PANEL)

    # -- top bar ---------------------------------------------------------------
    top = tk.Frame(root, padx=8, pady=6, bg=BG)
    top.pack(fill="x")
    path_lbl = tk.Label(top, text=log_path, anchor="w", font=FONT_SMALL,
                        fg=SUBTLE, bg=BG)
    path_lbl.pack(side="left", fill="x", expand=True)

    state = {"tracker": None, "sessions": load_sessions(log_path),
             "summaries": None, "records": {}, "new_records": set()}

    tk.Label(top, text="Session:", font=FONT_SMALL, bg=BG).pack(
        side="left", padx=(8, 2))
    session_var = tk.StringVar()
    session_box = ttk.Combobox(top, textvariable=session_var,
                               state="readonly", width=42)
    session_box.pack(side="left")

    def reload_session_list():
        state["sessions"] = load_sessions(log_path)
        state["summaries"] = None   # stale -- rebuilt lazily
        labels = [s["label"] for s in state["sessions"]] or ["(empty log)"]
        session_box.configure(values=labels)
        session_box.current(len(labels) - 1)   # default: most recent

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def striped_insert(tree, name, values, tags=()):
        n = len(tree.get_children())
        tree.insert("", "end", text=name, values=values,
                    tags=tags + (("odd",) if n % 2 else ("even",)))

    def setup_stripes(tree):
        tree.tag_configure("odd", background="#f7f8fa")
        tree.tag_configure("even", background=PANEL)
        tree.tag_configure("best", background=BEST_BG)

    # shared filter state (Graphs tab + ability tables stay in sync)
    filter_cat = tk.StringVar(value="All")
    filter_text = tk.StringVar(value="")
    CAT_CHOICES = ["All"] + [CATEGORY_LABELS[c] for c in CATEGORIES]
    LABEL_TO_CAT = {v: k for k, v in CATEGORY_LABELS.items()}

    def ability_passes(name, s):
        cat = filter_cat.get()
        if cat != "All" and s.get("category") != LABEL_TO_CAT.get(cat):
            return False
        needle = filter_text.get().strip().lower()
        return needle in name.lower() if needle else True

    def make_filter_row(parent, on_change):
        row = tk.Frame(parent, bg=BG, pady=4)
        tk.Label(row, text="Category:", font=FONT_SMALL, bg=BG).pack(side="left")
        cat_box = ttk.Combobox(row, textvariable=filter_cat, state="readonly",
                               values=CAT_CHOICES, width=11)
        cat_box.pack(side="left", padx=(2, 10))
        cat_box.bind("<<ComboboxSelected>>", lambda e: on_change())
        tk.Label(row, text="Search:", font=FONT_SMALL, bg=BG).pack(side="left")
        ent = tk.Entry(row, textvariable=filter_text, width=18, font=FONT)
        ent.pack(side="left", padx=(2, 10))
        ent.bind("<KeyRelease>", lambda e: on_change())
        return row

    # -- Overview tab ------------------------------------------------------------
    overview = tk.Frame(nb, padx=12, pady=10, bg=PANEL)
    nb.add(overview, text="Overview")
    split_canvas = tk.Canvas(overview, height=64, bg=PANEL,
                             highlightthickness=0)
    split_canvas.pack(fill="x")
    overview_lbl = tk.Label(overview, justify="left", anchor="nw",
                            font=FONT_MONO, fg=INK, bg=PANEL)
    overview_lbl.pack(fill="both", expand=True, anchor="nw", pady=(8, 0))

    def draw_split_bar(tracker):
        """Color-coded damage-split bar + legend with category names."""
        split_canvas.delete("all")
        w = max(split_canvas.winfo_width(), 500)
        totals = {c: getattr(tracker, f"{c}_dmg_out") for c in CATEGORIES}
        total = sum(totals.values())
        split_canvas.create_text(0, 10, anchor="w", fill=INK,
                                 font=FONT_TITLE, text="Damage given by source")
        if not total:
            split_canvas.create_text(0, 34, anchor="w", fill=SUBTLE,
                                     font=FONT, text="(no damage this session)")
            return
        x = 0
        for c in CATEGORIES:
            if not totals[c]:
                continue
            bw = int((w - 2) * totals[c] / total)
            split_canvas.create_rectangle(x, 24, x + max(bw, 1), 40,
                                          fill=CAT_COLORS[c], outline="")
            x += max(bw, 1)
        lx = 0
        for c in CATEGORIES:
            if not totals[c]:
                continue
            pct = round(100 * totals[c] / total)
            txt = f"{CATEGORY_LABELS[c]} {pct}%"
            split_canvas.create_rectangle(lx, 48, lx + 9, 57,
                                          fill=CAT_COLORS[c], outline="")
            t = split_canvas.create_text(lx + 13, 52, anchor="w", text=txt,
                                         fill=SUBTLE, font=FONT_SMALL)
            lx = split_canvas.bbox(t)[2] + 14

    # -- Graphs tab ---------------------------------------------------------------
    graphs = tk.Frame(nb, padx=8, pady=6, bg=BG)
    nb.add(graphs, text="Graphs")
    graph_controls = tk.Frame(graphs, bg=BG)
    graph_controls.pack(fill="x")
    tk.Label(graph_controls, text="Show:", font=FONT_SMALL, bg=BG).pack(side="left")
    GRAPH_CHOICES = ("Damage by ability", "Healing by ability",
                     "DPS per fight (last 20)")
    graph_var = tk.StringVar(value=GRAPH_CHOICES[0])
    graph_box = ttk.Combobox(graph_controls, textvariable=graph_var,
                             state="readonly", values=GRAPH_CHOICES, width=22)
    graph_box.pack(side="left", padx=(2, 10))
    graph_filter_holder = tk.Frame(graphs, bg=BG)
    graph_filter_holder.pack(fill="x")
    graph_canvas = tk.Canvas(graphs, bg=PANEL, highlightthickness=0)
    graph_canvas.pack(fill="both", expand=True, pady=(4, 0))
    graph_hint = tk.Label(
        graphs, bg=BG, fg=SUBTLE, font=FONT_SMALL, justify="left",
        text="Bars are named after the actual spell/ability from the log. "
             "Use Category + Search to compare just the things you're "
             "testing. DPS-per-fight: green = above this session's average.")
    graph_hint.pack(anchor="w", pady=(2, 0))

    def redraw_graph(*_):
        tracker = state["tracker"]
        if tracker is None:
            return
        choice = graph_var.get()
        if choice == "Damage by ability":
            rows = []
            for name, s in tracker.ability_rows(kind="dmg"):
                if not ability_passes(name, s):
                    continue
                color = CAT_COLORS.get(s["category"], MUTED_BAR)
                crit = f", {s['crits']} crit" if s["crits"] else ""
                rows.append((name, s["total"], color,
                             f"({s['hits']} hits{crit}, big {_fmt_num(s['biggest'])})"))
            draw_hbar_chart(graph_canvas, rows[:16], "Damage by ability",
                            "(nothing matches the current filter)")
        elif choice == "Healing by ability":
            rows = []
            for name, s in tracker.ability_rows(kind="heal"):
                needle = filter_text.get().strip().lower()
                if needle and needle not in name.lower():
                    continue
                rows.append((name, s["total"], HEAL_COLOR,
                             f"({s['hits']} casts, big {_fmt_num(s['biggest'])})"))
            draw_hbar_chart(graph_canvas, rows[:16], "Healing by ability",
                            "(no healing matches the current filter)")
        else:   # DPS per fight
            fights = [f for f in reversed(tracker.history)
                      if f.actors.get(YOU_LABEL)]
            bars = []
            vals = []
            for f in fights:
                you = f.actor(YOU_LABEL)
                dps = you["dmg_out"] / f.elapsed()
                vals.append(dps)
                t = datetime.fromtimestamp(f.start_wall).strftime("%H:%M")
                bars.append((t, dps, None, f"{dps:.0f}"))
            avg = sum(vals) / len(vals) if vals else 0
            bars = [(x, v, GOOD if v >= avg else MUTED_BAR, tl)
                    for (x, v, _c, tl) in bars]
            draw_vbar_chart(graph_canvas, bars,
                            "Your DPS per fight (active combat time, chronological)",
                            avg=avg, avg_label=f"avg {avg:.1f}")

    graph_box.bind("<<ComboboxSelected>>", redraw_graph)
    make_filter_row(graph_filter_holder,
                    lambda: (redraw_graph(), refresh_ability_tables())
                    ).pack(fill="x")
    graph_canvas.bind("<Configure>", redraw_graph)

    # -- Abilities: Damage tab -----------------------------------------------------
    dmg_frame = tk.Frame(nb, bg=BG)
    nb.add(dmg_frame, text="Damage by Ability")
    make_filter_row(dmg_frame, lambda: (refresh_ability_tables(),
                                        redraw_graph())).pack(fill="x", padx=4)
    dmg_tree = ttk.Treeview(dmg_frame,
                            columns=("total", "hits", "crits", "biggest", "type"),
                            show="tree headings")
    for col, label, w in (("total", "Total", 100), ("hits", "Hits", 60),
                          ("crits", "Crits", 60), ("biggest", "Biggest", 80),
                          ("type", "Type", 90)):
        dmg_tree.heading(col, text=label)
        dmg_tree.column(col, width=w, anchor="center")
    dmg_tree.column("#0", width=220)
    dmg_tree.heading("#0", text="Ability")
    dmg_tree.pack(fill="both", expand=True, padx=4, pady=(0, 4))
    setup_stripes(dmg_tree)

    # -- Abilities: Healing tab ------------------------------------------------------
    heal_frame = tk.Frame(nb, bg=BG)
    nb.add(heal_frame, text="Healing by Ability")
    heal_tree = ttk.Treeview(heal_frame, columns=("total", "hits", "biggest"),
                             show="tree headings")
    for col, label, w in (("total", "Total", 100), ("hits", "Casts", 60),
                          ("biggest", "Biggest", 80)):
        heal_tree.heading(col, text=label)
        heal_tree.column(col, width=w, anchor="center")
    heal_tree.column("#0", width=220)
    heal_tree.heading("#0", text="Spell")
    heal_tree.pack(fill="both", expand=True, padx=4, pady=4)
    setup_stripes(heal_tree)

    def refresh_ability_tables():
        tracker = state["tracker"]
        if tracker is None:
            return
        dmg_tree.delete(*dmg_tree.get_children())
        for name, s in tracker.ability_rows(kind="dmg"):
            if not ability_passes(name, s):
                continue
            striped_insert(dmg_tree, name, (
                _fmt_num(s["total"]), s["hits"], s["crits"],
                _fmt_num(s["biggest"]),
                CATEGORY_LABELS.get(s["category"], s["category"])))
        heal_tree.delete(*heal_tree.get_children())
        for name, s in tracker.ability_rows(kind="heal"):
            striped_insert(heal_tree, name, (
                _fmt_num(s["total"]), s["hits"], _fmt_num(s["biggest"])))

    # -- Sessions tab ------------------------------------------------------------
    sess_frame = tk.Frame(nb, padx=8, pady=6, bg=BG)
    nb.add(sess_frame, text="Sessions")

    records_lbl = tk.Label(sess_frame, bg=BG, fg=RECORD, font=FONT_BOLD,
                           justify="left", anchor="w")
    records_lbl.pack(fill="x")

    sess_cols = ("start", "zone", "len", "fights", "dps", "kills", "kph",
                 "deaths", "big")
    sess_tree = ttk.Treeview(sess_frame, columns=sess_cols,
                             show="tree headings", height=8)
    for col, label, w in (("start", "Start", 150), ("zone", "Zone", 120),
                          ("len", "Length", 60), ("fights", "Fights", 50),
                          ("dps", "Avg DPS", 70), ("kills", "Kills", 50),
                          ("kph", "Kills/hr", 60), ("deaths", "Deaths", 50),
                          ("big", "Big hit", 60)):
        sess_tree.heading(col, text=label)
        sess_tree.column(col, width=w,
                         anchor="w" if col in ("start", "zone") else "center")
    sess_tree.column("#0", width=32)
    sess_tree.heading("#0", text="#")
    sess_tree.pack(fill="x")
    setup_stripes(sess_tree)

    sess_chart_row = tk.Frame(sess_frame, bg=BG)
    sess_chart_row.pack(fill="x", pady=(6, 0))
    tk.Label(sess_chart_row, text="Compare by:", font=FONT_SMALL,
             bg=BG).pack(side="left")
    SESS_METRICS = (("Avg combat DPS", "avg_dps"),
                    ("Kills per hour", "kph"),
                    ("Total damage", "dmg"),
                    ("Biggest hit", "biggest"))
    sess_metric_var = tk.StringVar(value=SESS_METRICS[0][0])
    sess_metric_box = ttk.Combobox(
        sess_chart_row, textvariable=sess_metric_var, state="readonly",
        values=[m for m, _ in SESS_METRICS], width=16)
    sess_metric_box.pack(side="left", padx=(2, 8))
    tk.Label(sess_chart_row, bg=BG, fg=SUBTLE, font=FONT_SMALL,
             text="★ best session for the metric · double-click a row to "
                  "open that session").pack(side="left")

    sess_canvas = tk.Canvas(sess_frame, bg=PANEL, highlightthickness=0,
                            height=210)
    sess_canvas.pack(fill="both", expand=True, pady=(4, 0))

    def ensure_summaries():
        if state["summaries"] is None:
            state["summaries"] = [summarize_session(s, log_path)
                                  for s in state["sessions"]]
            char = char_name_from(log_path)
            state["records"], state["new_records"] = \
                update_records(char, state["summaries"])
        return state["summaries"]

    def refresh_sessions_tab():
        summaries = ensure_summaries()
        recs = state["records"]
        bits = []
        for key in ("best_avg_dps", "best_kph", "biggest_hit"):
            r = recs.get(key)
            if not r:
                continue
            new = "  🏆 NEW RECORD!" if key in state["new_records"] else ""
            zone = f" in {r['zone']}" if r.get("zone") else ""
            bits.append(f"{RECORD_TITLES[key]}: {r['display']}"
                        f"  ({r['when']}{zone}){new}")
        records_lbl.config(text="Personal records —  " + "   |   ".join(bits)
                           if bits else "Personal records: none yet -- go fight something!")

        best_dps = max((s["avg_dps"] for s in summaries
                        if s["fights"] >= RECORD_MIN_FIGHTS), default=None)
        best_kph = max((s["kph"] for s in summaries
                        if s["kills"] >= RECORD_MIN_KILLS), default=None)
        sess_tree.delete(*sess_tree.get_children())
        for i, s in enumerate(summaries):
            star_d = " ★" if best_dps is not None and s["avg_dps"] == best_dps else ""
            star_k = " ★" if best_kph is not None and s["kph"] == best_kph else ""
            tags = ("best",) if (star_d or star_k) else ()
            sess_tree.insert("", "end", text=str(i + 1), tags=tags, values=(
                s["ts"], s["zone"] or "?", _fmt_minutes(s["minutes"]),
                s["fights"], f"{s['avg_dps']:.1f}{star_d}", s["kills"],
                f"{s['kph']:.1f}{star_k}", s["deaths"],
                _fmt_num(s["biggest"])))
        redraw_sess_chart()

    def redraw_sess_chart(*_):
        summaries = state["summaries"]
        if not summaries:
            return
        key = dict(SESS_METRICS)[sess_metric_var.get()]
        vals = [s[key] for s in summaries]
        best = max(range(len(vals)), key=lambda i: vals[i]) \
            if any(v > 0 for v in vals) else None
        bars = []
        for i, s in enumerate(summaries):
            v = s[key]
            top = f"{v:.1f}" if key in ("avg_dps", "kph") else _fmt_num(v)
            color = ACCENT if i == best else MUTED_BAR
            bars.append((str(i + 1), v, color, top))
        draw_vbar_chart(sess_canvas, bars,
                        f"{sess_metric_var.get()} by session", best=best)

    sess_metric_box.bind("<<ComboboxSelected>>", redraw_sess_chart)
    sess_canvas.bind("<Configure>", redraw_sess_chart)

    def open_session_row(_e):
        sel = sess_tree.selection()
        if not sel:
            return
        idx = sess_tree.index(sel[0])
        if 0 <= idx < len(state["sessions"]):
            session_box.current(idx)
            refresh()
            nb.select(overview)

    sess_tree.bind("<Double-Button-1>", open_session_row)

    # -- Stance / Invocation tab ----------------------------------------------------
    combo_frame = tk.Frame(nb, padx=12, pady=12, bg=BG)
    nb.add(combo_frame, text="Stance / Invocation")
    tk.Label(combo_frame, text="Stances (known effects from eqlwiki.com)",
             font=FONT_BOLD, bg=BG).pack(anchor="w")
    stance_tree = ttk.Treeview(combo_frame,
                               columns=("fights", "dps", "dtps", "effect"),
                               show="tree headings", height=4)
    for col, label, w in (("fights", "Fights", 60), ("dps", "Avg DPS", 90),
                          ("dtps", "Avg DTPS", 90), ("effect", "Known effect", 320)):
        stance_tree.heading(col, text=label)
        stance_tree.column(col, width=w, anchor="w" if col == "effect" else "center")
    stance_tree.column("#0", width=150)
    stance_tree.heading("#0", text="Stance")
    stance_tree.pack(fill="x", pady=(2, 12))
    setup_stripes(stance_tree)

    tk.Label(combo_frame, text="Invocations (known effects from eqlwiki.com)",
             font=FONT_BOLD, bg=BG).pack(anchor="w")
    invoc_tree = ttk.Treeview(combo_frame,
                              columns=("fights", "dps", "dtps", "effect"),
                              show="tree headings", height=4)
    for col, label, w in (("fights", "Fights", 60), ("dps", "Avg DPS", 90),
                          ("dtps", "Avg DTPS", 90), ("effect", "Known effect", 320)):
        invoc_tree.heading(col, text=label)
        invoc_tree.column(col, width=w, anchor="w" if col == "effect" else "center")
    invoc_tree.column("#0", width=150)
    invoc_tree.heading("#0", text="Invocation")
    invoc_tree.pack(fill="x", pady=(2, 4))
    setup_stripes(invoc_tree)

    tk.Label(combo_frame,
             text="Grouped by whichever Stance/Invocation was active when each "
                  "fight started, averaged across all completed fights in that "
                  "bucket (rates use ACTIVE combat time). More fights = a more "
                  "trustworthy number.",
             wraplength=760, justify="left", font=FONT_SMALL, fg=SUBTLE,
             bg=BG).pack(anchor="w", pady=(8, 0))

    # -- Spell casts tab --------------------------------------------------------------
    casts_frame = tk.Frame(nb, bg=BG)
    nb.add(casts_frame, text="Spells Cast")
    casts_tree = ttk.Treeview(casts_frame,
                              columns=("count", "mana", "cast", "recast"),
                              show="tree headings")
    for col, label, w in (("count", "Casts", 60), ("mana", "Mana", 70),
                          ("cast", "Cast Time", 80), ("recast", "Recast", 80)):
        casts_tree.heading(col, text=label)
        casts_tree.column(col, width=w, anchor="center")
    casts_tree.column("#0", width=220)
    casts_tree.heading("#0", text="Spell")
    casts_tree.pack(fill="both", expand=True, padx=4, pady=4)
    setup_stripes(casts_tree)

    # -- Passive Healing (estimate) tab -----------------------------------------------
    heal_est_frame = tk.Frame(nb, padx=12, pady=12, bg=BG)
    nb.add(heal_est_frame, text="Passive Healing (est.)")

    heal_est_controls = tk.Frame(heal_est_frame, bg=BG)
    heal_est_controls.pack(fill="x")
    tk.Label(heal_est_controls, text="Class:", bg=BG).pack(side="left")
    heal_class_var = tk.StringVar(value="Bard")
    heal_class_menu = ttk.Combobox(heal_est_controls, textvariable=heal_class_var,
                                   values=CLASS_NAMES, state="readonly", width=14)
    heal_class_menu.pack(side="left", padx=(4, 12))
    tk.Label(heal_est_controls, text="Caster level:", bg=BG).pack(side="left")
    heal_level_var = tk.StringVar(value="50")
    heal_level_entry = tk.Entry(heal_est_controls, textvariable=heal_level_var,
                                width=5, font=FONT)
    heal_level_entry.pack(side="left", padx=(4, 12))

    heal_est_tree = ttk.Treeview(
        heal_est_frame, columns=("minlvl", "base", "formula", "max", "est"),
        show="tree headings")
    for col, label, w in (("minlvl", "Min Lvl", 60), ("base", "Base", 60),
                          ("formula", "Formula", 70), ("max", "Max", 60),
                          ("est", "Est./tick", 90)):
        heal_est_tree.heading(col, text=label)
        heal_est_tree.column(col, width=w, anchor="center")
    heal_est_tree.column("#0", width=280)
    heal_est_tree.heading("#0", text="Spell")
    heal_est_tree.pack(fill="both", expand=True, pady=(8, 0))
    setup_stripes(heal_est_tree)

    def refresh_heal_estimates(*_ignored):
        heal_est_tree.delete(*heal_est_tree.get_children())
        try:
            level = max(1, int(heal_level_var.get()))
        except ValueError:
            level = 50
            heal_level_var.set("50")
        cls = heal_class_var.get()
        for info in SPELL_DB.find_class_heals(cls, max_level=level):
            hp = info.hp_effects()[0]
            est = info.estimated_hp_value(level)
            striped_insert(heal_est_tree, info.name, (
                info.min_level_for(cls), hp.base_value, hp.formula,
                hp.max_value, f"{est:g}" if est is not None else ""))

    heal_class_menu.bind("<<ComboboxSelected>>", refresh_heal_estimates)
    heal_level_entry.bind("<Return>", refresh_heal_estimates)
    tk.Button(heal_est_controls, text="Refresh",
              command=refresh_heal_estimates).pack(side="left")

    tk.Label(heal_est_frame,
             text="Beneficial spells for the selected class with a positive "
                  "SPA-0 (HP) effect -- candidate heals / heal-over-time "
                  "songs, pulled straight from spells_us.txt. ESTIMATES from "
                  "the spell's own base/formula/max data (EQEmu classic-era "
                  "reference math, not confirmed as EQL's exact behavior); "
                  "PER-TICK for heal-over-time effects. There's no log line "
                  "showing which song was active, so cross-check a guess "
                  "manually.",
             wraplength=760, justify="left", font=FONT_SMALL, fg=SUBTLE,
             bg=BG).pack(anchor="w", pady=(10, 0))

    # -- Calibration tab -----------------------------------------------------------
    calib_frame = tk.Frame(nb, bg=BG)
    nb.add(calib_frame, text="Unrecognized lines")
    calib_txt = tk.Text(calib_frame, wrap="none", bg=PANEL, fg=INK,
                        font=FONT_MONO, relief="flat")
    calib_txt.pack(fill="both", expand=True, padx=4, pady=4)

    # -- refresh -----------------------------------------------------------------------
    def refresh():
        idx = session_box.current()
        sessions = state["sessions"]
        lines = sessions[idx]["lines"] \
            if sessions and 0 <= idx < len(sessions) else None
        tracker = build_tracker(log_path, lines)
        state["tracker"] = tracker

        dmg_total = sum(getattr(tracker, f"{c}_dmg_out") for c in CATEGORIES)

        def _pct(n):
            return f" ({round(100*n/dmg_total)}%)" if dmg_total else ""

        top_dmg = [n for n, _s in tracker.ability_rows(kind="dmg")[:3]]
        top_heal = [n for n, _s in tracker.ability_rows(kind="heal")[:2]]
        overview_lbl.config(text=(
            f"Session length:      {_fmt_minutes(tracker.session_elapsed()/60)}"
            f"        Fights: {tracker.fights_completed}"
            f"        Avg combat DPS: {tracker.avg_combat_dps():.1f}"
            f"   DTPS: {tracker.avg_combat_dtps():.1f}\n\n"
            f"Damage given (total):    {_fmt_num(dmg_total)}\n"
            f"  Melee:                 {_fmt_num(tracker.melee_dmg_out)}{_pct(tracker.melee_dmg_out)}\n"
            f"  Skill:                 {_fmt_num(tracker.skill_dmg_out)}{_pct(tracker.skill_dmg_out)}\n"
            f"  Spell:                 {_fmt_num(tracker.spell_dmg_out)}{_pct(tracker.spell_dmg_out)}\n"
            f"  Song:                  {_fmt_num(tracker.song_dmg_out)}{_pct(tracker.song_dmg_out)}\n"
            f"  Dmg Shield:            {_fmt_num(tracker.ds_dmg_out)}{_pct(tracker.ds_dmg_out)}\n"
            f"  Pet:                   {_fmt_num(tracker.pet_dmg_out)}{_pct(tracker.pet_dmg_out)}\n"
            f"Top damage:              {', '.join(top_dmg) or '--'}\n\n"
            f"Physical damage taken:   {_fmt_num(tracker.physical_dmg_in)}\n"
            f"Spell/Song dmg taken:    {_fmt_num(tracker.spell_dmg_in + tracker.song_dmg_in)}\n"
            f"Dmg Shield taken:        {_fmt_num(tracker.ds_dmg_in)}\n"
            f"Damage your pet took:    {_fmt_num(tracker.pet_dmg_in)}\n\n"
            f"Healing given:           {_fmt_num(tracker.heal_out_total)}"
            f"{('   (' + ', '.join(top_heal) + ')') if top_heal else ''}\n"
            f"Healing received:        {_fmt_num(tracker.heal_in_total)}\n\n"
            f"Kills:                   {len(tracker.kills)}  ({tracker.kills_per_hour():.1f}/hr)\n"
            f"Deaths:                  {len(tracker.deaths)}\n\n"
            f"Current Stance:          {tracker.stance or 'unknown'}\n"
            f"Current Invocation:      {tracker.invocation or 'unknown'}\n"
        ))
        draw_split_bar(tracker)
        refresh_ability_tables()
        redraw_graph()

        stance_tree.delete(*stance_tree.get_children())
        perf = tracker.stance_performance()
        for name in list(STANCES) + [k for k in perf if k not in STANCES]:
            g = perf.get(name)
            if not g:
                continue
            striped_insert(stance_tree, name, (
                g["fights"], f"{g['avg_dps']:.1f}", f"{g['avg_dtps']:.1f}",
                STANCES.get(name, "")))

        invoc_tree.delete(*invoc_tree.get_children())
        perf = tracker.invocation_performance()
        for name in list(INVOCATIONS) + [k for k in perf if k not in INVOCATIONS]:
            g = perf.get(name)
            if not g:
                continue
            striped_insert(invoc_tree, name, (
                g["fights"], f"{g['avg_dps']:.1f}", f"{g['avg_dtps']:.1f}",
                INVOCATIONS.get(name, "")))

        casts_tree.delete(*casts_tree.get_children())
        for name, count in sorted(tracker.spell_casts.items(),
                                  key=lambda kv: -kv[1]):
            info = SPELL_DB.lookup(name)
            mana = info.mana if info else ""
            cast = f"{info.cast_time_s:.1f}s" if info else ""
            recast = f"{info.recast_time_s}s" if info else ""
            striped_insert(casts_tree, name, (count, mana, cast, recast))

        calib_txt.configure(state="normal")
        calib_txt.delete("1.0", "end")
        if tracker.unmatched:
            calib_txt.insert("1.0", "\n".join(tracker.unmatched))
        else:
            calib_txt.insert("1.0", "(nothing unrecognized -- every combat-"
                                    "flavored line in this log matched a "
                                    "known pattern)")
        calib_txt.configure(state="disabled")

        refresh_sessions_tab()

    def change_log():
        nonlocal log_path
        chosen = filedialog.askopenfilename(
            title="Select your EverQuest log file (eqlog_*.txt)",
            initialdir=os.path.dirname(log_path) or None,
            filetypes=[("EQ log files", "eqlog_*.txt"),
                       ("Text files", "*.txt"), ("All files", "*.*")])
        if chosen and os.path.isfile(chosen):
            log_path = chosen
            path_lbl.config(text=log_path)
            root.title(f"EQL Session Report -- "
                       f"{char_name_from(log_path) or 'Unknown'}")
            reload_session_list()
            refresh()

    def refresh_full():
        """Re-read the log (new sessions may have been appended), keep the
        user's session choice if it still exists, then rebuild."""
        prev = session_box.current()
        prev_len = len(state["sessions"])
        reload_session_list()
        if 0 <= prev < prev_len - 1 and prev < len(state["sessions"]):
            session_box.current(prev)   # was viewing an older session: keep it
        refresh()

    session_box.bind("<<ComboboxSelected>>", lambda e: refresh())
    tk.Button(top, text="Change log...", command=change_log).pack(
        side="right", padx=(6, 0))
    tk.Button(top, text="Refresh", command=refresh_full).pack(side="right")

    reload_session_list()
    refresh()
    refresh_heal_estimates()
    root.mainloop()


def main():
    log_path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not log_path or not os.path.isfile(log_path):
        hidden = tk.Tk(); hidden.withdraw()
        log_path = filedialog.askopenfilename(
            title="Select your EverQuest log file (eqlog_*.txt)",
            filetypes=[("EQ log files", "eqlog_*.txt"),
                      ("Text files", "*.txt"), ("All files", "*.*")])
        hidden.destroy()
    if not log_path or not os.path.isfile(log_path):
        print("No log file selected/found. Pass the path as an argument:")
        print('  python eql_session_report.py "C:\\EQ\\Logs\\eqlog_Name_server.txt"')
        sys.exit(1)
    run_report(log_path)


if __name__ == "__main__":
    main()
