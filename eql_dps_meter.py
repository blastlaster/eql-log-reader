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
  * Theme       -- the suite's shared theme set (16-bit Window by default):
                   16-bit Window / CRT Terminal / Arcade LED / Vintage /
                   Neon HUD (transparent: no background at all -- neon
                   pink/orange/yellow/blue/green text and bars float
                   directly over the game, every text black-outlined so
                   it reads over bright footage; Windows only, elsewhere
                   it falls back to a dark background)
                   (monochrome, no scanlines/bevels, and DMG SOURCES render
                   as plain text rows instead of bars)
  * Layout      -- Vertical (compact column) / Horizontal (wide strip, all
                   meters side by side) / All timescales (the vertical
                   column with a 3x3 grid up top: DMG/HEAL/TAKEN rows x
                   now-per-second (rolling 30s), last-minute (last 60s as
                   /m), and whole-Combat-per-hour columns; runs on the
                   per-hour Combat timeout, 1h-5h, and switching into it
                   backfills the window from the log -- see below)
  * Rate window -- Fight average (totals over active combat time, steadier)
                   / Rolling 10s / Rolling 30s (what's hitting right now --
                   better reflects bursts, e.g. a big hit no longer gets
                   averaged away by a long fight)
  * Rate units  -- Per second (DPS/HPS/DTPS) / Per minute (DPM/HPM/DTPM) /
                   Per hour (DPH/HPH/DTPH). Same numbers x60 / x3600;
                   per-minute reads better at low levels where per-second
                   rates are single digits, per-hour suits whole-grind
                   sustained output. Each unit mode remembers its OWN
                   Combat timeout (see below) and swaps it in on switch.
  * DMG sources -- Percent only (default) / Damage + percent: adds the
                   actual damage dealt per source onto the DMG SOURCES
                   graph (at the end of each bar in the vertical layout,
                   in place of the rate in the horizontal one); the % keeps
                   its usual spot either way.
  * Combat timeout -- how long without damage before the current Combat
                   ends. The preset list follows the unit mode, and each
                   mode remembers its own pick: per-second 5s-60s (split
                   fights cleanly per mob vs. keep chained pulls together),
                   per-minute 1m/5m/15m/30m/45m/60m (chain whole pulls),
                   per-hour 1h-5h (a grind session is one Combat). Applies
                   immediately -- switch mid-session to feel out the right
                   value. (The rate itself is safe either way: rates divide
                   by ACTIVE combat time, so the timeout mostly changes how
                   fights are GROUPED, not the number.) Moving to a LONGER
                   window (units, timeout, or the tri layout) re-pulls that
                   much log history so the Combat shows the whole-window
                   total immediately; "Reset current fight" starts from
                   scratch when that's what you want.

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
  * Text size   -- Standard (100%) / Elder (200%) / Legend (250%): scales
                   the FONTS and the layout together, for eyes that want
                   bigger text; independent of Size above
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
    get_theme,
    POLL_INTERVAL_MS, SEED_BYTES, luma as _luma,
)
from eql_combat_tracker import (CombatTracker, YOU_LABEL, PET_LABEL,
                                TS_ONLY_RE, LOG_TS_FMT)
from eql_spell_db import SPELL_DB

RENDER_INTERVAL_MS = 90      # animation frame rate (independent of log polling)
BAR_EASE = 0.30              # how quickly the readout numbers ease toward target

# Two selectable layouts -- see set_layout()/render() below. Vertical is the
# original compact column; Horizontal lays the same meters out side by side
# in a wide strip (handy for docking along a screen edge).
CANVAS_WIDTH_V = 260
CANVAS_HEIGHT_V = 292
CANVAS_WIDTH_H = 620
CANVAS_HEIGHT_H = 128   # includes the ALL TIME bottom row

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
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


def _fmt_countdown(secs):
    secs = int(secs)
    if secs >= 3600:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}"
    if secs >= 60:
        return f"{secs // 60}:{secs % 60:02d}"
    return f"{secs}s"


def _buff_display_rows(tracker, max_rows=6):
    """(label, time-text) for the buffs/debuffs currently active on YOU
    (tracked from the spell file's cast-on-you/fade messages), soonest to
    expire first. The countdown is an ESTIMATE from the spell's own
    duration formula (see eql_spell_db.duration_seconds caveats), scaled
    by YOUR level when the log has revealed it (/who or a level-up line;
    L50 assumed until then). Buffs someone ELSE cast on you scale by the
    CASTER's level, which the log never shows -- those estimates read
    long/short by the level difference:
      3:42  -- estimated time left
      ?     -- past its estimated end but no fade message seen yet
      perm  -- permanent until removed
      +1:07 -- unknown duration (ambiguous message / not in the spell
               file); shows time since it landed instead
    A quoted-message label (spell ambiguous) still gets a real countdown
    when every candidate spell shares one duration estimate -- whole spell
    lines share a message AND a duration formula, so which spell it is
    doesn't change when it ends."""
    now = time.time()
    lvl = tracker.player_level or 50
    rows = []
    for label, start in tracker.active_buffs.items():
        info = SPELL_DB.lookup(label)
        dur = info.duration_seconds(lvl) if info else 0
        if not info:
            cands = tracker._active_buff_cands.get(label) or ()
            durs = {(i.duration_seconds(lvl) if i else None)
                    for i in (SPELL_DB.lookup(c) for c in cands)}
            if len(durs) == 1 and None not in durs:
                dur = durs.pop()
        if dur and dur > 0:
            rem = dur - (now - start)
            if rem <= 0:
                rows.append((float("inf"), label, "?"))
            else:
                rows.append((rem, label, _fmt_countdown(rem)))
        elif dur == -1:
            rows.append((float("inf"), label, "perm"))
        else:
            rows.append((float("inf"), label,
                         "+" + _fmt_countdown(now - start)))
    rows.sort(key=lambda r: (r[0], r[1]))
    return [(lbl, txt) for _, lbl, txt in rows[:max_rows]]


def _resist_display_rows(fight, is_live, max_rows=4):
    """(spell, 'xN') for YOUR spells/songs a mob resisted DURING the
    current fight, most-resisted first. Empty when idle -- the tally
    clears with the fight, so it's a live nudge that an ability isn't
    landing on THIS enemy and needs swapping (lifetime counts stay in the
    Session Report's Resisted column)."""
    if not (is_live and fight and fight.spell_resists):
        return []
    rows = sorted(fight.spell_resists.items(),
                  key=lambda kv: (-kv[1], kv[0]))
    return [(name, f"x{n}") for name, n in rows[:max_rows]]


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
        # "sec" (DPS/HPS/DTPS), "min" (DPM/HPM/DTPM), "hour" (DPH/HPH/DTPH)
        "units": "sec",
        # seconds without damage dealt/received before the Combat ends.
        # Each unit mode keeps its OWN timeout (switching units swaps it in):
        # per-second tunes fight grouping by feel (5-60s); per-minute chains
        # pulls (1m-60m); per-hour groups whole grind sessions (1h-5h).
        "idle_timeout": 45,
        "idle_timeout_min": 60,
        "idle_timeout_hour": 3600,
        # DMG SOURCES rows: False = percent only, True = also draw the
        # actual damage dealt on the graph (% keeps its usual spot)
        "seg_show_amount": False,
        # BUFFS block: active buffs/debuffs on you with estimated countdowns
        "show_buffs": True,
        # RESISTED block: per-fight tally of your spells a mob resisted
        "show_resisted": True,
        # Text size: 1.0 standard, 2.0 "Elder", 2.5 "Legend" -- scales the
        # FONTS (and the layout with them), independent of "scale" above
        # which shrinks the element footprint while keeping fonts fixed
        "font_scale": 1.0,
        "scale": 1.0,   # element size; fonts stay constant (see SIZE_STEPS)
    })

    RATE_MODES = (("fight", "Fight average"),
                  ("rolling10", "Rolling 10s"),
                  ("rolling30", "Rolling 30s"))
    RATE_BADGE = {"fight": "avg", "rolling10": "10s", "rolling30": "30s"}
    UNIT_MODES = (("sec", "Per second (DPS)"), ("min", "Per minute (DPM)"),
                  ("hour", "Per hour (DPH)"))
    # Combat-timeout presets PER unit mode: per-second tunes fight grouping
    # by feel; per-minute chains pulls; per-hour makes a grind session one
    # Combat. Each mode remembers its own choice (TIMEOUT_KEYS).
    TIMEOUT_CHOICES = {"sec": (5, 15, 30, 45, 60),
                       "min": (60, 300, 900, 1800, 2700, 3600),
                       "hour": (3600, 7200, 10800, 14400, 18000)}
    TIMEOUT_KEYS = {"sec": "idle_timeout", "min": "idle_timeout_min",
                    "hour": "idle_timeout_hour"}
    TIMEOUT_DEFAULTS = {"sec": 45, "min": 60, "hour": 3600}

    def unit_mode():
        # the tri layout shows all three timescales at once; its shared
        # sections (segments, badge, pet strip) and its Combat timeout
        # read at the hour scale
        if settings.get("layout") == "tri":
            return "hour"
        u = settings.get("units", "sec")
        return u if u in ("sec", "min", "hour") else "sec"

    def unit_factor():
        """Multiplier from per-second rates to the displayed unit."""
        return {"sec": 1.0, "min": 60.0, "hour": 3600.0}[unit_mode()]

    def active_timeout():
        """The Combat idle timeout for the ACTIVE unit mode (seconds)."""
        mode = unit_mode()
        return float(settings.get(TIMEOUT_KEYS[mode], TIMEOUT_DEFAULTS[mode]))

    def fmt_rate(v):
        """A rate readout: raw for per-second/minute (unchanged behavior),
        abbreviated in per-hour mode where values run 5-7 digits (108.0K)."""
        return _fmt_num(v) if unit_mode() == "hour" and v >= 1000 \
            else f"{v:.0f}"

    def unit_labels():
        """(DPS, HPS, DTPS, PET-DPS, PET-DTPS, /s) labels for the active
        rate unit -- same numbers x60 / x3600 and relabeled in per-minute /
        per-hour mode."""
        mode = unit_mode()
        if mode == "min":
            return ("DPM", "HPM", "DTPM", "PET DPM", "PET DTPM", "/m")
        if mode == "hour":
            return ("DPH", "HPH", "DTPH", "PET DPH", "PET DTPH", "/h")
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

    def font_scale():
        """Text-size multiplier (1.0 / 2.0 "Elder" / 2.5 "Legend"). Layout
        metrics multiply by this too, so everything grows together."""
        return float(settings.get("font_scale", 1.0))

    def mono(size, weight="normal"):
        fs = font_scale()
        key = (settings["theme"], size, weight, fs)
        f = _font_cache.get(key)
        if f is None:
            f = tkfont.Font(family=get_theme(settings["theme"])["font_mono"][0],
                            size=int(round(size * fs)), weight=weight)
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

    def _bytes_for_window(path, secs):
        """How many tail bytes of `path` cover the last `secs` seconds of
        LOG time: scan backwards in 256KB steps until a chunk's first
        timestamp predates the cutoff. Bounded at 32MB. Lets long Combat
        timeouts (minutes/hours) seed their whole window instead of the
        default half-megabyte tail."""
        from datetime import datetime
        try:
            size = os.path.getsize(path)
        except OSError:
            return SEED_BYTES
        cap = min(size, 32 * 1024 * 1024)
        cutoff = time.time() - secs - 120   # margin for the current gap
        offset = 256 * 1024
        try:
            with open(path, "rb") as f:
                while offset < cap:
                    f.seek(size - offset)
                    chunk = f.read(4096).decode("utf-8", errors="replace")
                    # first line of the chunk is usually partial -- skip it
                    for line in chunk.splitlines()[1:]:
                        m = TS_ONLY_RE.match(line)
                        if not m:
                            continue
                        try:
                            ts = datetime.strptime(
                                m.group("ts"), LOG_TS_FMT).timestamp()
                        except ValueError:
                            pass
                        else:
                            if ts <= cutoff:
                                return offset
                        break
                    offset += 256 * 1024
        except OSError:
            pass
        return cap

    def open_log(path):
        prev_store = live.get("alltime")
        if prev_store:
            prev_store.save()   # switching characters -- flush the old file
        # the tracker's song-vs-spell fallback needs spells_us.txt; the log
        # lives in <game dir>\Logs, so the game dir is two levels up
        SPELL_DB.set_game_dir_hint(os.path.dirname(os.path.dirname(path)))
        tracker = CombatTracker(
            self_name=char_name_from(path),
            idle_timeout=active_timeout())
        watcher = LogWatcher(path)
        watcher.add_handler(tracker.handle_line)
        # long Combat windows (per-minute/hour modes, tri layout) seed
        # enough log tail to FILL the window -- the current fight shows
        # the whole-window total right away instead of starting empty
        t = active_timeout()
        watcher.seed(max_bytes=max(SEED_BYTES, _bytes_for_window(path, t))
                     if t > 90 else SEED_BYTES)
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

    def reseed_for_timeout(old_timeout):
        """Called after a units/timeout/layout change. Moving to a LONGER
        Combat window re-pulls that much log history so the fight shows
        the whole-window total immediately ("Reset current fight" starts
        from scratch instead); a shorter window just applies -- the next
        idle check regroups naturally."""
        new = active_timeout()
        if new > old_timeout + 0.5:
            path = settings.get("log_path")
            if path and os.path.isfile(path):
                open_log(path)
                anim.clear()
                return
        live["tracker"].idle_timeout = new

    # display-value easing, keyed by a small fixed set of readout rows
    anim = {}

    def get_anim(name):
        return anim.setdefault(name, {"disp": 0.0, "target": 0.0})

    # -- rendering -------------------------------------------------------------
    def theme():
        return get_theme(settings["theme"])

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
        f = unit_factor()
        if f != 1.0:
            rates = {k: v * f for k, v in rates.items()}
        return rates

    def _tri_targets(tracker, vals, elapsed):
        """The tri layout's three timescales per metric: rolling 30s (what
        is hitting NOW), the last 60s expressed per minute (recent pace),
        and the current Combat's average per hour (the whole grind -- the
        tri layout groups Combats on the 1-5h timeout)."""
        rs = tracker.rolling_sum
        e30 = max(1.0, min(30.0, elapsed))
        e60 = max(1.0, min(60.0, elapsed))
        out = {}
        for metric, key in (("dps", "dmg_out"), ("hps", "heal_out"),
                            ("tps", "dmg_in")):
            out[f"t3_{metric}_s"] = rs(key, 30.0) / e30
            out[f"t3_{metric}_m"] = rs(key, 60.0) / e60 * 60.0
        out["t3_dps_h"] = vals["dmg"] / elapsed * 3600.0
        out["t3_hps_h"] = vals["heal"] / elapsed * 3600.0
        out["t3_tps_h"] = vals["taken"] / elapsed * 3600.0
        return out

    def draw_strip(y, w, segs, size=8):
        """One CENTERED stat line built from (text, color) segments --
        color variety keeps the dense bottom-section strips scannable."""
        f = mono(size)
        total = sum(f.measure(t) for t, _ in segs)
        x = (w - total) // 2
        for t, c in segs:
            draw_text(x, y, anchor="w", text=t, fill=c, font=f)
            x += f.measure(t)

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
        if w < 240 * font_scale():
            short = {"melee": "m", "spell": "s", "song": "sg", "ds": "ds"}
            return "·".join(f"{short[l]} {fmt_rate(v)}" for l, v in parts)
        return " · ".join(f"{l} {fmt_rate(v)}" for l, v in parts)

    def draw_segment_row_vertical(y, w, th, label, dps_val, pct_txt, max_dps,
                                  color, amount_txt=None, rate_txt=None):
        fs = font_scale()
        label_w = int(40 * fs)
        draw_text(8, y, anchor="w", text=label, fill=color, font=mono(8, "bold"))
        track_x0, track_x1 = 8 + label_w, w - int(46 * fs)
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
        bh = int(5 * fs)
        canvas.create_rectangle(track_x0, y - bh, track_x1, y + bh,
                                outline=th["dim"])
        fill_w = int(track_w * frac)
        if fill_w > 0:
            canvas.create_rectangle(track_x0, y - bh + 1,
                                    track_x0 + fill_w, y + bh - 1,
                                    fill=color, outline="")
        if amount_txt:
            # actual damage dealt. Wide fill: right-aligned INSIDE the bar,
            # auto-contrasted against the bar color (dark text on bright
            # bars). Narrow fill: just after it, over the empty track,
            # where the theme fg already contrasts the bg.
            est_w = int(7 * fs) * len(amount_txt)
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
        # The tri layout ("All timescales") reuses this column, swapping
        # the big single readouts for a 3x3 grid: rolling 30s (/s), last
        # 60s as per-minute (/m), and the whole Combat per hour (/h).
        tri = settings.get("layout") == "tri"
        s = settings.get("scale", 1.0)
        fs = font_scale()   # text-size preset scales fonts AND layout
        w = int(max(200 if tri else 160,
                    (330 if tri else CANVAS_WIDTH_V) * s) * fs)
        row_big = int(max(20, 22 * s) * fs)
        row_sub = int(max(11, 13 * s) * fs)
        row_seg = int(max(12, 16 * s) * fs)
        row_ft = int(max(13, 17 * s) * fs)
        row_pet = int(max(16, 18 * s) * fs)
        gap = int(max(11, 14 * s) * fs)
        # PET DPS/DTPS rows appear only when this session has a pet
        has_pet = bool(tracker.pet_names) or \
            tracker.pet_dmg_out > 0 or tracker.pet_dmg_in > 0
        at = live.get("alltime")
        at_lines = 0
        if at:   # header + stats + kills, then stance/invoc lines if any
            at_lines = 3 + (1 if at.time_pcts("stance_secs") else 0) \
                         + (1 if at.time_pcts("invocation_secs") else 0)
        buff_rows = _buff_display_rows(tracker) \
            if settings.get("show_buffs", True) else []
        resist_rows = _resist_display_rows(fight, is_live) \
            if settings.get("show_resisted", True) else []
        top_block = (int(14 * fs) + 3 * row_big) if tri \
            else (3 * row_big + 2 * row_sub)
        h = (gap + top_block
             + (2 * row_pet if has_pet else 0) + gap + gap
             + len(SEGMENTS) * row_seg + 4
             + ((2 * gap + len(resist_rows) * row_ft + 4) if resist_rows else 0)
             + ((2 * gap + len(buff_rows) * row_ft + 4) if buff_rows else 0)
             + gap + (3 + at_lines) * row_ft + 4)
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
        if tri:
            # 3x3 grid: metric rows x timescale columns. Cell right edges
            # split the width after the row label.
            for t3k, v in _tri_targets(tracker, vals, elapsed).items():
                get_anim(t3k)["target"] = v
            col_r = [int(46 * fs) + (w - int(54 * fs)) * (i + 1) // 3
                     for i in range(3)]
            for i, hdr in enumerate(("now/s", "1m/m", "combat/h")):
                draw_text(col_r[i], y, anchor="e", text=hdr,
                          fill=th["dim"], font=mono(7))
            y += int(14 * fs)
            for label, metric, color in (("DMG", "dps", th["accent"]),
                                         ("HEAL", "hps", th["fg"]),
                                         ("TAKEN", "tps", th["warn"])):
                draw_text(8, y, anchor="w", text=label, fill=th["dim"],
                          font=mono(9, "bold"))
                for i, sfx in enumerate(("s", "m", "h")):
                    a = get_anim(f"t3_{metric}_{sfx}")
                    a["disp"] += (a["target"] - a["disp"]) * BAR_EASE
                    draw_text(col_r[i], y, anchor="e",
                              text=fmt_rate(a["disp"]) if is_live else "--",
                              fill=color, font=mono(11, "bold"))
                y += row_big
        else:
            for label, key, color, has_split in (
                    (lab_d, "dps", th["accent"], True),
                    (lab_h, "hps", th["fg"], False),
                    (lab_t, "tps", th["warn"], True)):
                a = get_anim(key)
                a["disp"] += (a["target"] - a["disp"]) * BAR_EASE
                draw_text(8, y, anchor="w", text=label, fill=th["dim"],
                                   font=mono(9, "bold"))
                draw_text(w - 8, y, anchor="e",
                                   text=fmt_rate(a['disp']) if is_live else "--",
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
                                   text=fmt_rate(a['disp']) if is_live else "--",
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
            rate_txt = (fmt_rate(seg_dps[label] * unit_factor())
                        + unit_sfx) if is_live else None
            draw_segment_row_vertical(y, w, th, label, seg_dps[label],
                                      f"{pct}%" if is_live else "--",
                                      max_seg_dps, seg_colors[label],
                                      amount_txt=_fmt_num(amt)
                                      if (show_amt and is_live and amt) else None,
                                      rate_txt=rate_txt)
            y += row_seg
        y += 4

        # -- your spells a mob resisted THIS fight -- a live nudge that an
        #    ability isn't landing on this enemy; clears with the fight ------
        if resist_rows:
            canvas.create_line(8, y, w - 8, y, fill=th["dim"])
            y += gap
            draw_text(w // 2, y, text="— RESISTED —",
                      fill=th["warn"], font=mono(9, "bold"))
            y += gap
            maxch = max(10, int((w - 50 * fs) // (6 * fs)))
            for name, txt in resist_rows:
                nm = name if len(name) <= maxch else name[:maxch - 1] + "…"
                draw_text(8, y, anchor="w", text=nm, fill=th["fg"],
                          font=mono(8))
                draw_text(w - 8, y, anchor="e", text=txt, fill=th["warn"],
                          font=mono(8))
                y += row_ft
            y += 4

        # -- active buffs/debuffs on you, est. countdowns (see
        #    _buff_display_rows for the time-text legend) --------------------
        if buff_rows:
            canvas.create_line(8, y, w - 8, y, fill=th["dim"])
            y += gap
            draw_text(w // 2, y, text="— BUFFS —",
                      fill=th["accent"], font=mono(9, "bold"))
            y += gap
            maxch = max(10, int((w - 70 * fs) // (6 * fs)))
            for label, txt in buff_rows:
                name = label if len(label) <= maxch \
                    else label[:maxch - 1] + "…"
                draw_text(8, y, anchor="w", text=name, fill=th["fg"],
                          font=mono(8))
                draw_text(w - 8, y, anchor="e", text=txt, fill=th["dim"],
                          font=mono(8))
                y += row_ft
            y += 4

        canvas.create_line(8, y, w - 8, y, fill=th["dim"])
        y += gap
        # -- bottom section: centered strips, labels dim / values colored
        #    so the dense stat lines stay scannable --------------------------
        cx = w // 2
        sep = " " if w < 240 * fs else "   "
        dim, fg, acc, warn = th["dim"], th["fg"], th["accent"], th["warn"]
        if is_live:
            draw_strip(y, w, [
                ("acc ", dim), (f"{vals['acc']}%", fg), (sep, dim),
                ("crit ", dim), (f"{vals['critpct']}%", fg), (sep, dim),
                ("big ", dim), (_fmt_num(vals['biggest']), warn)])
        else:
            draw_strip(y, w, [("acc --", dim), (sep, dim),
                              ("crit --", dim), (sep, dim), ("big --", dim)])
        y += row_ft

        kph = tracker.kills_per_hour()
        draw_strip(y, w, [("kills ", dim), (str(len(tracker.kills)), acc),
                          (f"  ({kph:.1f}/hr)", dim)])
        y += row_ft
        draw_strip(y, w, [(tracker.stance or "?", acc), (" / ", dim),
                          (tracker.invocation or "?", fg)])

        # -- ALL TIME: lifetime numbers right under the current ones, so
        #    better-or-worse-than-usual is one glance -----------------------
        if at:
            y += row_ft
            draw_text(cx, y, text="— ALL TIME —",
                               fill=th["accent"], font=mono(9, "bold"))
            y += row_ft
            draw_strip(y, w, [
                ("acc ", dim), (f"{at.acc_pct()}%", fg), (sep, dim),
                ("crit ", dim), (f"{at.crit_pct()}%", fg), (sep, dim),
                ("big ", dim), (_fmt_num(at.data['biggest']), warn)])
            y += row_ft
            draw_strip(y, w, [
                ("kills ", dim), (str(at.data['kills']), acc),
                ("  deaths ", dim), (str(at.data['deaths']), warn)])
            for key, label in (("stance_secs", "stance"),
                               ("invocation_secs", "invoc")):
                pcts = at.time_pcts(key)
                if not pcts:
                    continue
                y += row_ft
                segs = [(f"{label}: ", dim)]
                for n, p in pcts[:3]:
                    segs += [(_abbr(n), fg), (f" {p}%", dim), (" ", dim)]
                draw_strip(y, w, segs)

    def render_horizontal(th, tracker, fight, is_live):
        # Element size: only the width scales (row heights are font-bound);
        # texts tighten instead of shrinking so fonts stay the same. The
        # text-size preset (fs) scales the whole fixed-coordinate grid
        # along with the fonts -- z() maps design coordinates to pixels.
        s = settings.get("scale", 1.0)
        fs = font_scale()

        def z(v):
            return int(v * fs)

        w = int(max(380, CANVAS_WIDTH_H * s) * fs)
        buff_rows = _buff_display_rows(tracker) \
            if settings.get("show_buffs", True) else []
        resist_rows = _resist_display_rows(fight, is_live) \
            if settings.get("show_resisted", True) else []
        h = z(CANVAS_HEIGHT_H) + (z(14) if buff_rows else 0) \
            + (z(14) if resist_rows else 0)
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
            cx = z(10) + i * col_w
            draw_text(cx, z(10), anchor="nw", text=label, fill=th["dim"],
                               font=mono(9, "bold"))
            if has_split and is_live:
                draw_text(
                    cx + z(44), z(11), anchor="nw",
                    text=_split_text(key, vals, 0),   # always abbreviated
                    fill=th["dim"], font=mono(7))
            draw_text(cx, z(24), anchor="nw",
                               text=fmt_rate(a['disp']) if is_live else "--",
                               fill=color, font=mono(18, "bold"))

        canvas.create_line(6, z(56), w - 6, z(56), fill=th["dim"])

        # Row 2: Melee / Skill / Spell / Song / DS / Pet -- rate + % of total
        seg_colors = {lbl: th.get(SEG_COLOR_ROLE[lbl], th["fg"])
                      for lbl, _ in SEGMENTS}
        col5 = w // len(SEGMENTS)
        show_amt = settings.get("seg_show_amount", False)
        for i, (label, key) in enumerate(SEGMENTS):
            amt = vals[key]
            seg_dps = amt / elapsed * unit_factor()
            pct = 0 if vals["dmg_all"] == 0 else round(100 * amt / vals["dmg_all"])
            cx = z(10) + i * col5
            draw_text(cx, z(62), anchor="nw", text=label,
                               fill=seg_colors[label], font=mono(9, "bold"))
            if not is_live:
                txt = "--"
            elif show_amt:
                # actual damage dealt; the % keeps its usual spot
                txt = f"{_fmt_num(amt)} ({pct}%)"
            else:
                txt = f"{fmt_rate(seg_dps)}{unit_sfx} ({pct}%)"
            draw_text(cx, z(76), anchor="nw", text=txt,
                               fill=th["fg"], font=mono(9, "bold"))

        # Rows 3-6 are CENTERED strips with colored values (labels dim,
        # values colored) -- the old single dim left-aligned line was a
        # wall of text at this density.
        dim, fg, acc, warn = th["dim"], th["fg"], th["accent"], th["warn"]
        kph = tracker.kills_per_hour()
        pad = "  " if w < 520 * fs else "      "

        # Row 3: current-fight stats + pet + kills + stance/invocation
        if is_live:
            segs = [("acc ", dim), (f"{vals['acc']}%", fg),
                    ("  crit ", dim), (f"{vals['critpct']}%", fg),
                    ("  big ", dim), (_fmt_num(vals['biggest']), warn)]
        else:
            segs = [("acc --  crit --  big --", dim)]
        if bool(tracker.pet_names) or tracker.pet_dmg_out > 0 \
           or tracker.pet_dmg_in > 0:
            if is_live:
                pd, pt = get_anim("pet_dps"), get_anim("pet_tps")
                pd["disp"] += (pd["target"] - pd["disp"]) * BAR_EASE
                pt["disp"] += (pt["target"] - pt["disp"]) * BAR_EASE
                u, ut = {"sec": ("dps", "dtps"), "min": ("dpm", "dtpm"),
                         "hour": ("dph", "dtph")}[unit_mode()]
                segs += [(f"{pad}pet ", dim),
                         (f"{fmt_rate(pd['disp'])}{u}", acc), ("/", dim),
                         (f"{fmt_rate(pt['disp'])}{ut}", warn)]
            else:
                segs += [(f"{pad}pet --", dim)]
        segs += [(f"{pad}kills ", dim), (str(len(tracker.kills)), acc),
                 (f" ({kph:.1f}/hr)", dim),
                 (pad, dim), (tracker.stance or "?", acc), (" / ", dim),
                 (tracker.invocation or "?", fg)]
        draw_strip(z(103), w, segs)

        # Row 4: ALL TIME -- lifetime numbers for at-a-glance comparison
        at = live.get("alltime")
        if at:
            segs = [("ALL TIME  ", acc),
                    ("acc ", dim), (f"{at.acc_pct()}%", fg),
                    ("  crit ", dim), (f"{at.crit_pct()}%", fg),
                    ("  big ", dim), (_fmt_num(at.data['biggest']), warn),
                    ("  kills ", dim), (str(at.data['kills']), acc)]
            for key in ("stance_secs", "invocation_secs"):
                pcts = at.time_pcts(key)
                if pcts:
                    segs.append((pad, dim))
                    for n, p in pcts[:3]:
                        segs += [(_abbr(n), fg), (f" {p}% ", dim)]
            draw_strip(z(118), w, segs)

        # Row 5: active buffs/debuffs on you, est. countdowns (see
        # _buff_display_rows for the time-text legend)
        if buff_rows:
            segs = [("BUFFS  ", acc)]
            for lbl, txt in buff_rows:
                nm = lbl if len(lbl) <= 16 else lbl[:15] + "…"
                segs += [(nm, fg), (f" {txt}  ", dim)]
            draw_strip(z(133), w, segs)

        # Row 6: your spells a mob resisted THIS fight (clears with it)
        if resist_rows:
            ry = z(133) + (z(14) if buff_rows else 0)
            segs = [("RESISTED  ", warn)]
            for n, t in resist_rows:
                nm = n if len(n) <= 20 else n[:19] + "…"
                segs += [(nm, fg), (f" {t}  ", warn)]
            draw_strip(ry, w, segs)

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
            # "vertical" and "tri" share the column renderer -- tri swaps
            # the big readouts for the 3x3 timescale grid
            render_vertical(th, tracker, fight, in_combat)

        if th.get("glow") and random.random() < 0.04:
            title_lbl.configure(fg=_mix(th["accent"], th["fg"], 0.5))

        elapsed_txt = ""
        if in_combat:
            # show the fight's wall-clock span; rates divide by ACTIVE time
            m, s = divmod(int(fight.span()), 60)
            hrs, m = divmod(m, 60)
            elapsed_txt = f"{hrs}:{m:02d}:{s:02d}" if hrs else f"{m}:{s:02d}"
        badge = RATE_BADGE.get(settings.get("rate_mode", "fight"), "avg")
        badge += {"sec": "", "min": "·/min", "hour": "·/hr"}[unit_mode()]
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
        old = active_timeout()
        settings["layout"] = name
        settings.save()
        reseed_for_timeout(old)   # tri layout runs on the hour timeout

    def set_rate_mode(name):
        settings["rate_mode"] = name
        settings.save()
        anim.clear()   # jump readouts to the new scale instead of easing

    def set_units(name):
        old = active_timeout()
        settings["units"] = name
        settings.save()
        # each unit mode keeps its own Combat timeout -- swap it in, and
        # backfill the window when it grew
        reseed_for_timeout(old)
        anim.clear()   # x60/x3600 jump would look silly eased

    def set_timeout(v):
        old = active_timeout()
        settings[TIMEOUT_KEYS[unit_mode()]] = v
        settings.save()
        reseed_for_timeout(old)   # applies immediately; backfills if longer

    def set_seg_amount(v):
        settings["seg_show_amount"] = v
        settings.save()

    def set_font_scale(v):
        settings["font_scale"] = v
        settings.save()
        _font_cache.clear()   # drop stale-size font objects

    def set_show_buffs(v):
        settings["show_buffs"] = v
        settings.save()

    def set_show_resisted(v):
        settings["show_resisted"] = v
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
        try:
            if getattr(sys, "frozen", False):
                target = os.path.join(APP_DIR, "eql_session_report.exe")
                if not os.path.isfile(target):
                    return
                subprocess.Popen([target, live["watcher"].path], cwd=APP_DIR)
            else:
                script = os.path.join(APP_DIR, "eql_session_report.py")
                if not os.path.isfile(script):
                    return
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
                           ("horizontal", "Horizontal"),
                           ("tri", "All timescales (s · m · h)")):
            mark = "● " if settings.get("layout", "vertical") == key else "   "
            layout_menu.add_command(label=mark + label,
                                    command=lambda k=key: set_layout(k))
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
        cur_to = active_timeout()
        for v in TIMEOUT_CHOICES[unit_mode()]:
            mark = "● " if abs(cur_to - v) < 0.5 else "   "
            if v % 3600 == 0:
                txt = f"{v // 3600}h"
            elif v % 60 == 0:
                txt = f"{v // 60}m"
            else:
                txt = f"{v}s"
            to_menu.add_command(label=f"{mark}{txt} without damage",
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

        text_menu = tk.Menu(m, tearoff=0)
        cur_fs = settings.get("font_scale", 1.0)
        for v, label in ((1.0, "Standard (100%)"),
                         (2.0, "Elder (200%)"),
                         (2.5, "Legend (250%)")):
            mark = "● " if abs(cur_fs - v) < 0.01 else "   "
            text_menu.add_command(label=mark + label,
                                  command=lambda v=v: set_font_scale(v))
        m.add_cascade(label="Text size", menu=text_menu)

        op = tk.Menu(m, tearoff=0)
        for v in (1.0, 0.9, 0.75, 0.6, 0.45):
            op.add_command(label=f"{int(v*100)}%", command=lambda v=v: set_opacity(v))
        m.add_cascade(label="Opacity", menu=op)

        m.add_separator()
        cur_b = settings.get("show_buffs", True)
        m.add_command(
            label=("● " if cur_b else "   ") + "Show active buffs",
            command=lambda: set_show_buffs(not cur_b))
        cur_r = settings.get("show_resisted", True)
        m.add_command(
            label=("● " if cur_r else "   ") + "Show resisted (per fight)",
            command=lambda: set_show_resisted(not cur_r))
        m.add_command(label="Reset current fight", command=reset_fight)
        _report_sibling = "eql_session_report.exe" if getattr(sys, "frozen", False) \
            else "eql_session_report.py"
        if os.path.isfile(os.path.join(APP_DIR, _report_sibling)):
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
