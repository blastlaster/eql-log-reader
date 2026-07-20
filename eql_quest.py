#!/usr/bin/env python3
"""
EQL Atlas -- Quest Window (Phase 3)
====================================
The Atlas collector's quest companion: search the distilled Project Quarm
quest database (eql_quest_db.json.gz, built by eql_quest_db_build.py from
github.com/SecretsOTheP/quests), keep a personal "quests I'm on" list per
character, and track item completion automatically from the same loot
lines the collector already parses -- with manual +/- correction for
items you already had or handed away.

What each part does:

  * Search      -- type 2+ letters: matches quest names (synthesized from
                   the reward), NPC names, zones, and every required or
                   reward item name. Enter / double-click / [Add] puts the
                   quest on your list. Only quests from expansions enabled
                   in the Atlas right-click menu are shown.
  * My Quests   -- your active list, with live [have/need] progress.
                   ▶ marks the TRACKED quest, ✓ a completed one.
  * Detail pane -- hand-in NPC and zone, each required item with its
                   progress and where it's known to drop (your own Atlas
                   observations first, then the baseline's best sources),
                   the reward, and the NPC's success dialogue.
  * Track       -- makes the selected quest current: its outstanding
                   items ring their looted spots on the Atlas map (violet,
                   'quest' layer) and the hand-in NPC gets a labeled
                   violet pin at its spawn point whenever you're in its
                   zone. [Guide] routes you to the selected item, and
                   [Hand-in] to the quest NPC, with the Atlas guide (A*
                   in zone, zone-by-zone across the world).
  * Availability -- the Quarm data is a claim, not a promise. When your
                   log shows an NPC speaking a quest's hand-in success
                   dialogue, that quest is marked ✔ confirmed on EQL --
                   permanently, per character, including hand-ins found
                   in your imported history.
  * Clear       -- removes ONLY the tracked quest from the list; the rest
                   of the list is untouched.

Item counting credits the tracked quest first, then the oldest quest on
the list that still needs the item; only loot that happens AFTER a quest
was added counts (so re-imports can't inflate progress), and the manual
+/- buttons fix anything the log can't know.

Not a standalone tool -- eql_atlas.py owns this window (right-click the
Atlas panel -> Quest window). Quest availability data is Quarm's; EQL may
differ -- treat unknown quests as "probably right until proven otherwise",
the same deal as the loot baseline.
"""

import gzip
import json
import os
import re
import sys
import time
import tkinter as tk
from tkinter import font as tkfont

DB_NAME = "eql_quest_db.json.gz"
SEARCH_MAX = 40             # search result rows
CREDIT_MAX = 999            # manual counter ceiling
SOURCES_MAX = 3             # drop-source hints per required item

QUEST_COLOR = "#b18aff"     # quest map marks: violet in every theme,
                            # apart from find's orange and the loot palette


def _norm_say(text):
    """Normalize dialogue for hand-in matching: case/whitespace folded,
    apostrophes dropped (the log renders some as backticks)."""
    return re.sub(r"\s+", " ", re.sub(r"[`']", "", text)).strip().lower()


def fmt_coin(copper):
    if copper <= 0:
        return "0c"
    parts = []
    for label, mult in (("p", 1000), ("g", 100), ("s", 10), ("c", 1)):
        v, copper = divmod(copper, mult)
        if v:
            parts.append(f"{v}{label}")
    return " ".join(parts[:2]) or "0c"


# ----------------------------------------------------------------------------
# The shipped quest database (read-only)
# ----------------------------------------------------------------------------
class QuestDB:
    """Read-only view of eql_quest_db.json.gz. Everything degrades to
    empty when the file is absent -- the window then says so instead of
    breaking the Atlas."""

    def __init__(self, app_dir):
        self.ok = False
        self.quests = []                 # each augmented with qid + name
        self.items = {}                  # id str -> display name
        self.zones = {}                  # short -> long
        self._by_qid = {}
        self._by_npc = {}                # npc lower -> [quest, ...]
        self._name_to_id = {}
        for d in (app_dir, getattr(sys, "_MEIPASS", None)):
            if not d:
                continue
            path = os.path.join(d, DB_NAME)
            if not os.path.isfile(path):
                continue
            try:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    b = json.load(f)
            except (OSError, ValueError):
                continue
            if b.get("format") != 1:
                continue
            self.items = b.get("items", {})
            self.zones = b.get("zones", {})
            self.quests = []
            for q in b.get("quests", []):
                q["qid"] = self._qid(q)
                dup = self._by_qid.get(q["qid"])
                if dup is not None:
                    # same NPC, same turn-in (e.g. faction-tier branches of
                    # one quest): merge rewards, keep the first dialogue
                    dup["ri"] = sorted(set(dup["ri"]) | set(q["ri"]))
                    dup["rc"] += [g for g in q["rc"] if g not in dup["rc"]]
                    continue
                self._by_qid[q["qid"]] = q
                self._by_npc.setdefault(q["n"].lower(), []).append(q)
                self.quests.append(q)
            for q in self.quests:     # after merges, so rewards are final
                q["name"] = self._name(q)
                q["blob"] = self._blob(q)
                q["say"] = _norm_say(q.get("txt", ""))
            for iid, name in self.items.items():
                self._name_to_id.setdefault(name.lower(), int(iid))
            self.ok = True
            break

    def item_name(self, iid):
        return self.items.get(str(iid), f"item {iid}")

    def item_id(self, name):
        return self._name_to_id.get(name.lower(), 0)

    def get(self, qid):
        return self._by_qid.get(qid)

    def _qid(self, q):
        """Stable across DB rebuilds: zone + NPC + the required set."""
        req = "+".join(f"{iid}x{n}" for iid, n in q["req"])
        return f"{q['z']}|{q['n']}|{req or q['coin']}"

    def _name(self, q):
        """Scripts carry no quest names -- synthesize one players will
        recognize: the reward when there is one, the turn-in otherwise."""
        if q["ri"]:
            return self.item_name(q["ri"][0])
        if q["rc"]:
            return self.item_name(q["rc"][0][0]) + " (choice)"
        if q["req"]:
            return self.item_name(q["req"][0][0]) + " turn-in"
        return f"{fmt_coin(q['coin'])} donation"

    def _blob(self, q):
        """Lowercased haystack the search matches against."""
        parts = [q["name"], q["n"], q["z"], self.zones.get(q["z"], ""),
                 q.get("txt", "")]
        parts += [self.item_name(iid) for iid, _ in q["req"]]
        parts += [self.item_name(iid) for iid in q["ri"]]
        for grp in q["rc"]:
            parts += [self.item_name(iid) for iid in grp]
        return " | ".join(parts).lower()

    def match_handin(self, npc, text):
        """Quest qids whose success dialogue this observed NPC say-line
        opens with -- the availability signal: seeing it means the quest
        demonstrably EXISTS on EQL. Scripts often splice the player's name
        mid-sentence, so only the stored first literal segment is compared,
        and only when it's long enough (>= 20 chars) to be distinctive."""
        cands = self._by_npc.get(npc.lower())
        if not cands:
            return []
        said = _norm_say(text)
        out = []
        for q in cands:
            stored = q.get("say", "")
            if len(stored) >= 20 and said.startswith(stored[:48]):
                out.append(q["qid"])
        return out

    def search(self, term, era_allowed):
        """Era-gated substring search, best matches first: quest-name hits
        outrank NPC hits outrank item/dialogue hits."""
        low = term.lower()
        hits = []
        for q in self.quests:
            if q["era"] > era_allowed or low not in q["blob"]:
                continue
            if low in q["name"].lower():
                rank = 0
            elif low in q["n"].lower():
                rank = 1
            elif any(low in self.item_name(iid).lower()
                     for iid, _ in q["req"]):
                rank = 2
            else:
                rank = 3
            hits.append((rank, q["name"].lower(), q))
        hits.sort(key=lambda h: (h[0], h[1]))
        return [q for _, _, q in hits[:SEARCH_MAX]]


# ----------------------------------------------------------------------------
# Per-character quest state
# ----------------------------------------------------------------------------
class QuestState:
    """What this character is on, and how far along. Schema (format 1):

    quests.<qid>: {added: epoch, have: {item_id_str: count}}
    order:     qids, oldest first (also the loot-credit spill order)
    current:   the TRACKED qid (map marks + guide), or null
    confirmed: {qid: epoch first seen} -- quests whose hand-in success
               dialogue has been OBSERVED in this character's log, i.e.
               proven to exist on EQL (the Quarm data alone is a claim)
    """

    def __init__(self, path):
        self.path = path
        self.dirty = False               # unsaved changes
        self.rev = 0                     # bumped on every change (UI redraw)
        self.suspend_credit = False      # True while Re-scan replays history
        self.data = {"format": 1, "quests": {}, "order": [], "current": None,
                     "confirmed": {}}
        try:
            with open(path, "r", encoding="utf-8") as f:
                on_disk = json.load(f)
            if on_disk.get("format") == 1:
                self.data = on_disk
                self.data.setdefault("confirmed", {})
        except (OSError, ValueError):
            pass

    def save(self):
        if not self.dirty:
            return
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, separators=(",", ":"))
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError:
            pass

    def _touch(self):
        self.dirty = True
        self.rev += 1
        self.save()                      # tiny file: write-through

    # -- list management ----------------------------------------------------
    def qids(self):
        return list(self.data["order"])

    def on_list(self, qid):
        return qid in self.data["quests"]

    def add(self, qid):
        if qid in self.data["quests"]:
            return False
        self.data["quests"][qid] = {"added": int(time.time()), "have": {}}
        self.data["order"].append(qid)
        self._touch()
        return True

    def remove(self, qid):
        self.data["quests"].pop(qid, None)
        self.data["order"] = [i for i in self.data["order"] if i != qid]
        if self.data["current"] == qid:
            self.data["current"] = None
        self._touch()

    def current(self):
        return self.data["current"]

    def set_current(self, qid):
        self.data["current"] = qid if self.on_list(qid) else None
        self._touch()

    def clear_current(self):
        """The Quest Clear command: drops ONLY the tracked quest."""
        cur = self.data["current"]
        if cur:
            self.remove(cur)             # remove() also unsets current

    # -- availability (observed hand-ins) -----------------------------------
    def confirm(self, qid, t):
        """A hand-in's success dialogue was seen: mark the quest as proven
        to exist on EQL. Idempotent; survives DB rebuilds via the qid."""
        if qid in self.data["confirmed"]:
            return False
        self.data["confirmed"][qid] = int(t)
        self._touch()
        return True

    def is_confirmed(self, qid):
        return qid in self.data["confirmed"]

    def confirmed_count(self):
        return len(self.data["confirmed"])

    def handin_target(self, qdb):
        """(npc_lower, zone_short, label) for the tracked quest's hand-in
        NPC -- the map pins it whenever you're in that zone."""
        cur = self.data["current"]
        q = qdb.get(cur) if cur else None
        if not q:
            return None
        return (q["n"].lower(), q["z"], q["n"])

    # -- item progress ------------------------------------------------------
    def have(self, qid, iid):
        rec = self.data["quests"].get(qid)
        return int(rec["have"].get(str(iid), 0)) if rec else 0

    def set_have(self, qid, iid, n):
        rec = self.data["quests"].get(qid)
        if not rec:
            return
        rec["have"][str(iid)] = max(0, min(CREDIT_MAX, int(n)))
        self._touch()

    def complete(self, qdb, qid):
        q = qdb.get(qid)
        if not q or not q["req"]:
            return False
        return all(self.have(qid, iid) >= n for iid, n in q["req"])

    def progress(self, qdb, qid):
        q = qdb.get(qid)
        if not q:
            return 0, 0
        need = sum(n for _, n in q["req"])
        got = sum(min(self.have(qid, iid), n) for iid, n in q["req"])
        return got, need

    def credit_loot(self, qdb, item_name, qty, t):
        """A loot line happened: fill the tracked quest first, then spill
        to the oldest quest that still needs the item. Loot older than a
        quest's add time never counts (guards Re-scan double-credit; the
        +/- buttons cover items you already owned)."""
        if self.suspend_credit or qty <= 0:
            return False
        iid = qdb.item_id(item_name)
        if not iid:
            return False
        cur = self.data["current"]
        order = ([cur] if cur else []) + [i for i in self.data["order"]
                                          if i != cur]
        changed = False
        for qid in order:
            if qty <= 0:
                break
            rec = self.data["quests"].get(qid)
            q = qdb.get(qid)
            if not rec or not q or t < rec["added"]:
                continue
            need = dict(q["req"]).get(iid)
            if not need:
                continue
            have = int(rec["have"].get(str(iid), 0))
            if have >= need:
                continue
            take = min(qty, need - have)
            rec["have"][str(iid)] = have + take
            qty -= take
            changed = True
        if changed:
            self._touch()
        return changed

    def outstanding_items(self, qdb):
        """Display names of the TRACKED quest's still-missing items, in
        quest order -- the Atlas panel lists these as 'still need: ...'."""
        cur = self.data["current"]
        q = qdb.get(cur) if cur else None
        if not q:
            return []
        return [qdb.item_name(iid) for iid, n in q["req"]
                if self.have(cur, iid) < n]

    def outstanding_names(self, qdb):
        """Lowercased names of the TRACKED quest's still-missing items --
        the Atlas map rings these wherever you've looted them ('quest'
        layer)."""
        return {n.lower() for n in self.outstanding_items(qdb)}


# ----------------------------------------------------------------------------
# The window
# ----------------------------------------------------------------------------
class QuestWindow:
    """Decorated, resizable, optionally always-on-top quest browser.
    eql_atlas.py owns it; tick() feeds it the live tracker/db/baseline."""

    def __init__(self, parent, settings, save_settings, theme_of,
                 qdb, state, era_allowed):
        self.settings = settings
        self.save_settings = save_settings
        self.theme_of = theme_of
        self.qdb = qdb
        self.state = state
        self.era_allowed = era_allowed   # () -> max enabled era level
        self._ctx = None                 # (tracker, db, baseline) from tick
        self._drawn_rev = -1
        self._mode = "list"              # or "search"
        self._results = []               # search-mode quest dicts
        self._sel_qid = None
        self._sel_item = None            # selected required-item id
        self._rowmap = []                # detail row -> payload
        self._conf_n = state.confirmed_count()   # flash on new confirms
        self._flash_until = 0.0          # status shows guide readout after

        self.top = tk.Toplevel(parent)
        self.top.title("EQL Atlas -- Quests")
        self.top.geometry(settings.get("quest_geom", "430x540+180+120"))
        self.top.attributes("-topmost", bool(settings.get("quest_pin", True)))
        self.top.protocol("WM_DELETE_WINDOW", self.hide)
        self.top.minsize(340, 360)

        self.mono = tkfont.Font(family="Consolas", size=9)
        self.bold = tkfont.Font(family="Consolas", size=9, weight="bold")

        self.bar = tk.Frame(self.top)
        self.bar.pack(fill="x")
        self.title_lbl = tk.Label(self.bar, text=" QUESTS", font=self.bold,
                                  anchor="w")
        self.title_lbl.pack(side="left", pady=2)
        self.close_lbl = tk.Label(self.bar, text=" ✕ ", cursor="hand2",
                                  font=self.bold)
        self.close_lbl.pack(side="right", padx=2)
        self.close_lbl.bind("<Button-1>", lambda e: self.hide())
        self.pin_var = tk.BooleanVar(value=bool(settings.get("quest_pin",
                                                             True)))
        self.pin_chip = tk.Checkbutton(self.bar, text="pin",
                                       variable=self.pin_var,
                                       command=self._toggle_pin,
                                       indicatoron=False, relief="flat",
                                       bd=0, cursor="hand2",
                                       offrelief="flat", overrelief="flat",
                                       highlightthickness=0)
        self.pin_chip.pack(side="right", padx=2)

        srow = tk.Frame(self.top)
        srow.pack(fill="x", padx=6, pady=(4, 2))
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(srow, textvariable=self.search_var,
                                     relief="flat", font=self.mono)
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.search_clear = tk.Label(srow, text=" ✕ ", cursor="hand2",
                                     font=self.mono)
        self.search_clear.pack(side="right")
        self.search_clear.bind("<Button-1>", lambda e: self._search_off())
        self.search_var.trace_add("write", self._on_search_typed)
        self.search_entry.bind("<Return>", self._on_search_enter)
        self.search_entry.bind("<Escape>", lambda e: self._search_off())

        self.list_hdr = tk.Label(self.top, text=" MY QUESTS", anchor="w",
                                 font=self.bold)
        self.list_hdr.pack(fill="x", padx=6)
        lframe = tk.Frame(self.top)
        lframe.pack(fill="x", padx=6)
        self.qlist = tk.Listbox(lframe, height=7, font=self.mono,
                                relief="flat", activestyle="none",
                                highlightthickness=0, exportselection=False)
        qscroll = tk.Scrollbar(lframe, command=self.qlist.yview)
        self.qlist.config(yscrollcommand=qscroll.set)
        self.qlist.pack(side="left", fill="both", expand=True)
        qscroll.pack(side="right", fill="y")
        self.qlist.bind("<<ListboxSelect>>", self._on_pick)
        self.qlist.bind("<Double-Button-1>", self._on_double)

        brow = tk.Frame(self.top)
        brow.pack(fill="x", padx=6, pady=3)
        self.buttons = []

        def btn(label, cmd):
            b = tk.Button(brow, text=label, relief="flat", command=cmd,
                          font=self.mono, cursor="hand2")
            b.pack(side="left", padx=(0, 4))
            self.buttons.append(b)
            return b

        self.add_btn = btn("Add", self._do_add)
        self.track_btn = btn("Track ▶", self._do_track)
        self.plus_btn = btn("＋", lambda: self._bump(+1))
        self.minus_btn = btn("－", lambda: self._bump(-1))
        self.guide_btn = btn("Guide", self._do_guide)
        self.handin_btn = btn("Hand-in", self._do_handin)
        self.remove_btn = btn("Remove", self._do_remove)
        self.clear_btn = btn("Clear ▶", self._do_clear)

        dframe = tk.Frame(self.top)
        dframe.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.detail = tk.Listbox(dframe, font=self.mono, relief="flat",
                                 activestyle="none", highlightthickness=0,
                                 exportselection=False)
        dscroll = tk.Scrollbar(dframe, command=self.detail.yview)
        self.detail.config(yscrollcommand=dscroll.set)
        self.detail.pack(side="left", fill="both", expand=True)
        dscroll.pack(side="right", fill="y")
        self.detail.bind("<<ListboxSelect>>", self._on_detail_pick)

        self.status = tk.Label(self.top, text="", anchor="w", font=self.mono)
        self.status.pack(fill="x", padx=6, pady=(0, 4))

        self.top.bind("<Configure>", self._on_configure)
        self.apply_theme()
        self._refresh(full=True)

    # -- window plumbing ------------------------------------------------------
    def visible(self):
        return self.top.winfo_exists() and self.top.state() == "normal"

    def show(self):
        self.top.deiconify()
        self.settings["quest_open"] = True
        self.save_settings()
        self._refresh(full=True)

    def hide(self):
        self.top.withdraw()
        self.settings["quest_open"] = False
        self.save_settings()

    def toggle(self):
        self.hide() if self.visible() else self.show()

    def _on_configure(self, e):
        if e.widget is self.top:
            self.settings["quest_geom"] = self.top.geometry()

    def _toggle_pin(self):
        self.settings["quest_pin"] = self.pin_var.get()
        self.save_settings()
        try:
            self.top.attributes("-topmost", self.pin_var.get())
        except tk.TclError:
            pass
        self._restyle_pin()

    def _restyle_pin(self):
        th = self._th
        on = self.pin_var.get()
        # solid black on the bright accent chip -- suite-wide rule: text
        # never wears a theme's bg color (see the map window's chips)
        self.pin_chip.configure(selectcolor=th["accent"], bg=th["panel"],
                                fg="#000000" if on else th["dim"],
                                activebackground=th["accent"],
                                activeforeground="#000000", font=self.mono,
                                padx=8, pady=2)

    def apply_theme(self):
        th = self.theme_of()
        # ghost-style transparent themes have a chroma-key bg -- a normal
        # decorated window painted with it would punch holes; use the
        # panel tone as the window ground instead
        self._th = dict(th)
        if th.get("transparent"):
            self._th["bg"] = th["panel"]
        th = self._th
        self.top.configure(bg=th["bg"])
        self.bar.configure(bg=th["panel"])
        self.title_lbl.configure(bg=th["panel"], fg=th["accent"])
        self.close_lbl.configure(bg=th["panel"], fg=th["dim"])
        self.list_hdr.configure(bg=th["bg"], fg=th["dim"])
        self.status.configure(bg=th["bg"], fg=th["dim"])
        self.search_entry.configure(bg=th["panel"], fg=th["fg"],
                                    insertbackground=th["accent"])
        self.search_clear.configure(bg=th["bg"], fg=th["dim"])
        for lb in (self.qlist, self.detail):
            lb.configure(bg=th["panel"], fg=th["fg"],
                         selectbackground=th["accent"],
                         selectforeground="#000000")
        for f in self.top.pack_slaves():
            if isinstance(f, tk.Frame):
                f.configure(bg=th["bg"])
        for b in self.buttons:
            b.configure(bg=th["panel"], fg=th["fg"], padx=8, pady=2,
                        activebackground=th["accent"],
                        activeforeground="#000000")
        self._restyle_pin()
        self._refresh(full=True)

    # -- data plumbing --------------------------------------------------------
    def tick(self, tracker, db, baseline):
        self._ctx = (tracker, db, baseline)
        self._push_marks(tracker)
        n = self.state.confirmed_count()
        if n > self._conf_n and self.visible():
            self._flash("hand-in observed -- quest confirmed on EQL ✔")
        self._conf_n = n
        if not self.visible():
            return
        if self.state.rev != self._drawn_rev:
            self._refresh()
        # the status line: short-lived notices win, then the PERSISTENT
        # guide readout -- it stays up for the whole trip and re-ranges
        # as the player moves ('Guide'/'Hand-in' again toggles it off)
        if time.time() >= self._flash_until:
            gs = self._guide_status(tracker)
            cur = self.status.cget("text")
            if (" " + gs if gs else "") != cur:
                self.status.config(text=" " + gs if gs else "")

    def _push_marks(self, tracker):
        """Keep the map's quest layer in sync with the tracked quest:
        missing-item marks plus the hand-in NPC pin."""
        need = self.state.outstanding_items(self.qdb)
        if getattr(tracker, "quest_need", None) != need:
            tracker.quest_need = need
            tracker.quest_marks = {n.lower() for n in need}
        npc = self.state.handin_target(self.qdb)
        if getattr(tracker, "quest_npc", None) != npc:
            tracker.quest_npc = npc

    # -- search ---------------------------------------------------------------
    def _on_search_typed(self, *_):
        term = self.search_var.get().strip()
        if len(term) < 2:
            if self._mode == "search":
                self._mode = "list"
                self._refresh(full=True)
            return
        if not self.qdb.ok:
            return
        self._mode = "search"
        self._results = self.qdb.search(term, self.era_allowed())
        self._refresh(full=True)

    def _search_off(self):
        self.search_var.set("")
        self._mode = "list"
        self._refresh(full=True)

    def _on_search_enter(self, _e):
        if self._mode == "search" and self._results:
            sel = self.qlist.curselection()
            self._add_result(sel[0] if sel else 0)

    def _add_result(self, idx):
        if 0 <= idx < len(self._results):
            q = self._results[idx]
            if self.state.add(q["qid"]):
                self._flash(f"added: {q['name']}")
            else:
                self._flash("already on your list")
            self._sel_qid = q["qid"]
            self._refresh(full=True)

    # -- selection ------------------------------------------------------------
    def _picked_qid(self):
        sel = self.qlist.curselection()
        if not sel:
            return None
        i = sel[0]
        if self._mode == "search":
            return self._results[i]["qid"] if i < len(self._results) else None
        qids = self.state.qids()
        return qids[i] if i < len(qids) else None

    def _on_pick(self, _e):
        qid = self._picked_qid()
        if qid and qid != self._sel_qid:
            self._sel_qid = qid
            self._sel_item = None
            self._draw_detail()

    def _on_double(self, e):
        if self._mode == "search":
            self._add_result(self.qlist.nearest(e.y))
        else:
            self._do_track()

    def _on_detail_pick(self, _e):
        sel = self.detail.curselection()
        if not sel:
            return
        payload = (self._rowmap[sel[0]] if sel[0] < len(self._rowmap)
                   else None)
        if payload and payload[0] == "item":
            self._sel_item = payload[1]

    # -- buttons --------------------------------------------------------------
    def _flash(self, text):
        """Show a notice for a few seconds; afterwards the status line
        falls back to the live guide readout (see tick)."""
        self._flash_until = time.time() + 4
        self.status.config(text=" " + text)

    def _guide_status(self, tracker):
        """The live guide readout: distance to the target while you close
        in, the zone route while you travel, re-derived every tick so it
        follows the player. Empty when no guide is active."""
        g = getattr(tracker, "guide", None)
        if not g:
            return ""
        if g.get("target") and g.get("zone") == tracker.zone_short:
            if tracker.loc and time.time() - tracker.loc[3] <= 120:
                d = ((tracker.loc[0] - g["target"][0]) ** 2
                     + (tracker.loc[1] - g["target"][1]) ** 2) ** 0.5
                s = f"guide: {g['label']}  {d:.0f} away"
            else:
                s = f"guide: {g['label']} marked on map -- /loc to range"
            who = g.get("who")
            if who:
                s += "  <- " + ", ".join(who[:2])
            return s
        if g.get("route"):
            return "guide: " + " > ".join(g["route"])
        return f"guide: {g['label']} -- no route from here"

    def _do_add(self):
        if self._mode == "search":
            sel = self.qlist.curselection()
            self._add_result(sel[0] if sel else 0)
        else:
            self._flash("search for a quest first, then Add")

    def _do_track(self):
        qid = self._picked_qid() or self._sel_qid
        if not qid:
            self._flash("select a quest to track")
            return
        if not self.state.on_list(qid):
            self.state.add(qid)
        if self.state.current() == qid:
            self.state.set_current(None)     # toggle off
            self._flash("tracking stopped")
        else:
            self.state.set_current(qid)
            q = self.qdb.get(qid)
            self._flash(f"tracking: {q['name'] if q else qid}")
        if self._ctx:
            self._push_marks(self._ctx[0])
        self._refresh(full=True)

    def _bump(self, delta):
        qid = self._sel_qid
        if not qid or not self.state.on_list(qid):
            self._flash("select one of MY QUESTS first")
            return
        q = self.qdb.get(qid)
        if not q or not q["req"]:
            return
        iid = self._sel_item
        if iid is None or iid not in {i for i, _ in q["req"]}:
            if len(q["req"]) == 1:
                iid = q["req"][0][0]
            else:
                self._flash("click a required item row, then +/-")
                return
        self.state.set_have(qid, iid, self.state.have(qid, iid) + delta)
        self._sel_item = iid
        if self._ctx:
            self._push_marks(self._ctx[0])
        self._refresh()

    def _toggle_guide(self, name):
        """Start guiding to `name`, or STOP if that's already the active
        guide -- the buttons are toggles, and the status line keeps the
        live readout up for the whole trip."""
        tracker = self._ctx[0]
        g = getattr(tracker, "guide", None)
        if g and g.get("label", "").lower() == name.lower():
            tracker.run_local_command("guide off")
            self._flash(f"guide stopped ({name})")
            return
        tracker.run_local_command(f"guide {name}")
        if not getattr(tracker, "guide", None):
            # guide refused (nothing known / no route) -- surface why
            reply = tracker.cmd_last[1] if tracker.cmd_last else []
            self._flash(reply[0] if reply else f"no route to {name}")
        else:
            self._flash_until = 0.0      # show the readout immediately

    def _do_guide(self):
        """Toggle guiding to the selected required item."""
        if not self._ctx:
            return
        qid = self._sel_qid
        q = self.qdb.get(qid) if qid else None
        if not q or not q["req"]:
            self._flash("select a quest with required items")
            return
        iid = self._sel_item
        if iid is None or iid not in {i for i, _ in q["req"]}:
            # default to the first still-missing item
            iid = next((i for i, n in q["req"]
                        if self.state.have(qid, i) < n), q["req"][0][0])
        self._toggle_guide(self.qdb.item_name(iid))

    def _do_handin(self):
        """Toggle guiding to the selected quest's hand-in NPC (guide
        understands NPC names, not just items)."""
        if not self._ctx:
            return
        qid = self._picked_qid() or self._sel_qid
        q = self.qdb.get(qid) if qid else None
        if not q:
            self._flash("select a quest first")
            return
        self._toggle_guide(q["n"])

    def _do_remove(self):
        qid = self._picked_qid() or self._sel_qid
        if not qid or not self.state.on_list(qid):
            self._flash("select one of MY QUESTS first")
            return
        q = self.qdb.get(qid)
        self.state.remove(qid)
        if self._sel_qid == qid:
            self._sel_qid = None
        if self._ctx:
            self._push_marks(self._ctx[0])
        self._flash(f"removed: {q['name'] if q else qid}")
        self._refresh(full=True)

    def _do_clear(self):
        """Quest Clear: drop only the TRACKED quest, keep the list."""
        cur = self.state.current()
        if not cur:
            self._flash("no tracked quest (Track ▶ one first)")
            return
        q = self.qdb.get(cur)
        self.state.clear_current()
        if self._sel_qid == cur:
            self._sel_qid = None
        if self._ctx:
            self._push_marks(self._ctx[0])
        self._flash(f"cleared: {q['name'] if q else cur}")
        self._refresh(full=True)

    # -- rendering ------------------------------------------------------------
    def _wrap(self, text, indent="  "):
        """Wrap to the detail listbox's pixel width."""
        max_px = max(self.detail.winfo_width() - 24, 180)
        if self.mono.measure(text) <= max_px:
            return [text]
        out, cur = [], ""
        for word in text.split(" "):
            trial = f"{cur} {word}".strip()
            if cur and self.mono.measure(trial) > max_px:
                out.append(cur)
                cur = indent + word
            else:
                cur = trial
        out.append(cur)
        return out

    def _refresh(self, full=False):
        self._drawn_rev = self.state.rev
        if full or self._mode == "list":
            self._draw_qlist()
        self._draw_detail()

    def _draw_qlist(self):
        th = self._th
        self.qlist.delete(0, "end")
        if self._mode == "search":
            self.list_hdr.config(
                text=f" SEARCH -- {len(self._results)} match(es), "
                     f"Enter/double-click adds")
            for q in self._results:
                on = self.state.on_list(q["qid"])
                conf = self.state.is_confirmed(q["qid"])
                zone = self.qdb.zones.get(q["z"], q["z"])
                mark = "✓ " if on else "  "
                badge = "  ✔EQL" if conf else ""
                self.qlist.insert("end", f"{mark}{q['name']} -- {q['n']} "
                                         f"[{zone}]{badge}")
                self.qlist.itemconfig(
                    "end", fg=th["alt"] if on
                    else th["accent"] if conf else th["fg"])
            if not self._results:
                hint = ("quest DB missing -- run eql_quest_db_build.py"
                        if not self.qdb.ok else
                        "no matches in the enabled expansions")
                self.qlist.insert("end", f"  {hint}")
                self.qlist.itemconfig("end", fg=th["dim"])
            return
        self.list_hdr.config(text=" MY QUESTS")
        qids = self.state.qids()
        cur = self.state.current()
        for qid in qids:
            q = self.qdb.get(qid)
            if not q:
                self.qlist.insert("end", f"?  {qid.split('|')[1]} "
                                         f"(not in this quest DB)")
                self.qlist.itemconfig("end", fg=th["dim"])
                continue
            got, need = self.state.progress(self.qdb, qid)
            done = self.state.complete(self.qdb, qid)
            lead = "▶" if qid == cur else " "
            tick = "✓" if done else f"{got}/{need}" if need else "--"
            self.qlist.insert("end", f"{lead} [{tick}] {q['name']} "
                                     f"-- {q['n']}")
            self.qlist.itemconfig(
                "end", fg=th["alt"] if done
                else th["accent"] if qid == cur else th["fg"])
        if not qids:
            self.qlist.insert("end", "  nothing yet -- search a quest, "
                                     "item, NPC, or zone above")
            self.qlist.itemconfig("end", fg=th["dim"])
        # keep the selection visible on redraws
        if self._sel_qid in qids:
            i = qids.index(self._sel_qid)
            self.qlist.selection_clear(0, "end")
            self.qlist.selection_set(i)
            self.qlist.see(i)

    def _sources_for(self, iid, name):
        """Where a required item can be found: your own Atlas observations
        first (ground truth), then the baseline's best drop rates --
        era-gated like everything else."""
        rows = []
        tracker = db = baseline = None
        if self._ctx:
            tracker, db, baseline = self._ctx
        low = name.lower()
        if db:
            mine = {}
            for zshort, z in db.data["zones"].items():
                for m in z["mobs"].values():
                    for item, d in m["drops"].items():
                        if item.lower() == low:
                            mine[zshort] = mine.get(zshort, 0) + d["count"]
            for zshort, n in sorted(mine.items(), key=lambda kv: -kv[1]):
                rows.append((f"you: {zshort} x{n}", "alt"))
        if baseline and baseline.ok and len(rows) < SOURCES_MAX:
            allowed = self.era_allowed()
            bh = []
            for zshort, zn in baseline.npcs.items():
                if baseline.zone_era(zshort) > allowed:
                    continue
                for rec in zn.values():
                    for bid, pct in rec["loot"]:
                        bn = baseline.item_name(bid)
                        if ((bid == iid)
                                or (bn and bn.lower() == low)):
                            bh.append((pct, zshort, rec["name"]))
            bh.sort(key=lambda h: -h[0])
            for pct, zshort, mob in bh[:SOURCES_MAX - len(rows)]:
                rows.append((f"map: {mob}, {zshort} {pct:g}%", "warn"))
        if not rows:
            rows.append(("no known drop source (vendor/forage/ground?)",
                         "dim"))
        return rows[:SOURCES_MAX]

    def _draw_detail(self):
        th = self._th
        self.detail.delete(0, "end")
        self._rowmap = []
        qid = self._sel_qid
        q = self.qdb.get(qid) if qid else None

        def row(text, role, payload=None, wrap=True):
            segs = self._wrap(text) if wrap else [text]
            for seg in segs:
                self.detail.insert("end", seg)
                self.detail.itemconfig("end", fg=th.get(role, th["fg"]))
                self._rowmap.append(payload)

        if not q:
            if not self.qdb.ok:
                row("quest DB missing", "warn")
                row("run eql_quest_db_build.py (see its header) and put "
                    "eql_quest_db.json.gz next to the Atlas", "dim")
            else:
                row("select a quest above to see its details", "dim")
                n = len([1 for x in self.qdb.quests
                         if x["era"] <= self.era_allowed()])
                row(f"{n} quests known in the enabled expansions", "dim")
            return
        on_list = self.state.on_list(qid)
        cur = self.state.current() == qid
        row(q["name"] + ("   ◀ tracking" if cur else ""), "accent")
        zone = self.qdb.zones.get(q["z"], q["z"])
        row(f"hand in to: {q['n']} -- {zone}", "fg")
        if self.state.is_confirmed(qid):
            row("✔ confirmed on EQL -- this hand-in has been seen "
                "in your log", "alt")
        else:
            row("availability unconfirmed (Quarm data; EQL may differ)",
                "dim")
        if q["coin"]:
            row(f"donation: {fmt_coin(q['coin'])}", "fg")
        if q["req"]:
            row("required:", "dim")
            for iid, need in q["req"]:
                name = self.qdb.item_name(iid)
                have = self.state.have(qid, iid) if on_list else 0
                done = have >= need
                sel = "» " if iid == self._sel_item else "  "
                row(f"{sel}[{have}/{need}] {name}",
                    "alt" if done else "fg", payload=("item", iid),
                    wrap=False)
                if not done:
                    for text, role in self._sources_for(iid, name):
                        row(f"      {text}", role)
        if q["ri"]:
            names = ", ".join(self.qdb.item_name(i) for i in q["ri"])
            row(f"reward: {names}", "warn")
        for grp in q["rc"]:
            names = ", ".join(self.qdb.item_name(i) for i in grp)
            row(f"reward, one of: {names}", "warn")
        if q.get("txt"):
            row(f'"{q["txt"]}"', "dim")
        if not on_list:
            row("(not on your list -- Add it to start tracking)", "dim")
        # restore item-row selection
        if self._sel_item is not None:
            for i, p in enumerate(self._rowmap):
                if p and p[0] == "item" and p[1] == self._sel_item:
                    self.detail.selection_clear(0, "end")
                    self.detail.selection_set(i)
                    break
