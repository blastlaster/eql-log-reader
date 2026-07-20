#!/usr/bin/env python3
"""
EQL Friend Overlay
==================
A live, always-on-top overlay that tails your EverQuest Legends log file and
shows which friends are online -- with level, class combo, race, and zone.

Usage:
    python eql_friend_overlay.py "C:\\EverQuest\\Logs\\eqlog_Miranda_rivervale.txt"

Or just run it with no arguments and pick the log file from the dialog.

How it works
------------
The game writes a "Friends currently on EverQuest Legends:" block to the log
every time the friends list refreshes. These blocks interleave messily with
chat and combat spam (headers can appear back-to-back, and entries sometimes
land *after* the next header). To stay stable:

  * Every header line increments a global "snapshot" counter.
  * Every friend entry stamps that friend with the current snapshot number.
  * A friend goes OFFLINE only after missing MISS_THRESHOLD consecutive
    snapshots (default 3). This absorbs the interleaving without flicker.

Non-friend /who searches (/who, /who all, /who <class/level/zone> ...) print
the same entry format under a different header ("Players in EverQuest
Legends:") and are explicitly EXCLUDED -- search results never enter the
roster. Only "/who friend [all]" blocks ("Friends currently on ...") count.
Optionally, /who search results can pop up in their own closable window
(right-click menu -> "Pop up /who results in own window").

Everyone ever seen stays in the roster (persisted to a JSON file next to this
script), so offline friends remain listed by name. Right-click a friend to
remove them from the roster; right-click the overlay for options -- including
Theme: the suite's shared theme set (16-bit Window by default, plus CRT
Terminal, Arcade LED, Vintage, and the transparent Neon HUD, which floats
bare neon text over the game -- identical across every applet). The friend
list and the /who window render on canvases, so Neon HUD text carries the
same black outline as the DPS meter and stays readable over bright footage.

Notes:
  * Extensible: LogWatcher dispatches every parsed line to registered
    handlers, so future trackers (XP trends, loot, etc.) can plug in.
"""

import json
import os
import re
import sys
import time
from datetime import datetime

from eql_overlay_common import (RETRO_THEMES, DEFAULT_THEME, get_theme,
                                luma, data_path, install_tk_error_logger)

# ----------------------------------------------------------------------------
# Configuration defaults (all UI-tunable settings persist to SETTINGS_FILE)
# ----------------------------------------------------------------------------
MISS_THRESHOLD = 3          # snapshots a friend can miss before marked offline
POLL_INTERVAL_MS = 250      # how often to check the log for new lines
SEED_BYTES = 512 * 1024     # how much of the log tail to parse on startup

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = data_path("eql_friend_overlay_settings.json", APP_DIR)
ERROR_LOG = data_path("eql_errors.log", APP_DIR)
LEGACY_ROSTER_FILE = data_path("eql_friend_overlay_roster.json", APP_DIR)

# ----------------------------------------------------------------------------
# Themes: the shared suite theme set (RETRO_THEMES in eql_overlay_common),
# identical across every applet, defaulting to "16-bit Window". Status-dot
# colors derive from theme roles -- online=accent, offline=dim, afk=warn --
# and rows use the theme's mono font. (The overlay's original "Classic
# Slate" palette was retired when the suite standardized on one theme set;
# get_theme() maps any old saved "classic" key to the default.)
# ----------------------------------------------------------------------------
FRIEND_THEMES = RETRO_THEMES
DEFAULT_FRIEND_THEME = DEFAULT_THEME


def roster_path_for(log_path):
    """Per-character roster file, derived from eqlog_<Char>_<server>.txt."""
    base = re.split(r"[\\/]", log_path)[-1]     # basename, either separator
    ident = re.sub(r"^eqlog_", "", base)
    ident = re.sub(r"\.txt$", "", ident, flags=re.IGNORECASE)
    ident = re.sub(r"[^A-Za-z0-9_-]", "_", ident) or "default"
    return data_path(f"eql_friend_overlay_roster_{ident}.json", APP_DIR)

# ----------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------
TS_RE = r"\[(?P<ts>[A-Za-z]{3} [A-Za-z]{3} \d{2} \d{2}:\d{2}:\d{2} \d{4})\]"

HEADER_RE = re.compile(TS_RE + r" Friends currently on ")

# Non-friend /who results (/who, /who all, /who <class|level|zone> ...) use
# the exact same entry format but a different header and an explicit footer:
#   [ts] Players in EverQuest Legends:
#   [ts] ---------------------------
#   [ts] <entries...>
#   [ts] There are 13 players in EverQuest Legends.
# Footer variants: "There is 1 player in ...", "There are no players in ...
# that match those who filters.", "Your who request was cut short... too
# many players."  Friend blocks have NO footer -- they end implicitly.
WHO_HEADER_RE = re.compile(TS_RE + r" Players in ")
WHO_END_RE = re.compile(
    TS_RE + r" (?:There (?:are \d+|is \d+|are no) players? in "
            r"|Your who request was cut short)")

# [10 WAR/ROG/BER] Djelmo (Barbarian)  ZONE: Blackburrow (blackburrow)
# Tolerant of: 1-3+ classes, multi-word races ("Half Elf"), a guild tag
# ("Brucelee (Gnome) <Ruthless> ZONE: ..."), missing ZONE (anonymous
# players), trailing whitespace, and instanced zones that print only a raw
# id with no parenthesized short name (ZONE: blackburrow_4).
ENTRY_RE = re.compile(
    TS_RE +
    r" \[(?P<level>\d+) (?P<classes>[A-Z]{1,4}(?:/[A-Z]{1,4})*)\]"
    r" (?P<name>[A-Za-z]+)"
    r" \((?P<race>[^)]+)\)"
    r"(?:\s+<(?P<guild>[^>]+)>)?"
    r"(?:\s+ZONE:\s*(?P<zone>.+?))?"
    r"\s*$"
)

# splits "Qeynos Hills (qeytoqrg)" into long/short; plain ids fall through
ZONE_SPLIT_RE = re.compile(r"^(?P<long>.+?)\s*\((?P<short>[^)]+)\)$")

# [ANONYMOUS] Somename  -- keep them online, just without details
ANON_RE = re.compile(TS_RE + r" \[ANONYMOUS\] (?P<name>[A-Za-z]+)\s*$")

# "List of Friends" block: authoritative roster import.
#   [ts] List of Friends
#   [ts] -----------------
#   [ts] Djelmo
#   [ts] Becca
#   [ts] You have 2 friend(s).
LIST_HEADER_RE = re.compile(TS_RE + r" List of Friends\s*$")
LIST_END_RE = re.compile(TS_RE + r" You have (?P<count>\d+) friend\(s\)\.")
# Names appear as typed at /friend time, so they may be lowercase ("zork").
LIST_NAME_RE = re.compile(TS_RE + r" (?P<name>[A-Za-z]{3,})\s*$")
LIST_DASHES_RE = re.compile(TS_RE + r" -{3,}\s*$")
LIST_MAX_SPAN = 300   # abort collecting if the terminator never shows up


def canon(name):
    """Canonical character-name form (Zork), matching how the game
    prints names in /who output and chat regardless of how they were
    typed into /friend."""
    return name.capitalize()

# AFK auto-reply: Djelmo tells you, 'Sorry, I am A.F.K. (Away From Keyboard)...
# Matched loosely on the prefix so custom AFK messages still trigger it.
AFK_RE = re.compile(
    TS_RE + r" (?P<name>[A-Z][a-z]+) tells you, "
    r"'Sorry, I am A\.F\.K\. \(Away From Keyboard\)")

# Any other communication from a character clears their AFK flag:
# tells you / says / shouts / auctions / group / guild / ooc / channels
COMM_RE = re.compile(
    TS_RE + r" (?P<name>[A-Z][a-z]+) "
    r"(?:tells you|says out of character|says|shouts|auctions"
    r"|tells the group|tells the guild|tells [A-Za-z]+\d*:\d+),")

LOG_TS_FMT = "%a %b %d %H:%M:%S %Y"


class FriendsTracker:
    """Consumes log lines; maintains online/offline state for friends."""

    def __init__(self, roster_path=LEGACY_ROSTER_FILE,
                 miss_threshold=MISS_THRESHOLD, on_change=None):
        self.roster_path = roster_path
        self.miss_threshold = miss_threshold
        self.on_change = on_change          # callback() when state changes
        self.snapshot = 0                   # increments on each header
        self.friends = {}                   # name -> dict
        self.last_refresh = None            # datetime of last header
        self._collecting = False            # inside a "List of Friends" block
        self._pending_names = []            # names gathered in that block
        self._collect_span = 0              # lines seen since block started
        self._who_mode = None               # "friend" | "general" | None
        self._load_roster()

    # -- persistence ---------------------------------------------------------
    def _load_roster(self):
        path = self.roster_path
        if not os.path.isfile(path) and os.path.isfile(LEGACY_ROSTER_FILE):
            path = LEGACY_ROSTER_FILE      # migrate pre-per-character roster
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for name, info in saved.items():
                info["online"] = False
                info["last_snapshot"] = -10**9
                self.friends[canon(name)] = info
        except (OSError, ValueError):
            pass

    def save_roster(self):
        out = {}
        for name, f in self.friends.items():
            out[name] = {k: f.get(k) for k in
                         ("level", "classes", "race", "guild", "zone_long",
                          "zone_short", "last_seen")}
        try:
            with open(self.roster_path, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)
        except OSError:
            pass

    def remove(self, name):
        name = canon(name)
        if name in self.friends:
            del self.friends[name]
            self.save_roster()
            self._notify()

    # -- line handling -------------------------------------------------------
    def handle_line(self, line):
        # "List of Friends" roster import ---------------------------------
        if LIST_HEADER_RE.match(line):
            self._collecting = True
            self._pending_names = []
            self._collect_span = 0
            return
        if self._collecting:
            self._collect_span += 1
            end = LIST_END_RE.match(line)
            if end:
                self._sync_roster(self._pending_names,
                                  int(end.group("count")))
                self._collecting = False
                return
            nm = LIST_NAME_RE.match(line)
            if nm and not LIST_DASHES_RE.match(line):
                self._pending_names.append(nm.group("name"))
                return
            if self._collect_span > LIST_MAX_SPAN or HEADER_RE.match(line):
                self._collecting = False   # never saw terminator; abort
            # fall through: interleaved chat/system lines parse normally

        m = HEADER_RE.match(line)
        if m:
            self._who_mode = "friend"
            self.snapshot += 1
            try:
                self.last_refresh = datetime.strptime(m.group("ts"), LOG_TS_FMT)
            except ValueError:
                self.last_refresh = datetime.now()
            self._sweep_offline()
            return

        # Non-friend /who block: header opens it, footer closes it. Entries
        # inside must NOT be treated as friends (a /who search would
        # otherwise dump every match into the roster).
        if WHO_HEADER_RE.match(line):
            self._who_mode = "general"
            return
        if WHO_END_RE.match(line):
            self._who_mode = None
            return

        m = ENTRY_RE.match(line)
        if m:
            if self._who_mode != "friend":
                return                      # entry belongs to a /who search
            d = m.groupdict()
            zone_long = zone_short = None
            if d["zone"]:
                z = d["zone"].strip()
                zm = ZONE_SPLIT_RE.match(z)
                if zm:
                    zone_long, zone_short = zm.group("long"), zm.group("short")
                else:                       # instanced zone: raw id only
                    zone_long = zone_short = z
            self._mark_online(
                d["name"],
                level=d["level"], classes=d["classes"], race=d["race"],
                guild=d["guild"],
                zone_long=zone_long,
                zone_short=zone_short,
                ts=d["ts"],
            )
            return

        m = ANON_RE.match(line)
        if m:
            if self._who_mode != "friend":
                return                      # entry belongs to a /who search
            self._mark_online(m.group("name"), ts=m.group("ts"),
                              level=None, classes="ANON", race=None,
                              zone_long=None, zone_short=None)
            return

        # AFK auto-reply -> flag them yellow (check before generic comm,
        # since the AFK reply is itself a "tells you" line)
        m = AFK_RE.match(line)
        if m and canon(m.group("name")) in self.friends:
            self.set_afk(m.group("name"), True)
            return

        # Any other communication from a friend -> they're back
        m = COMM_RE.match(line)
        if m and self.friends.get(canon(m.group("name")), {}).get("afk"):
            self.set_afk(m.group("name"), False)

    def set_afk(self, name, value):
        f = self.friends.get(canon(name))
        if f is not None and f.get("afk", False) != value:
            f["afk"] = value
            self._notify()

    def _mark_online(self, name, **info):
        name = canon(name)
        f = self.friends.setdefault(name, {})
        was_online = f.get("online", False)
        changed = not was_online
        for k in ("level", "classes", "race", "guild",
                  "zone_long", "zone_short"):
            if info.get(k) is not None and f.get(k) != info[k]:
                f[k] = info[k]
                changed = True
        f["online"] = True
        f["last_snapshot"] = self.snapshot
        f["last_seen"] = info.get("ts")
        if changed:
            self.save_roster()
            self._notify()

    def _sweep_offline(self):
        changed = False
        for f in self.friends.values():
            if f.get("online") and \
               self.snapshot - f.get("last_snapshot", 0) >= self.miss_threshold:
                f["online"] = False
                f["afk"] = False
                changed = True
        if changed:
            self._notify()

    def _sync_roster(self, names, expected_count):
        """Reconcile the roster against a full 'List of Friends' dump.

        Adds any new names (as offline until seen in a who-friends block).
        Removals only happen when our collected count matches the game's own
        'You have N friend(s).' total -- if an interleaved line ate a name,
        we add-only rather than wrongly deleting someone.
        """
        names = list(dict.fromkeys(canon(n) for n in names))  # canonical, deduped
        changed = False
        for n in names:
            if n not in self.friends:
                self.friends[n] = {"online": False, "last_snapshot": -10**9}
                changed = True
        if len(names) == expected_count:
            listed = set(names)
            for n in [k for k in self.friends if k not in listed]:
                del self.friends[n]
                changed = True
        if changed:
            self.save_roster()
            self._notify()

    def _notify(self):
        if self.on_change:
            self.on_change()

    # -- views ---------------------------------------------------------------
    def sorted_friends(self):
        """Online first (alphabetical), then offline (alphabetical)."""
        return sorted(self.friends.items(),
                      key=lambda kv: (not kv[1].get("online", False),
                                      kv[0].lower()))


class WhoTracker:
    """Collects non-friend /who result blocks ("Players in ..." header).

    Calls on_block(block) each time a block completes, where block is:
        {"ts": datetime, "entries": [groupdict...], "footer": str}
    Friend blocks are ignored entirely -- this tracker only exists so the
    overlay can (optionally) pipe /who searches to their own window.
    """

    MAX_SPAN = 120   # give up if the footer never arrives

    def __init__(self, on_block=None):
        self.on_block = on_block
        self.last_block = None
        self._active = False
        self._entries = []
        self._ts = None
        self._span = 0

    def handle_line(self, line):
        m = WHO_HEADER_RE.match(line)
        if m:
            self._active = True
            self._entries = []
            self._span = 0
            try:
                self._ts = datetime.strptime(m.group("ts"), LOG_TS_FMT)
            except ValueError:
                self._ts = datetime.now()
            return
        if not self._active:
            return
        self._span += 1
        if WHO_END_RE.match(line):
            self._active = False
            footer = line.split("] ", 1)[-1].strip()
            self.last_block = {"ts": self._ts, "entries": self._entries,
                               "footer": footer}
            if self.on_block:
                self.on_block(self.last_block)
            return
        m = ENTRY_RE.match(line)
        if m:
            self._entries.append(m.groupdict())
            return
        m = ANON_RE.match(line)
        if m:
            self._entries.append({"ts": m.group("ts"), "level": None,
                                  "classes": "ANON", "name": m.group("name"),
                                  "race": None, "guild": None, "zone": None})
            return
        # A friend header or a runaway block ends collection quietly.
        if HEADER_RE.match(line) or self._span > self.MAX_SPAN:
            self._active = False


class LogWatcher:
    """Tails a log file; dispatches every complete line to handlers.

    Handles log growth, truncation/rotation, and partial trailing lines.
    Register additional handlers for future trackers (XP, loot, etc.).
    """

    def __init__(self, path):
        self.path = path
        self.handlers = []
        self._pos = 0
        self._buf = b""

    def add_handler(self, fn):
        self.handlers.append(fn)

    def seed(self, max_bytes=SEED_BYTES):
        """Parse the tail of the existing log so state is warm on startup."""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        start = max(0, size - max_bytes)
        with open(self.path, "rb") as f:
            f.seek(start)
            data = f.read()
            self._pos = f.tell()
        if start > 0:                       # drop the first partial line
            nl = data.find(b"\n")
            data = data[nl + 1:] if nl >= 0 else b""
        for raw in data.splitlines():
            self._dispatch(raw)

    def poll(self):
        """Read any new bytes; dispatch complete new lines."""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return                          # file temporarily missing
        if size < self._pos:                # truncated/rotated -> start over
            self._pos = 0
            self._buf = b""
        if size == self._pos:
            return
        with open(self.path, "rb") as f:
            f.seek(self._pos)
            chunk = f.read()
            self._pos = f.tell()
        self._buf += chunk
        while b"\n" in self._buf:
            raw, self._buf = self._buf.split(b"\n", 1)
            self._dispatch(raw)

    def _dispatch(self, raw):
        line = raw.decode("cp1252", errors="replace").rstrip("\r")
        for fn in self.handlers:
            fn(line)


# ----------------------------------------------------------------------------
# Overlay UI
# ----------------------------------------------------------------------------
def run_overlay(log_path):
    import tkinter as tk
    from tkinter import font as tkfont

    settings = {
        "x": 40, "y": 40,
        "hide_offline": False,
        "opacity": 0.88,
        "compact": False,
        "who_window": False,        # pipe /who searches to their own window
        "who_x": 340, "who_y": 40,
        # collapsed /who window: only the title bar shows (it carries the
        # player count and the time of the last /who pull)
        "who_min": False,
        # collapsed MAIN list: only the title bar (char + online count)
        # shows; the bar FLASHES when a friend's status changes underneath
        "main_min": False,
        # element size (1.0/0.9/0.8/0.7/0.6): shrinks paddings and spacing;
        # FONTS STAY THE SAME so the data stays readable. Text height sets
        # the floor, so most of the gain is in margins and row spacing.
        "scale": 1.0,
        # text size: 1.0 standard, 2.0 "Elder", 2.5 "Legend" -- scales the
        # FONTS themselves; the list layout follows font metrics, so rows
        # and the /who window grow with them
        "font_scale": 1.0,
        "theme": DEFAULT_FRIEND_THEME,   # any key of FRIEND_THEMES
    }
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings.update(json.load(f))
    except (OSError, ValueError):
        pass

    def save_settings():
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2)
        except OSError:
            pass

    def sc(v):
        """Scale a padding/spacing value by the current element size
        (floor semantics: paddings shrink to 0 before fonts ever would)."""
        return int(v * settings.get("scale", 1.0))

    def current_theme():
        return get_theme(settings.get("theme", DEFAULT_FRIEND_THEME))

    def outlined_text(cnv, x, y, **kw):
        """cnv.create_text that honors the theme's "outline" color (Neon
        HUD sets black): stamps the text in the outline color at 8 one-
        pixel offsets first, then draws it normally on top. Only text
        meaningfully brighter than the outline is stroked -- outlining
        dark text with dark pixels would smear it into a blob. Themes
        without "outline" pay nothing extra."""
        ol = current_theme().get("outline")
        if ol and luma(kw.get("fill", "#000000")) - luma(ol) > 60:
            okw = dict(kw)
            okw["fill"] = ol
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1),
                           (0, -1), (0, 1), (-1, 0), (1, 0)):
                cnv.create_text(x + dx, y + dy, **okw)
        return cnv.create_text(x, y, **kw)

    # theme-driven palette -- these names are rebound by apply_theme()
    # (defined below, once the chrome widgets exist); everything that builds
    # widgets just reads whatever the current values are
    BG = PANEL = FG = DIM = ACCENT = "#000000"
    ONLINE_DOT = OFFLINE_DOT = AFK_DOT = "#000000"

    root = tk.Tk()
    install_tk_error_logger(root, "eql_friend_overlay", ERROR_LOG)
    root.title("EQL Friends")
    root.overrideredirect(True)             # borderless
    root.attributes("-topmost", True)       # float over the game
    try:
        root.attributes("-alpha", settings["opacity"])
    except tk.TclError:
        pass
    root.configure(bg=BG)
    root.geometry(f"+{settings['x']}+{settings['y']}")

    title_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
    name_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
    info_font = tkfont.Font(family="Segoe UI", size=8)
    dot_font = tkfont.Font(family="Segoe UI", size=9)

    # text-size presets (Standard/Elder/Legend) scale the fonts; the list
    # and /who window measure font metrics when drawing, so their layout
    # follows automatically
    _FONT_BASES = ((title_font, 10), (name_font, 10),
                   (info_font, 8), (dot_font, 9))

    def apply_font_scale():
        fs = float(settings.get("font_scale", 1.0))
        for f, base in _FONT_BASES:
            f.configure(size=int(round(base * fs)))

    apply_font_scale()

    # -- title bar (drag handle) ----------------------------------------------
    bar = tk.Frame(root, bg=PANEL, cursor="fleur")
    bar.pack(fill="x")
    title_lbl = tk.Label(bar, text="  FRIENDS", bg=PANEL, fg=ACCENT,
                         font=title_font, anchor="w")
    title_lbl.pack(side="left", pady=2)
    min_lbl = tk.Label(bar, text=" – ", bg=PANEL, fg=DIM, font=title_font,
                       cursor="hand2")
    min_lbl.pack(side="right", padx=2)
    status_lbl = tk.Label(bar, text="", bg=PANEL, fg=DIM, font=info_font)
    status_lbl.pack(side="right", padx=6)

    # canvas (not a Frame of Labels) so text can carry the Neon HUD outline
    body = tk.Canvas(root, bg=BG, highlightthickness=0, width=200, height=40)
    body.pack(fill="both", expand=True, padx=6, pady=(2, 6))

    def apply_scale():
        """Re-apply size-dependent paddings (fonts are untouched)."""
        title_lbl.pack_configure(pady=sc(2))
        status_lbl.pack_configure(padx=sc(6))
        # pack_configure would re-manage a minimize-hidden body
        if not settings.get("main_min", False):
            body.pack_configure(padx=sc(6), pady=(sc(2), sc(6)))

    apply_scale()

    # -- dragging --------------------------------------------------------------
    drag = {"x": 0, "y": 0}

    def on_press(e):
        drag["x"], drag["y"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

    def on_drag(e):
        settings["x"], settings["y"] = e.x_root - drag["x"], e.y_root - drag["y"]
        root.geometry(f"+{settings['x']}+{settings['y']}")

    def on_release(_):
        save_settings()

    for w in (bar, title_lbl):
        w.bind("<ButtonPress-1>", on_press)
        w.bind("<B1-Motion>", on_drag)
        w.bind("<ButtonRelease-1>", on_release)

    # -- minimize: collapse to the title bar (char + online count stay) ------
    # While minimized the bar FLASHES whenever a friend's status changes
    # (online/offline/AFK) -- the list is hidden, so the bar is the alert.
    flash = {"job": None, "n": 0, "sig": None}

    def _bar_flash_colors(lit):
        """Bar chrome in alert (accent bg, solid-black text -- never a
        theme's bg color, which Neon HUD chroma-keys) or normal colors."""
        bg = ACCENT if lit else PANEL
        bar.configure(bg=bg)
        title_lbl.configure(bg=bg, fg="#000000" if lit else ACCENT)
        status_lbl.configure(bg=bg, fg="#000000" if lit else DIM)
        min_lbl.configure(bg=bg, fg="#000000" if lit else DIM)

    def stop_flash():
        if flash["job"] is not None:
            try:
                root.after_cancel(flash["job"])
            except ValueError:
                pass
            flash["job"] = None
        flash["n"] = 0
        _bar_flash_colors(False)

    def _flash_step():
        flash["job"] = None
        if flash["n"] <= 0 or not settings.get("main_min", False):
            stop_flash()
            return
        flash["n"] -= 1
        _bar_flash_colors(flash["n"] % 2 == 1)
        flash["job"] = root.after(350, _flash_step)

    def start_flash():
        flash["n"] = 10                  # ~3.5s of blinking
        if flash["job"] is None:
            _flash_step()

    def apply_main_min():
        mini = settings.get("main_min", False)
        min_lbl.config(text=" □ " if mini else " – ")
        if mini:
            body.pack_forget()
        else:
            stop_flash()                 # you're looking at the list now
            body.pack(fill="both", expand=True, padx=sc(6),
                      pady=(sc(2), sc(6)))

    def toggle_main_min(e=None):
        settings["main_min"] = not settings.get("main_min", False)
        save_settings()
        apply_main_min()

    min_lbl.bind("<Button-1>", toggle_main_min)

    # -- /who results window (optional) ----------------------------------------
    # Non-friend /who searches ("Players in EverQuest Legends:") never touch
    # the friends roster. If enabled, they pop up here instead -- a separate
    # draggable overlay with its own close button.
    who_win = {"top": None, "body": None, "title": None}

    def ensure_who_window():
        if who_win["top"] is not None and who_win["top"].winfo_exists():
            return
        top = tk.Toplevel(root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        try:
            top.attributes("-alpha", settings["opacity"])
        except tk.TclError:
            pass
        top.configure(bg=BG)
        if current_theme().get("transparent"):
            try:
                top.attributes("-transparentcolor", BG)
            except tk.TclError:
                pass
        top.geometry(f"+{settings['who_x']}+{settings['who_y']}")

        wbar = tk.Frame(top, bg=PANEL, cursor="fleur")
        wbar.pack(fill="x")
        wtitle = tk.Label(wbar, text="  WHO", bg=PANEL, fg=ACCENT,
                          font=title_font, anchor="w")
        wtitle.pack(side="left", pady=sc(2))
        close = tk.Label(wbar, text=" ✕ ", bg=PANEL, fg=DIM,
                         font=title_font, cursor="hand2")
        close.pack(side="right", padx=sc(2))
        close.bind("<Button-1>", lambda e: top.withdraw())
        close.bind("<Enter>", lambda e: close.config(fg=FG))
        close.bind("<Leave>", lambda e: close.config(fg=DIM))
        wbody = tk.Canvas(top, bg=BG, highlightthickness=0)

        # minimize: collapse to just the title bar -- it already carries
        # the player count and the last-pull time ("WHO (12)  09:41:33")
        minb = tk.Label(wbar, bg=PANEL, fg=DIM, font=title_font,
                        cursor="hand2")

        def apply_who_min():
            if settings.get("who_min", False):
                wbody.pack_forget()
                minb.config(text=" □ ")
            else:
                wbody.pack(fill="both", expand=True, padx=sc(6),
                           pady=(sc(2), sc(6)))
                minb.config(text=" – ")

        def toggle_who_min(e=None):
            settings["who_min"] = not settings.get("who_min", False)
            apply_who_min()
            save_settings()

        minb.pack(side="right")
        minb.bind("<Button-1>", toggle_who_min)
        minb.bind("<Enter>", lambda e: minb.config(fg=FG))
        minb.bind("<Leave>", lambda e: minb.config(fg=DIM))
        apply_who_min()

        wdrag = {"x": 0, "y": 0}

        def wpress(e):
            wdrag["x"] = e.x_root - top.winfo_x()
            wdrag["y"] = e.y_root - top.winfo_y()

        def wmotion(e):
            settings["who_x"] = e.x_root - wdrag["x"]
            settings["who_y"] = e.y_root - wdrag["y"]
            top.geometry(f"+{settings['who_x']}+{settings['who_y']}")

        for w in (wbar, wtitle):
            w.bind("<ButtonPress-1>", wpress)
            w.bind("<B1-Motion>", wmotion)
            w.bind("<ButtonRelease-1>", lambda e: save_settings())

        who_win.update(top=top, body=wbody, title=wtitle)

    def show_who_block(block):
        if block is None:
            return
        ensure_who_window()
        top, wb, wtitle = who_win["top"], who_win["body"], who_win["title"]
        wb.delete("all")
        ts = block["ts"].strftime("%H:%M:%S") if block["ts"] else ""
        wtitle.config(text=f"  WHO ({len(block['entries'])})  {ts}")
        name_h = name_font.metrics("linespace")
        info_h = info_font.metrics("linespace")
        y = 4
        max_w = 180
        for d in block["entries"]:
            x = 8
            outlined_text(wb, x, y + name_h / 2, anchor="w", text=d["name"],
                          fill=FG, font=name_font)
            x += name_font.measure(d["name"]) + 6
            bits = []
            if d.get("level") and d.get("classes"):
                bits.append(f"{d['level']} {d['classes']}")
            elif d.get("classes") == "ANON":
                bits.append("anonymous")
            if d.get("race"):
                bits.append(d["race"])
            if d.get("guild"):
                bits.append(f"<{d['guild']}>")
            z = (d.get("zone") or "").strip()
            if z:
                zm = ZONE_SPLIT_RE.match(z)
                bits.append(zm.group("long") if zm else z)
            if bits:
                txt = "  ".join(bits)
                outlined_text(wb, x, y + name_h / 2, anchor="w", text=txt,
                              fill=ACCENT, font=info_font)
                x += info_font.measure(txt)
            max_w = max(max_w, x + 12)
            y += name_h
        y += 3
        outlined_text(wb, 8, y + info_h / 2, anchor="w", text=block["footer"],
                      fill=DIM, font=info_font)
        max_w = max(max_w, 8 + info_font.measure(block["footer"]) + 12)
        y += info_h + 6
        wb.configure(width=max_w, height=y, bg=BG)
        top.deiconify()
        top.lift()
        top.attributes("-topmost", True)

    def on_who_block(block):
        if settings["who_window"]:
            show_who_block(block)

    # -- tracker + watcher -------------------------------------------------------
    dirty = {"flag": True}

    def mark_dirty():
        dirty["flag"] = True

    # -- theming ----------------------------------------------------------------
    def apply_theme():
        """Rebind the palette names and restyle the static chrome. The row
        widgets are rebuilt every render, so they pick up new colors on the
        next tick; the /who window is destroyed to rebuild restyled."""
        nonlocal BG, PANEL, FG, DIM, ACCENT, ONLINE_DOT, OFFLINE_DOT, AFK_DOT
        t = current_theme()
        BG, PANEL, FG, DIM, ACCENT = (t["bg"], t["panel"], t["fg"],
                                      t["dim"], t["accent"])
        ONLINE_DOT = t.get("online_dot", t["accent"])
        OFFLINE_DOT = t.get("offline_dot", t["dim"])
        AFK_DOT = t.get("afk_dot", t["warn"])
        fam = t.get("font_ui") or t["font_mono"][0]
        for f in (title_font, name_font, info_font, dot_font):
            f.configure(family=fam)
        root.configure(bg=BG)
        bar.configure(bg=PANEL)
        title_lbl.configure(bg=PANEL, fg=ACCENT)
        status_lbl.configure(bg=PANEL, fg=DIM)
        min_lbl.configure(bg=PANEL, fg=DIM)
        body.configure(bg=BG)
        # transparent theme (Neon HUD): bg is a chroma key -- only the text
        # floats over the game; the title bar keeps its non-key panel color
        # as the drag/right-click handle
        try:
            root.attributes("-transparentcolor",
                            BG if t.get("transparent") else "")
        except tk.TclError:
            pass   # non-Windows: theme still works with a dark bg
        if who_win["top"] is not None and who_win["top"].winfo_exists():
            who_win["top"].destroy()
        who_win.update(top=None, body=None, title=None)
        mark_dirty()

    apply_theme()

    def char_name_from(path):
        m = re.match(r"eqlog_([A-Za-z]+)", os.path.basename(path))
        return m.group(1) if m else "Friends"

    live = {}   # holds current tracker/watcher/char so we can swap logs

    def open_log(path):
        tracker = FriendsTracker(roster_path=roster_path_for(path),
                                 on_change=mark_dirty)
        who_tracker = WhoTracker(on_block=on_who_block)
        watcher = LogWatcher(path)
        watcher.add_handler(tracker.handle_line)
        # (future trackers: watcher.add_handler(xp_tracker.handle_line), etc.)
        watcher.seed()
        # who handler attaches AFTER seeding so stale /who blocks in the log
        # tail don't pop the window at startup
        watcher.add_handler(who_tracker.handle_line)
        live["tracker"], live["watcher"] = tracker, watcher
        live["who"] = who_tracker
        live["char"] = char_name_from(path)
        settings["log_path"] = path
        save_settings()
        mark_dirty()

    open_log(log_path)

    # -- rendering ---------------------------------------------------------------
    row_hits = []   # (y0, y1, name, afk) -- canvas hit zones for right-click

    def render():
        body.delete("all")
        row_hits.clear()

        tracker = live["tracker"]
        rows = tracker.sorted_friends()
        # status signature: while minimized, any online/offline/AFK
        # transition flashes the title bar (the list itself is hidden)
        sig = tuple(sorted((name, f.get("online", False),
                            bool(f.get("afk"))) for name, f in rows))
        if (flash["sig"] is not None and sig != flash["sig"]
                and settings.get("main_min", False)):
            start_flash()
        flash["sig"] = sig
        online_ct = sum(1 for _, f in rows if f.get("online"))
        title_lbl.config(
            text=f"  {live['char'].upper()}  {online_ct}/{len(rows)}")

        name_h = name_font.metrics("linespace")
        info_h = info_font.metrics("linespace")
        x_dot, x_txt = 12, 24
        y = max(2, sc(3))
        max_w = 170
        shown = 0
        for name, f in rows:
            online = f.get("online", False)
            afk = online and f.get("afk", False)
            if settings["hide_offline"] and not online:
                continue
            shown += 1
            y0 = y
            outlined_text(body, x_dot, y + name_h / 2, text="●",
                          fill=AFK_DOT if afk else
                               ONLINE_DOT if online else OFFLINE_DOT,
                          font=dot_font)
            label = name + ("  (AFK)" if afk else "")
            outlined_text(body, x_txt, y + name_h / 2, anchor="w",
                          text=label, fill=FG if online else DIM,
                          font=name_font)
            max_w = max(max_w, x_txt + name_font.measure(label) + 12)
            y += name_h

            if online and not settings["compact"]:
                bits = []
                if f.get("level") and f.get("classes"):
                    bits.append(f"{f['level']} {f['classes']}")
                if f.get("race"):
                    bits.append(f["race"])
                if f.get("zone_long"):
                    bits.append(f["zone_long"])
                if bits:
                    txt = "  ".join(bits)
                    outlined_text(body, x_txt, y + info_h / 2, anchor="w",
                                  text=txt, fill=ACCENT, font=info_font)
                    max_w = max(max_w, x_txt + info_font.measure(txt) + 12)
                    y += info_h
            y += max(1, sc(2))
            row_hits.append((y0, y, name, afk))

        if shown == 0:
            msg = "no friends to show"
            outlined_text(body, 8, y + info_h / 2, anchor="w", text=msg,
                          fill=DIM, font=info_font)
            max_w = max(max_w, 8 + info_font.measure(msg) + 12)
            y += info_h + 4
        body.configure(width=max_w, height=y + max(2, sc(3)))

        if tracker.last_refresh:
            status_lbl.config(
                text="upd " + tracker.last_refresh.strftime("%H:%M:%S"))

    # -- context menu on title bar --------------------------------------------
    def toggle_hide_offline():
        settings["hide_offline"] = not settings["hide_offline"]
        save_settings()
        mark_dirty()

    def toggle_compact():
        settings["compact"] = not settings["compact"]
        save_settings()
        mark_dirty()

    def toggle_who_window():
        settings["who_window"] = not settings["who_window"]
        save_settings()
        if not settings["who_window"] and who_win["top"] is not None \
           and who_win["top"].winfo_exists():
            who_win["top"].withdraw()

    def set_theme(k):
        settings["theme"] = k
        save_settings()
        apply_theme()

    def set_scale(v):
        settings["scale"] = v
        save_settings()
        apply_scale()
        # rebuild the /who window with the new paddings next time it shows
        if who_win["top"] is not None and who_win["top"].winfo_exists():
            who_win["top"].destroy()

    def set_font_scale(v):
        settings["font_scale"] = v
        save_settings()
        apply_font_scale()
        # the /who window sizes itself from font metrics at draw time --
        # rebuild it so the next /who lays out at the new size
        if who_win["top"] is not None and who_win["top"].winfo_exists():
            who_win["top"].destroy()
        who_win.update(top=None, body=None, title=None)
        mark_dirty()

    def set_opacity(v):
        settings["opacity"] = v
        save_settings()
        try:
            root.attributes("-alpha", v)
        except tk.TclError:
            pass

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

    def main_menu(e):
        m = tk.Menu(root, tearoff=0)
        m.add_command(label="Change log file... (switch character)",
                      command=change_log)
        m.add_separator()
        m.add_checkbutton(label="Hide offline friends",
                          onvalue=True, offvalue=False,
                          variable=tk.BooleanVar(value=settings["hide_offline"]),
                          command=toggle_hide_offline)
        m.add_checkbutton(label="Compact (names only)",
                          onvalue=True, offvalue=False,
                          variable=tk.BooleanVar(value=settings["compact"]),
                          command=toggle_compact)
        m.add_separator()
        m.add_checkbutton(label="Pop up /who results in own window",
                          onvalue=True, offvalue=False,
                          variable=tk.BooleanVar(value=settings["who_window"]),
                          command=toggle_who_window)
        if live.get("who") and live["who"].last_block:
            m.add_command(label="Show last /who results",
                          command=lambda: show_who_block(
                              live["who"].last_block))
        th_menu = tk.Menu(m, tearoff=0)
        cur_theme = settings.get("theme", DEFAULT_FRIEND_THEME)
        for key, spec in FRIEND_THEMES.items():
            mark = "● " if key == cur_theme else "   "
            th_menu.add_command(label=mark + spec["label"],
                                command=lambda k=key: set_theme(k))
        m.add_cascade(label="Theme", menu=th_menu)
        op = tk.Menu(m, tearoff=0)
        for v in (1.0, 0.88, 0.75, 0.6, 0.45):
            op.add_command(label=f"{int(v*100)}%",
                           command=lambda v=v: set_opacity(v))
        m.add_cascade(label="Opacity", menu=op)
        size_menu = tk.Menu(m, tearoff=0)
        cur = settings.get("scale", 1.0)
        for v in (1.0, 0.9, 0.8, 0.7, 0.6):
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
        m.add_separator()
        m.add_command(label="Quit", command=root.destroy)
        m.tk_popup(e.x_root, e.y_root)

    def body_menu(e):
        """Right-click on the friend list: per-friend menu when a row is
        hit (same options the old per-row labels offered), otherwise the
        main options menu."""
        for y0, y1, name, afk in row_hits:
            if y0 <= e.y <= y1:
                m = tk.Menu(root, tearoff=0)
                tracker = live["tracker"]
                if afk:
                    m.add_command(label=f"Mark {name} as back",
                                  command=lambda n=name: tracker.set_afk(n, False))
                m.add_command(label=f"Remove {name} from roster",
                              command=lambda n=name: tracker.remove(n))
                m.tk_popup(e.x_root, e.y_root)
                return
        main_menu(e)

    bar.bind("<Button-3>", main_menu)
    title_lbl.bind("<Button-3>", main_menu)
    body.bind("<Button-3>", body_menu)

    # -- poll loop -----------------------------------------------------------
    def tick():
        # reschedule must survive any parsing hiccup, or tailing dies
        try:
            live["watcher"].poll()
            if dirty["flag"]:
                dirty["flag"] = False
                render()
        finally:
            root.after(POLL_INTERVAL_MS, tick)

    apply_main_min()
    render()
    tick()
    root.mainloop()


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
DEFAULT_INSTALL_DIR = r"C:\Users\Public\Daybreak Game Company\Installed Games"


def find_default_log():
    """Return (initialdir, initialfile) for the file dialog.

    Prefers the Daybreak default install path; within it, drills into the
    folder actually containing eqlog_*.txt files (usually <game>\\Logs) and
    preselects the most recently written log. Falls back to C:\\, then home.
    """
    import glob
    candidates = [DEFAULT_INSTALL_DIR, "C:\\", os.path.expanduser("~")]
    for base in candidates:
        if not os.path.isdir(base):
            continue
        if base == DEFAULT_INSTALL_DIR:
            # search a few levels deep for eqlog files (e.g. <game>\Logs)
            logs = []
            for depth in ("", "*", os.path.join("*", "*"),
                          os.path.join("*", "*", "*")):
                logs += glob.glob(os.path.join(base, depth, "eqlog_*.txt"))
            if logs:
                newest = max(logs, key=os.path.getmtime)
                return os.path.dirname(newest), os.path.basename(newest)
        return base, ""
    return "", ""


def main():
    if len(sys.argv) > 1:
        log_path = sys.argv[1]
    else:
        # reuse the last log this tool was watching, if it still exists
        log_path = ""
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f).get("log_path", "")
            if saved and os.path.isfile(saved):
                log_path = saved
        except (OSError, ValueError):
            pass
    if not log_path:
        try:
            import tkinter as tk
            from tkinter import filedialog
            hidden = tk.Tk(); hidden.withdraw()
            initialdir, initialfile = find_default_log()
            log_path = filedialog.askopenfilename(
                title="Select your EverQuest log file (eqlog_*.txt)",
                initialdir=initialdir or None,
                initialfile=initialfile or None,
                filetypes=[("EQ log files", "eqlog_*.txt"),
                           ("Text files", "*.txt"), ("All files", "*.*")])
            hidden.destroy()
        except Exception:
            log_path = ""
    if not log_path or not os.path.isfile(log_path):
        print("No log file selected/found. Pass the path as an argument:")
        print('  python eql_friend_overlay.py "C:\\EQ\\Logs\\eqlog_Name_server.txt"')
        sys.exit(1)
    run_overlay(log_path)


if __name__ == "__main__":
    main()
