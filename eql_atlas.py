#!/usr/bin/env python3
"""
EQL Atlas -- Loot & Spawn Collector (Phase 1)
==============================================
Passive cartography companion: tails the log and builds a per-character
database of what died where and what it dropped -- the foundation the Atlas
map overlay (Phase 2) will draw its heatmaps, named-spawn lists, and
observed drop rates from.

What it records, entirely from lines the game already writes:
  * zone           "You have entered Befallen 4 (Refined)."
  * position       "Your Location is -933.38, -207.04, -67.46"   (/loc)
  * kills          "You have slain a ghoul!"  (+ group kills via
                   "<mob> has been slain by <player>!")
  * loot           "You looted a Bandages from a ghoul's corpse and sold it
                   for 3 silver and 6 copper."  -- EQL auto-loots on kill and
                   names the mob, so drops attribute exactly; the
                   "to create a <item> +N" upgrade and "stored it in your
                   currency/tradeskill depot" variants are tracked too
  * corpse coin    "You receive 3 gold, 3 silver and 8 copper from the
                   corpse."  (attributed to the most recent kill)

Positions come from /loc, so they're only as fresh as the player's last
/loc -- fold one into a hotbutton you already spam and every kill gets a
coordinate. Events without a recent /loc are still counted, just unplaced.

The optional eql_atlas_baseline.json.gz (built by eql_atlas_baseline_build.py
from the public Project Quarm database -- same EQMacEmu lineage as EQL, same
item ID space) supplies the "pre-discovered" layer: expected drop rates,
spawn points, and item IDs. Your observed data corroborates, contradicts,
or extends it -- a drop the baseline doesn't know about is flagged NEW in
the status panel. Everything works without the baseline file; you just
start from a blank map.

Commands ride a PRIVATE chat channel the player creates in-game
(/join <name>:<password>, ideally set as the default typing channel):

    find <item|npc> where is it? matches items, mobs, NPCs, and named
                    alike (your data first, then baseline)
    guide <item|npc> lead me to it, cross-zone
    note <text>     pin a note at your last /loc
    fav <item>      favorite an item (map pins in Phase 2)
    help            list commands

Safety model, in order of strength: (1) the log itself authenticates the
speaker -- only the player's own messages render as "You tell <chan>:N",
anyone else is "<Name> tells" and is never parsed; (2) commands stay LOCKED
until a /list line proves the channel has exactly one member, and lock
again the instant anyone else speaks in it; (3) public channels, /say,
tells, and group chat are never parsed for commands, period. The channel
name is auto-learned from the game's own setup lines; the channel PASSWORD
that appears in those lines is never stored or displayed anywhere.

Data file: eql_atlas_<Name>_<Server>.json next to the other per-character
files. On first run the ENTIRE existing log is imported (your whole hunting
history becomes data); afterwards the saved log position means each launch
only catches up on what happened since -- nothing is ever double-counted.

Usage:
    python eql_atlas.py <path-to-eqlog_Name_Server.txt>
    python eql_atlas.py --replay <log>     # headless: import + summary, no UI
"""

import json
import os
import re
import sys
import time
from collections import deque

from eql_overlay_common import (LogWatcher, RETRO_THEMES, DEFAULT_THEME,
                                 get_theme, data_path, luma,
                                 install_tk_error_logger, POLL_INTERVAL_MS)

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = data_path("eql_atlas_settings.json", APP_DIR)
ERROR_LOG = data_path("eql_errors.log", APP_DIR)
BASELINE_NAME = "eql_atlas_baseline.json.gz"

AUTO_SAVE_S = 30          # flush the DB this often while dirty
# expansion gating: EQL is vanilla-era; baseline zones from later eras are
# hidden from search/guide until the matching era is enabled in the
# right-click menu (values = the dump's zone.expansion numbers)
ERA_LEVELS = [("kunark", 1), ("velious", 2), ("luclin", 3), ("pop", 4)]

# Hand-curated classic zone connections. The DB's zone_points/doors data
# routes through nonsense (Befallen "connecting" to Kithicor); geography
# is small and stable, so we just state it. Undirected -- listed once,
# mirrored at load. Zones absent here fall back to the DB-derived graph.
CURATED_ADJ = {
    "feerrott": ["cazicthule", "innothule", "rathemtn", "fearplane"],
    "innothule": ["feerrott", "grobb", "guktop", "gukbottom", "sro"],
    "grobb": [], "cazicthule": [], "fearplane": [],
    "guktop": ["gukbottom", "innothule"], "gukbottom": ["innothule"],
    "sro": ["oasis", "innothule"], "oasis": ["nro", "sro"],
    "nro": ["freporte", "oasis"],
    "freporte": ["freportn", "freportw", "nro", "oot"],
    "freportn": ["freportw", "ecommons"], "freportw": ["ecommons"],
    "ecommons": ["commons", "nektulos", "freportw", "freportn", "freporte"],
    "commons": ["ecommons", "kithicor", "befallen"],
    "befallen": ["commons"],
    "kithicor": ["commons", "highpass", "rivervale"],
    "highpass": ["highkeep", "kithicor", "eastkarana"], "highkeep": [],
    "rivervale": ["kithicor", "misty"], "misty": ["rivervale", "runnyeye"],
    "runnyeye": ["misty", "beholder"],
    "beholder": ["runnyeye", "eastkarana"],
    "eastkarana": ["northkarana", "highpass", "beholder"],
    "northkarana": ["eastkarana", "southkarana", "qey2hh1"],
    "qey2hh1": ["northkarana", "qeytoqrg"],
    "southkarana": ["northkarana", "lakerathe", "paw"], "paw": [],
    "lakerathe": ["southkarana", "rathemtn", "arena"], "arena": [],
    "rathemtn": ["lakerathe", "feerrott"],
    "qeytoqrg": ["qeynos2", "qey2hh1", "blackburrow", "qrg"], "qrg": [],
    "blackburrow": ["qeytoqrg", "everfrost"],
    "everfrost": ["blackburrow", "halas", "permafrost"],
    "halas": [], "permafrost": [],
    "qeynos": ["qeynos2", "qcat", "erudnext"],
    "qeynos2": ["qeynos", "qeytoqrg", "qcat"], "qcat": [],
    "erudnext": ["tox", "qeynos", "erudnint"], "erudnint": [],
    "tox": ["erudnext", "paineel", "kerraridge"],
    "paineel": ["tox", "hole"], "hole": [], "kerraridge": [],
    "nektulos": ["ecommons", "lavastorm", "neriaka"],
    "lavastorm": ["nektulos", "soltemple", "soldunga", "soldungb", "najena"],
    "najena": [], "soltemple": [], "soldunga": ["soldungb", "lavastorm"],
    "soldungb": ["lavastorm"],
    "neriaka": ["nektulos", "neriakb"], "neriakb": ["neriakc"], "neriakc": [],
    "oot": ["freporte", "butcher"],
    "butcher": ["cauldron", "kaladima", "gfaydark", "oot"],
    "cauldron": ["unrest", "kedge", "butcher"], "unrest": [], "kedge": [],
    "kaladima": ["kaladimb", "butcher"], "kaladimb": [],
    "gfaydark": ["butcher", "crushbone", "felwithea", "lfaydark"],
    "crushbone": [], "felwithea": ["felwitheb", "gfaydark"], "felwitheb": [],
    "lfaydark": ["gfaydark", "mistmoore", "steamfont", "cauldron"],
    "mistmoore": [], "steamfont": ["lfaydark", "akanon", "butcher"],
    "akanon": [],
    "warrens": ["tox", "stonebrunt"], "stonebrunt": [],
    "erudsxing": ["erudnext", "qeynos"],
    "hateplane": ["oasis"],           # sword on spectre isle
    "airplane": ["freporte"],         # glowing globe, East Freeport
}
_ADJ = {}
for _z, _ns in CURATED_ADJ.items():
    for _n in _ns:
        _ADJ.setdefault(_z, set()).add(_n)
        _ADJ.setdefault(_n, set()).add(_z)
    _ADJ.setdefault(_z, set())
LOC_MAX_AGE_S = 60        # a /loc older than this no longer places events
COIN_WINDOW_S = 20        # corpse coin attributes to a kill this recent
COIN_PAIR_S = 3           # ...but it usually lands right BEFORE the kill
                          # line (197:2 in real logs), so hold it this long
                          # and marry it to the next "You have slain"
EVENTS_CAP = 4000         # per zone; oldest heatmap events roll off first
CMD_RESPONSE_S = 90       # how long a command's answer stays on the panel

_MONTH = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}

_EQLOG_RE = re.compile(r"eqlog_([A-Za-z0-9]+)_([A-Za-z0-9]+)\.txt$", re.I)

RE_ZONE = re.compile(r"^You have entered (.+)\.$")
# /who rows carry the zone too -- "ZONE: Befallen (befallen)" for base
# zones, "ZONE: najena_16" (short name + instance) for instanced ones. The
# player's OWN row pins the current zone without needing a zone-in line.
RE_WHO_ROW = re.compile(r"^\[(?:\d+ [A-Z/]+|ANONYMOUS)\] (\w+) "
                        r".*?ZONE: (?:(.+?) \((\w+)\)|(\w+))")
RE_LOC = re.compile(r"^Your Location is (-?[\d.]+), (-?[\d.]+), (-?[\d.]+)")
RE_SLAIN_SELF = re.compile(r"^You have slain (.+)!$")
RE_DEATH_SELF = re.compile(r"^You have been slain by (.+)!$|^You died\.$")
RE_SLAIN_OTHER = re.compile(r"^(.+) has been slain by (.+)!$")
# EQL loot lines come in two families. Auto-processed drops ("You looted"):
# sold to the vendor, upgraded into a +N item, or banked into the
# currency/tradeskill-depot tabs. Items KEPT in inventory use the classic
# dashed form instead: --You have looted a <item> from <mob>'s corpse.--
RE_LOOT = re.compile(
    r"^You looted (?:an? |the )?(?:(\d+) )?(.+?) from (.+?)'s corpse"
    r"(?: and sold it for (.+?)\.| to create (?:an? |the )?(.+?)"
    r"| and stored it in your [a-z ]+)?\.?$")
RE_LOOT_KEPT = re.compile(
    r"^--You have looted (?:an? |the )?(?:(\d+) )?(.+?) "
    r"from (.+?)'s corpse\.--$")
RE_COIN_CORPSE = re.compile(r"^You receive (.+?) from the corpse\.$")
RE_COIN_SPLIT = re.compile(r"^You receive (.+?) as your split\.?$")
# -- private command channel ------------------------------------------------
# Commands ride a password-protected chat channel the player creates
# (/join secret:pw). The log self-authenticates the speaker: only the
# player's own messages render as "You tell <chan>:N" -- anyone else is
# "<Name> tells <chan>:N" and can never match RE_CMD_SELF. The channel
# name is auto-learned from the setup lines below; passwords in those
# lines are NEVER stored, displayed, or written anywhere.
RE_CH_SET = re.compile(r"^Set channels to be joined at login: "
                       r"([A-Za-z0-9]+):\S+")
RE_CH_DEFAULT = re.compile(r"^Your default channel for typing is now "
                           r"Chat to Channel ([A-Za-z0-9]+):\S+\.$")
RE_CH_LIST = re.compile(r"^Channels: (.+)$")
RE_CH_ENTRY = re.compile(r"\d+=([A-Za-z0-9]+)\((\d+)\)")
RE_CMD_SELF = re.compile(r"^You tell ([A-Za-z0-9]+):\d+, '(.*)'$")
RE_TELL_OTHER = re.compile(r"^([A-Za-z0-9]+) tells ([A-Za-z0-9]+):\d+, ")
# NPC dialogue -- "Beek Guinders says 'Hey, great! ...'" (player says carry
# a comma in some clients; accept both). The quest layer matches these
# against stored turn-in success dialogue to confirm a quest EXISTS on EQL.
RE_NPC_SAY = re.compile(r"^([A-Za-z][A-Za-z0-9`' ]{1,40}?) says,? '(.*)'$")
RE_PLUS_SUFFIX = re.compile(r"\s\+\d+$")
_COIN_RE = re.compile(r"(\d+)\s+(platinum|gold|silver|copper)")
_COIN_MULT = {"platinum": 1000, "gold": 100, "silver": 10, "copper": 1}


def parse_ts(line):
    """'[Mon Jul 13 22:22:18 2026] rest' -> (epoch_seconds, rest) or None.
    Hand-rolled: strptime is locale-sensitive and ~10x slower, which matters
    when importing a whole multi-year log on first run."""
    if len(line) < 27 or line[0] != "[":
        return None
    try:
        mon = _MONTH[line[5:8]]
        day = int(line[9:11])
        hh = int(line[12:14])
        mm = int(line[15:17])
        ss = int(line[18:20])
        year = int(line[21:25])
        t = time.mktime((year, mon, day, hh, mm, ss, 0, 0, -1))
    except (KeyError, ValueError, OverflowError):
        return None
    return t, line[27:]


def char_server_from(log_path):
    m = _EQLOG_RE.search(os.path.basename(log_path))
    return (m.group(1), m.group(2)) if m else ("Unknown", "unknown")


def coin_to_copper(text):
    """'3 gold, 3 silver and 8 copper' (or space-separated) -> total copper."""
    return sum(int(n) * _COIN_MULT[d] for n, d in _COIN_RE.findall(text))


def fmt_coin(copper):
    if copper <= 0:
        return "0c"
    parts = []
    for label, mult in (("p", 1000), ("g", 100), ("s", 10), ("c", 1)):
        v, copper = divmod(copper, mult)
        if v:
            parts.append(f"{v}{label}")
    return " ".join(parts[:2]) or "0c"    # two largest denominations reads best


def norm_zone_long(name):
    """Log zone name -> comparable base form: 'Befallen 4 (Refined)' and
    'The Lesser Faydark' both reduce to their plain zone ('befallen',
    'lesser faydark') so EQL's Awakened/Adaptive/Refined instances land on
    the base zone's map and baseline data."""
    s = name.lower().strip()
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)     # (Refined) / (Awakened) / ...
    s = re.sub(r"\s*\d+$", "", s)              # instance number
    if s.startswith("the "):
        s = s[4:]
    return s.strip()


# ----------------------------------------------------------------------------
# Baseline (optional, shipped): the distilled Project Quarm database
# ----------------------------------------------------------------------------
class Baseline:
    """Read-only view of eql_atlas_baseline.json.gz. Everything degrades to
    'unknown' when the file is absent -- the collector never needs it."""

    def __init__(self):
        self.ok = False
        self.zones = {}
        self.npcs = {}
        self._long_to_short = {}
        self._name_to_id = {}
        self._items = {}
        for d in (APP_DIR, getattr(sys, "_MEIPASS", None)):
            if not d:
                continue
            path = os.path.join(d, BASELINE_NAME)
            if os.path.isfile(path):
                try:
                    import gzip
                    with gzip.open(path, "rt", encoding="utf-8") as f:
                        b = json.load(f)
                except (OSError, ValueError):
                    continue
                self.zones = b.get("zones", {})
                self.npcs = b.get("npcs", {})
                self._items = b.get("items", {})
                for short, z in self.zones.items():
                    self._long_to_short[norm_zone_long(z["long"])] = short
                for iid, name in self._items.items():
                    # several ids can share a name (Bone Chips has a few);
                    # first wins = lowest id = the classic-era one
                    self._name_to_id.setdefault(name.lower(), int(iid))
                self.ok = True
                break

    def zone_short(self, long_name):
        """-> (short_key, mapped). Unmapped zones (EQL customs) still get a
        stable slug key so their data is collected, just without baseline."""
        base = norm_zone_long(long_name)
        short = self._long_to_short.get(base)
        if short:
            return short, True
        return re.sub(r"[^a-z0-9]+", "_", base).strip("_") or "unknown", False

    def item_id(self, base_name):
        return self._name_to_id.get(base_name.lower(), 0)

    def item_name(self, item_id):
        return self._items.get(str(item_id))

    def item_names(self):
        return list(self._items.values())

    def zone_era(self, short):
        """0 classic, 1 kunark, 2/3 velious+luclin, 4 PoP, 99 special."""
        return int(self.zones.get(short, {}).get("era") or 0)

    def route(self, src, dests):
        """BFS over zone connections: shortest [src..dest] path to the
        nearest of `dests`, or None if unreachable."""
        if src in dests:
            return [src]
        def neighbors(z):
            # curated geography wins; DB-derived links only for zones the
            # curated table doesn't know (mostly later-era content)
            if z in _ADJ:
                return _ADJ[z]
            return self.zones.get(z, {}).get("adj", [])

        seen = {src}
        queue = deque([[src]])
        while queue:
            path = queue.popleft()
            for nxt in neighbors(path[-1]):
                if nxt in seen:
                    continue
                seen.add(nxt)
                p2 = path + [nxt]
                if nxt in dests:
                    return p2
                queue.append(p2)
        return None

    def mob(self, zone_short, mob_name):
        return self.npcs.get(zone_short, {}).get(mob_name.lower())

    def drop_status(self, zone_short, mob_name, item_base_name):
        """'known' / 'new' / None (no baseline coverage to judge against).
        Compared by NAME, not id: duplicate item ids share names (several
        Bone Chips ids exist), and the log only ever gives us a name."""
        rec = self.mob(zone_short, mob_name)
        if not self.ok or rec is None:
            return None
        low = item_base_name.lower()
        for iid, _ in rec["loot"]:
            n = self._items.get(str(iid))
            if n and n.lower() == low:
                return "known"
        return "new"


# ----------------------------------------------------------------------------
# Per-character database
# ----------------------------------------------------------------------------
class AtlasDB:
    """The observed layer. Schema (format 1):

    totals:  lifetime counters across all zones
    log_pos: {abs-lowercased-log-path: byte offset already ingested} -- the
             no-double-count guarantee across restarts
    zones.<short>.mobs.<name>:  kills / kills_group / coin_copper /
             coin_events / last_seen / drops.<base item name>:
             {count, sold_copper, upgrades, item_id}
    zones.<short>.events: newest-last rows, capped at EVENTS_CAP:
             [t, kind, mob, item, y, x, z, copper]  kind: K kill, L loot,
             C coin, N note (text rides in the item slot), D death (the
             killer rides in the mob slot); y/x/z null when no fresh /loc
             placed the event
    zones.<short>.notes: [{t, text, loc}] -- the "note <text>" command
    favorites: item names marked with "fav <item>" (map pins in Phase 2)
    """

    def __init__(self, char, server):
        self.path = data_path(f"eql_atlas_{char}_{server}.json", APP_DIR)
        self.dirty = False
        self.data = {"format": 1, "char": char, "server": server,
                     "created": int(time.time()), "updated": 0,
                     "log_pos": {},
                     "totals": {"kills": 0, "kills_group": 0, "loots": 0,
                                "coin_copper": 0},
                     "zones": {}}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                on_disk = json.load(f)
            if on_disk.get("format") == 1:
                self.data = on_disk
        except (OSError, ValueError):
            pass

    def save(self):
        if not self.dirty:
            return
        self.data["updated"] = int(time.time())
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, separators=(",", ":"))
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError:
            pass

    # -- log position (per log file, so shared DBs never re-ingest) --------
    def log_pos(self, log_path):
        return self.data["log_pos"].get(os.path.abspath(log_path).lower())

    def set_log_pos(self, log_path, pos):
        self.data["log_pos"][os.path.abspath(log_path).lower()] = pos
        self.dirty = True

    def reset(self):
        """Wipe observed data (Re-scan uses this before a full re-import)."""
        self.data["totals"] = {"kills": 0, "kills_group": 0, "loots": 0,
                               "coin_copper": 0}
        self.data["zones"] = {}
        self.data["log_pos"] = {}
        self.dirty = True

    # -- recording ----------------------------------------------------------
    def _zone(self, short, long_name, mapped):
        z = self.data["zones"].get(short)
        if z is None:
            z = self.data["zones"][short] = {"long": long_name,
                                             "mapped": mapped,
                                             "mobs": {}, "events": []}
        return z

    def _mob(self, zone, name, t, authoritative=True):
        """Mobs key case-insensitively: group-kill lines start the sentence
        with the mob name ('A ghoul has been slain...'), so the same ghoul
        arrives both as 'a ghoul' and 'A ghoul'. Mid-sentence sources (your
        own slain/loot lines) carry the true casing and win the display
        name; sentence-start sources only fill it in when nothing better
        has been seen yet."""
        key = name.lower()
        m = zone["mobs"].get(key)
        if m is None:
            m = zone["mobs"][key] = {"name": name, "kills": 0,
                                     "kills_group": 0, "coin_copper": 0,
                                     "coin_events": 0, "last_seen": 0,
                                     "drops": {}}
        if authoritative:
            m["name"] = name
        m["last_seen"] = max(m["last_seen"], int(t))
        return m

    def _event(self, zone, row):
        zone["events"].append(row)
        if len(zone["events"]) > EVENTS_CAP:
            del zone["events"][:len(zone["events"]) - EVENTS_CAP]

    def record_kill(self, short, long_name, mapped, t, mob, loc, group=False):
        z = self._zone(short, long_name, mapped)
        m = self._mob(z, mob, t, authoritative=not group)
        key = "kills_group" if group else "kills"
        m[key] += 1
        self.data["totals"][key] += 1
        y, x, zz = loc if loc else (None, None, None)
        self._event(z, [int(t), "K", mob, None, y, x, zz, 0])
        self.dirty = True

    def record_loot(self, short, long_name, mapped, t, mob, item, item_id,
                    loc, qty=1, sold_copper=0, upgrade=False):
        z = self._zone(short, long_name, mapped)
        m = self._mob(z, mob, t)
        d = m["drops"].get(item)
        if d is None:
            d = m["drops"][item] = {"count": 0, "sold_copper": 0,
                                    "upgrades": 0, "item_id": item_id}
        d["count"] += qty
        d["sold_copper"] += sold_copper
        if upgrade:
            d["upgrades"] += 1
        if item_id and not d["item_id"]:
            d["item_id"] = item_id
        self.data["totals"]["loots"] += qty
        y, x, zz = loc if loc else (None, None, None)
        self._event(z, [int(t), "L", mob, item, y, x, zz, sold_copper])
        self.dirty = True

    def record_death(self, short, long_name, mapped, t, killer, loc):
        z = self._zone(short, long_name, mapped)
        y, x, zz = loc if loc else (None, None, None)
        self._event(z, [int(t), "D", killer, None, y, x, zz, 0])
        self.dirty = True

    def record_note(self, short, long_name, mapped, t, text, loc):
        z = self._zone(short, long_name, mapped)
        y, x, zz = loc if loc else (None, None, None)
        z.setdefault("notes", []).append(
            {"t": int(t), "text": text, "loc": [y, x, zz] if loc else None})
        self._event(z, [int(t), "N", None, text, y, x, zz, 0])
        self.dirty = True

    def record_coin(self, short, long_name, mapped, t, mob, copper, loc):
        z = self._zone(short, long_name, mapped)
        if mob:
            m = self._mob(z, mob, t)
            m["coin_copper"] += copper
            m["coin_events"] += 1
        self.data["totals"]["coin_copper"] += copper
        y, x, zz = loc if loc else (None, None, None)
        self._event(z, [int(t), "C", mob, None, y, x, zz, copper])
        self.dirty = True


# ----------------------------------------------------------------------------
# Log line -> events
# ----------------------------------------------------------------------------
class AtlasTracker:
    """Feeds on raw log lines; writes to the DB; keeps the little bit of
    rolling state (current zone, last /loc, last kill) attribution needs.
    UI-free so --replay and tests can drive it headless."""

    def __init__(self, db, baseline, session_from=None, command_channel=""):
        self.db = db
        self.base = baseline
        # events stamped earlier than this don't count toward the session
        # numbers in the panel (backlog import is history, not "today")
        self.session_from = time.time() if session_from is None else session_from
        self.zone_short = None
        self.zone_long = None
        self.zone_mapped = False
        # restarting with a caught-up log means no zone line will be read
        # until the next zoning -- pick up where we left off instead (the
        # import corrects this if the log shows later zoning, and a /who
        # re-pins it live)
        lz = db.data.get("last_zone")
        if lz:
            self.zone_short = lz["short"]
            self.zone_long = lz["long"]
            self.zone_mapped = lz["mapped"]
        self.loc = None                  # (y, x, z, t)
        self.trail = deque(maxlen=300)   # recent (y, x, z, t) -- map trail/heading
        self.last_kill = None            # (mob, t)
        self.pending_coin = None         # (copper, t, loc) awaiting its kill
        self.session = {"kills": 0, "loots": 0, "coin": 0}
        self.last_drop = None            # (item, mob, status) for the panel
        self.novel_finds = 0
        # -- command channel state ------------------------------------------
        self.cmd_channel = (command_channel or "").lower()
        self.cmd_members = None          # None until a /list line is seen
        self.cmd_enabled = False         # only True at member count == 1
        self.cmd_last = None             # (typed text, [reply lines], t)
        self.find_query = None           # (lowercased term, t) -> map markers
        self.find_exclude = set()        # 'clear <item>' carve-outs
        self.guide = None                # 'guide <item>' navigation state
        self.era_ok = lambda short: True # rebound by the UI's era settings
        self.on_channel_learned = None   # UI hook: persist the learned name
        self.on_loot = None              # quest hook: (item, qty, t) on loot
        self.on_npc_say = None           # quest hook: (npc, text, t) on says
        self.quest_marks = set()         # tracked quest's missing item names
                                         # (lowercased) -- map 'quest' layer
        self.quest_need = []             # same, display-cased -- panel line
        self.quest_npc = None            # tracked quest's hand-in target:
                                         # (npc_lower, zone_short, label)

    def _set_zone(self, short, long_name, mapped):
        self.zone_short = short
        self.zone_long = long_name
        self.zone_mapped = mapped
        self.db.data["last_zone"] = {"short": short, "long": long_name,
                                     "mapped": mapped}
        self.db.dirty = True
        self._guide_retarget()           # each zone-in re-aims the guide

    def _fresh_loc(self, t):
        if self.loc and t - self.loc[3] <= LOC_MAX_AGE_S:
            return self.loc[:3]
        return None

    def _in_session(self, t):
        return t >= self.session_from

    # -- pending corpse coin ------------------------------------------------
    # "You receive ... from the corpse." almost always lands one line BEFORE
    # its "You have slain <mob>!" -- so coin waits in pending_coin until the
    # kill arrives (COIN_PAIR_S), and only falls back to the previous kill /
    # unattributed when nothing follows.
    def _emit_coin(self, t, copper, mob, loc):
        self.db.record_coin(self.zone_short, self.zone_long, self.zone_mapped,
                            t, mob, copper, loc)
        if self._in_session(t):
            self.session["coin"] += copper

    def flush_pending(self, now=None):
        """Settle held coin. `now=None` forces it (shutdown/zone change)."""
        if not self.pending_coin:
            return
        copper, pt, ploc = self.pending_coin
        if now is not None and now - pt <= COIN_PAIR_S:
            return                       # its kill line may still be coming
        self.pending_coin = None
        mob = None
        if self.last_kill and pt - self.last_kill[1] <= COIN_WINDOW_S:
            mob = self.last_kill[0]
        if self.zone_short:
            self._emit_coin(pt, copper, mob, ploc)

    def feed(self, line):
        ts = parse_ts(line)
        if not ts:
            return
        t, rest = ts
        self.flush_pending(t)            # settle coin whose kill never came

        m = RE_LOC.match(rest)
        if m:
            self.loc = (float(m.group(1)), float(m.group(2)),
                        float(m.group(3)), t)
            self.trail.append((self.loc[0], self.loc[1], self.loc[2], t))
            return

        # -- command channel (works even before the first zone-in) ----------
        if self._feed_channel(t, rest):
            return

        # NPC dialogue: only consumed by the quest layer's hand-in
        # confirmation; multi-word speaker names can't be players, and the
        # hook does its own exact-dialogue matching
        if self.on_npc_say:
            m = RE_NPC_SAY.match(rest)
            if m:
                self.on_npc_say(m.group(1), m.group(2), t)
                return

        m = RE_ZONE.match(rest)
        if m:
            name = m.group(1)
            # "You have entered an area where levitation..." style notices
            # share the prefix but aren't zones
            if name.lower().startswith("an area"):
                return
            self.flush_pending()
            short, mapped = self.base.zone_short(name)
            self._set_zone(short, name, mapped)
            self.loc = None              # coordinates don't survive zoning
            self.trail.clear()
            self.last_kill = None
            return

        m = RE_WHO_ROW.match(rest)
        if m:
            # only OUR row is authoritative -- /who searches list players
            # standing in other zones too
            if m.group(1).lower() != (self.db.data.get("char") or "").lower():
                return
            long_disp, short = m.group(2), (m.group(3) or m.group(4))
            base_short = re.sub(r"_\d+$", "", short).lower()
            mapped = base_short in self.base.zones
            if not long_disp:
                long_disp = (self.base.zones[base_short]["long"]
                             if mapped else short)
            if base_short != self.zone_short:
                self._set_zone(base_short, long_disp, mapped)
            return

        if self.zone_short is None:
            return                       # can't attribute anything yet

        m = RE_SLAIN_SELF.match(rest)
        if m:
            mob = m.group(1)
            self.last_kill = (mob, t)
            loc = self._fresh_loc(t)
            self.db.record_kill(self.zone_short, self.zone_long,
                                self.zone_mapped, t, mob, loc)
            if self._in_session(t):
                self.session["kills"] += 1
            if self.pending_coin and t - self.pending_coin[1] <= COIN_PAIR_S:
                copper, pt, ploc = self.pending_coin
                self.pending_coin = None
                self._emit_coin(pt, copper, mob, ploc or loc)
            return

        m = RE_LOOT.match(rest)
        km = RE_LOOT_KEPT.match(rest) if not m else None
        if m or km:
            if m:
                qty, raw_item, mob, sold_text, created = (
                    int(m.group(1) or 1), m.group(2), m.group(3),
                    m.group(4), m.group(5))
            else:                        # kept in inventory (dashed form)
                qty, raw_item, mob = (int(km.group(1) or 1), km.group(2),
                                      km.group(3))
                sold_text = created = None
            base_item = RE_PLUS_SUFFIX.sub("", raw_item)
            item_id = self.base.item_id(base_item)
            sold = coin_to_copper(sold_text) if sold_text else 0
            self.db.record_loot(self.zone_short, self.zone_long,
                                self.zone_mapped, t, mob, base_item, item_id,
                                self._fresh_loc(t), qty=qty, sold_copper=sold,
                                upgrade=bool(created))
            status = self.base.drop_status(self.zone_short, mob, base_item)
            if status == "new":
                self.novel_finds += 1
            self.last_drop = (base_item, mob, status)
            if self.on_loot:
                self.on_loot(base_item, qty, t)
            if self._in_session(t):
                self.session["loots"] += qty
                self.session["coin"] += sold
            return

        m = RE_COIN_CORPSE.match(rest) or RE_COIN_SPLIT.match(rest)
        if m:
            copper = coin_to_copper(m.group(1))
            if not copper:
                return
            self.flush_pending()         # two coins can't share one kill
            self.pending_coin = (copper, t, self._fresh_loc(t))
            return

        m = RE_DEATH_SELF.match(rest)
        if m:
            killer = m.group(1) or "misadventure"
            # the death spot is wherever the last /loc put us; grab it
            # BEFORE wiping position state
            self.db.record_death(self.zone_short, self.zone_long,
                                 self.zone_mapped, t, killer,
                                 self._fresh_loc(t))
            # respawn teleports us to the bind/zone-in point: the trail
            # must NOT draw a line from the corpse to the respawn spot
            self.trail.clear()
            self.loc = None
            self.last_kill = None
            self.flush_pending()
            return

        m = RE_SLAIN_OTHER.match(rest)
        if m:
            mob, killer = m.group(1), m.group(2)
            # "<player> has been slain by <mob>!" is a death notice, not a
            # kill: real killers are a bare player name or someone's pet
            if " " in killer and not killer.endswith("`s pet"):
                return
            if " " not in mob and mob[:1].isupper():
                return                   # a player died; mourn, don't count
            # the mob name opens the sentence, so its article arrives
            # capitalized -- undo that so 'A ghoul' is 'a ghoul'
            first, _, _ = mob.partition(" ")
            if first in ("A", "An", "The"):
                mob = mob[0].lower() + mob[1:]
            # group corpses pay coin to you too -- arm attribution for the
            # "You receive ... from the corpse." that may follow
            self.last_kill = (mob, t)
            self.db.record_kill(self.zone_short, self.zone_long,
                                self.zone_mapped, t, mob, self._fresh_loc(t),
                                group=True)
            return

    # -- command channel ------------------------------------------------------
    def _learn_channel(self, name):
        name = name.lower()
        if name != self.cmd_channel:
            self.cmd_channel = name
            self.cmd_members = None      # fresh channel, unverified
            self.cmd_enabled = False
            # channels are per-character -- remember in the DB so a restart
            # (which skips already-ingested log) still knows the channel
            self.db.data["command_channel"] = name
            self.db.dirty = True
            if self.on_channel_learned:
                self.on_channel_learned(name)

    def _feed_channel(self, t, rest):
        """Channel setup, membership, and command lines. Returns True when
        the line was channel traffic (handled), False otherwise."""
        m = RE_CH_SET.match(rest) or RE_CH_DEFAULT.match(rest)
        if m:
            self._learn_channel(m.group(1))
            return True

        m = RE_CH_LIST.match(rest)
        if m:
            if self.cmd_channel:
                found = None
                for name, count in RE_CH_ENTRY.findall(m.group(1)):
                    if name.lower() == self.cmd_channel:
                        found = int(count)
                self.cmd_members = found
                self.cmd_enabled = (found == 1)
            return True

        m = RE_CMD_SELF.match(rest)
        if m:
            if m.group(1).lower() == self.cmd_channel:
                self._handle_command(t, m.group(2))
                return True
            return False                 # some other channel's chatter

        m = RE_TELL_OTHER.match(rest)
        if m and m.group(2).lower() == self.cmd_channel and self.cmd_channel:
            # someone else is IN the command channel -- it is no longer
            # private. Their text is never parsed (only "You tell" is), but
            # lock commands until a /list shows the count back at 1.
            self.cmd_members = None
            self.cmd_enabled = False
            self.cmd_last = ("(channel breach)",
                             [f"{m.group(1)} is in '{self.cmd_channel}'!",
                              "commands locked -- /list when private again"],
                             t)
            return True
        return False

    def _respond(self, t, typed, lines):
        self.cmd_last = (typed, lines[:6], t)

    def _handle_command(self, t, text):
        text = text.strip()
        if not text:
            return
        if not self.cmd_enabled:
            why = ("channel not verified private yet -- type /list "
                   "(need (1) member)" if self.cmd_members != 1 else
                   "commands locked")
            self._respond(t, text, [why])
            return
        verb, _, arg = text.partition(" ")
        verb, arg = verb.lower(), arg.strip()
        if verb == "find" and arg:
            self._respond(t, text, self._cmd_find(arg))
        elif verb == "guide" and arg:
            self._respond(t, text, self._cmd_guide(arg))
        elif verb == "clear":
            self._respond(t, text, self._cmd_clear(arg))
        elif verb == "note" and arg:
            self._respond(t, text, self._cmd_note(t, arg))
        elif verb == "fav" and arg:
            self._respond(t, text, self._cmd_fav(arg))
        elif verb == "help":
            self._respond(t, text,
                          ["find <item|npc>  -- where it is (+map marks)",
                           "guide <item|npc> -- lead me to it (guide off)",
                           "clear <item> -- unmark one item (clear = all)",
                           "note <text>  -- pin a note at your /loc",
                           "fav <item>   -- favorite an item"])
        else:
            self._respond(t, text, [f"unknown: '{verb}' -- try help"])

    def _cmd_find(self, term):
        low = term.lower()
        if low in ("off", "clear"):
            self.find_query = None
            self.find_exclude = set()
            return ["find markers cleared"]
        self.find_query = (low, time.time())
        self.find_exclude = set()        # new search starts unfiltered
        lines = []
        # your own observations first -- they're EQL ground truth
        seen = []
        for zshort, z in self.db.data["zones"].items():
            for key, mrec in z["mobs"].items():
                for item, d in mrec["drops"].items():
                    if low in item.lower():
                        seen.append((zshort == self.zone_short, d["count"],
                                     zshort, mrec["name"], item,
                                     mrec["kills"]))
        seen.sort(key=lambda h: (-h[0], -h[1]))
        for here, n, zshort, mob, item, kills in seen[:2]:
            rate = f" ({n}/{kills}k)" if kills else ""
            lines.append(f"you: {item} <- {mob}, {zshort}{rate}")
        # then the baseline, current zone preferred, best rate first
        bhits = []
        for zshort, zn in self.base.npcs.items():
            if not self.era_ok(zshort):
                continue
            for key, rec in zn.items():
                for iid, pct in rec["loot"]:
                    nm = self.base.item_name(iid)
                    if nm and low in nm.lower():
                        bhits.append((zshort == self.zone_short, pct,
                                      zshort, rec["name"], nm))
        bhits.sort(key=lambda h: (-h[0], -h[1]))
        for here, pct, zshort, mob, nm in bhits[:4 - len(lines)]:
            lines.append(f"map: {nm} <- {mob}, {zshort} {pct:g}%")
        # mobs and NPCs match too (quest givers, named, anything): your
        # own kills first, then the baseline's spawn knowledge
        mseen = []
        for zshort, z in self.db.data["zones"].items():
            for key, mrec in z["mobs"].items():
                if low in key:
                    mseen.append((zshort == self.zone_short,
                                  mrec["kills"] + mrec["kills_group"],
                                  zshort, mrec["name"]))
        mseen.sort(key=lambda h: (-h[0], -h[1]))
        for here, k, zshort, name in mseen[:2]:
            lines.append(f"you: {name} in {zshort} ({k} kills)")
        bmob = []
        for zshort, zn in self.base.npcs.items():
            if not self.era_ok(zshort):
                continue
            for key, rec in zn.items():
                if low in key:
                    bmob.append((zshort == self.zone_short,
                                 rec["named"], zshort, rec))
        bmob.sort(key=lambda h: (-h[0], -h[1]))
        for here, named, zshort, rec in bmob[:2]:
            lo_l, hi_l = rec["level"]
            tag = " *named*" if named else ""
            lines.append(f"map: {rec['name']}, {zshort} "
                         f"lvl {lo_l}-{hi_l}{tag}")
        # matching spots in THIS zone get orange markers on the map window
        z = self.db.data["zones"].get(self.zone_short)
        if z:
            spots = sum(1 for e in z["events"]
                        if e[4] is not None
                        and ((e[1] == "L" and e[3] and low in e[3].lower())
                             or (e[1] == "K" and e[2]
                                 and low in e[2].lower())))
            if spots:
                lines.append(f"{spots} spot(s) marked here")
        return lines or [f"no '{term}' anywhere yet -- go discover it"]

    def run_local_command(self, text):
        """Commands typed into the tool's own UI (the panel search bar).
        The channel gate exists to reject OTHER PLAYERS' text from the
        log; input typed directly into the overlay is the player."""
        was = self.cmd_enabled
        self.cmd_enabled = True
        try:
            self._handle_command(time.time(), text)
        finally:
            self.cmd_enabled = was

    # -- guide: walk the player to an item ----------------------------------
    def _cmd_guide(self, arg):
        low = arg.lower()
        if low in ("off", "clear"):
            self.guide = None
            return ["guide cleared"]
        self.guide = {"item": low, "label": arg, "target": None,
                      "zone": None, "route": []}
        self._guide_retarget()
        g = self.guide
        if g["target"]:
            return [f"guiding to {arg} -- follow the map line",
                    "'guide off' to stop"]
        if g["route"]:
            nxt = g["route"][1] if len(g["route"]) > 1 else g["route"][0]
            return ["route: " + " > ".join(g["route"]),
                    f"head to {nxt} -- I re-aim at every zone-in"]
        if g.get("dests"):
            zs = ", ".join(sorted(g["dests"])[:4])
            self.guide = None
            return [f"'{arg}' is known in: {zs}",
                    "but no connected route from here"]
        self.guide = None
        return [f"no known source of '{arg}' in the enabled eras"]

    def _guide_retarget(self):
        """Re-aim the guide for the current zone: nearest known loot spot
        here (yours first, else baseline spawn points of droppers), or a
        BFS route through zone connections to the closest zone that has
        the item. Runs on the guide command and on every zone change."""
        g = self.guide
        if not g:
            return
        low = g["item"]
        here = self.zone_short
        g["target"] = None
        g["route"] = []
        g["zone"] = None
        g["who"] = []

        def zone_who(short):
            """WHO to look for once you're in the target zone: empty when
            the guide target IS a mob/NPC (the label already names it),
            else the mobs that drop the item -- your own observed sources
            first, the baseline's otherwise. Feeds the panel's
            'from: ...' line and the Quest window's readout."""
            z = self.db.data["zones"].get(short)
            names = []
            if z:
                for key, mrec in z["mobs"].items():
                    if low in key:
                        return []        # guiding to the mob itself
                    if any(low in item.lower() for item in mrec["drops"]):
                        names.append(mrec["name"])
            if not names:
                hits = []
                for key, rec in self.base.npcs.get(short, {}).items():
                    if low in key:
                        return []
                    for iid, pct in rec["loot"]:
                        nm = self.base.item_name(iid)
                        if nm and low in nm.lower():
                            hits.append((pct, rec["name"]))
                            break
                names = [n for _, n in sorted(hits, key=lambda h: -h[0])]
            return names[:3]

        def zone_spots(short):
            """Where `low` can be found in a zone: spots you've looted the
            item or killed the matching mob (guides work for NPCs and named
            too), else baseline spawn points of matching mobs / droppers."""
            spots = []
            z = self.db.data["zones"].get(short)
            if z:
                spots += [(e[4], e[5], e[6] or 0) for e in z["events"]
                          if e[4] is not None
                          and ((e[1] == "L" and e[3] and low in e[3].lower())
                               or (e[1] == "K" and e[2]
                                   and low in e[2].lower()))]
            if not spots:
                for key, rec in self.base.npcs.get(short, {}).items():
                    if (low in key
                            or any(low in (self.base.item_name(i) or "").lower()
                                   for i, _ in rec["loot"])):
                        spots += [(s[1], s[0], s[2]) for s in rec["spawns"]]
            return spots

        if here:
            spots = zone_spots(here)
            if spots:
                if self.loc:
                    py, px = self.loc[0], self.loc[1]
                    spots.sort(key=lambda s: (s[0] - py) ** 2
                               + (s[1] - px) ** 2)
                g["target"] = spots[0]
                g["zone"] = here
                g["who"] = zone_who(here)
                return
        dests = set()
        for zshort, z in self.db.data["zones"].items():
            if zshort != here and any(
                    (e[1] == "L" and e[3] and low in e[3].lower())
                    or (e[1] == "K" and e[2] and low in e[2].lower())
                    for e in z["events"]):
                dests.add(zshort)
        for zshort, zn in self.base.npcs.items():
            if zshort == here or not self.era_ok(zshort):
                continue
            for key, rec in zn.items():
                if (low in key
                        or any(low in (self.base.item_name(i) or "").lower()
                               for i, _ in rec["loot"])):
                    dests.add(zshort)
                    break
        g["dests"] = dests
        if here and dests:
            path = self.base.route(here, dests)
            if path:
                g["route"] = path
                g["zone"] = path[-1]

    def _cmd_clear(self, arg):
        """'clear bone chip' unmarks matching items from the find rings;
        bare 'clear' wipes all marks (same as 'find off')."""
        if not arg:
            self.find_query = None
            self.find_exclude = set()
            return ["find markers cleared"]
        if not self.find_query:
            return ["no find markers active"]
        self.find_exclude.add(arg.lower())
        return [f"cleared '{arg}' from the map marks"]

    def _cmd_note(self, t, text):
        if not self.zone_short:
            return ["no zone yet -- notes need a zone"]
        loc = self._fresh_loc(t)
        self.db.record_note(self.zone_short, self.zone_long,
                            self.zone_mapped, t, text, loc)
        where = (f"at {loc[0]:.0f}, {loc[1]:.0f}" if loc
                 else "unplaced (no fresh /loc)")
        return [f"noted in {self.zone_short}: {text}", where]

    def _cmd_fav(self, term):
        favs = self.db.data.setdefault("favorites", [])
        base_item = RE_PLUS_SUFFIX.sub("", term)
        if base_item.lower() in (f.lower() for f in favs):
            return [f"'{base_item}' is already a favorite"]
        favs.append(base_item)
        self.db.dirty = True
        return [f"favorited: {base_item} ({len(favs)} total)"]


# ----------------------------------------------------------------------------
# Import / catch-up (shared by first run, restarts, --replay, and Re-scan)
# ----------------------------------------------------------------------------
def ingest(log_path, tracker, from_pos, progress=None):
    """Feed the log from byte offset `from_pos` to EOF through the tracker.
    Returns the new offset (always on a line boundary). Chunked so a full
    first-run import of a many-MB log stays memory-flat."""
    db = tracker.db
    try:
        size = os.path.getsize(log_path)
    except OSError:
        return from_pos
    pos = min(from_pos, size)
    if pos == size:
        return pos
    buf = b""
    done = pos
    with open(log_path, "rb") as f:
        f.seek(pos)
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                tracker.feed(raw.decode("cp1252", "replace").rstrip("\r"))
            done = f.tell() - len(buf)
            if progress:
                progress(done, size)
    db.set_log_pos(log_path, done)
    return done


# ----------------------------------------------------------------------------
# Headless replay (validation / bulk import without the overlay)
# ----------------------------------------------------------------------------
_RE_CH_ANY = re.compile(
    r"(?:Set channels to be joined at login: "
    r"|Your default channel for typing is now Chat to Channel )"
    r"([A-Za-z0-9]+):")


def learn_channel_from_log(log_path, tracker):
    """Fallback channel discovery: the setup lines ('Set channels to be
    joined at login: ...') may sit in log territory that's already been
    ingested and will never be re-read. Scans the WHOLE log (one regex
    pass, well under a second) for the most recent one -- and only ever
    runs while no channel is known, i.e. at most once per character."""
    try:
        with open(log_path, "rb") as f:
            data = f.read().decode("cp1252", "replace")
    except OSError:
        return
    last = None
    for m in _RE_CH_ANY.finditer(data):
        last = m.group(1)
    if last:
        tracker._learn_channel(last)


def replay(log_path):
    char, server = char_server_from(log_path)
    baseline = Baseline()
    db = AtlasDB(char, server)
    try:
        age = time.time() - os.path.getmtime(db.path)
        if age < AUTO_SAVE_S * 2:
            print(f"WARNING: {os.path.basename(db.path)} was written "
                  f"{age:.0f}s ago -- if the Atlas overlay is running, its "
                  f"autosaves and this replay will overwrite each other. "
                  f"Close the overlay first.")
    except OSError:
        pass
    tracker = AtlasTracker(db, baseline, session_from=float("inf"),
                           command_channel=db.data.get("command_channel", ""))
    t0 = time.time()
    start = db.log_pos(log_path) or 0
    end = ingest(log_path, tracker, start)
    tracker.flush_pending()
    if not tracker.cmd_channel:
        learn_channel_from_log(log_path, tracker)
    db.save()

    tot = db.data["totals"]
    print(f"replayed {end - start:,} bytes in {time.time() - t0:.1f}s "
          f"(baseline: {'ok' if baseline.ok else 'MISSING'})")
    print(f"totals: {tot['kills']} kills (+{tot['kills_group']} group), "
          f"{tot['loots']} loots, coin {fmt_coin(tot['coin_copper'])}")
    for short, z in sorted(db.data["zones"].items()):
        kills = sum(m["kills"] for m in z["mobs"].values())
        loots = sum(d["count"] for m in z["mobs"].values()
                    for d in m["drops"].values())
        coin = sum(m["coin_copper"] for m in z["mobs"].values())
        tag = "" if z["mapped"] else "  [unmapped]"
        print(f"\n{z['long']} ({short}){tag}: {kills} kills, {loots} loots, "
              f"{fmt_coin(coin)}")
        top = sorted(z["mobs"].items(),
                     key=lambda kv: -(kv[1]["kills"] + kv[1]["kills_group"]))
        for key, mrec in top[:8]:
            drops = sorted(mrec["drops"].items(), key=lambda kv: -kv[1]["count"])
            dtxt = ", ".join(f"{item} x{d['count']}" for item, d in drops[:4])
            for item, d in drops:
                st = baseline.drop_status(short, key, item)
                if st == "new":
                    dtxt += f"  [NEW: {item}]"
                    break
            print(f"  {mrec['name']:<26} k{mrec['kills']:<5} "
                  f"g{mrec['kills_group']:<5} {dtxt}")
    print(f"\nDB -> {db.path}")


# ----------------------------------------------------------------------------
# Overlay UI
# ----------------------------------------------------------------------------
def run_overlay(log_path):
    import tkinter as tk
    from tkinter import font as tkfont, messagebox
    from eql_atlas_map import AtlasMapWindow
    from eql_quest import QuestDB, QuestState, QuestWindow

    settings = {"x": 40, "y": 260, "opacity": 0.88, "theme": DEFAULT_THEME,
                "panel_width": 340, "font_size": 9,
                "map_open": False, "map_geom": "760x600+120+80",
                "map_layers": {}}
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

    char, server = char_server_from(log_path)
    baseline = Baseline()
    db = AtlasDB(char, server)
    tracker = AtlasTracker(db, baseline,
                           command_channel=db.data.get("command_channel", ""))

    def era_level():
        allowed = 0
        for name, lvl in ERA_LEVELS:
            if settings.get("eras", {}).get(name):
                allowed = max(allowed, lvl)
        return allowed

    def era_ok(short):
        return baseline.zone_era(short) <= era_level()

    tracker.era_ok = era_ok

    # -- quest layer: DB + per-character progress live even while the Quest
    # window is closed, so loot keeps crediting quests either way
    quest_db = QuestDB(APP_DIR)
    quest_state = QuestState(
        data_path(f"eql_quest_{char}_{server}.json", APP_DIR))

    def push_quest_marks():
        tracker.quest_need = quest_state.outstanding_items(quest_db)
        tracker.quest_marks = {n.lower() for n in tracker.quest_need}

    def on_loot(item, qty, t):
        if quest_state.credit_loot(quest_db, item, qty, t):
            push_quest_marks()

    def on_npc_say(npc, text, t):
        # observed hand-in success dialogue = the quest exists on EQL
        for qid in quest_db.match_handin(npc, text):
            quest_state.confirm(qid, t)

    tracker.on_loot = on_loot
    if quest_db.ok:
        tracker.on_npc_say = on_npc_say
    push_quest_marks()
    tracker.quest_npc = quest_state.handin_target(quest_db)

    root = tk.Tk()
    install_tk_error_logger(root, "eql_atlas", ERROR_LOG)
    root.title("EQL Atlas")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", settings["opacity"])
    except tk.TclError:
        pass

    title_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
    mono = tkfont.Font(family=get_theme(settings["theme"])["font_mono"][0],
                       size=int(settings.get("font_size", 9)))

    bar = tk.Frame(root, cursor="fleur")
    bar.pack(fill="x")
    title_lbl = tk.Label(bar, text="  ATLAS", font=title_font, anchor="w")
    title_lbl.pack(side="left", pady=2)
    close_lbl = tk.Label(bar, text=" ✕ ", font=title_font, cursor="hand2")
    close_lbl.pack(side="right", padx=2)
    min_lbl = tk.Label(bar, text=" – ", font=title_font, cursor="hand2")
    min_lbl.pack(side="right")
    char_lbl = tk.Label(bar, text=f"{char} ", font=mono)
    char_lbl.pack(side="right")
    body = tk.Canvas(root, highlightthickness=0, width=252, height=120)
    body.pack(fill="both", expand=True, padx=6, pady=(2, 6))

    # -- search bar: the panel-side alternative to the in-game 'find'
    # command. Typing autofills matching items AND mobs (baseline +
    # observed); Enter or a click runs the search -- full results land in
    # a scrolling list, and item searches drop the same orange map marks.
    srow = tk.Frame(root)
    srow.pack(fill="x", padx=6, pady=(2, 0), before=body)
    search_var = tk.StringVar()
    search_entry = tk.Entry(srow, textvariable=search_var, relief="flat",
                            font=mono)
    search_entry.pack(side="left", fill="x", expand=True, ipady=3)
    search_clear = tk.Label(srow, text=" ✕ ", cursor="hand2", font=mono)
    search_clear.pack(side="right")
    slist_frame = tk.Frame(root)
    slist = tk.Listbox(slist_frame, height=9, font=mono, relief="flat",
                       activestyle="none", highlightthickness=0)
    sscroll = tk.Scrollbar(slist_frame, command=slist.yview)
    slist.config(yscrollcommand=sscroll.set)
    slist.pack(side="left", fill="both", expand=True)
    sscroll.pack(side="right", fill="y")
    sstate = {"mode": "hidden", "payloads": []}

    _mob_index = []                     # (display, lower, zone) -- baseline
    _item_index = []                    # (display, lower)

    def search_indexes():
        if not _item_index:
            seen = set()
            for nm in baseline.item_names():
                if nm.lower() not in seen:
                    seen.add(nm.lower())
                    _item_index.append((nm, nm.lower()))
            for zshort, zn in baseline.npcs.items():
                for rec in zn.values():
                    _mob_index.append((rec["name"], rec["name"].lower(),
                                       zshort))
        return _item_index, _mob_index

    def observed_indexes():
        items, mobs = {}, []
        for zshort, zz in db.data["zones"].items():
            for key, m in zz["mobs"].items():
                mobs.append((m["name"], key, zshort))
                for item in m["drops"]:
                    items.setdefault(item.lower(), item)
        return items, mobs

    def show_list(on):
        if on and not slist_frame.winfo_ismapped():
            slist_frame.pack(fill="x", padx=6, pady=(2, 0), before=body)
        elif not on and slist_frame.winfo_ismapped():
            slist_frame.pack_forget()

    def hide_search(clear=True):
        if clear:
            search_var.set("")
        sstate["mode"] = "hidden"
        sstate["payloads"] = []
        show_list(False)

    def fill_list(groups, spacers=False):
        """groups: ([(text, color-role), ...], payload) -- a logical entry
        may span several styled lines (e.g. an item row plus its colored
        '[zone] rate' detail line). Long lines wrap; rowmap ties every
        listbox row back to its group so clicks, group highlighting, and
        double-click all resolve correctly; spacer rows separate groups."""
        slist.delete(0, "end")
        sstate["rowmap"] = []
        sstate["payloads"] = [p for _, p in groups]
        # wrap against the listbox's REAL rendered width when it's on
        # screen -- the configured panel width can drift from reality
        # (font size, padding); fall back to it only before first map
        lw = slist.winfo_width()
        max_px = (lw if lw > 60
                  else int(settings.get("panel_width", 340)) - 28) - 12
        i = 0
        for gid, (glines, _p) in enumerate(groups):
            for text, role in glines:
                for seg in wrap_px(text, max_px):
                    slist.insert("end", seg)
                    slist.itemconfig(i, fg=th.get(role, th["fg"]))
                    sstate["rowmap"].append(gid)
                    i += 1
            if spacers and gid < len(groups) - 1:
                slist.insert("end", "")
                sstate["rowmap"].append(None)
                i += 1
        show_list(bool(groups))

    def row_gid(listbox_index):
        rm = sstate.get("rowmap", [])
        if 0 <= listbox_index < len(rm):
            return rm[listbox_index]
        return None

    def row_payload(listbox_index):
        gid = row_gid(listbox_index)
        if gid is not None and gid < len(sstate["payloads"]):
            return sstate["payloads"][gid]
        return None

    def highlight_group(gid):
        """Selecting any line of an entry highlights the whole entry."""
        slist.selection_clear(0, "end")
        for i, g in enumerate(sstate.get("rowmap", [])):
            if g == gid:
                slist.selection_set(i)

    def suggest(*_):
        if sstate.get("ph"):             # placeholder text isn't a query
            return
        text = search_var.get().strip().lower()
        if len(text) < 2:
            hide_search(clear=False)
            return
        bitems, bmobs = search_indexes()
        oitems, omobs = observed_indexes()
        # items: yours first, then baseline; prefix matches outrank contains
        names = {}
        for low, disp in oitems.items():
            names.setdefault(low, disp)
        for disp, low in bitems:
            names.setdefault(low, disp)
        hits = sorted((not low.startswith(text), low)
                      for low, d in names.items() if text in low)
        groups = []
        for _, low in hits[:18]:
            disp = names[low]
            groups.append(([(f"item  {disp}", "alt")], ("item", disp)))
        seen = set()
        mob_hits = []
        for disp, low, zshort in omobs + bmobs:
            if text in low and (low, zshort) not in seen:
                # hide mobs from disabled eras (your own observations in
                # omobs always pass -- you were literally there)
                if (disp, low, zshort) not in omobs and not era_ok(zshort):
                    continue
                seen.add((low, zshort))
                mob_hits.append((not low.startswith(text), low, disp, zshort))
        for _, low, disp, zshort in sorted(mob_hits)[:18]:
            groups.append(([(f"mob   {disp}  [{zshort}]", "fg")],
                           ("mob", disp, zshort)))
        sstate["mode"] = "suggest"
        fill_list(groups or [([("no matches", "dim")], None)])

    def run_search(kind, *payload):
        unminimize()                     # results deserve a visible panel
        groups = []
        if kind == "item":
            term = payload[0]
            low = term.lower()
            here = tracker.zone_short
            mine = []
            for zshort, zz in db.data["zones"].items():
                for key, m in zz["mobs"].items():
                    for item, d in m["drops"].items():
                        if low in item.lower():
                            rate = (f"{d['count']}/{m['kills']}k"
                                    if m["kills"] else f"x{d['count']}")
                            mine.append((zshort != here, item,
                                         m["name"], zshort, rate))
            for _, item, mob, zshort, rate in sorted(mine):
                groups.append(([(f"you  {item} <- {mob}", "alt"),
                                (f"     [{zshort}]  {rate}", "accent")],
                               ("item", item)))
            bh = []
            for zshort, zn in baseline.npcs.items():
                if not era_ok(zshort):
                    continue
                for rec in zn.values():
                    for iid, pct in rec["loot"]:
                        nm = baseline.item_name(iid)
                        if nm and low in nm.lower():
                            bh.append((zshort != here, -pct, nm,
                                       rec["name"], zshort, pct))
            bh.sort()
            for _, _, nm, mob, zshort, pct in bh[:150]:
                groups.append(([(f"map  {nm} <- {mob}", "warn"),
                                (f"     [{zshort}]  {pct:g}%", "accent")],
                               ("item", nm)))
            tracker.find_query = (low, time.time())   # orange map marks
            tracker.find_exclude = set()
        else:                                          # mob
            name, zshort = payload
            key = name.lower()
            orec = (db.data["zones"].get(zshort, {})
                    .get("mobs", {}).get(key))
            if orec:
                groups.append(([(f"you  {orec['kills']} kills "
                                 f"(+{orec['kills_group']} group)  "
                                 f"{fmt_coin(orec['coin_copper'])}", "alt")],
                               None))
                for item, d in sorted(orec["drops"].items(),
                                      key=lambda kv: -kv[1]["count"]):
                    groups.append(([(f"you  {item} x{d['count']}", "alt")],
                                   ("item", item)))
            rec = baseline.mob(zshort, key)
            if rec:
                lo, hi = rec["level"]
                groups.append(([(f"map  lvl {lo}-{hi}  "
                                 f"{len(rec['spawns'])} spawn point(s)  "
                                 f"[{zshort}]", "warn")], None))
                for iid, pct in rec["loot"][:120]:
                    nm = baseline.item_name(iid) or f"item {iid}"
                    groups.append(([(f"map  {nm}  {pct:g}%", "warn")],
                                   ("item", nm)))
        sstate["mode"] = "results"
        fill_list(groups or [([("nothing known yet -- go discover it",
                                "dim")], None)], spacers=True)
        redraw()

    def on_list_click(e):
        i = slist.nearest(e.y)
        if sstate["mode"] == "suggest":
            payload = row_payload(i)
            if payload:
                run_search(*payload)
        else:                            # results: highlight the whole entry
            gid = row_gid(i)
            if gid is not None:
                slist.after(1, lambda: highlight_group(gid))

    def on_list_double(e):
        # double-click an entry anywhere = run find on it
        payload = row_payload(slist.nearest(e.y))
        if payload:
            run_search(*payload)

    CMD_VERBS = ("find", "guide", "note", "fav", "clear", "help")

    def on_search_enter(_e):
        raw = search_var.get().strip()
        if not raw or sstate.get("ph"):
            return
        # full command support: 'guide lambent stone' typed here behaves
        # exactly like the chat channel (no gate -- this IS the player)
        if raw.split(" ", 1)[0].lower() in CMD_VERBS:
            tracker.run_local_command(raw)
            hide_search()
            redraw()
            return
        if sstate["mode"] == "suggest" and sstate["payloads"]:
            sel = slist.curselection()
            payload = row_payload(sel[0] if sel else 0)
            if payload:
                run_search(*payload)
        else:
            run_search("item", raw)

    # dimmed 'search' placeholder so the empty box reads as a search bar
    def ph_on():
        if not search_var.get() and not sstate.get("ph"):
            sstate["ph"] = True
            search_entry.configure(fg=th["dim"])
            search_var.set("search")

    def ph_off(*_):
        if sstate.get("ph"):
            sstate["ph"] = False
            search_var.set("")
            search_entry.configure(fg=th["fg"])

    def hide_search_full():
        hide_search()
        if root.focus_get() is not search_entry:
            ph_on()

    # -- panel minimize: fold to the title bar; any search (typed here or
    # -- via the chat channel) pops it back open
    def apply_panel_min():
        mini = settings.get("panel_min", False)
        min_lbl.config(text=" □ " if mini else " – ")
        if mini:
            state["min_t"] = time.time()
            show_list(False)
            srow.pack_forget()
            body.pack_forget()
        else:
            body.pack(fill="both", expand=True, padx=6, pady=(2, 6))
            srow.pack(fill="x", padx=6, pady=(2, 0), before=body)
            show_list(sstate["mode"] != "hidden")
            redraw()

    def toggle_panel_min():
        settings["panel_min"] = not settings.get("panel_min", False)
        save_settings()
        apply_panel_min()

    def unminimize():
        if settings.get("panel_min"):
            settings["panel_min"] = False
            save_settings()
            apply_panel_min()

    min_lbl.bind("<Button-1>", lambda e: toggle_panel_min())
    slist.configure(selectmode="extended")   # group highlight needs multi
    slist.bind("<ButtonRelease-1>", on_list_click)
    slist.bind("<Double-Button-1>", on_list_double)
    search_entry.bind("<Return>", on_search_enter)
    search_entry.bind("<Escape>", lambda e: hide_search_full())
    search_entry.bind("<FocusIn>", ph_off)
    search_entry.bind("<FocusOut>", lambda e: ph_on())
    search_clear.bind("<Button-1>", lambda e: hide_search_full())
    # borderless overlays don't take keyboard focus on their own -- claim
    # it when the box is clicked
    search_entry.bind(
        "<Button-1>",
        lambda e: root.after(1, lambda: (root.focus_force(),
                                         search_entry.focus_set())))
    search_var.trace_add("write", suggest)

    th = {}                                  # rebound by apply_theme()

    def apply_theme():
        nonlocal th
        th = get_theme(settings["theme"])
        mono.configure(family=th["font_mono"][0])
        root.configure(bg=th["bg"])
        bar.configure(bg=th["panel"])
        title_lbl.configure(bg=th["panel"], fg=th["accent"])
        char_lbl.configure(bg=th["panel"], fg=th["dim"])
        close_lbl.configure(bg=th["panel"], fg=th["dim"])
        body.configure(bg=th["bg"])
        srow.configure(bg=th["bg"])
        min_lbl.configure(bg=th["panel"], fg=th["dim"])
        search_entry.configure(bg=th["panel"],
                               fg=th["dim"] if sstate.get("ph") else th["fg"],
                               insertbackground=th["accent"])
        search_clear.configure(bg=th["bg"], fg=th["dim"])
        slist_frame.configure(bg=th["bg"])
        # selected-row text is SOLID BLACK, never th["bg"]: on the Neon
        # HUD theme the bg color is the chroma key, and key-colored text
        # renders as see-through holes over the game footage
        slist.configure(bg=th["panel"], fg=th["fg"],
                        selectbackground=th["accent"],
                        selectforeground="#000000")
        if th.get("transparent"):
            try:
                root.attributes("-transparentcolor", th["bg"])
            except tk.TclError:
                pass
        else:
            try:
                root.attributes("-transparentcolor", "")
            except tk.TclError:
                pass

    def outlined_text(x, y, **kw):
        ol = th.get("outline")
        if ol and luma(kw.get("fill", "#000000")) - luma(ol) > 60:
            okw = dict(kw, fill=ol)
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1),
                           (0, -1), (0, 1), (-1, 0), (1, 0)):
                body.create_text(x + dx, y + dy, **okw)
        return body.create_text(x, y, **kw)

    state = {"importing": False, "note": ""}

    def zone_lifetime():
        z = db.data["zones"].get(tracker.zone_short)
        if not z:
            return 0, 0, 0
        kills = sum(m["kills"] for m in z["mobs"].values())
        loots = sum(d["count"] for m in z["mobs"].values()
                    for d in m["drops"].values())
        coin = sum(m["coin_copper"] for m in z["mobs"].values())
        return kills, loots, coin

    def wrap_px(text, max_px):
        """Greedy word-wrap to a pixel budget; continuations are indented.
        The panel has a FIXED width -- long item/mob names and command
        replies wrap instead of growing the window off the screen edge."""
        if mono.measure(text) <= max_px:
            return [text]
        out, cur = [], ""
        for word in text.split(" "):
            trial = f"{cur} {word}".strip()
            if cur and mono.measure(trial) > max_px:
                out.append(cur)
                cur = "    " + word
            else:
                cur = trial
        out.append(cur)
        return out

    def redraw():
        body.delete("all")
        lh = mono.metrics("linespace") + 2
        lines = []                        # (text, color)
        if state["importing"]:
            lines.append((state["note"], th["warn"]))
        if tracker.zone_long:
            tag = "" if tracker.zone_mapped else "  [uncharted]"
            lines.append((tracker.zone_long + tag, th["accent"]))
        else:
            lines.append(("zone unknown -- /who will sync it", th["dim"]))
        if tracker.loc:
            age = int(time.time() - tracker.loc[3])
            y, x, z = tracker.loc[:3]
            lines.append((f"loc {y:.0f}, {x:.0f}, {z:.0f}   {age}s ago",
                          th["fg"] if age <= LOC_MAX_AGE_S else th["dim"]))
        else:
            lines.append(("no /loc yet -- hotbutton one!", th["dim"]))
        s = tracker.session
        lines.append((f"session   K {s['kills']}  L {s['loots']}  "
                      f"{fmt_coin(s['coin'])}", th["fg"]))
        zk, zl, zc = zone_lifetime()
        lines.append((f"all-time  K {zk}  L {zl}  {fmt_coin(zc)}", th["dim"]))
        g = tracker.guide
        if g:
            if g.get("target") and g.get("zone") == tracker.zone_short:
                if tracker.loc:
                    d = ((tracker.loc[0] - g["target"][0]) ** 2
                         + (tracker.loc[1] - g["target"][1]) ** 2) ** 0.5
                    lines.append((f"guide: {g['label']}  {d:.0f} away",
                                  th["warn"]))
                else:
                    lines.append((f"guide: {g['label']} marked on map",
                                  th["warn"]))
                # arrived in the right zone: name WHO to look for -- the
                # mob(s) that drop the item; guiding to a mob/NPC itself
                # needs no extra line, the label already names it
                if g.get("who"):
                    lines.append(("  from: " + ", ".join(g["who"]),
                                  th["alt"]))
            elif g.get("route"):
                lines.append(("guide: " + " > ".join(g["route"]),
                              th["warn"]))
        # tracked quest: standing in the hand-in zone names the NPC to
        # find (and the still-missing drops, if any)
        qn = getattr(tracker, "quest_npc", None)
        if qn and qn[1] == tracker.zone_short:
            lines.append((f"quest: hand in to {qn[2]}", th["warn"]))
            need = getattr(tracker, "quest_need", None)
            if need:
                lines.append(("  still need: " + ", ".join(need[:3]),
                              th["alt"]))
        if tracker.last_drop:
            item, mob, status = tracker.last_drop
            color = th["warn"] if status == "new" else th["alt"]
            new = "  ★NEW" if status == "new" else ""
            lines.append((f"{item}{new}", color))
            lines.append((f"  <- {mob}", th["dim"]))
        # command channel status + the last command's reply
        if tracker.cmd_channel:
            if tracker.cmd_enabled:
                lines.append((f"cmd [{tracker.cmd_channel}] ready",
                              th["alt"]))
            else:
                lines.append((f"cmd [{tracker.cmd_channel}] LOCKED -- "
                              f"/list to verify (1)", th["warn"]))
        else:
            lines.append(("cmd: none -- /join <name>:<password>", th["dim"]))
        if tracker.cmd_last and time.time() - tracker.cmd_last[2] < CMD_RESPONSE_S:
            typed, reply, _ = tracker.cmd_last
            lines.append((f"> {typed}", th["accent"]))
            for r in reply:
                # your own observations vs baseline knowledge read very
                # differently -- color them apart (you=bright, map=warm)
                if r.startswith("you:"):
                    color = th["alt"]
                elif r.startswith("map:"):
                    color = th["warn"]
                else:
                    color = th["fg"]
                lines.append((f"  {r}", color))
        base_txt = (f"baseline ok -- {tracker.novel_finds} new finds"
                    if baseline.ok else "no baseline file")
        lines.append((base_txt, th["dim"]))

        panel_w = int(settings.get("panel_width", 340))
        wrapped = [(seg, color) for text, color in lines
                   for seg in wrap_px(text, panel_w - 12)]
        body.configure(width=panel_w, height=lh * len(wrapped) + 4)
        for i, (text, color) in enumerate(wrapped):
            outlined_text(6, 4 + i * lh, text=text, fill=color,
                          anchor="nw", font=mono)

    # -- dragging -----------------------------------------------------------
    drag = {"x": 0, "y": 0}

    def on_press(e):
        drag["x"], drag["y"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

    def on_drag(e):
        settings["x"], settings["y"] = e.x_root - drag["x"], e.y_root - drag["y"]
        root.geometry(f"+{settings['x']}+{settings['y']}")

    for w in (bar, title_lbl, char_lbl):
        w.bind("<ButtonPress-1>", on_press)
        w.bind("<B1-Motion>", on_drag)
        w.bind("<ButtonRelease-1>", lambda e: save_settings())

    def shutdown(*_):
        tracker.flush_pending()
        db.set_log_pos(log_path, watcher._pos)
        db.save()
        save_settings()
        root.destroy()

    close_lbl.bind("<Button-1>", shutdown)
    close_lbl.bind("<Enter>", lambda e: close_lbl.config(fg=th["fg"]))
    close_lbl.bind("<Leave>", lambda e: close_lbl.config(fg=th["dim"]))
    root.protocol("WM_DELETE_WINDOW", shutdown)

    # -- right-click menu -----------------------------------------------------
    # -- map window (Phase 2) -- created lazily, owned by this overlay -------
    mapw = {"win": None}

    def ensure_map():
        if mapw["win"] is None or not mapw["win"].top.winfo_exists():
            game_root = os.path.dirname(os.path.dirname(os.path.abspath(log_path)))
            dirs = [os.path.join(game_root, "maps", "brewall"),
                    os.path.join(game_root, "maps")]
            mapw["win"] = AtlasMapWindow(root, settings, save_settings,
                                         lambda: get_theme(settings["theme"]),
                                         dirs)
            mapw["win"].set_zone(tracker.zone_short, tracker.zone_long)
        return mapw["win"]

    def toggle_map():
        win = ensure_map()
        win.toggle()

    # -- quest window -- created lazily, owned by this overlay ---------------
    questw = {"win": None}

    def ensure_quest():
        if questw["win"] is None or not questw["win"].top.winfo_exists():
            questw["win"] = QuestWindow(root, settings, save_settings,
                                        lambda: get_theme(settings["theme"]),
                                        quest_db, quest_state, era_level)
            return questw["win"], True
        return questw["win"], False

    def toggle_quest():
        win, created = ensure_quest()
        # a fresh Toplevel is already on screen -- toggling it would hide
        # the window the click meant to open
        win.show() if created else win.toggle()

    def pick_theme(k):
        settings["theme"] = k
        save_settings()
        apply_theme()
        if mapw["win"] and mapw["win"].top.winfo_exists():
            mapw["win"].apply_theme()
        if questw["win"] and questw["win"].top.winfo_exists():
            questw["win"].apply_theme()
        redraw()

    def set_opacity(v):
        settings["opacity"] = v
        save_settings()
        try:
            root.attributes("-alpha", v)
        except tk.TclError:
            pass

    def set_panel(width, font_size):
        if width:
            settings["panel_width"] = width
        if font_size:
            settings["font_size"] = font_size
            mono.configure(size=font_size)
        save_settings()
        redraw()

    def rescan():
        if not messagebox.askyesno(
                "Rebuild Atlas database",
                f"Wipe {char}'s observed data and re-import the entire log "
                f"from the beginning?\n\nThe baseline layer is untouched; "
                f"this only rebuilds what was seen in this log."):
            return
        db.reset()
        tracker.zone_short = tracker.zone_long = None
        tracker.novel_finds = 0
        # replayed history must not re-credit quest item progress
        quest_state.suspend_credit = True
        try:
            run_import(0)
        finally:
            quest_state.suspend_credit = False

    def show_menu(e):
        menu = tk.Menu(root, tearoff=0, bg=th["panel"], fg=th["fg"],
                       activebackground=th["accent"],
                       activeforeground="#000000")
        tmenu = tk.Menu(menu, tearoff=0, bg=th["panel"], fg=th["fg"],
                        activebackground=th["accent"],
                        activeforeground="#000000")
        for key, spec in RETRO_THEMES.items():
            mark = "● " if key == settings["theme"] else "   "
            tmenu.add_command(label=mark + spec["label"],
                              command=lambda k=key: pick_theme(k))
        menu.add_cascade(label="Theme", menu=tmenu)
        omenu = tk.Menu(menu, tearoff=0, bg=th["panel"], fg=th["fg"],
                        activebackground=th["accent"],
                        activeforeground="#000000")
        for v in (1.0, 0.88, 0.75, 0.6):
            mark = "● " if abs(settings["opacity"] - v) < 0.01 else "   "
            omenu.add_command(label=f"{mark}{int(v * 100)}%",
                              command=lambda v=v: set_opacity(v))
        menu.add_cascade(label="Opacity", menu=omenu)
        wmenu = tk.Menu(menu, tearoff=0, bg=th["panel"], fg=th["fg"],
                        activebackground=th["accent"],
                        activeforeground="#000000")
        for w in (300, 340, 400, 480):
            mark = "● " if settings.get("panel_width", 340) == w else "   "
            wmenu.add_command(label=f"{mark}{w}px",
                              command=lambda w=w: set_panel(w, None))
        menu.add_cascade(label="Panel width", menu=wmenu)
        fmenu = tk.Menu(menu, tearoff=0, bg=th["panel"], fg=th["fg"],
                        activebackground=th["accent"],
                        activeforeground="#000000")
        for fs in (8, 9, 10, 12, 14):
            mark = "● " if settings.get("font_size", 9) == fs else "   "
            fmenu.add_command(label=f"{mark}{fs}pt",
                              command=lambda fs=fs: set_panel(None, fs))
        menu.add_cascade(label="Text size", menu=fmenu)
        emenu = tk.Menu(menu, tearoff=0, bg=th["panel"], fg=th["fg"],
                        activebackground=th["accent"],
                        activeforeground="#000000")

        def toggle_era(name):
            eras = settings.setdefault("eras", {})
            eras[name] = not eras.get(name)
            save_settings()

        for name, _lvl in ERA_LEVELS:
            on = settings.get("eras", {}).get(name)
            emenu.add_command(label=("● " if on else "   ") + f"enable {name}",
                              command=lambda n=name: toggle_era(n))
        menu.add_cascade(label="Expansions", menu=emenu)
        menu.add_separator()
        menu.add_command(label="Map window", command=toggle_map)
        menu.add_command(label="Quest window", command=toggle_quest)
        menu.add_command(label="Save now", command=db.save)
        menu.add_command(label="Re-scan full log...", command=rescan)
        menu.add_separator()
        menu.add_command(label="Close", command=shutdown)
        menu.tk_popup(e.x_root, e.y_root)

    body.bind("<Button-3>", show_menu)
    # header right-click works like every other tool in the suite -- the
    # labels sit on top of the bar, so they need their own bindings
    for w in (bar, title_lbl, char_lbl, min_lbl, srow, search_entry):
        w.bind("<Button-3>", show_menu)

    # -- import backlog, then tail --------------------------------------------
    watcher = LogWatcher(log_path)
    watcher.add_handler(tracker.feed)

    def run_import(from_pos):
        """Synchronous catch-up (first run: the whole log). Keeps the panel
        alive with progress via update_idletasks -- worst case a few
        seconds for a multi-year log."""
        state["importing"] = True

        def progress(done, size):
            state["note"] = f"importing history... {done * 100 // max(size, 1)}%"
            redraw()
            root.update_idletasks()

        end = ingest(log_path, tracker, from_pos, progress)
        watcher._pos = end               # tail exactly where the import ended
        state["importing"] = False
        state["note"] = ""
        db.save()
        redraw()

    apply_theme()
    ph_on()
    apply_panel_min()
    # a saved position (or panel growth in an old build) can leave the
    # window hanging off the screen edge -- pull it back into view
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    settings["x"] = max(0, min(int(settings["x"]),
                               sw - int(settings.get("panel_width", 340))))
    settings["y"] = max(0, min(int(settings["y"]), sh - 160))
    root.geometry(f"+{settings['x']}+{settings['y']}")
    saved = db.log_pos(log_path)
    run_import(saved if saved is not None else 0)
    if not tracker.cmd_channel:
        learn_channel_from_log(log_path, tracker)

    last_save = [time.time()]

    if settings.get("map_open"):
        ensure_map().show()
    if settings.get("quest_open"):
        ensure_quest()[0].show()

    def poll():
        watcher.poll()
        if db.dirty and time.time() - last_save[0] >= AUTO_SAVE_S:
            db.set_log_pos(log_path, watcher._pos)
            db.save()
            last_save[0] = time.time()
        # a chat-channel command while minimized pops the panel back open
        if (settings.get("panel_min") and tracker.cmd_last
                and tracker.cmd_last[2] > state.get("min_t", 0)):
            unminimize()
        if not settings.get("panel_min"):
            redraw()
        if mapw["win"] and mapw["win"].top.winfo_exists():
            mapw["win"].tick(tracker, db, baseline)
        if questw["win"] and questw["win"].top.winfo_exists():
            questw["win"].tick(tracker, db, baseline)
        root.after(POLL_INTERVAL_MS, poll)

    poll()
    root.mainloop()


def main():
    args = sys.argv[1:]
    if args and args[0] == "--replay":
        if len(args) != 2 or not os.path.isfile(args[1]):
            raise SystemExit("usage: python eql_atlas.py --replay <eqlog file>")
        replay(args[1])
        return
    log_path = args[0] if args else ""
    if not log_path:
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                log_path = json.load(f).get("log_path", "")
        except (OSError, ValueError):
            pass
    if not log_path or not os.path.isfile(log_path):
        import tkinter as tk
        from tkinter import filedialog
        tk.Tk().withdraw()
        log_path = filedialog.askopenfilename(
            title="Select your EverQuest log file (eqlog_*.txt)",
            filetypes=[("EQ log files", "eqlog_*.txt"),
                       ("Text files", "*.txt"), ("All files", "*.*")])
    if not log_path or not os.path.isfile(log_path):
        raise SystemExit("no log file selected")
    run_overlay(log_path)


if __name__ == "__main__":
    main()
