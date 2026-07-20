#!/usr/bin/env python3
"""
EQL Atlas -- Quest DB Builder (dev tool)
=========================================
One-time distiller for the Atlas Quest window: walks a checkout of the
Project Quarm quest scripts (github.com/SecretsOTheP/quests -- the same
EQMacEmu lineage as EQL) and boils every item turn-in it can statically
recognize down into one compact, gzipped JSON.

A "quest" here is one successful `check_turn_in` branch in an NPC's Lua
script: WHO you hand items to, WHERE they stand, WHAT they want (item ids +
counts, or coin), what they SAY when you succeed, and what they hand back
(QuestReward / SummonItem items, including choose-one random rewards).
Quest scripts have no display names, so the Quest window synthesizes one
from the reward/turn-in items -- searchable by NPC, zone, and item name.

Item and zone names come from a Quarm database dump (quarm_*.sql from
github.com/SecretsOTheP/EQMacEmu, utils/sql/database_full -- the same file
eql_atlas_baseline_build.py consumes), because the quest DB must know
names the loot baseline prunes: the log reports loot by NAME, and quest
auto-tracking matches those names against required-item ids. The dump's
zone table also supplies each quest's expansion era, so the Quest window
can honor the Atlas expansion locks.

This is NOT one of the runnable overlays -- it's a build step, re-run only
when the quest scripts or dump meaningfully change:

    python eql_quest_db_build.py <quests-repo-dir> <quarm_dump.sql>

Output: eql_quest_db.json.gz next to the scripts (shipped with the suite;
eql_quest.py loads it read-only at startup).

Parsing is deliberately static and conservative: only literal
`{item1 = 1234, ...}` tables count. Branches gated on variables, custom
wrapper functions, or computed ids are skipped and tallied in the summary
-- better to miss a quest than to invent one.
"""

import gzip
import json
import os
import re
import sys
import time

from eql_atlas_baseline_build import read_table

OUT_NAME = "eql_quest_db.json.gz"

# check_turn_in(e.self, e.trade, {  ...covers the (self, trade, ... and
# (npc, trade, ... spellings too; the item_lib-first custom wrappers are
# intentionally NOT matched (their tables aren't literal anyway).
RE_TURNIN = re.compile(
    r"check_turn_in\(\s*(?:e\.self|self|npc)\s*,\s*(?:e\.trade|trade)\s*,"
    r"\s*\{([^{}]*)\}")
RE_PAIR = re.compile(r"(\w+)\s*=\s*(\d+)\b")
RE_NONLIT = re.compile(r"(\w+)\s*=\s*(?![\s\d])")  # item1 = some_variable
RE_ITEMKEY = re.compile(r"^item\d+$")
RE_SAY = re.compile(r':(?:Say|Emote)\(\s*"((?:[^"\\]|\\.)*)"')
RE_SUMMON = re.compile(r"SummonItem\(\s*(\d+)")
RE_CHOOSE = re.compile(r"ChooseRandom\(([\d\s,]+)\)")
RE_INT = re.compile(r"\d+")

COIN_MULT = {"copper": 1, "silver": 10, "gold": 100, "platinum": 1000}

SKIP_FILES = {"player.lua", "script_init.lua", "global_player.lua"}


def npc_display(fname):
    """Beek_Guinders.lua -> 'Beek Guinders'; #Lord_Inquisitor.lua ->
    'Lord Inquisitor'; trailing script-variant digits are dropped the same
    way the loot baseline cleans npc_types names."""
    name = os.path.splitext(fname)[0].lstrip("#").replace("_", " ").strip()
    return re.sub(r"\d+$", "", name).strip()


def call_span(text, start):
    """Given `start` at the '(' of a call, return the index just past its
    matching ')'; string-aware enough for these scripts (quotes inside the
    argument list are rare and never contain unbalanced parens)."""
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def rewards_in(span):
    """Reward item ids inside one successful-turn-in branch.
    Returns (definite ids, choose-one groups)."""
    items, groups = [], []
    for m in re.finditer(r"QuestReward\s*(\()", span):
        args = span[m.end(1):call_span(span, m.start(1)) - 1]
        body = args.split(",", 1)[1] if "," in args else ""
        for cm in RE_CHOOSE.finditer(body):
            grp = [int(n) for n in RE_INT.findall(cm.group(1))]
            if grp:
                groups.append(sorted(set(grp)))
        body = RE_CHOOSE.sub("", body)
        b = body.lstrip()
        if b.startswith("{"):
            im = re.search(r"items\s*=\s*\{([\d\s,]*)\}", body)
            if im:
                items += [int(n) for n in RE_INT.findall(im.group(1))]
            im = re.search(r"itemid\s*=\s*(\d+)", body)
            if im:
                items.append(int(im.group(1)))
        else:
            # positional: copper, silver, gold, platinum, itemid[, exp]
            # math.random(...) coin args were stripped with ChooseRandom
            # above only if random -- split shallowly instead
            parts = []
            depth = 0
            cur = ""
            for c in body:
                if c == "," and depth == 0:
                    parts.append(cur)
                    cur = ""
                    continue
                depth += c in "({["
                depth -= c in ")}]"
                cur += c
            parts.append(cur)
            if len(parts) >= 5:
                tok = parts[4].strip()
                if tok.isdigit() and int(tok) > 0:
                    items.append(int(tok))
    for m in re.finditer(r"SummonItem\s*(\()", span):
        args = span[m.end(1):call_span(span, m.start(1)) - 1]
        cm = RE_CHOOSE.search(args)
        if cm:
            grp = sorted({int(n) for n in RE_INT.findall(cm.group(1))})
            if grp:
                groups.append(grp)
        else:
            im = RE_INT.search(args)
            if im:
                items.append(int(im.group(0)))
    return sorted(set(items)), groups


def say_in(span):
    m = RE_SAY.search(span)
    if not m:
        return ""
    txt = m.group(1).replace('\\"', '"').replace("\\'", "'")
    # dialogue is concatenated around runtime bits (" .. e.other:Race() ..
    # ") -- the first literal segment reads fine on its own
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:220]


def parse_file(path):
    """All statically-recognizable turn-in branches in one NPC script.
    Returns (quests, n_skipped)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return [], 0
    hits = list(RE_TURNIN.finditer(text))
    out, skipped = [], 0
    for i, m in enumerate(hits):
        body = m.group(1)
        req = {}
        coin = 0
        for key, val in RE_PAIR.findall(body):
            if RE_ITEMKEY.match(key):
                iid = int(val)
                if iid > 0:
                    req[iid] = req.get(iid, 0) + 1
            elif key in COIN_MULT:
                coin += int(val) * COIN_MULT[key]
        if RE_NONLIT.search(body) or not (req or coin):
            skipped += 1
            continue
        span = text[m.end():hits[i + 1].start() if i + 1 < len(hits)
                    else len(text)]
        ri, rc = rewards_in(span)
        out.append({"req": sorted(req.items()), "coin": coin,
                    "ri": ri, "rc": rc, "txt": say_in(span)})
    return out, skipped


def build(quests_dir, dump_path, out_path):
    t0 = time.time()
    print(f"reading {dump_path} ...")
    with open(dump_path, "r", encoding="utf-8", errors="replace") as f:
        sql = f.read()
    items_all = {it["id"]: it["Name"] for it in read_table(sql, "items")}
    zones_all = {}
    for z in read_table(sql, "zone"):
        short = (z["short_name"] or "").strip().lower()
        if short and short not in zones_all:
            zones_all[short] = (z["long_name"],
                                int(z.get("expansion") or 0))
    del sql
    print(f"  items: {len(items_all)}  zones: {len(zones_all)}")

    quests = []
    n_files = n_skipped = 0
    unera = set()
    for zone in sorted(os.listdir(quests_dir)):
        zdir = os.path.join(quests_dir, zone)
        if not os.path.isdir(zdir) or zone in ("global", ".git"):
            continue
        long_name, era = zones_all.get(zone.lower(), (None, None))
        if era is None:
            era = 99                      # unknown zone: custom/late content
            unera.add(zone)
        for fname in sorted(os.listdir(zdir)):
            if not fname.endswith(".lua") or fname in SKIP_FILES:
                continue
            found, skipped = parse_file(os.path.join(zdir, fname))
            n_skipped += skipped
            if not found:
                continue
            n_files += 1
            npc = npc_display(fname)
            for q in found:
                q["z"] = zone.lower()
                q["n"] = npc
                q["era"] = era
                quests.append(q)

    # ship names for every id any quest touches (required + rewards) --
    # the loot baseline prunes to droppable ids, which quest items
    # (notes, dropped-off deliveries) often aren't
    used = set()
    for q in quests:
        used.update(iid for iid, _ in q["req"])
        used.update(q["ri"])
        for grp in q["rc"]:
            used.update(grp)
    items = {str(i): items_all[i] for i in sorted(used) if i in items_all}
    zones = {}
    for q in quests:
        z = q["z"]
        if z not in zones:
            long_name, era = zones_all.get(z, (None, None))
            zones[z] = long_name or z

    out = {
        "format": 1,
        "source": os.path.basename(dump_path),
        "built": time.strftime("%Y-%m-%d"),
        "credit": ("Quest data: Project Quarm quest scripts -- "
                   "github.com/SecretsOTheP/quests -- and the Quarm/TAKP "
                   "(EQMacEmu) database. Availability on EQL is refined "
                   "in-game by EQL Log Reader."),
        "quests": quests,
        "items": items,
        "zones": zones,
    }
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))

    n_zones = len({q["z"] for q in quests})
    print(f"\nwrote {out_path}")
    print(f"  quests: {len(quests)} (from {n_files} NPC scripts, "
          f"{n_zones} zones); {n_skipped} non-literal branches skipped")
    if unera:
        print(f"  zones with unknown era (gated behind all locks): "
              f"{len(unera)}")
    print(f"  item names: {len(items)}  size: "
          f"{os.path.getsize(out_path):,} bytes  ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python eql_quest_db_build.py "
                         "<quests-repo-dir> <quarm_dump.sql>")
    here = os.path.dirname(os.path.abspath(__file__))
    build(sys.argv[1], sys.argv[2], os.path.join(here, OUT_NAME))
