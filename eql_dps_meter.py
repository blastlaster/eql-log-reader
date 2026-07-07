#!/usr/bin/env python3
"""
EQL DPS/HPS Meter
==================
A retro, always-on-top overlay that tails your EverQuest Legends log file and
shows your own live combat output -- part of the same EQL Log Reader overlay
family as eql_friend_overlay.py (shares log-tailing code from
eql_overlay_common.py). Scoped to the current character only: reliably
seeing group/raid members' damage needs chat filters most people don't run,
so this tracks "you" and nothing else.

Usage:
    python eql_dps_meter.py "C:\\...\\Logs\\eqlog_Miranda_rivervale.txt"

Or run with no arguments to pick the log file from a dialog, or launch it
from the Friends overlay's right-click menu ("Open DPS/HPS meter").

Right-click the overlay for:
  * Theme       -- CRT Terminal / Arcade LED / 16-bit Window / Vintage /
                   Neon HUD (transparent: no background at all -- neon
                   pink/orange/yellow/blue/green text and bars float
                   directly over the game, every text black-outlined so
                   it reads over bright footage; Windows only, elsewhere
                   it falls back to a dark background)
                   (monochrome, no scanlines/bevels, and DMG SOURCES render
                   as plain text rows instead of bars)
  * Layout      -- Vertical (compact column) / Horizontal (wide strip, all
                   meters side by side)
  * Rate window -- Fight average (totals over active combat time, steadier)
                   / Rolling 10s / Rolling 30s (what's hitting right now --
                   better reflects bursts, e.g. a big hit no longer gets
                   averaged away by a long fight)
  * Rate units  -- Per second (DPS/HPS/DTPS) / Per minute (DPM/HPM/DTPM).
                   Same numbers x60; per-minute reads better at low levels
                   where per-second rates are single digits.
  * DMG sources -- Percent only (default) / Damage + percent: adds the
                   actual damage dealt per source onto the DMG SOURCES
                   graph (at the end of each bar in the vertical layout,
                   in place of the rate in the horizontal one); the % keeps
                   its usual spot either way.
  * Combat timeout -- 5s / 15s / 30s / 45s / 60s without damage before the
                   current Combat ends. Short = fights split cleanly per
                   mob (bleed-over stops) but chained pulls fragment; long
                   = chained pulls stay one fight but downtime bleeds
                   between them. Applies immediately -- switch mid-session
                   to feel out the right value. (DPS itself is safe either
                   way: rates divide by ACTIVE combat time, so the timeout
                   mostly changes how fights are GROUPED, not the number.)

All-time visualizer
---------------------
The bottom section ends with an ALL TIME block: lifetime accuracy / crit
rate / biggest hit / kills+deaths for this character, plus the share of
combat time spent in each Stance and Invocation -- your current fight's
numbers sit right above it, so better-or-worse-than-usual is one glance.
Persisted per character in eql_alltime_<char>_<server>.json next to this
script. It accumulates what the meter actually OBSERVES while running:
the log tail seeded at startup is baselined out (relaunching never
double-counts), which also means history from before the feature existed
isn't backfilled. Stance/Invocation time only accrues during live combat,
so AFK time can't skew the percentages.
  * Size        -- 100% / 90% / 80% / 70% / 60%: shrinks the overlay's
                   footprint (width, bars, spacing) while FONTS STAY THE
                   SAME; long texts abbreviate instead of shrinking, so the
                   data stays readable at every size
  * Opacity
  * Reset current fight
  * Open Session Report... (detailed breakdowns -- see eql_session_report.py)
  * Show unrecognized combat lines (calibration aid, see eql_combat_tracker.py)

Damage source breakdown & pet
--------------------------------
Below the main DPS/HPS/DTPS numbers, the meter shows damage output split
six ways -- Melee, Skill (Kick/Bash/...), Spell (casts, procs, weapon
poisons), Song (Bard songs), DS (your damage shield burning attackers),
and Pet -- each with its own DPS and share of the combined (you + pet)
output this fight. DPS and DTPS also show a small "melee · spell
(· song · ds)" split of your own numbers; song and ds only appear when
they've contributed this fight.

If you have a pet, PET DPS and PET DTPS appear as their own rows, separate
from your numbers (your DPS is yours alone). All pet damage types count --
melee, skills, and spells/DoTs. Pets are recognized automatically for
"<Charname>`s warder"-style names; proper-named pets (necro/mage style)
are learned from the "/pet leader" announcement ("Jenann says, 'My leader
is Monomate.'"), so use /pet leader once after summoning. See
eql_combat_tracker.py's docstring for the Melee vs. Skill verb caveat.

What the live meter shows (and doesn't)
-----------------------------------------
The meter is about RIGHT NOW: the current Combat (damage-bounded, closes
after the selected Combat timeout without damage dealt/received) inside
the current Session (the "Welcome to EverQuest Legends!" login banner
resets everything -- old sessions in the same log file never skew the
numbers). When the timeout passes, the combat-scoped readouts reset to "--";
kills/hr and stance are session stats and stay visible. For backtracing
older data, use the Session Report (right-click menu), which can replay
any past session from the log.

Notes on accuracy
------------------
Combat log line formats follow this client's confirmed conventions (see
eql_combat_tracker.py docstring). The deeper breakdowns (per-spell
ranking, stance/invocation comparison, passive-healing estimates) live in
the session report, since they don't fit a small always-on-top window
well.
"""

import json
import os
import random
import sys
import time

from eql_overlay_common import (
    LogWatcher, Settings, make_draggable, RETRO_THEMES, DEFAULT_THEME,
    POLL_INTERVAL_MS, luma as _luma,
)
from eql_combat_tracker import CombatTracker, YOU_LABEL, PET_LABEL

RENDER_INTERVAL_MS = 90      # animation frame rate (independent of log polling)
BAR_EASE = 0.30              # how quickly the readout numbers ease toward target

# Two selectable layouts -- see set_layout()/render() below. Vertical is the
# original compact column; Horizontal lays the same meters out side by side
# in a wide strip (handy for docking along a screen edge).
CANVAS_WIDTH_V = 260
CANVAS_HEIGHT_V = 292
CANVAS_WIDTH_H = 620
CANVAS_HEIGHT_H = 128   # includes the ALL TIME bottom row

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(APP_DIR, "eql_dps_meter_settings.json")

# Damage-source segment display order/colors are keyed to theme roles shared
# with the main DPS/HPS/DTPS readout (accent/fg/warn/bad/dim) to keep the
# palette small and consistent.
SEGMENTS = (("Melee", "melee_dmg_out"), ("Skill", "skill_dmg_out"),
            ("Spell", "spell_dmg_out"), ("Song", "song_dmg_out"),
            ("DS", "ds_dmg_out"), ("Pet", "pet_dmg_out"))
SEG_COLOR_ROLE = {"Melee": "accent", "Skill": "fg", "Spell": "warn",
                  "Song": "bad", "DS": "alt", "Pet": "dim"}
# category groups for the melee/spell/song/ds sub-readouts under DPS and
# DTPS (song and ds only appear when they have damage this fight)
MELEE_CATS = ("melee", "skill")
SPELL_CATS = ("spell",)
SONG_CATS = ("song",)
DS_CATS = ("ds",)

# selectable element sizes -- these scale the overlay's footprint (canvas
# width, spacing, bar lengths) while FONTS STAY THE SAME, with per-row
# minimum heights tied to the font so data never becomes unreadable
SIZE_STEPS = (1.0, 0.9, 0.8, 0.7, 0.6)


# abbreviations for the ALL TIME stance/invocation share line; unknown
# names fall back to their first 3 letters
ABBREV = {"Offense Stance": "Off", "Defense Stance": "Def",
          "Mage Hunter Stance": "MH", "Recover": "Rec",
          "Over Channel": "OC", "Spell Blade": "SB"}


def _abbr(name):
    return ABBREV.get(name, name[:3])


class AllTimeStore:
    """Per-character lifetime stats (persisted JSON): swing accuracy,
    crits, biggest hit, kills/deaths, and how long each Stance/Invocation
    was active during combat. Fed by session-counter DELTAS observed while
    the meter runs -- the log tail seeded at startup is excluded via a
    baseline snapshot, so relaunching the meter never double-counts (the
    flip side: this is history observed by the meter, not a backfill of
    the whole log). Stance/Invocation seconds accrue only during live
    combat so AFK time can't skew the shares."""

    COUNTERS = ("hits", "misses", "crits", "kills", "deaths")

    def __init__(self, log_path):
        base = os.path.splitext(os.path.basename(log_path))[0]
        if base.startswith("eqlog_"):
            base = base[len("eqlog_"):]
        self.path = os.path.join(APP_DIR, f"eql_alltime_{base}.json")
        self.data = {"hits": 0, "misses": 0, "crits": 0, "kills": 0,
                     "deaths": 0, "biggest": 0, "combat_secs": 0.0,
                     "stance_secs": {}, "invocation_secs": {}}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for k in self.data:
                if k in loaded and type(loaded[k]) is type(self.data[k]):
                    self.data[k] = loaded[k]
        except (OSError, ValueError):
            pass
        self._baseline = None
        self._last_tick = None
        self._dirty = False
        self._last_save = time.time()

    @staticmethod
    def _totals(tracker):
        return {"hits": tracker.swings_hit, "misses": tracker.swings_missed,
                "crits": tracker.crit_count, "kills": len(tracker.kills),
                "deaths": len(tracker.deaths)}

    def snapshot_baseline(self, tracker):
        """Call right after LogWatcher.seed(): whatever the tracker counted
        so far came from the old log tail and must not be re-added."""
        self._baseline = self._totals(tracker)

    def tick(self, tracker, in_combat):
        """Fold new session-counter deltas into the lifetime totals.
        Called from the poll loop (~4x/s); saves at most every 15s."""
        now = time.time()
        totals = self._totals(tracker)
        if self._baseline is None:
            self._baseline = totals
        if any(totals[k] < self._baseline[k] for k in self.COUNTERS):
            # session reset (login banner) -- counters restarted from zero
            self._baseline = {k: 0 for k in self.COUNTERS}
        for k in self.COUNTERS:
            d = totals[k] - self._baseline[k]
            if d > 0:
                self.data[k] += d
                self._dirty = True
        self._baseline = totals
        if tracker.biggest_hit > self.data["biggest"]:
            self.data["biggest"] = tracker.biggest_hit
            self._dirty = True
        if in_combat and self._last_tick is not None:
            dt = min(now - self._last_tick, 2.0)
            if dt > 0:
                self.data["combat_secs"] += dt
                for key, cur in (("stance_secs", tracker.stance),
                                 ("invocation_secs", tracker.invocation)):
                    if cur:
                        self.data[key][cur] = \
                            self.data[key].get(cur, 0.0) + dt
                self._dirty = True
        self._last_tick = now
        if self._dirty and now - self._last_save > 15.0:
            self.save()

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            self._dirty = False
            self._last_save = time.time()
        except OSError:
            pass

    # -- display helpers -------------------------------------------------------
    def acc_pct(self):
        n = self.data["hits"] + self.data["misses"]
        return round(100 * self.data["hits"] / n) if n else 0

    def crit_pct(self):
        return round(100 * self.data["crits"] / self.data["hits"]) \
            if self.data["hits"] else 0

    def time_pcts(self, key):
        """[(name, pct)] sorted descending, for "stance_secs" /
        "invocation_secs"."""
        total = sum(self.data[key].values())
        if not total:
            return []
        out = [(n, round(100 * s / total))
               for n, s in self.data[key].items()]
        return sorted(out, key=lambda kv: -kv[1])


def _fmt_num(n):
    n = int(n)
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1000:
        return f"{n/1000:.1f}K"
    return str(n)


def _contrast_on(color, th):
    """A text color readable ON a bar filled with `color`: something dark
    over bright bars, the theme's (bright) fg over dark bars. Transparent
    themes provide "ink" as the dark color -- their bg is a chroma key, and
    text drawn in the key color would punch a see-through hole."""
    return th.get("ink", th["bg"]) if _luma(color) >= 100 else th["fg"]


def _mix(c1, c2, t):
    """Linear-interpolate two '#rrggbb' colors; t in [0,1]."""
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r, g, b = (round(r1 + (r2 - r1) * t), round(g1 + (g2 - g1) * t),
              round(b1 + (b2 - b1) * t))
    return f"#{r:02x}{g:02x}{b:02x}"


def run_overlay(log_path):
    import tkinter as tk
    from tkinter import font as tkfont

    settings = Settings(SETTINGS_FILE, {
        "x": 380, "y": 40,
        "opacity": 0.90,
        "theme": DEFAULT_THEME,
        "layout": "vertical",
        # "fight" = totals / active combat time (downtime between chained
        # pulls is capped out of the denominator -- see eql_combat_tracker's
        # ACTIVE_GAP_CAP); "rolling10"/"rolling30" = what's hitting right now
        "rate_mode": "fight",
        "units": "sec",   # "sec" (DPS/HPS/DTPS) or "min" (DPM/HPM/DTPM)
        # seconds without damage dealt/received before the Combat ends --
        # selectable (5-60s) so fight grouping can be tuned by feel
        "idle_timeout": 45,
        # DMG SOURCES rows: False = percent only, True = also draw the
        # actual damage dealt on the graph (% keeps its usual spot)
        "seg_show_amount": False,
        "scale": 1.0,   # element size; fonts stay constant (see SIZE_STEPS)
    })

    RATE_MODES = (("fight", "Fight average"),
                  ("rolling10", "Rolling 10s"),
                  ("rolling30", "Rolling 30s"))
    RATE_BADGE = {"fight": "avg", "rolling10": "10s", "rolling30": "30s"}
    UNIT_MODES = (("sec", "Per second (DPS)"), ("min", "Per minute (DPM)"))
    TIMEOUT_CHOICES = (5, 15, 30, 45, 60)

    def per_min():
        return settings.get("units", "sec") == "min"

    def unit_labels():
        """(DPS, HPS, DTPS, PET-DPS, PET-DTPS, /s) labels for the active
        rate unit -- everything x60 and relabeled in per-minute mode."""
        if per_min():
            return ("DPM", "HPM", "DTPM", "PET DPM", "PET DTPM", "/m")
        return ("DPS", "HPS", "DTPS", "PET DPS", "PET DTPS", "/s")

    root = tk.Tk()
    root.title("EQL Combat")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", settings["opacity"])
    except tk.TclError:
        pass
    root.geometry(f"+{settings['x']}+{settings['y']}")

    _font_cache = {}

    def mono(size, weight="normal"):
        key = (settings["theme"], size, weight)
        f = _font_cache.get(key)
        if f is None:
            f = tkfont.Font(family=RETRO_THEMES[settings["theme"]]["font_mono"][0],
                            size=size, weight=weight)
            _font_cache[key] = f
        return f

    bar = tk.Frame(root, cursor="fleur")
    bar.pack(fill="x")
    title_lbl = tk.Label(bar, text="  COMBAT", anchor="w")
    title_lbl.pack(side="left", pady=2, padx=(2, 0))
    status_lbl = tk.Label(bar, text="", anchor="e")
    status_lbl.pack(side="right", padx=4)

    canvas = tk.Canvas(root, width=CANVAS_WIDTH_V, height=CANVAS_HEIGHT_V,
                       highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    make_draggable(root, (bar, title_lbl), settings)

    # -- tracker + watcher ---------------------------------------------------
    live = {}

    def char_name_from(path):
        import re
        m = re.match(r"eqlog_([A-Za-z]+)", os.path.basename(path))
        return m.group(1) if m else "You"

    def open_log(path):
        prev_store = live.get("alltime")
        if prev_store:
            prev_store.save()   # switching characters -- flush the old file
        tracker = CombatTracker(
            self_name=char_name_from(path),
            idle_timeout=float(settings.get("idle_timeout", 45)))
        watcher = LogWatcher(path)
        watcher.add_handler(tracker.handle_line)
        watcher.seed()
        alltime = AllTimeStore(path)
        # everything counted so far came from the seeded log tail -- already
        # observed in a previous run, must not be double-counted
        alltime.snapshot_baseline(tracker)
        live["tracker"], live["watcher"] = tracker, watcher
        live["alltime"] = alltime
        live["char"] = char_name_from(path)
        settings["log_path"] = path
        settings.save()

    open_log(log_path)

    # display-value easing, keyed by a small fixed set of readout rows
    anim = {}

    def get_anim(name):
        return anim.setdefault(name, {"disp": 0.0, "target": 0.0})

    # -- rendering -------------------------------------------------------------
    def theme():
        return RETRO_THEMES[settings["theme"]]

    _chroma = {"cur": None}

    def paint_chrome():
        th = theme()
        root.configure(bg=th["bg"])
        bar.configure(bg=th["panel"])
        title_lbl.configure(bg=th["panel"], fg=th["accent"], font=mono(10, "bold"))
        status_lbl.configure(bg=th["panel"], fg=th["dim"], font=mono(8))
        canvas.configure(bg=th["bg"])
        # transparent themes: the bg color is a chroma key -- only text/bars
        # show, the game world is visible through everything else. The title
        # bar keeps its (non-key) panel color as the drag/right-click handle.
        want = th["bg"] if th.get("transparent") else ""
        if _chroma["cur"] != want:
            try:
                root.attributes("-transparentcolor", want)
            except tk.TclError:
                pass   # non-Windows: theme still works with a dark bg
            _chroma["cur"] = want

    def draw_background(w, h):
        th = theme()
        canvas.create_rectangle(0, 0, w, h, fill=th["bg"], outline="")
        if th.get("glow"):
            for y in range(0, h, 4):
                canvas.create_line(0, y, w, y, fill=th["scanline"])
        elif th.get("border_light"):   # pixel theme: chunky bevel border
            canvas.create_rectangle(1, 1, w - 2, h - 2, outline=th["border_light"], width=2)
            canvas.create_rectangle(0, 0, w - 1, h - 1, outline=th["border_dark"], width=1)
        # text-only theme: flat background, no decoration at all

    def draw_text(x, y, **kw):
        """canvas.create_text that honors the theme's "outline" color (the
        Neon HUD sets black): the text is first stamped in the outline
        color at 8 one-pixel offsets, then drawn normally on top -- a
        stroke effect, keeping neon text readable over bright game
        footage. Only text meaningfully BRIGHTER than the outline gets
        stroked: dark ink drawn on a bright bar (the in-bar damage
        numbers) already has its contrast, and a black outline around
        black text would smear it into an unreadable blob. Themes without
        "outline" pay nothing extra."""
        ol = theme().get("outline")
        if ol and _luma(kw.get("fill", "#000000")) - _luma(ol) > 60:
            okw = dict(kw)
            okw["fill"] = ol
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1),
                           (0, -1), (0, 1), (-1, 0), (1, 0)):
                canvas.create_text(x + dx, y + dy, **okw)
        return canvas.create_text(x, y, **kw)

    def _live_values(fight, elapsed):
        """Pull the current fight's live numbers for YOU_LABEL (and the
        pet's, separately), independent of which layout renders them."""
        you = fight.actor(YOU_LABEL) if fight else None
        pet = fight.actors.get(PET_LABEL) if fight else None
        vals = {
            "dmg": you["dmg_out"] if you else 0,
            "heal": you["heal_out"] if you else 0,
            "taken": you["dmg_in"] if you else 0,
            "hits": you["hits"] if you else 0,
            "misses": you["misses"] if you else 0,
            "crits": you["crits"] if you else 0,
            "biggest": you["biggest_hit"] if you else 0,
        }
        for label, key in SEGMENTS:
            vals[key] = you[key] if you else 0
        # melee/spell/song/ds splits for the sub-readouts (pet excluded --
        # it has its own PET DPS/DTPS rows and DMG SOURCES row)
        for suffix, cats in (("m", MELEE_CATS), ("s", SPELL_CATS),
                             ("g", SONG_CATS), ("d", DS_CATS)):
            vals[f"dmg_{suffix}"] = \
                sum(you[f"{c}_dmg_out"] for c in cats) if you else 0
            vals[f"taken_{suffix}"] = \
                sum(you[f"{c}_dmg_in"] for c in cats) if you else 0
        # the pet is its own actor: its total output feeds the Pet source
        # row and the separate PET DPS/DTPS readouts
        vals["pet_dmg"] = pet["dmg_out"] if pet else 0
        vals["pet_taken"] = pet["dmg_in"] if pet else 0
        vals["pet_dmg_out"] = vals["pet_dmg"]   # Pet row in DMG SOURCES
        # source-share percentages are over you + pet combined
        vals["dmg_all"] = vals["dmg"] + vals["pet_dmg"]
        vals["acc"] = 0 if (vals["hits"] + vals["misses"]) == 0 else \
            round(100 * vals["hits"] / (vals["hits"] + vals["misses"]))
        vals["critpct"] = 0 if vals["hits"] == 0 else round(100 * vals["crits"] / vals["hits"])
        return vals

    def _rate_targets(tracker, fight, is_live, vals, elapsed):
        """DPS/HPS/DTPS (plus melee/spell splits) for the selected rate
        window. Fight mode divides fight totals by ACTIVE combat time;
        rolling modes divide the last-N-seconds totals by N (clamped to the
        fight's own age so a fresh fight doesn't look artificially weak).
        All values are x60 in per-minute mode (DPM/HPM/DTPM)."""
        mode = settings.get("rate_mode", "fight")
        if mode == "fight" or not is_live:
            rates = {"dps": vals["dmg"] / elapsed,
                     "hps": vals["heal"] / elapsed,
                     "tps": vals["taken"] / elapsed,
                     "dps_m": vals["dmg_m"] / elapsed,
                     "dps_s": vals["dmg_s"] / elapsed,
                     "dps_g": vals["dmg_g"] / elapsed,
                     "dps_d": vals["dmg_d"] / elapsed,
                     "tps_m": vals["taken_m"] / elapsed,
                     "tps_s": vals["taken_s"] / elapsed,
                     "tps_g": vals["taken_g"] / elapsed,
                     "tps_d": vals["taken_d"] / elapsed,
                     "pet_dps": vals["pet_dmg"] / elapsed,
                     "pet_tps": vals["pet_taken"] / elapsed}
        else:
            window = 10.0 if mode == "rolling10" else 30.0
            eff = max(1.0, min(window, elapsed))
            rs = tracker.rolling_sum
            rates = {"dps": rs("dmg_out", window) / eff,
                     "hps": rs("heal_out", window) / eff,
                     "tps": rs("dmg_in", window) / eff,
                     "dps_m": rs("dmg_out", window, cats=MELEE_CATS) / eff,
                     "dps_s": rs("dmg_out", window, cats=SPELL_CATS) / eff,
                     "dps_g": rs("dmg_out", window, cats=SONG_CATS) / eff,
                     "dps_d": rs("dmg_out", window, cats=DS_CATS) / eff,
                     "tps_m": rs("dmg_in", window, cats=MELEE_CATS) / eff,
                     "tps_s": rs("dmg_in", window, cats=SPELL_CATS) / eff,
                     "tps_g": rs("dmg_in", window, cats=SONG_CATS) / eff,
                     "tps_d": rs("dmg_in", window, cats=DS_CATS) / eff,
                     "pet_dps": rs("pet_out", window) / eff,
                     "pet_tps": rs("pet_in", window) / eff}
        if per_min():
            rates = {k: v * 60.0 for k, v in rates.items()}
        return rates

    # (term label, rate-key suffix, always shown?) -- song and ds only
    # appear once they've contributed this fight, keeping the line short
    SPLIT_TERMS = (("melee", "m", True), ("spell", "s", True),
                   ("song", "g", False), ("ds", "d", False))

    def _split_text(prefix, vals, w):
        """Sub-readout under/beside DPS ("dps") and DTPS ("tps"):
        "melee 12 · spell 3" plus " · song 5" / " · ds 2" whenever those
        have contributed this fight. Abbreviates on narrow canvases so the
        FONT can stay the same size."""
        base = "dmg" if prefix == "dps" else "taken"
        parts = []
        for lbl, sfx, always in SPLIT_TERMS:
            if not always and vals[f"{base}_{sfx}"] <= 0:
                continue
            a = get_anim(f"{prefix}_{sfx}")
            a["disp"] += (a["target"] - a["disp"]) * BAR_EASE
            parts.append((lbl, a["disp"]))
        if w < 240:
            short = {"melee": "m", "spell": "s", "song": "sg", "ds": "ds"}
            return "·".join(f"{short[l]} {v:.0f}" for l, v in parts)
        return " · ".join(f"{l} {v:.0f}" for l, v in parts)

    def draw_segment_row_vertical(y, w, th, label, dps_val, pct_txt, max_dps,
                                  color, amount_txt=None, rate_txt=None):
        label_w = 40
        draw_text(8, y, anchor="w", text=label, fill=color, font=mono(8, "bold"))
        track_x0, track_x1 = 8 + label_w, w - 46
        if th.get("text_only"):
            # Vintage theme: no bar -- rate (and damage, if the DMG
            # sources option adds it) as text, % in its usual spot
            mid = "  ".join(x for x in (rate_txt, amount_txt) if x)
            draw_text(track_x0, y, anchor="w", text=mid,
                               fill=th["fg"], font=mono(8))
            draw_text(w - 6, y, anchor="e", text=pct_txt,
                               fill=th["fg"], font=mono(8, "bold"))
            return
        track_w = track_x1 - track_x0
        frac = 0 if max_dps <= 0 else max(0.0, min(1.0, dps_val / max_dps))
        canvas.create_rectangle(track_x0, y - 5, track_x1, y + 5, outline=th["dim"])
        fill_w = int(track_w * frac)
        if fill_w > 0:
            canvas.create_rectangle(track_x0, y - 4, track_x0 + fill_w, y + 4,
                                    fill=color, outline="")
        if amount_txt:
            # actual damage dealt. Wide fill: right-aligned INSIDE the bar,
            # auto-contrasted against the bar color (dark text on bright
            # bars). Narrow fill: just after it, over the empty track,
            # where the theme fg already contrasts the bg.
            est_w = 7 * len(amount_txt)
            if fill_w > est_w + 10:
                draw_text(track_x0 + fill_w - 4, y, anchor="e",
                                   text=amount_txt,
                                   fill=_contrast_on(color, th),
                                   font=mono(7, "bold"))
            else:
                tx = min(track_x0 + fill_w + 4, track_x1 - est_w)
                draw_text(max(tx, track_x0 + 2), y, anchor="w",
                                   text=amount_txt, fill=th["fg"],
                                   font=mono(7))
        draw_text(w - 6, y, anchor="e", text=pct_txt,
                           fill=th["fg"], font=mono(8, "bold"))

    def render_vertical(th, tracker, fight, is_live):
        # Element size: widths, bar lengths, and spacing scale down; fonts
        # never change, and each row keeps a minimum height that fits its
        # font -- so a smaller overlay stays readable.
        s = settings.get("scale", 1.0)
        w = max(160, int(CANVAS_WIDTH_V * s))
        row_big = max(20, int(22 * s))
        row_sub = max(11, int(13 * s))
        row_seg = max(12, int(16 * s))
        row_ft = max(13, int(17 * s))
        row_pet = max(16, int(18 * s))
        gap = max(11, int(14 * s))
        # PET DPS/DTPS rows appear only when this session has a pet
        has_pet = bool(tracker.pet_names) or \
            tracker.pet_dmg_out > 0 or tracker.pet_dmg_in > 0
        at = live.get("alltime")
        at_lines = 0
        if at:   # header + stats + kills, then stance/invoc lines if any
            at_lines = 3 + (1 if at.time_pcts("stance_secs") else 0) \
                         + (1 if at.time_pcts("invocation_secs") else 0)
        h = (gap + 3 * row_big + 2 * row_sub
             + (2 * row_pet if has_pet else 0) + gap + gap
             + len(SEGMENTS) * row_seg + 4 + gap
             + (3 + at_lines) * row_ft + 4)
        canvas.configure(width=w, height=h)
        draw_background(w, h)

        # is_live means "in Combat right now" -- when idle, everything
        # combat-scoped renders as "--" (session stats below still show)
        elapsed = fight.elapsed() if (is_live and fight) else 0.001
        vals = _live_values(fight if is_live else None, elapsed)

        rates = _rate_targets(tracker, fight, is_live, vals, elapsed)
        for k, v in rates.items():
            get_anim(k)["target"] = v

        lab_d, lab_h, lab_t, lab_pd, lab_pt, unit_sfx = unit_labels()
        y = gap
        for label, key, color, has_split in (
                (lab_d, "dps", th["accent"], True),
                (lab_h, "hps", th["fg"], False),
                (lab_t, "tps", th["warn"], True)):
            a = get_anim(key)
            a["disp"] += (a["target"] - a["disp"]) * BAR_EASE
            draw_text(8, y, anchor="w", text=label, fill=th["dim"],
                               font=mono(9, "bold"))
            draw_text(w - 8, y, anchor="e",
                               text=f"{a['disp']:.0f}" if is_live else "--",
                               fill=color, font=mono(14, "bold"))
            y += row_big
            if has_split:
                if is_live:
                    draw_text(
                        w - 8, y - 6, anchor="e",
                        text=_split_text(key, vals, w),
                        fill=th["dim"], font=mono(8))
                y += row_sub

        # -- your pet, separate from you --------------------------------------
        if has_pet:
            for label, key, color in ((lab_pd, "pet_dps", th["accent"]),
                                      (lab_pt, "pet_tps", th["warn"])):
                a = get_anim(key)
                a["disp"] += (a["target"] - a["disp"]) * BAR_EASE
                draw_text(8, y, anchor="w", text=label,
                                   fill=th["dim"], font=mono(8, "bold"))
                draw_text(w - 8, y, anchor="e",
                                   text=f"{a['disp']:.0f}" if is_live else "--",
                                   fill=color, font=mono(11, "bold"))
                y += row_pet

        canvas.create_line(8, y, w - 8, y, fill=th["dim"])
        y += gap

        # -- damage source breakdown: Melee/Skill/Spell/Song/DS/Pet ----------
        # section headers render centered + accent so they pop against the
        # dim data lines
        draw_text(w // 2, y, text="— DMG SOURCES —",
                           fill=th["accent"], font=mono(9, "bold"))
        y += gap
        seg_colors = {lbl: th.get(SEG_COLOR_ROLE[lbl], th["fg"])
                      for lbl, _ in SEGMENTS}
        seg_dps = {label: vals[key] / elapsed for label, key in SEGMENTS}
        max_seg_dps = max(seg_dps.values()) if seg_dps else 0
        show_amt = settings.get("seg_show_amount", False)
        for label, key in SEGMENTS:
            amt = vals[key]
            pct = 0 if vals["dmg_all"] == 0 else round(100 * amt / vals["dmg_all"])
            rate_txt = (f"{seg_dps[label] * (60.0 if per_min() else 1.0):.0f}"
                        f"{unit_sfx}") if is_live else None
            draw_segment_row_vertical(y, w, th, label, seg_dps[label],
                                      f"{pct}%" if is_live else "--",
                                      max_seg_dps, seg_colors[label],
                                      amount_txt=_fmt_num(amt)
                                      if (show_amt and is_live and amt) else None,
                                      rate_txt=rate_txt)
            y += row_seg
        y += 4

        canvas.create_line(8, y, w - 8, y, fill=th["dim"])
        y += gap
        # -- bottom section: everything centered, dim data under accent
        #    headers ---------------------------------------------------------
        cx = w // 2
        sep = " " if w < 240 else "   "
        if is_live:
            acc_txt = (f"acc {vals['acc']}%{sep}crit {vals['critpct']}%"
                       f"{sep}big {_fmt_num(vals['biggest'])}")
        else:
            acc_txt = f"acc --{sep}crit --{sep}big --"
        draw_text(cx, y, text=acc_txt, fill=th["dim"], font=mono(8))
        y += row_ft

        kph = tracker.kills_per_hour()
        draw_text(cx, y,
                           text=f"kills {len(tracker.kills)}  ({kph:.1f}/hr)",
                           fill=th["dim"], font=mono(8))
        y += row_ft
        stance_txt = tracker.stance or "?"
        inv_txt = tracker.invocation or "?"
        draw_text(cx, y, text=f"{stance_txt} / {inv_txt}",
                           fill=th["dim"], font=mono(8))

        # -- ALL TIME: lifetime numbers right under the current ones, so
        #    better-or-worse-than-usual is one glance -----------------------
        if at:
            y += row_ft
            draw_text(cx, y, text="— ALL TIME —",
                               fill=th["accent"], font=mono(9, "bold"))
            y += row_ft
            draw_text(
                cx, y,
                text=(f"acc {at.acc_pct()}%{sep}crit {at.crit_pct()}%"
                      f"{sep}big {_fmt_num(at.data['biggest'])}"),
                fill=th["dim"], font=mono(8))
            y += row_ft
            draw_text(
                cx, y,
                text=(f"kills {at.data['kills']}  "
                      f"deaths {at.data['deaths']}"),
                fill=th["dim"], font=mono(8))
            for key, label in (("stance_secs", "stance"),
                               ("invocation_secs", "invoc")):
                pcts = at.time_pcts(key)
                if not pcts:
                    continue
                y += row_ft
                txt = " ".join(f"{_abbr(n)} {p}%" for n, p in pcts[:3])
                draw_text(cx, y, text=f"{label}: {txt}",
                                   fill=th["dim"], font=mono(8))

    def render_horizontal(th, tracker, fight, is_live):
        # Element size: only the width scales (row heights are font-bound);
        # texts tighten instead of shrinking so fonts stay the same.
        s = settings.get("scale", 1.0)
        w = max(380, int(CANVAS_WIDTH_H * s))
        h = CANVAS_HEIGHT_H
        canvas.configure(width=w, height=h)
        draw_background(w, h)

        # is_live means "in Combat right now" -- when idle, everything
        # combat-scoped renders as "--" (session stats still show)
        elapsed = fight.elapsed() if (is_live and fight) else 0.001
        vals = _live_values(fight if is_live else None, elapsed)

        rates = _rate_targets(tracker, fight, is_live, vals, elapsed)
        for k, v in rates.items():
            get_anim(k)["target"] = v

        col_w = w // 3

        # Row 1: DPS / HPS / DTPS side by side; melee/spell(/song) split next
        # to the label for DPS and DTPS
        lab_d, lab_h, lab_t, _pd, _pt, unit_sfx = unit_labels()
        for i, (label, key, color, has_split) in enumerate((
                (lab_d, "dps", th["accent"], True),
                (lab_h, "hps", th["fg"], False),
                (lab_t, "tps", th["warn"], True))):
            a = get_anim(key)
            a["disp"] += (a["target"] - a["disp"]) * BAR_EASE
            cx = 10 + i * col_w
            draw_text(cx, 10, anchor="nw", text=label, fill=th["dim"],
                               font=mono(9, "bold"))
            if has_split and is_live:
                draw_text(
                    cx + 44, 11, anchor="nw",
                    text=_split_text(key, vals, 0),   # always abbreviated
                    fill=th["dim"], font=mono(7))
            draw_text(cx, 24, anchor="nw",
                               text=f"{a['disp']:.0f}" if is_live else "--",
                               fill=color, font=mono(18, "bold"))

        canvas.create_line(6, 56, w - 6, 56, fill=th["dim"])

        # Row 2: Melee / Skill / Spell / Song / DS / Pet -- rate + % of total
        seg_colors = {lbl: th.get(SEG_COLOR_ROLE[lbl], th["fg"])
                      for lbl, _ in SEGMENTS}
        col5 = w // len(SEGMENTS)
        show_amt = settings.get("seg_show_amount", False)
        for i, (label, key) in enumerate(SEGMENTS):
            amt = vals[key]
            seg_dps = amt / elapsed * (60.0 if per_min() else 1.0)
            pct = 0 if vals["dmg_all"] == 0 else round(100 * amt / vals["dmg_all"])
            cx = 10 + i * col5
            draw_text(cx, 62, anchor="nw", text=label,
                               fill=seg_colors[label], font=mono(9, "bold"))
            if not is_live:
                txt = "--"
            elif show_amt:
                # actual damage dealt; the % keeps its usual spot
                txt = f"{_fmt_num(amt)} ({pct}%)"
            else:
                txt = f"{seg_dps:.0f}{unit_sfx} ({pct}%)"
            draw_text(cx, 76, anchor="nw", text=txt,
                               fill=th["fg"], font=mono(9, "bold"))

        # Row 3: everything else, as one horizontal strip
        kph = tracker.kills_per_hour()
        stance_txt = tracker.stance or "?"
        inv_txt = tracker.invocation or "?"
        pad = "  " if w < 520 else "      "
        if is_live:
            combat_bits = (f"acc {vals['acc']}%  crit {vals['critpct']}%  "
                           f"big {_fmt_num(vals['biggest'])}")
        else:
            combat_bits = "acc --  crit --  big --"
        pet_bits = ""
        if bool(tracker.pet_names) or tracker.pet_dmg_out > 0 \
           or tracker.pet_dmg_in > 0:
            if is_live:
                pd, pt = get_anim("pet_dps"), get_anim("pet_tps")
                pd["disp"] += (pd["target"] - pd["disp"]) * BAR_EASE
                pt["disp"] += (pt["target"] - pt["disp"]) * BAR_EASE
                u = "dpm" if per_min() else "dps"
                ut = "dtpm" if per_min() else "dtps"
                pet_bits = (f"pet {pd['disp']:.0f}{u}/"
                            f"{pt['disp']:.0f}{ut}{pad}")
            else:
                pet_bits = f"pet --{pad}"
        summary = (f"{combat_bits}{pad}{pet_bits}"
                  f"kills {len(tracker.kills)} ({kph:.1f}/hr){pad}"
                  f"{stance_txt} / {inv_txt}")
        draw_text(10, 98, anchor="nw", text=summary, fill=th["dim"], font=mono(8))

        # Row 4: ALL TIME -- lifetime numbers for at-a-glance comparison
        at = live.get("alltime")
        if at:
            st = " ".join(f"{_abbr(n)} {p}%"
                          for n, p in at.time_pcts("stance_secs")[:3])
            iv = " ".join(f"{_abbr(n)} {p}%"
                          for n, p in at.time_pcts("invocation_secs")[:3])
            tail = "  ".join(x for x in (st, iv) if x)
            # accent-bold header prefix so it pops against the dim data
            hdr = "ALL TIME"
            draw_text(10, 113, anchor="nw", text=hdr,
                               fill=th["accent"], font=mono(8, "bold"))
            alltxt = (f"acc {at.acc_pct()}%  crit {at.crit_pct()}%  "
                      f"big {_fmt_num(at.data['biggest'])}  "
                      f"kills {at.data['kills']}"
                      + (f"{pad}{tail}" if tail else ""))
            draw_text(14 + mono(8, "bold").measure(hdr), 113,
                               anchor="nw", text=alltxt,
                               fill=th["dim"], font=mono(8))

    prev_state = {"live": False}

    def render():
        th = theme()
        paint_chrome()
        tracker = live["tracker"]
        tracker.maybe_timeout()
        fight, is_live = tracker.snapshot()

        # The live meter shows the CURRENT Combat only: once no damage has
        # been dealt/received for the idle timeout, everything combat-scoped
        # resets to "--" instead of lingering on the last fight's numbers.
        in_combat = is_live and fight is not None
        if prev_state["live"] and not in_combat:
            anim.clear()   # next combat starts fresh, no easing from old data
        prev_state["live"] = in_combat

        canvas.delete("all")
        if settings.get("layout", "vertical") == "horizontal":
            render_horizontal(th, tracker, fight, in_combat)
        else:
            render_vertical(th, tracker, fight, in_combat)

        if th.get("glow") and random.random() < 0.04:
            title_lbl.configure(fg=_mix(th["accent"], th["fg"], 0.5))

        elapsed_txt = ""
        if in_combat:
            # show the fight's wall-clock span; rates divide by ACTIVE time
            m, s = divmod(int(fight.span()), 60)
            elapsed_txt = f"{m}:{s:02d}"
        badge = RATE_BADGE.get(settings.get("rate_mode", "fight"), "avg")
        if per_min():
            badge += "·/min"
        status_lbl.configure(
            text=("● LIVE " if in_combat else "idle ")
                 + elapsed_txt + f" · {badge}")

        root.after(RENDER_INTERVAL_MS, render)

    # -- log polling loop (independent of the faster render loop) --------------
    def poll():
        live["watcher"].poll()
        tracker = live["tracker"]
        live["alltime"].tick(tracker, tracker.current is not None)
        root.after(POLL_INTERVAL_MS, poll)

    # -- context menu ----------------------------------------------------------
    def set_theme(name):
        settings["theme"] = name
        settings.save()

    def set_layout(name):
        settings["layout"] = name
        settings.save()

    def set_rate_mode(name):
        settings["rate_mode"] = name
        settings.save()
        anim.clear()   # jump readouts to the new scale instead of easing

    def set_units(name):
        settings["units"] = name
        settings.save()
        anim.clear()   # x60 jump would look silly eased

    def set_timeout(v):
        settings["idle_timeout"] = v
        settings.save()
        live["tracker"].idle_timeout = float(v)   # applies immediately

    def set_seg_amount(v):
        settings["seg_show_amount"] = v
        settings.save()

    def quit_app():
        store = live.get("alltime")
        if store:
            store.save()
        root.destroy()

    def set_scale(v):
        settings["scale"] = v
        settings.save()

    def set_opacity(v):
        settings["opacity"] = v
        settings.save()
        try:
            root.attributes("-alpha", v)
        except Exception:
            pass

    def reset_fight():
        live["tracker"].force_end_fight()
        anim.clear()

    def show_unmatched():
        import tkinter as tk
        tracker = live["tracker"]
        win = tk.Toplevel(root)
        win.title("Unrecognized combat-like lines")
        win.attributes("-topmost", True)
        txt = tk.Text(win, width=100, height=24, bg="#101418", fg="#d8dee6")
        txt.pack(fill="both", expand=True)
        if tracker.unmatched:
            txt.insert("1.0", "\n".join(tracker.unmatched))
        else:
            txt.insert("1.0", "(none yet -- fight something with logging on)")
        txt.configure(state="disabled")

    def change_log():
        from tkinter import filedialog
        initialdir = os.path.dirname(settings.get("log_path", "")) or None
        chosen = filedialog.askopenfilename(
            title="Select your EverQuest log file (eqlog_*.txt)",
            initialdir=initialdir,
            filetypes=[("EQ log files", "eqlog_*.txt"),
                       ("Text files", "*.txt"), ("All files", "*.*")])
        if chosen and os.path.isfile(chosen):
            open_log(chosen)
            anim.clear()

    def open_report():
        import subprocess
        script = os.path.join(APP_DIR, "eql_session_report.py")
        if not os.path.isfile(script):
            return
        try:
            subprocess.Popen([sys.executable, script, live["watcher"].path], cwd=APP_DIR)
        except OSError:
            pass

    def main_menu(e):
        import tkinter as tk
        m = tk.Menu(root, tearoff=0)

        th_menu = tk.Menu(m, tearoff=0)
        for key, spec in RETRO_THEMES.items():
            th_menu.add_command(label=spec["label"], command=lambda k=key: set_theme(k))
        m.add_cascade(label="Theme", menu=th_menu)

        layout_menu = tk.Menu(m, tearoff=0)
        for key, label in (("vertical", "Vertical (Recommended)"),
                          ("horizontal", "Horizontal")):
            layout_menu.add_command(label=label, command=lambda k=key: set_layout(k))
        m.add_cascade(label="Layout", menu=layout_menu)

        rate_menu = tk.Menu(m, tearoff=0)
        for key, label in RATE_MODES:
            mark = "● " if settings.get("rate_mode", "fight") == key else "   "
            rate_menu.add_command(label=mark + label,
                                  command=lambda k=key: set_rate_mode(k))
        m.add_cascade(label="Rate window", menu=rate_menu)

        unit_menu = tk.Menu(m, tearoff=0)
        for key, label in UNIT_MODES:
            mark = "● " if settings.get("units", "sec") == key else "   "
            unit_menu.add_command(label=mark + label,
                                  command=lambda k=key: set_units(k))
        m.add_cascade(label="Rate units", menu=unit_menu)

        to_menu = tk.Menu(m, tearoff=0)
        cur_to = settings.get("idle_timeout", 45)
        for v in TIMEOUT_CHOICES:
            mark = "● " if abs(cur_to - v) < 0.5 else "   "
            to_menu.add_command(label=f"{mark}{v}s without damage",
                                command=lambda v=v: set_timeout(v))
        m.add_cascade(label="Combat timeout", menu=to_menu)

        seg_menu = tk.Menu(m, tearoff=0)
        cur_seg = settings.get("seg_show_amount", False)
        for val, label in ((False, "Percent only"),
                           (True, "Damage + percent")):
            mark = "● " if cur_seg == val else "   "
            seg_menu.add_command(label=mark + label,
                                 command=lambda v=val: set_seg_amount(v))
        m.add_cascade(label="DMG sources", menu=seg_menu)

        size_menu = tk.Menu(m, tearoff=0)
        cur = settings.get("scale", 1.0)
        for v in SIZE_STEPS:
            mark = "● " if abs(cur - v) < 0.01 else "   "
            size_menu.add_command(label=f"{mark}{int(v*100)}%",
                                  command=lambda v=v: set_scale(v))
        m.add_cascade(label="Size", menu=size_menu)

        op = tk.Menu(m, tearoff=0)
        for v in (1.0, 0.9, 0.75, 0.6, 0.45):
            op.add_command(label=f"{int(v*100)}%", command=lambda v=v: set_opacity(v))
        m.add_cascade(label="Opacity", menu=op)

        m.add_separator()
        m.add_command(label="Reset current fight", command=reset_fight)
        if os.path.isfile(os.path.join(APP_DIR, "eql_session_report.py")):
            m.add_command(label="Open Session Report...", command=open_report)
        m.add_command(label="Show unrecognized combat lines...", command=show_unmatched)
        m.add_command(label="Change log file...", command=change_log)
        m.add_separator()
        m.add_command(label="Quit", command=quit_app)
        m.tk_popup(e.x_root, e.y_root)

    bar.bind("<Button-3>", main_menu)
    title_lbl.bind("<Button-3>", main_menu)
    canvas.bind("<Button-3>", main_menu)

    render()
    poll()
    root.mainloop()


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
def main():
    log_path = ""
    if len(sys.argv) > 1:
        log_path = sys.argv[1]
    else:
        try:
            s = Settings(SETTINGS_FILE)
            saved = s.get("log_path", "")
            if saved and os.path.isfile(saved):
                log_path = saved
        except Exception:
            pass
    if not log_path:
        try:
            import tkinter as tk
            from tkinter import filedialog
            hidden = tk.Tk(); hidden.withdraw()
            log_path = filedialog.askopenfilename(
                title="Select your EverQuest log file (eqlog_*.txt)",
                filetypes=[("EQ log files", "eqlog_*.txt"),
                          ("Text files", "*.txt"), ("All files", "*.*")])
            hidden.destroy()
        except Exception:
            log_path = ""
    if not log_path or not os.path.isfile(log_path):
        print("No log file selected/found. Pass the path as an argument:")
        print('  python eql_dps_meter.py "C:\\EQ\\Logs\\eqlog_Name_server.txt"')
        sys.exit(1)
    run_overlay(log_path)


if __name__ == "__main__":
    main()
