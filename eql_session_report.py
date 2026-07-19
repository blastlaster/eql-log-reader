#!/usr/bin/env python3
"""
EQL Session Report
====================
A detailed, read-once breakdown of a play session: damage split eight
ways (Melee / Skill / Ranged / Spell / Poison / Song / Damage Shield /
Pet), heal totals, kill rate, which spell/ability is carrying your damage
or healing, and how your Stance/Invocation choices compare in practice. This is the deep-dive
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

The report uses the suite's shared theme set (RETRO_THEMES in
eql_overlay_common; "16-bit Window" by default) -- pick another from the
"Theme" dropdown in the top bar. The transparent Neon HUD theme is overlay-
only and isn't offered here (a chroma-keyed background makes no sense for
a regular window). The choice persists to eql_session_report_settings.json
next to this script.

Tabs
-----
* Overview     -- the dashboard: headline stat cards (length, DPS, kills,
                  biggest hit, ...), the color-coded damage-split bar,
                  damage TAKEN by type, top damage + healing abilities as
                  bar charts, DPS per fight over time, and the
                  Stance/Invocation performance tables -- one scrollable
                  page, most of the session at a glance.
* Abilities    -- the full sortable data: damage by ability and healing by
                  ability tables (with category filter + name search), and
                  every spell cast with mana/cast/recast from
                  spells_us.txt.
* Sessions     -- EVERY session in the log side by side: length, fights,
                  avg combat DPS, kills/hr, deaths, and the BUILD (/who
                  class combination) each session ran under. The best
                  session per metric gets a star, a chart compares them
                  visually, a Build filter narrows the table+chart to one
                  class combination (compare your builds against each
                  other), and PERSONAL RECORDS persist to a per-character
                  JSON across runs -- beat one and it's flagged NEW
                  RECORD. Double-click a session row to open that session.
* Encounters   -- every mob you've fought across EVERY session in the
                  log: search a name, see each attempt (when, session,
                  zone, duration, DPS, damage dealt/taken, deaths,
                  stance), open one for its full detail (abilities,
                  heals, casts, resists, rates at /s /m /h), and compare
                  two -- best-vs-worst in one click -- with a ranked
                  "what changed" analysis of the likely impact drivers
                  (ability mix shares, stance/invocation, resist rate,
                  damage taken, accuracy, deaths).
* Diagnostics  -- Passive Healing (est.) reference math and the
                  unrecognized-lines calibration view.

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

Passive Healing (est.): some effects -- notably a Bard's heal-over-time
songs -- never produce a "healed X for N hit points" log line at all.
The Diagnostics tab estimates candidate heal magnitudes from
`spells_us.txt` itself (EQEmu classic-era reference math, PER-TICK for
HoTs -- cross-check a manual guess, don't treat as exact).
"""

import json
import os
import re
import sys
import tkinter as tk
from datetime import datetime
from tkinter import ttk, filedialog

from eql_combat_tracker import (
    CombatTracker, YOU_LABEL, PET_LABEL, STANCES, INVOCATIONS,
    CATEGORY_LABELS, CATEGORIES,
)
from eql_overlay_common import (Settings, RETRO_THEMES, DEFAULT_THEME,
                                get_theme, data_path,
                                install_tk_error_logger)
from eql_spell_db import SPELL_DB, CLASS_NAMES

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = data_path("eql_session_report_settings.json", APP_DIR)
ERROR_LOG = data_path("eql_errors.log", APP_DIR)

SESSION_MARK = "Welcome to EverQuest Legends!"
ZONE_RE = re.compile(r"You have entered (.+)\.")

# The report is a regular window, so transparent (chroma-key) themes are
# excluded from its picker -- those only make sense floating over the game.
SUITE_THEMES = {k: v for k, v in RETRO_THEMES.items()
                if not v.get("transparent")}


def _report_theme_key(key):
    """Clamp a saved theme key to one this window can actually use."""
    return key if key in SUITE_THEMES else DEFAULT_THEME


# -- report palette ----------------------------------------------------------
# Derived from the suite-wide shared theme set (RETRO_THEMES in
# eql_overlay_common; "16-bit Window" by default) so this report matches the
# overlays. apply_palette() rebinds the module-level names every module-level
# draw helper reads; picking a new theme from the top bar rebuilds the whole
# window with the new palette.
def _mix(c1, c2, t):
    """Blend two '#rrggbb' colors: t=0 -> c1, t=1 -> c2. Used to derive the
    report-only tones (gridlines, row stripes, highlights) that the compact
    overlay themes don't define directly."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def apply_palette(theme_key):
    global THEME, BG, PANEL, INK, SUBTLE, GRID, ACCENT, GOOD, MUTED_BAR
    global BEST_BG, RECORD, STRIPE, HEAD_BG, CAT_COLORS, HEAL_COLOR
    global FONT, FONT_SMALL, FONT_BOLD, FONT_TITLE, FONT_MONO, FONT_BIG
    th = get_theme(_report_theme_key(theme_key))
    THEME = th
    BG = th["bg"]                        # window background
    PANEL = th["panel"]                  # card/table background
    INK = th["fg"]                       # main text
    SUBTLE = th["dim"]                   # captions / secondary text
    GRID = _mix(PANEL, INK, 0.18)        # separators, chart gridlines
    ACCENT = th["accent"]                # interactive highlights
    GOOD = th["alt"]                     # above-average bars
    MUTED_BAR = _mix(PANEL, INK, 0.35)   # below-average bars
    BEST_BG = _mix(PANEL, th["warn"], 0.25)   # best-session row highlight
    RECORD = th["warn"]                  # personal-record gold
    STRIPE = _mix(PANEL, BG, 0.5)        # odd table rows
    HEAD_BG = _mix(PANEL, INK, 0.12)     # table headings

    CAT_COLORS = {   # one color per damage category, used everywhere
        "melee": th["alt"], "skill": th["accent"], "spell": th["warn"],
        "song": th["bad"], "ds": th["fg"], "pet": th["dim"],
        # ranged/poison blend two roles -- the compact overlay themes only
        # define six, and these two must stay distinguishable from all
        "ranged": _mix(th["accent"], th["warn"], 0.5),
        "poison": _mix(th["alt"], th["bad"], 0.5),
    }
    HEAL_COLOR = th["alt"]

    fam = th["font_mono"][0]
    FONT = (fam, 9)
    FONT_SMALL = (fam, 8)
    FONT_BOLD = (fam, 9, "bold")
    FONT_TITLE = (fam, 10, "bold")
    FONT_MONO = (fam, 10)
    FONT_BIG = (fam, 15, "bold")


apply_palette(DEFAULT_THEME)

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
    dmg_total = sum(getattr(tr, f"{c}_dmg_out") for c in CATEGORIES)
    biggest = max((s["biggest"] for s in tr.abilities_dmg.values()),
                  default=0)
    return {
        "label": sess["label"], "ts": sess["ts"], "zone": sess["zone"],
        # the class combination /who printed during THIS session ("?" when
        # no /who ran) -- the Sessions tab groups/filters by it so builds
        # can be compared against each other
        "classes": tr.player_classes or "?",
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


# -- encounters: per-mob fight history across every session ------------------
# The log IS the database: every completed Fight in every session becomes an
# "encounter" dict, grouped by the mob it was mostly against. Cross-session
# comparison (best vs worst attempt on the same boss) falls out for free.

_ARTICLE_RE = re.compile(r"^(?:an?|the)\s+", re.IGNORECASE)


def _mob_key(name):
    """Grouping key for a mob name: article-free, case-insensitive."""
    return _ARTICLE_RE.sub("", name).strip().lower()


def encounter_from_fight(fight, si, sess):
    """One completed Fight -> encounter dict, or None if it had no enemy
    (e.g. a heal-only stretch)."""
    you = fight.actors.get(YOU_LABEL)
    mobs = fight.enemies()
    if not you or not mobs or you["dmg_out"] + you["dmg_in"] <= 0:
        return None
    active = fight.elapsed()
    swings = you["hits"] + you["misses"]
    when = datetime.fromtimestamp(fight.start_wall)
    return {
        "mob": mobs[0][0],
        "mob_key": _mob_key(mobs[0][0]),
        "mobs": [(n, a["dmg_in"], a["dmg_out"]) for n, a in mobs],
        "session_i": si, "session_ts": sess["ts"], "zone": sess["zone"],
        "start": fight.start_wall,
        "when": when.strftime("%a %b %d %H:%M"),
        "span": fight.span(), "active": active,
        "dps": you["dmg_out"] / active,
        "dtps": you["dmg_in"] / active,
        "dmg": you["dmg_out"], "taken": you["dmg_in"],
        "healed": you["heal_out"],
        "acc": round(100 * you["hits"] / swings) if swings else 0,
        "critpct": round(100 * you["crits"] / you["hits"]) if you["hits"] else 0,
        "big": you["biggest_hit"],
        "kills": fight.kills, "deaths": fight.deaths,
        "stance": fight.stance or "?",
        "invocation": fight.invocation or "?",
        "abilities": {k: dict(v) for k, v in fight.abilities_dmg.items()},
        "heals": {k: dict(v) for k, v in fight.abilities_heal.items()},
        "casts": dict(fight.spell_casts),
        "resists": dict(fight.spell_resists),
    }


def collect_encounters(log_path, sessions):
    """Replay every session, collecting each completed fight."""
    SPELL_DB.set_game_dir_hint(os.path.dirname(os.path.dirname(log_path)))
    out = []
    me = char_name_from(log_path)
    for si, sess in enumerate(sessions):
        tracker = CombatTracker(self_name=me)
        fights = []
        tracker.fight_listeners.append(fights.append)
        for line in sess["lines"]:
            tracker.handle_line(line)
        tracker.force_end_fight()
        for f in fights:
            enc = encounter_from_fight(f, si, sess)
            if enc:
                out.append(enc)
    return out


def encounter_diff(a, b):
    """Ranked, plain-language differences between encounter `a` (the better
    one) and `b` (the worse one) -- what changed, biggest likely impact
    first. Heuristic: relative deltas of the levers you control (ability
    mix, stance, resists, damage taken), scored by magnitude."""
    diffs = []

    def add(score, text):
        diffs.append((score, text))

    if b["dps"] > 0:
        rel = (a["dps"] - b["dps"]) / b["dps"]
        add(999, f"your DPS: {a['dps']:.1f} vs {b['dps']:.1f} "
                 f"({rel:+.0%})")
    if a["stance"] != b["stance"]:
        add(3.0, f"stance: {a['stance']} vs {b['stance']}")
    if a["invocation"] != b["invocation"]:
        add(2.5, f"invocation: {a['invocation']} vs {b['invocation']}")

    # ability mix: share of your damage per ability
    sa = {k: v["total"] / max(a["dmg"], 1) for k, v in a["abilities"].items()}
    sb = {k: v["total"] / max(b["dmg"], 1) for k, v in b["abilities"].items()}
    for name in sorted(set(sa) | set(sb)):
        pa, pb = sa.get(name, 0.0), sb.get(name, 0.0)
        d = pa - pb
        if abs(d) >= 0.08:
            add(abs(d) * 6,
                f"{name}: {pa:.0%} of your damage vs {pb:.0%}")

    # resists per active minute -- casts that did nothing
    ra = sum(a["resists"].values()) / a["active"] * 60
    rb = sum(b["resists"].values()) / b["active"] * 60
    if abs(ra - rb) >= 0.5:
        add(min(abs(ra - rb) / 2, 4),
            f"spells resisted/min: {ra:.1f} vs {rb:.1f}")

    if b["dtps"] > 0.5 or a["dtps"] > 0.5:
        rel = (a["dtps"] - b["dtps"]) / max(b["dtps"], 0.5)
        if abs(rel) >= 0.25:
            add(min(abs(rel), 3),
                f"damage taken/s: {a['dtps']:.1f} vs {b['dtps']:.1f}")
    if abs(a["acc"] - b["acc"]) >= 4:
        add(abs(a["acc"] - b["acc"]) / 10,
            f"melee accuracy: {a['acc']}% vs {b['acc']}%")
    if abs(a["critpct"] - b["critpct"]) >= 3:
        add(abs(a["critpct"] - b["critpct"]) / 12,
            f"crit rate: {a['critpct']}% vs {b['critpct']}%")

    only_a = sorted(set(a["casts"]) - set(b["casts"]))
    only_b = sorted(set(b["casts"]) - set(a["casts"]))
    if only_a:
        add(1.5, "cast only in the better fight: " + ", ".join(only_a))
    if only_b:
        add(1.5, "cast only in the worse fight: " + ", ".join(only_b))

    ha = a["healed"] / a["active"]
    hb = b["healed"] / b["active"]
    if abs(ha - hb) >= 1 and max(ha, hb) > 0:
        add(min(abs(ha - hb) / max(hb, 1), 2),
            f"your healing/s: {ha:.1f} vs {hb:.1f}")
    if a["deaths"] != b["deaths"]:
        add(5.0, f"deaths: {a['deaths']} vs {b['deaths']}")

    diffs.sort(key=lambda kv: -kv[0])
    return [t for _, t in diffs]


def _fmt_num(n):
    n = int(n)
    if n >= 1_000_000:
        return f"{n/1_000_000:,.2f}M"
    if n >= 1000:
        return f"{n:,}"
    return str(n)


def _fmt_minutes(m):
    return f"{int(m)//60}h {int(m)%60:02d}m" if m >= 60 else f"{m:.0f}m"


def _ellipsize(s, n):
    return s if len(s) <= n else s[:n - 1] + "…"


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
# Canvas chart helpers (all theme-aware: pixel bevels, CRT/Arcade scanlines)
# ----------------------------------------------------------------------------
def _decorate(canvas, w, h):
    """Retro dressing for a chart canvas: scanlines on glow themes, a
    chunky bevel border on the pixel theme, nothing on Vintage."""
    if THEME.get("glow"):
        for y in range(0, h, 4):
            canvas.create_line(0, y, w, y, fill=THEME["scanline"])
    elif THEME.get("border_light"):
        canvas.create_rectangle(1, 1, w - 2, h - 2,
                                outline=THEME["border_light"], width=2)
        canvas.create_rectangle(0, 0, w - 1, h - 1,
                                outline=THEME["border_dark"], width=1)


def draw_hbar_chart(canvas, rows, title, empty_msg, max_note_w=130):
    """Horizontal bar chart: rows = (name, value, color, note). Names on
    the left, value + note on the bar's right. Scales to canvas width."""
    canvas.delete("all")
    w = max(canvas.winfo_width(), 320)
    h = max(canvas.winfo_height(), 80)
    _decorate(canvas, w, h)
    canvas.create_text(10, 14, anchor="w", text=title, fill=INK,
                       font=FONT_TITLE)
    if not rows:
        canvas.create_text(w // 2, h // 2, text=empty_msg, fill=SUBTLE,
                           font=FONT)
        return
    name_w = min(200, max(90, max(len(r[0]) for r in rows) * 7))
    x0 = name_w + 16
    x1 = w - max_note_w
    max_v = max(r[1] for r in rows) or 1
    y = 36
    row_h = 24
    for name, value, color, note in rows:
        canvas.create_text(name_w + 8, y + 8, anchor="e",
                           text=_ellipsize(name, 26), fill=INK, font=FONT)
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
    h = max(canvas.winfo_height(), 200)
    _decorate(canvas, w, h)
    canvas.create_text(10, 14, anchor="w", text=title, fill=INK,
                       font=FONT_TITLE)
    if not bars:
        canvas.create_text(w // 2, h // 2, text="(no data in this session)",
                           fill=SUBTLE, font=FONT)
        return
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
                               font=(FONT[0], 11))
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


def draw_stat_cards(canvas, cards):
    """Headline numbers as a wrapping row of retro stat cards:
    cards = (label, value, sub). Canvas height adjusts to the row count."""
    canvas.delete("all")
    w = max(canvas.winfo_width(), 320)
    card_w, card_h, gap = 152, 76, 8
    per_row = max(1, int((w - gap) // (card_w + gap)))
    rows = (len(cards) + per_row - 1) // per_row
    total_h = gap + rows * (card_h + gap)
    # only touch the height when it actually changes -- configuring it
    # unconditionally would fire <Configure> -> redraw -> configure forever
    if int(canvas["height"]) != total_h:
        canvas.configure(height=total_h)
    for i, (label, value, sub) in enumerate(cards):
        r, c = divmod(i, per_row)
        x = gap + c * (card_w + gap)
        y = gap + r * (card_h + gap)
        canvas.create_rectangle(x, y, x + card_w, y + card_h,
                                fill=PANEL, outline="")
        if THEME.get("glow"):
            for sy in range(y, y + card_h, 4):
                canvas.create_line(x, sy, x + card_w, sy,
                                   fill=THEME["scanline"])
        if THEME.get("border_light"):
            canvas.create_rectangle(x + 1, y + 1, x + card_w - 2,
                                    y + card_h - 2,
                                    outline=THEME["border_light"], width=2)
            canvas.create_rectangle(x, y, x + card_w - 1, y + card_h - 1,
                                    outline=THEME["border_dark"], width=1)
        else:
            canvas.create_rectangle(x, y, x + card_w, y + card_h,
                                    outline=GRID)
        canvas.create_text(x + 10, y + 14, anchor="w",
                           text=label.upper(), fill=SUBTLE, font=FONT_SMALL)
        canvas.create_text(x + 10, y + 38, anchor="w",
                           text=str(value), fill=ACCENT, font=FONT_BIG)
        if sub:
            canvas.create_text(x + 10, y + 61, anchor="w",
                               text=_ellipsize(str(sub), 22),
                               fill=SUBTLE, font=FONT_SMALL)


def draw_split_bar(canvas, tracker):
    """Color-coded damage-split bar + legend with category names."""
    canvas.delete("all")
    w = max(canvas.winfo_width(), 400)
    h = max(canvas.winfo_height(), 64)
    _decorate(canvas, w, h)
    pad = 10
    totals = {c: getattr(tracker, f"{c}_dmg_out") for c in CATEGORIES}
    total = sum(totals.values())
    canvas.create_text(pad, 14, anchor="w", fill=INK,
                       font=FONT_TITLE, text="Damage given by source")
    if not total:
        canvas.create_text(pad, 38, anchor="w", fill=SUBTLE,
                           font=FONT, text="(no damage this session)")
        return
    x = pad
    bar_w = w - 2 * pad
    for c in CATEGORIES:
        if not totals[c]:
            continue
        bw = int(bar_w * totals[c] / total)
        canvas.create_rectangle(x, 26, x + max(bw, 1), 44,
                                fill=CAT_COLORS[c], outline="")
        x += max(bw, 1)
    lx = pad
    for c in CATEGORIES:
        if not totals[c]:
            continue
        pct = round(100 * totals[c] / total)
        txt = f"{CATEGORY_LABELS[c]} {pct}%"
        canvas.create_rectangle(lx, 52, lx + 9, 61,
                                fill=CAT_COLORS[c], outline="")
        t = canvas.create_text(lx + 13, 56, anchor="w", text=txt,
                               fill=SUBTLE, font=FONT_SMALL)
        lx = canvas.bbox(t)[2] + 14


# ----------------------------------------------------------------------------
# Report window
# ----------------------------------------------------------------------------
def run_report(log_path):
    """Outer shell: builds the report window, and rebuilds it from scratch
    whenever a new theme is picked (every widget bakes its colors in at
    creation, so a clean rebuild beats chasing hundreds of configure()s).
    The window's size/position and the chosen log survive the rebuild."""
    settings = Settings(SETTINGS_FILE, {"theme": DEFAULT_THEME})
    ctx = {"log_path": log_path, "geometry": "1150x780"}
    while _report_window(ctx, settings):
        pass


def _report_window(ctx, settings):
    """One life of the report window. Returns True if it was closed by the
    theme picker (caller rebuilds), False if the user closed it."""
    apply_palette(settings.get("theme", DEFAULT_THEME))
    log_path = ctx["log_path"]
    restart = {"flag": False}

    root = tk.Tk()
    install_tk_error_logger(root, "eql_session_report", ERROR_LOG)
    root.title(f"EQL Session Report -- {char_name_from(log_path) or 'Unknown'}")
    root.geometry(ctx["geometry"])
    root.configure(bg=BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=BG, foreground=INK, font=FONT)
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(10, 5), font=FONT,
                    background=BG, foreground=SUBTLE)
    style.map("TNotebook.Tab",
              background=[("selected", PANEL)],
              foreground=[("selected", ACCENT)])
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                    foreground=INK, rowheight=22, font=FONT, borderwidth=0)
    style.map("Treeview",
              background=[("selected", ACCENT)],
              foreground=[("selected", BG)])
    style.configure("Treeview.Heading", font=FONT_BOLD, background=HEAD_BG,
                    foreground=INK, relief="flat")
    style.configure("TCombobox", fieldbackground=PANEL, background=PANEL,
                    foreground=INK, arrowcolor=INK)
    style.map("TCombobox",
              fieldbackground=[("readonly", PANEL)],
              foreground=[("readonly", INK)])
    style.configure("Vertical.TScrollbar", background=PANEL,
                    troughcolor=BG, arrowcolor=INK)
    # the combobox dropdown list is a plain Listbox; theme it via options
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", BG)
    root.option_add("*TCombobox*Listbox.font", FONT)

    def themed_button(parent, **kw):
        return tk.Button(parent, bg=PANEL, fg=INK, activebackground=ACCENT,
                         activeforeground=BG, relief="flat", font=FONT_SMALL,
                         padx=8, **kw)

    # -- top bar: two rows so the buttons can never be squeezed out ------------
    top = tk.Frame(root, padx=8, pady=6, bg=BG)
    top.pack(fill="x")

    row1 = tk.Frame(top, bg=BG)
    row1.pack(fill="x")
    # buttons are packed FIRST (from the right) so the expanding path label
    # can only ever shrink itself, not push them off the window
    change_btn = themed_button(row1, text="Change log...")
    change_btn.pack(side="right", padx=(6, 0))
    refresh_btn = themed_button(row1, text="Refresh")
    refresh_btn.pack(side="right")
    path_lbl = tk.Label(row1, text=log_path, anchor="w", font=FONT_SMALL,
                        fg=SUBTLE, bg=BG)
    path_lbl.pack(side="left", fill="x", expand=True)

    row2 = tk.Frame(top, bg=BG)
    row2.pack(fill="x", pady=(5, 0))
    tk.Label(row2, text="Session:", font=FONT_SMALL, bg=BG, fg=INK).pack(
        side="left")
    session_var = tk.StringVar()
    session_box = ttk.Combobox(row2, textvariable=session_var,
                               state="readonly", width=46)
    session_box.pack(side="left", padx=(2, 12))

    tk.Label(row2, text="Theme:", font=FONT_SMALL, bg=BG, fg=INK).pack(
        side="left")
    THEME_LABELS = {k: spec["label"] for k, spec in SUITE_THEMES.items()}
    LABEL_TO_THEME = {v: k for k, v in THEME_LABELS.items()}
    cur_key = _report_theme_key(settings.get("theme", DEFAULT_THEME))
    theme_var = tk.StringVar(value=THEME_LABELS[cur_key])
    theme_box = ttk.Combobox(row2, textvariable=theme_var, state="readonly",
                             values=list(THEME_LABELS.values()), width=16)
    theme_box.pack(side="left", padx=(2, 0))

    state = {"tracker": None, "encounters": None,
             "sessions": load_sessions(log_path),
             "summaries": None, "records": {}, "new_records": set()}

    def on_theme_pick(_e):
        key = LABEL_TO_THEME.get(theme_var.get(), DEFAULT_THEME)
        if key == settings.get("theme"):
            return
        settings["theme"] = key
        settings.save()
        ctx["geometry"] = root.geometry()   # rebuild in place
        restart["flag"] = True
        root.destroy()

    theme_box.bind("<<ComboboxSelected>>", on_theme_pick)

    def reload_session_list():
        state["sessions"] = load_sessions(log_path)
        state["summaries"] = None   # stale -- rebuilt lazily
        state["encounters"] = None
        labels = [s["label"] for s in state["sessions"]] or ["(empty log)"]
        session_box.configure(values=labels)
        session_box.current(len(labels) - 1)   # default: most recent

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=8, pady=(4, 8))

    def striped_insert(tree, name, values, tags=()):
        n = len(tree.get_children())
        tree.insert("", "end", text=name, values=values,
                    tags=tags + (("odd",) if n % 2 else ("even",)))

    def setup_stripes(tree):
        tree.tag_configure("odd", background=STRIPE)
        tree.tag_configure("even", background=PANEL)
        tree.tag_configure("best", background=BEST_BG, foreground=BG)

    # ==========================================================================
    # Overview tab -- the dashboard (scrollable)
    # ==========================================================================
    dash_outer = tk.Frame(nb, bg=BG)
    nb.add(dash_outer, text="Overview")
    dash_canvas = tk.Canvas(dash_outer, bg=BG, highlightthickness=0)
    dash_scroll = ttk.Scrollbar(dash_outer, orient="vertical",
                                command=dash_canvas.yview)
    dash = tk.Frame(dash_canvas, bg=BG)
    dash_win = dash_canvas.create_window((0, 0), window=dash, anchor="nw")
    dash_canvas.configure(yscrollcommand=dash_scroll.set)
    dash_scroll.pack(side="right", fill="y")
    dash_canvas.pack(side="left", fill="both", expand=True)
    dash.bind("<Configure>", lambda e: dash_canvas.configure(
        scrollregion=dash_canvas.bbox("all")))
    dash_canvas.bind("<Configure>", lambda e: dash_canvas.itemconfigure(
        dash_win, width=e.width))

    def _on_wheel(e):
        try:
            if nb.select() == str(dash_outer):
                dash_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        except tk.TclError:
            pass
    root.bind_all("<MouseWheel>", _on_wheel)

    # row 1: headline stat cards
    cards_canvas = tk.Canvas(dash, bg=BG, highlightthickness=0, height=92)
    cards_canvas.pack(fill="x", padx=4, pady=(4, 2))

    # row 2: damage split bar (left) + damage taken (right)
    split_row = tk.Frame(dash, bg=BG)
    split_row.pack(fill="x", padx=4, pady=2)
    split_canvas = tk.Canvas(split_row, bg=PANEL, highlightthickness=0,
                             height=110)
    split_canvas.pack(side="left", fill="both", expand=True, padx=(0, 4))
    taken_canvas = tk.Canvas(split_row, bg=PANEL, highlightthickness=0,
                             height=110, width=380)
    taken_canvas.pack(side="left", fill="y")

    # row 3: top damage abilities (left) + top healing (right)
    tops_row = tk.Frame(dash, bg=BG)
    tops_row.pack(fill="x", padx=4, pady=2)
    topdmg_canvas = tk.Canvas(tops_row, bg=PANEL, highlightthickness=0,
                              height=240)
    topdmg_canvas.pack(side="left", fill="both", expand=True, padx=(0, 4))
    topheal_canvas = tk.Canvas(tops_row, bg=PANEL, highlightthickness=0,
                               height=240)
    topheal_canvas.pack(side="left", fill="both", expand=True)

    # row 4: DPS per fight over the session
    dps_canvas = tk.Canvas(dash, bg=PANEL, highlightthickness=0, height=230)
    dps_canvas.pack(fill="x", padx=4, pady=2)

    # row 5: stance / invocation performance side by side
    combo_row = tk.Frame(dash, bg=BG)
    combo_row.pack(fill="x", padx=4, pady=(2, 8))

    stance_col = tk.Frame(combo_row, bg=BG)
    stance_col.pack(side="left", fill="both", expand=True, padx=(0, 4))
    stance_hdr = tk.Label(stance_col, text="Stances", font=FONT_TITLE,
                          bg=BG, fg=INK, anchor="w")
    stance_hdr.pack(fill="x")
    stance_tree = ttk.Treeview(stance_col,
                               columns=("fights", "dps", "dtps", "effect"),
                               show="tree headings", height=4)
    for col, label, w in (("fights", "Fights", 50), ("dps", "Avg DPS", 70),
                          ("dtps", "Avg DTPS", 70),
                          ("effect", "Known effect", 220)):
        stance_tree.heading(col, text=label)
        stance_tree.column(col, width=w,
                           anchor="w" if col == "effect" else "center")
    stance_tree.column("#0", width=120)
    stance_tree.heading("#0", text="Stance")
    stance_tree.pack(fill="x")
    setup_stripes(stance_tree)

    invoc_col = tk.Frame(combo_row, bg=BG)
    invoc_col.pack(side="left", fill="both", expand=True)
    invoc_hdr = tk.Label(invoc_col, text="Invocations", font=FONT_TITLE,
                         bg=BG, fg=INK, anchor="w")
    invoc_hdr.pack(fill="x")
    invoc_tree = ttk.Treeview(invoc_col,
                              columns=("fights", "dps", "dtps", "effect"),
                              show="tree headings", height=4)
    for col, label, w in (("fights", "Fights", 50), ("dps", "Avg DPS", 70),
                          ("dtps", "Avg DTPS", 70),
                          ("effect", "Known effect", 220)):
        invoc_tree.heading(col, text=label)
        invoc_tree.column(col, width=w,
                          anchor="w" if col == "effect" else "center")
    invoc_tree.column("#0", width=120)
    invoc_tree.heading("#0", text="Invocation")
    invoc_tree.pack(fill="x")
    setup_stripes(invoc_tree)

    tk.Label(dash, text="Stance/Invocation rows average completed fights "
                        "started under each; effects from eqlwiki.com. "
                        "More fights = a more trustworthy number.",
             font=FONT_SMALL, fg=SUBTLE, bg=BG, anchor="w",
             wraplength=900, justify="left").pack(fill="x", padx=6,
                                                  pady=(0, 8))

    # -- dashboard redraw helpers ----------------------------------------------
    def redraw_cards(*_):
        tracker = state["tracker"]
        if tracker is None:
            return
        dmg_total = sum(getattr(tracker, f"{c}_dmg_out") for c in CATEGORIES)
        top_dmg = [n for n, _s in tracker.ability_rows(kind="dmg")[:1]]
        big_name, big_val = "", 0
        for name, s in tracker.ability_rows(kind="dmg"):
            if s["biggest"] > big_val:
                big_name, big_val = name, s["biggest"]
        cards = [
            ("Session", _fmt_minutes(tracker.session_elapsed() / 60),
             f"{tracker.fights_completed} fights"),
            ("Avg DPS", f"{tracker.avg_combat_dps():.1f}", "active combat"),
            ("Avg DTPS", f"{tracker.avg_combat_dtps():.1f}", "damage taken"),
            ("Damage", _fmt_num(dmg_total),
             f"top: {top_dmg[0]}" if top_dmg else ""),
            ("Healing", _fmt_num(tracker.heal_out_total),
             f"received {_fmt_num(tracker.heal_in_total)}"),
            ("Kills", len(tracker.kills),
             f"{tracker.kills_per_hour():.1f}/hr"),
            ("Deaths", len(tracker.deaths), ""),
            ("Biggest hit", _fmt_num(big_val), big_name),
            ("Stance", _ellipsize(tracker.stance or "?", 12),
             f"invoc: {tracker.invocation or '?'}"),
        ]
        draw_stat_cards(cards_canvas, cards)

    def redraw_split(*_):
        if state["tracker"] is not None:
            draw_split_bar(split_canvas, state["tracker"])

    def redraw_taken(*_):
        tracker = state["tracker"]
        if tracker is None:
            return
        rows = [(label, val, color, "") for label, val, color in (
            ("Physical", tracker.physical_dmg_in, THEME["bad"]),
            ("Spell/Song", tracker.spell_dmg_in + tracker.song_dmg_in,
             THEME["warn"]),
            ("Dmg Shield", tracker.ds_dmg_in, THEME["accent"]),
            ("Your pet", tracker.pet_dmg_in, THEME["dim"]),
        ) if val]
        draw_hbar_chart(taken_canvas, rows, "Damage taken",
                        "(no damage taken)", max_note_w=70)

    def redraw_topdmg(*_):
        tracker = state["tracker"]
        if tracker is None:
            return
        rows = []
        for name, s in tracker.ability_rows(kind="dmg")[:8]:
            color = CAT_COLORS.get(s["category"], MUTED_BAR)
            crit = f", {s['crits']} crit" if s["crits"] else ""
            rows.append((name, s["total"], color,
                         f"({s['hits']} hits{crit})"))
        draw_hbar_chart(topdmg_canvas, rows, "Top damage abilities",
                        "(no damage this session)")

    def redraw_topheal(*_):
        tracker = state["tracker"]
        if tracker is None:
            return
        rows = []
        for name, s in tracker.ability_rows(kind="heal")[:8]:
            rows.append((name, s["total"], HEAL_COLOR,
                         f"({s['hits']} casts)"))
        draw_hbar_chart(topheal_canvas, rows, "Top healing abilities",
                        "(no healing this session)")

    def redraw_dps(*_):
        tracker = state["tracker"]
        if tracker is None:
            return
        fights = [f for f in reversed(tracker.history)
                  if f.actors.get(YOU_LABEL)]
        bars, vals = [], []
        for f in fights:
            you = f.actor(YOU_LABEL)
            dps = you["dmg_out"] / f.elapsed()
            vals.append(dps)
            t = datetime.fromtimestamp(f.start_wall).strftime("%H:%M")
            bars.append((t, dps, None, f"{dps:.0f}"))
        avg = sum(vals) / len(vals) if vals else 0
        bars = [(x, v, GOOD if v >= avg else MUTED_BAR, tl)
                for (x, v, _c, tl) in bars]
        draw_vbar_chart(dps_canvas, bars,
                        "Your DPS per fight (active combat time, "
                        "chronological; bright = above session average)",
                        avg=avg, avg_label=f"avg {avg:.1f}")

    for cnv, fn in ((cards_canvas, redraw_cards), (split_canvas, redraw_split),
                    (taken_canvas, redraw_taken),
                    (topdmg_canvas, redraw_topdmg),
                    (topheal_canvas, redraw_topheal),
                    (dps_canvas, redraw_dps)):
        cnv.bind("<Configure>", fn)

    # ==========================================================================
    # Abilities tab -- full sortable tables + spells cast
    # ==========================================================================
    abil_frame = tk.Frame(nb, bg=BG, padx=4, pady=4)
    nb.add(abil_frame, text="Abilities")

    # shared filter state (applies to the damage table; search also filters
    # the healing table)
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

    filter_row = tk.Frame(abil_frame, bg=BG, pady=4)
    filter_row.pack(fill="x")
    tk.Label(filter_row, text="Category:", font=FONT_SMALL, bg=BG,
             fg=INK).pack(side="left")
    cat_box = ttk.Combobox(filter_row, textvariable=filter_cat,
                           state="readonly", values=CAT_CHOICES, width=11)
    cat_box.pack(side="left", padx=(2, 10))
    tk.Label(filter_row, text="Search:", font=FONT_SMALL, bg=BG,
             fg=INK).pack(side="left")
    search_ent = tk.Entry(filter_row, textvariable=filter_text, width=18,
                          font=FONT, bg=PANEL, fg=INK, insertbackground=INK,
                          relief="flat")
    search_ent.pack(side="left", padx=(2, 10))

    tk.Label(abil_frame, text="Damage by ability", font=FONT_TITLE, bg=BG,
             fg=INK, anchor="w").pack(fill="x", pady=(4, 0))
    dmg_tree = ttk.Treeview(abil_frame,
                            columns=("total", "hits", "crits", "biggest",
                                     "type"),
                            show="tree headings", height=10)
    for col, label, w in (("total", "Total", 100), ("hits", "Hits", 60),
                          ("crits", "Crits", 60), ("biggest", "Biggest", 80),
                          ("type", "Type", 90)):
        dmg_tree.heading(col, text=label)
        dmg_tree.column(col, width=w, anchor="center")
    dmg_tree.column("#0", width=220)
    dmg_tree.heading("#0", text="Ability")
    dmg_tree.pack(fill="both", expand=True, pady=(2, 4))
    setup_stripes(dmg_tree)

    lower = tk.Frame(abil_frame, bg=BG)
    lower.pack(fill="both", expand=True)

    heal_col = tk.Frame(lower, bg=BG)
    heal_col.pack(side="left", fill="both", expand=True, padx=(0, 4))
    tk.Label(heal_col, text="Healing by ability", font=FONT_TITLE, bg=BG,
             fg=INK, anchor="w").pack(fill="x")
    heal_tree = ttk.Treeview(heal_col, columns=("total", "hits", "biggest"),
                             show="tree headings", height=8)
    for col, label, w in (("total", "Total", 90), ("hits", "Casts", 60),
                          ("biggest", "Biggest", 80)):
        heal_tree.heading(col, text=label)
        heal_tree.column(col, width=w, anchor="center")
    heal_tree.column("#0", width=180)
    heal_tree.heading("#0", text="Spell")
    heal_tree.pack(fill="both", expand=True, pady=(2, 0))
    setup_stripes(heal_tree)

    casts_col = tk.Frame(lower, bg=BG)
    casts_col.pack(side="left", fill="both", expand=True, padx=(0, 4))
    casts_hdr = tk.Label(casts_col,
                         text="Spells cast (spell data from spells_us.txt)",
                         font=FONT_TITLE, bg=BG, fg=INK, anchor="w")
    casts_hdr.pack(fill="x")
    casts_tree = ttk.Treeview(casts_col,
                              columns=("count", "mana", "cast", "recast",
                                       "duration", "resisted", "fizzled",
                                       "interrupted"),
                              show="tree headings", height=8)
    for col, label, w in (("count", "Casts", 50), ("mana", "Mana", 50),
                          ("cast", "Cast", 55), ("recast", "Recast", 60),
                          ("duration", "Duration", 70),
                          ("resisted", "Resisted", 60),
                          ("fizzled", "Fizzled", 55),
                          ("interrupted", "Interrupted", 75)):
        casts_tree.heading(col, text=label)
        casts_tree.column(col, width=w, anchor="center")
    casts_tree.column("#0", width=170)
    casts_tree.heading("#0", text="Spell")
    casts_tree.pack(fill="both", expand=True, pady=(2, 0))
    setup_stripes(casts_tree)
    outcomes_lbl = tk.Label(casts_col, text="", font=FONT_SMALL, bg=BG,
                            fg=SUBTLE, anchor="w")
    outcomes_lbl.pack(fill="x")

    def _fmt_dur(secs):
        if secs is None or secs == 0:
            return ""
        if secs < 0:
            return "perm"
        secs = int(secs)
        if secs >= 60:
            m, s = divmod(secs, 60)
            return f"{m}m {s}s" if s else f"{m}m"
        return f"{secs}s"

    buffs_col = tk.Frame(lower, bg=BG)
    buffs_col.pack(side="left", fill="both", expand=True)
    tk.Label(buffs_col, text="Buffs/debuffs on you (matched via "
                             "spells_us_str.txt messages)",
             font=FONT_TITLE, bg=BG, fg=INK, anchor="w").pack(fill="x")
    buffs_tree = ttk.Treeview(buffs_col,
                              columns=("gained", "faded", "uptime"),
                              show="tree headings", height=8)
    for col, label, w in (("gained", "Gained", 55), ("faded", "Faded", 55),
                          ("uptime", "Uptime", 80)):
        buffs_tree.heading(col, text=label)
        buffs_tree.column(col, width=w, anchor="center")
    buffs_tree.column("#0", width=190)
    buffs_tree.heading("#0", text="Spell / message")
    buffs_tree.pack(fill="both", expand=True, pady=(2, 0))
    setup_stripes(buffs_tree)
    tk.Label(buffs_col, text="An * marks a buff still active at the last "
                             "log line. Quoted rows are messages shared by "
                             "several spells (no unambiguous name).",
             font=FONT_SMALL, bg=BG, fg=SUBTLE, anchor="w",
             wraplength=380, justify="left").pack(fill="x")

    def refresh_ability_tables(*_):
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
                CATEGORY_LABELS.get(s["category"], s["category"])
                + (" (proc)" if s.get("proc") else "")))
        heal_tree.delete(*heal_tree.get_children())
        needle = filter_text.get().strip().lower()
        for name, s in tracker.ability_rows(kind="heal"):
            if needle and needle not in name.lower():
                continue
            striped_insert(heal_tree, name, (
                _fmt_num(s["total"]), s["hits"], _fmt_num(s["biggest"])))

    cat_box.bind("<<ComboboxSelected>>", refresh_ability_tables)
    search_ent.bind("<KeyRelease>", refresh_ability_tables)

    # ==========================================================================
    # Sessions tab -- session-vs-session comparison + personal records
    # ==========================================================================
    sess_frame = tk.Frame(nb, padx=8, pady=6, bg=BG)
    nb.add(sess_frame, text="Sessions")

    records_lbl = tk.Label(sess_frame, bg=BG, fg=RECORD, font=FONT_BOLD,
                           justify="left", anchor="w")
    records_lbl.pack(fill="x")

    sess_cols = ("start", "zone", "build", "len", "fights", "dps", "kills",
                 "kph", "deaths", "big")
    sess_tree = ttk.Treeview(sess_frame, columns=sess_cols,
                             show="tree headings", height=8)
    for col, label, w in (("start", "Start", 145), ("zone", "Zone", 105),
                          ("build", "Build", 75),
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
             bg=BG, fg=INK).pack(side="left")
    SESS_METRICS = (("Avg combat DPS", "avg_dps"),
                    ("Kills per hour", "kph"),
                    ("Total damage", "dmg"),
                    ("Biggest hit", "biggest"))
    sess_metric_var = tk.StringVar(value=SESS_METRICS[0][0])
    sess_metric_box = ttk.Combobox(
        sess_chart_row, textvariable=sess_metric_var, state="readonly",
        values=[m for m, _ in SESS_METRICS], width=16)
    sess_metric_box.pack(side="left", padx=(2, 8))
    # Build filter: the /who class combination each session ran under --
    # pick one to see (and chart) only that build's sessions, so builds
    # can be judged against each other on the same metrics
    tk.Label(sess_chart_row, text="Build:", font=FONT_SMALL,
             bg=BG, fg=INK).pack(side="left")
    sess_build_var = tk.StringVar(value="All builds")
    sess_build_box = ttk.Combobox(
        sess_chart_row, textvariable=sess_build_var, state="readonly",
        values=["All builds"], width=12)
    sess_build_box.pack(side="left", padx=(2, 8))
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

    def filtered_summaries():
        """(real_session_index, summary) pairs surviving the Build filter."""
        summaries = ensure_summaries()
        pick = sess_build_var.get()
        return [(i, s) for i, s in enumerate(summaries)
                if pick in ("All builds", "", s.get("classes") or "?")]

    def refresh_sessions_tab():
        summaries = ensure_summaries()
        builds = sorted({s.get("classes") or "?" for s in summaries})
        sess_build_box["values"] = ["All builds"] + builds
        if sess_build_var.get() not in sess_build_box["values"]:
            sess_build_var.set("All builds")
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

        rows = filtered_summaries()
        best_dps = max((s["avg_dps"] for _, s in rows
                        if s["fights"] >= RECORD_MIN_FIGHTS), default=None)
        best_kph = max((s["kph"] for _, s in rows
                        if s["kills"] >= RECORD_MIN_KILLS), default=None)
        sess_tree.delete(*sess_tree.get_children())
        for i, s in rows:
            star_d = " ★" if best_dps is not None and s["avg_dps"] == best_dps else ""
            star_k = " ★" if best_kph is not None and s["kph"] == best_kph else ""
            tags = ("best",) if (star_d or star_k) else ()
            sess_tree.insert("", "end", text=str(i + 1), tags=tags, values=(
                s["ts"], s["zone"] or "?", s.get("classes") or "?",
                _fmt_minutes(s["minutes"]),
                s["fights"], f"{s['avg_dps']:.1f}{star_d}", s["kills"],
                f"{s['kph']:.1f}{star_k}", s["deaths"],
                _fmt_num(s["biggest"])))
        redraw_sess_chart()

    def redraw_sess_chart(*_):
        if not state["summaries"]:
            return
        rows = filtered_summaries()
        if not rows:
            return
        key = dict(SESS_METRICS)[sess_metric_var.get()]
        vals = [s[key] for _, s in rows]
        best = max(range(len(vals)), key=lambda i: vals[i]) \
            if any(v > 0 for v in vals) else None
        bars = []
        for j, (i, s) in enumerate(rows):
            v = s[key]
            top = f"{v:.1f}" if key in ("avg_dps", "kph") else _fmt_num(v)
            color = ACCENT if j == best else MUTED_BAR
            bars.append((str(i + 1), v, color, top))
        pick = sess_build_var.get()
        title = f"{sess_metric_var.get()} by session" \
            + (f" — {pick}" if pick not in ("All builds", "") else "")
        draw_vbar_chart(sess_canvas, bars, title, best=best)

    sess_metric_box.bind("<<ComboboxSelected>>", redraw_sess_chart)
    sess_build_box.bind("<<ComboboxSelected>>",
                        lambda *_: refresh_sessions_tab())
    sess_canvas.bind("<Configure>", redraw_sess_chart)

    def open_session_row(_e):
        sel = sess_tree.selection()
        if not sel:
            return
        try:
            # the "#" column carries the REAL session number -- the table
            # may be filtered to one build, so the row position isn't it
            idx = int(sess_tree.item(sel[0], "text")) - 1
        except (ValueError, TypeError):
            return
        if 0 <= idx < len(state["sessions"]):
            session_box.current(idx)
            refresh()
            nb.select(dash_outer)

    sess_tree.bind("<Double-Button-1>", open_session_row)

    # ==========================================================================
    # Encounters tab -- per-mob fight history across EVERY session in the
    # log, with best-vs-worst comparison and ranked what-changed analysis
    # ==========================================================================
    enc_frame = tk.Frame(nb, padx=8, pady=6, bg=BG)
    nb.add(enc_frame, text="Encounters")

    enc_top = tk.Frame(enc_frame, bg=BG)
    enc_top.pack(fill="x")
    tk.Label(enc_top, text="Search mob:", font=FONT_SMALL, bg=BG,
             fg=INK).pack(side="left")
    enc_search_var = tk.StringVar()
    enc_search = tk.Entry(enc_top, textvariable=enc_search_var, font=FONT,
                          width=22, bg=PANEL, fg=INK, insertbackground=INK,
                          relief="flat")
    enc_search.pack(side="left", padx=(2, 10), ipady=2)
    tk.Label(enc_top, bg=BG, fg=SUBTLE, font=FONT_SMALL,
             text="pick a mob → its fights · select one for details, two "
                  "(Ctrl-click) to compare · or Compare best vs worst"
             ).pack(side="left")

    enc_mid = tk.Frame(enc_frame, bg=BG)
    enc_mid.pack(fill="x", pady=(4, 0))

    mob_tree = ttk.Treeview(enc_mid,
                            columns=("fights", "deaths", "best", "worst"),
                            show="tree headings", height=9)
    for col, label, w in (("fights", "Fights", 50), ("deaths", "Deaths", 50),
                          ("best", "Best DPS", 65), ("worst", "Worst DPS", 65)):
        mob_tree.heading(col, text=label)
        mob_tree.column(col, width=w, anchor="center")
    mob_tree.column("#0", width=170)
    mob_tree.heading("#0", text="Mob")
    mob_tree.pack(side="left", fill="y")
    setup_stripes(mob_tree)

    fight_tree = ttk.Treeview(
        enc_mid,
        columns=("when", "zone", "dur", "dps", "dealt", "taken", "deaths",
                 "stance"),
        show="tree headings", height=9)
    for col, label, w in (("when", "When", 115), ("zone", "Zone", 95),
                          ("dur", "Length", 55), ("dps", "DPS", 60),
                          ("dealt", "Dealt", 60), ("taken", "Taken", 60),
                          ("deaths", "Deaths", 50), ("stance", "Stance", 105)):
        fight_tree.heading(col, text=label)
        fight_tree.column(col, width=w,
                          anchor="w" if col in ("when", "zone", "stance")
                          else "center")
    fight_tree.column("#0", width=30)
    fight_tree.heading("#0", text="#")
    fight_tree.pack(side="left", fill="both", expand=True, padx=(6, 0))
    setup_stripes(fight_tree)

    enc_btns = tk.Frame(enc_frame, bg=BG)
    enc_btns.pack(fill="x", pady=(4, 0))
    cmp_bw_btn = tk.Button(enc_btns, text="Compare best vs worst",
                           font=FONT_SMALL, relief="flat",
                           bg=PANEL, fg=ACCENT, activebackground=BG)
    cmp_bw_btn.pack(side="left")
    cmp_sel_btn = tk.Button(enc_btns, text="Compare selected (2)",
                            font=FONT_SMALL, relief="flat",
                            bg=PANEL, fg=ACCENT, activebackground=BG)
    cmp_sel_btn.pack(side="left", padx=(6, 0))
    enc_status = tk.Label(enc_btns, bg=BG, fg=SUBTLE, font=FONT_SMALL,
                          anchor="w")
    enc_status.pack(side="left", padx=(12, 0))

    enc_txt = tk.Text(enc_frame, wrap="word", bg=PANEL, fg=INK,
                      font=FONT_MONO, relief="flat", height=15,
                      state="disabled")
    enc_txt.pack(fill="both", expand=True, pady=(4, 0))
    enc_txt.tag_configure("h", foreground=ACCENT,
                          font=(FONT_MONO[0], 10, "bold"))
    enc_txt.tag_configure("b", font=(FONT_MONO[0], 10, "bold"))
    enc_txt.tag_configure("dim", foreground=SUBTLE)
    enc_txt.tag_configure("good", foreground=GOOD)
    enc_txt.tag_configure("warn", foreground=RECORD)

    enc_ui = {"order": [], "rows": []}

    def _enc_put(text, tag=None):
        enc_txt.insert("end", text, (tag,) if tag else ())

    def ensure_encounters():
        if state["encounters"] is None:
            enc_status.config(text="replaying log…")
            root.config(cursor="watch")
            root.update_idletasks()
            try:
                state["encounters"] = collect_encounters(
                    log_path, state["sessions"])
            finally:
                root.config(cursor="")
            n = len(state["encounters"])
            enc_status.config(text=f"{n} encounters across "
                                   f"{len(state['sessions'])} sessions")
        return state["encounters"]

    def _enc_groups():
        groups = {}
        for e in ensure_encounters():
            g = groups.setdefault(e["mob_key"], {"name": e["mob"],
                                                 "list": []})
            g["list"].append(e)
        return groups

    def refresh_mob_tree(*_):
        groups = _enc_groups()
        q = enc_search_var.get().strip().lower()
        mob_tree.delete(*mob_tree.get_children())
        enc_ui["order"] = []
        for key in sorted(groups, key=lambda k: -len(groups[k]["list"])):
            if q and q not in key:
                continue
            lst = groups[key]["list"]
            best = max(e["dps"] for e in lst)
            worst = min(e["dps"] for e in lst)
            striped_insert(mob_tree, groups[key]["name"],
                           (len(lst), sum(e["deaths"] for e in lst),
                            f"{best:.1f}", f"{worst:.1f}"))
            enc_ui["order"].append(key)

    def _fmt_span(secs):
        m, s = divmod(int(secs), 60)
        return f"{m}:{s:02d}"

    def on_mob_select(_e=None):
        sel = mob_tree.selection()
        if not sel:
            return
        idx = mob_tree.index(sel[0])
        if not (0 <= idx < len(enc_ui["order"])):
            return
        key = enc_ui["order"][idx]
        rows = sorted((e for e in ensure_encounters()
                       if e["mob_key"] == key), key=lambda e: e["start"])
        enc_ui["rows"] = rows
        fight_tree.delete(*fight_tree.get_children())
        best = max((e["dps"] for e in rows), default=0)
        for i, e in enumerate(rows):
            star = " ★" if e["dps"] == best and len(rows) > 1 else ""
            striped_insert(
                fight_tree, str(i + 1),
                (e["when"], e["zone"] or "?", _fmt_span(e["span"]),
                 f"{e['dps']:.1f}{star}", _fmt_num(e["dmg"]),
                 _fmt_num(e["taken"]), e["deaths"], e["stance"]),
                tags=("best",) if star else ())
        if rows:
            show_encounter(max(rows, key=lambda e: e["dps"]))

    def _selected_encounters():
        return [enc_ui["rows"][fight_tree.index(s)]
                for s in fight_tree.selection()
                if 0 <= fight_tree.index(s) < len(enc_ui["rows"])]

    def _abil_lines(e, kind):
        src_d = e[kind]
        total = max(sum(v["total"] for v in src_d.values()), 1)
        rows = sorted(src_d.items(), key=lambda kv: -kv[1]["total"])
        out = []
        for name, v in rows[:10]:
            crit = f", {v['crits']} crit" if v.get("crits") else ""
            out.append(f"  {name:<28} {_fmt_num(v['total']):>8}  "
                       f"{v['total'] / total:>4.0%}  "
                       f"({v['hits']} hits{crit}, big {_fmt_num(v['biggest'])})")
        return out

    def show_encounter(e):
        enc_txt.config(state="normal")
        enc_txt.delete("1.0", "end")
        _enc_put(f"{e['mob']}", "h")
        _enc_put(f"   {e['when']}  ·  session {e['session_i'] + 1}"
                 + (f"  ·  {e['zone']}" if e["zone"] else "") + "\n", "dim")
        _enc_put(f"length {_fmt_span(e['span'])} (active "
                 f"{_fmt_span(e['active'])})   ")
        _enc_put(f"DPS {e['dps']:.1f}", "b")
        _enc_put(f"   DPM {_fmt_num(e['dps'] * 60)}   "
                 f"DPH {_fmt_num(e['dps'] * 3600)}\n")
        _enc_put(f"dealt {_fmt_num(e['dmg'])}   taken {_fmt_num(e['taken'])}"
                 f"   healed {_fmt_num(e['healed'])}   acc {e['acc']}%   "
                 f"crit {e['critpct']}%   big {_fmt_num(e['big'])}\n")
        _enc_put(f"kills {e['kills']}   ")
        _enc_put(f"deaths {e['deaths']}", "warn" if e["deaths"] else None)
        _enc_put(f"   stance {e['stance']} / {e['invocation']}\n")
        if e["resists"]:
            _enc_put("resisted: " + ",  ".join(
                f"{k} x{v}" for k, v in sorted(
                    e["resists"].items(), key=lambda kv: -kv[1])) + "\n",
                "warn")
        if e["abilities"]:
            _enc_put("\ndamage by ability:\n", "b")
            _enc_put("\n".join(_abil_lines(e, "abilities")) + "\n")
        if e["heals"]:
            _enc_put("\nhealing:\n", "b")
            _enc_put("\n".join(_abil_lines(e, "heals")) + "\n")
        if e["casts"]:
            _enc_put("\ncasts: " + ",  ".join(
                f"{k} x{v}" for k, v in sorted(e["casts"].items())) + "\n",
                "dim")
        if len(e["mobs"]) > 1:
            others = ",  ".join(f"{n} ({_fmt_num(di)} dmg to it)"
                                for n, di, _do in e["mobs"][1:6])
            _enc_put(f"\nalso in this fight: {others}\n", "dim")
        enc_txt.config(state="disabled")

    def show_compare(a, b):
        # a = higher DPS of the two
        if b["dps"] > a["dps"]:
            a, b = b, a
        enc_txt.config(state="normal")
        enc_txt.delete("1.0", "end")
        _enc_put(f"{a['mob']} — better vs worse attempt\n", "h")
        _enc_put(f"{'':<20}{'BETTER':>14}{'WORSE':>14}\n", "dim")
        rows = [
            ("when", a["when"], b["when"]),
            ("session", f"#{a['session_i'] + 1} {a['zone'] or ''}".strip(),
             f"#{b['session_i'] + 1} {b['zone'] or ''}".strip()),
            ("length", _fmt_span(a["span"]), _fmt_span(b["span"])),
            ("DPS", f"{a['dps']:.1f}", f"{b['dps']:.1f}"),
            ("DPM", _fmt_num(a["dps"] * 60), _fmt_num(b["dps"] * 60)),
            ("DPH", _fmt_num(a["dps"] * 3600), _fmt_num(b["dps"] * 3600)),
            ("damage dealt", _fmt_num(a["dmg"]), _fmt_num(b["dmg"])),
            ("damage taken", _fmt_num(a["taken"]), _fmt_num(b["taken"])),
            ("healed", _fmt_num(a["healed"]), _fmt_num(b["healed"])),
            ("accuracy", f"{a['acc']}%", f"{b['acc']}%"),
            ("crit", f"{a['critpct']}%", f"{b['critpct']}%"),
            ("biggest hit", _fmt_num(a["big"]), _fmt_num(b["big"])),
            ("resists", str(sum(a["resists"].values())),
             str(sum(b["resists"].values()))),
            ("deaths", str(a["deaths"]), str(b["deaths"])),
            ("stance", a["stance"], b["stance"]),
            ("invocation", a["invocation"], b["invocation"]),
        ]
        for label, va, vb in rows:
            tag = "b" if label in ("DPS",) else None
            _enc_put(f"{label:<20}", "dim")
            _enc_put(f"{va:>14}", tag or ("good" if va != vb else None))
            _enc_put(f"{vb:>14}\n", tag)
        _enc_put("\nwhat changed (ranked by likely impact):\n", "h")
        for i, text in enumerate(encounter_diff(a, b), 1):
            _enc_put(f"  {i}. {text}\n",
                     "good" if i <= 3 else None)
        enc_txt.config(state="disabled")

    def on_fight_select(_e=None):
        sel = _selected_encounters()
        if len(sel) == 1:
            show_encounter(sel[0])
        elif len(sel) == 2:
            show_compare(sel[0], sel[1])

    def compare_best_worst():
        rows = enc_ui["rows"]
        if len(rows) < 2:
            return
        show_compare(max(rows, key=lambda e: e["dps"]),
                     min(rows, key=lambda e: e["dps"]))

    def compare_selected():
        sel = _selected_encounters()
        if len(sel) == 2:
            show_compare(sel[0], sel[1])

    cmp_bw_btn.config(command=compare_best_worst)
    cmp_sel_btn.config(command=compare_selected)
    mob_tree.bind("<<TreeviewSelect>>", on_mob_select)
    fight_tree.bind("<<TreeviewSelect>>", on_fight_select)
    enc_search_var.trace_add("write", refresh_mob_tree)

    def _on_tab_changed(_e=None):
        try:
            if nb.nametowidget(nb.select()) is enc_frame \
               and state["encounters"] is None:
                refresh_mob_tree()
        except (KeyError, tk.TclError):
            pass

    nb.bind("<<NotebookTabChanged>>", _on_tab_changed)

    # ==========================================================================
    # Diagnostics tab -- passive healing estimates + unrecognized lines
    # ==========================================================================
    diag_frame = tk.Frame(nb, bg=BG, padx=8, pady=6)
    nb.add(diag_frame, text="Diagnostics")

    tk.Label(diag_frame, text="Passive Healing (est.)", font=FONT_TITLE,
             bg=BG, fg=INK, anchor="w").pack(fill="x")
    heal_est_controls = tk.Frame(diag_frame, bg=BG, pady=2)
    heal_est_controls.pack(fill="x")
    tk.Label(heal_est_controls, text="Class:", bg=BG, fg=INK,
             font=FONT).pack(side="left")
    heal_class_var = tk.StringVar(value="Bard")
    heal_class_menu = ttk.Combobox(heal_est_controls,
                                   textvariable=heal_class_var,
                                   values=CLASS_NAMES, state="readonly",
                                   width=14)
    heal_class_menu.pack(side="left", padx=(4, 12))
    tk.Label(heal_est_controls, text="Caster level:", bg=BG, fg=INK,
             font=FONT).pack(side="left")
    heal_level_var = tk.StringVar(value="50")
    heal_level_entry = tk.Entry(heal_est_controls,
                                textvariable=heal_level_var,
                                width=5, font=FONT, bg=PANEL, fg=INK,
                                insertbackground=INK, relief="flat")
    heal_level_entry.pack(side="left", padx=(4, 12))
    heal_verified_var = tk.BooleanVar(value=True)
    tk.Checkbutton(heal_est_controls, text="Verified spells only",
                   variable=heal_verified_var, font=FONT_SMALL,
                   bg=BG, fg=INK, selectcolor=PANEL, activebackground=BG,
                   activeforeground=INK,
                   command=lambda: refresh_heal_estimates()) \
        .pack(side="left", padx=(0, 12))

    heal_est_tree = ttk.Treeview(
        diag_frame, columns=("minlvl", "base", "formula", "max", "est"),
        show="tree headings", height=7)
    for col, label, w in (("minlvl", "Min Lvl", 60), ("base", "Base", 60),
                          ("formula", "Formula", 70), ("max", "Max", 60),
                          ("est", "Est./tick", 90)):
        heal_est_tree.heading(col, text=label)
        heal_est_tree.column(col, width=w, anchor="center")
    heal_est_tree.column("#0", width=280)
    heal_est_tree.heading("#0", text="Spell")
    heal_est_tree.pack(fill="x", pady=(2, 2))
    setup_stripes(heal_est_tree)

    tk.Label(diag_frame,
             text="Beneficial spells for the selected class with a positive "
                  "HP effect (SPA 0/79/100) -- candidate heals / "
                  "heal-over-time songs, pulled straight from spells_us.txt. "
                  "ESTIMATES from the spell's own base/formula/max data "
                  "(EQEmu classic-era reference math, not confirmed as EQL's "
                  "exact behavior); PER-TICK for heal-over-time effects. "
                  "'Verified spells only' keeps just the spells confirmed "
                  "obtainable on EQL's L1-50 server (wiki-verified lists "
                  "from the eql-info project) -- untick to see everything "
                  "in the raw Live-inherited file. There's no log line "
                  "showing which song was active, so cross-check a guess "
                  "manually.",
             wraplength=1000, justify="left", font=FONT_SMALL, fg=SUBTLE,
             bg=BG).pack(anchor="w", pady=(0, 8))

    def refresh_heal_estimates(*_ignored):
        heal_est_tree.delete(*heal_est_tree.get_children())
        try:
            level = max(1, int(heal_level_var.get()))
        except ValueError:
            level = 50
            heal_level_var.set("50")
        cls = heal_class_var.get()
        for info in SPELL_DB.find_class_heals(
                cls, max_level=level,
                verified_only=heal_verified_var.get()):
            hp = info.hp_effects()[0]
            est = info.estimated_hp_value(level)
            striped_insert(heal_est_tree, info.name, (
                info.min_level_for(cls), hp.base_value, hp.formula,
                hp.max_value, f"{est:g}" if est is not None else ""))

    heal_class_menu.bind("<<ComboboxSelected>>", refresh_heal_estimates)
    heal_level_entry.bind("<Return>", refresh_heal_estimates)
    themed_button(heal_est_controls, text="Refresh",
                  command=refresh_heal_estimates).pack(side="left")

    tk.Label(diag_frame, text="Unrecognized lines (parser calibration)",
             font=FONT_TITLE, bg=BG, fg=INK, anchor="w").pack(fill="x")
    calib_txt = tk.Text(diag_frame, wrap="none", bg=PANEL, fg=INK,
                        font=FONT_MONO, relief="flat", height=10)
    calib_txt.pack(fill="both", expand=True, pady=(2, 0))

    # -- refresh -----------------------------------------------------------------------
    def refresh():
        idx = session_box.current()
        sessions = state["sessions"]
        lines = sessions[idx]["lines"] \
            if sessions and 0 <= idx < len(sessions) else None
        tracker = build_tracker(log_path, lines)
        state["tracker"] = tracker

        # dashboard
        redraw_cards()
        redraw_split()
        redraw_taken()
        redraw_topdmg()
        redraw_topheal()
        redraw_dps()

        # stance / invocation tables
        stance_tree.delete(*stance_tree.get_children())
        perf = tracker.stance_performance()
        for name in list(STANCES) + [k for k in perf if k not in STANCES]:
            g = perf.get(name)
            if not g:
                continue
            striped_insert(stance_tree, name, (
                g["fights"], f"{g['avg_dps']:.1f}", f"{g['avg_dtps']:.1f}",
                STANCES.get(name, "")))
        stance_hdr.config(
            text=f"Stances   (current: {tracker.stance or 'unknown'})")

        invoc_tree.delete(*invoc_tree.get_children())
        perf = tracker.invocation_performance()
        for name in list(INVOCATIONS) + [k for k in perf if k not in INVOCATIONS]:
            g = perf.get(name)
            if not g:
                continue
            striped_insert(invoc_tree, name, (
                g["fights"], f"{g['avg_dps']:.1f}", f"{g['avg_dtps']:.1f}",
                INVOCATIONS.get(name, "")))
        invoc_hdr.config(
            text=f"Invocations   (current: {tracker.invocation or 'unknown'})")

        # abilities tab
        refresh_ability_tables()
        casts_tree.delete(*casts_tree.get_children())
        # duration estimates scale with caster level -- use the player's
        # real level when a /who (or level-up) line revealed it
        est_level = tracker.player_level or 50
        casts_hdr.config(
            text="Spells cast (spell data from spells_us.txt; durations "
                 f"estimated at L{est_level}"
                 + ("" if tracker.player_level else " -- level unknown, "
                    "refresh /who in-game to pin it") + ")")
        # spells only seen failing (resist/fizzle/interrupt) still get a row
        cast_names = set(tracker.spell_casts) | set(tracker.spell_resists) \
            | set(tracker.spell_fizzles) | set(tracker.spell_interrupts)
        for name in sorted(cast_names,
                           key=lambda n: -tracker.spell_casts.get(n, 0)):
            count = tracker.spell_casts.get(name, 0)
            info = SPELL_DB.lookup(name)
            mana = info.mana if info else ""
            cast = f"{info.cast_time_s:.1f}s" if info else ""
            recast = f"{info.recast_time_s:g}s" if info else ""
            dur = _fmt_dur(info.duration_seconds(est_level)) if info else ""
            resisted = tracker.spell_resists.get(name, "") or ""
            fizzled = tracker.spell_fizzles.get(name, "") or ""
            interrupted = tracker.spell_interrupts.get(name, "") or ""
            striped_insert(casts_tree, name,
                           (count, mana, cast, recast, dur, resisted,
                            fizzled, interrupted))
        outcomes_lbl.config(
            text=f"Fizzles: {tracker.fizzles}   "
                 f"Interrupts: {tracker.interrupts}   "
                 f"Resisted by target: {sum(tracker.spell_resists.values())}   "
                 f"Resisted by you: {tracker.resists_incoming}")

        buffs_tree.delete(*buffs_tree.get_children())
        for label, s in tracker.buff_rows():
            shown = ("* " + label) if s["active"] else label
            striped_insert(buffs_tree, shown,
                           (s["gained"], s["faded"], _fmt_dur(s["uptime"])))

        # diagnostics tab
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
            ctx["log_path"] = chosen   # survives a theme-change rebuild
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
    change_btn.config(command=change_log)
    refresh_btn.config(command=refresh_full)

    reload_session_list()
    refresh()
    refresh_heal_estimates()
    root.mainloop()
    return restart["flag"]


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
