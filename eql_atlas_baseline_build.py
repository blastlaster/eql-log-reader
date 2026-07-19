#!/usr/bin/env python3
"""
EQL Atlas -- Baseline Builder (dev tool)
=========================================
One-time distiller: reads a Project Quarm database dump (quarm_*.sql from
github.com/SecretsOTheP/EQMacEmu, utils/sql/database_full) and boils the
tables the Atlas overlay cares about down into one compact, gzipped JSON --
the "pre-discovered" baseline layer: which mobs spawn where (with spawn
chance and respawn timers), what they drop (with the server's actual drop
percentages), and what coin they carry.

This is NOT one of the runnable overlays -- it's a build step, re-run only
when a new dump version is released:

    python eql_atlas_baseline_build.py <path-to-quarm.sql>

Output: eql_atlas_baseline.json.gz next to the scripts (shipped with the
suite; eql_atlas.py loads it read-only at startup).

EQL runs on the same EQMacEmu lineage and keeps Quarm's item ID space for
classic items, so this baseline is "probably right until observed
otherwise" -- the Atlas overlay's whole job is corroborating it against
what actually drops in EQL. Credit: Project Quarm / TAKP (EQMacEmu) team;
browseable at pqdi.cc.

Parsing note: the dump is MySQL DDL + multi-megabyte single-line INSERTs.
No MySQL needed -- CREATE TABLE blocks give us column order, and a small
state machine walks the INSERT tuples (backslash escapes, doubled quotes,
NULLs). Only the tables we use are parsed; the rest (spells etc.) are
skipped entirely.
"""

import gzip
import json
import os
import re
import sys
import time

TABLES = ("zone", "items", "npc_types", "spawn2", "spawngroup", "spawnentry",
          "loottable", "loottable_entries", "lootdrop", "lootdrop_entries",
          "zone_points", "doors")

OUT_NAME = "eql_atlas_baseline.json.gz"

_ESC = {"0": "\0", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a",
        "'": "'", '"': '"', "\\": "\\", "%": "%", "_": "_"}


def table_columns(sql, table):
    """Column names, in order, from the CREATE TABLE block."""
    m = re.search(r"CREATE TABLE `%s` \((.*?)\n\)" % re.escape(table), sql, re.S)
    if not m:
        raise SystemExit(f"table `{table}` not found in dump")
    cols = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if line.startswith("`"):
            cols.append(line.split("`")[1])
    return cols


def _num(tok):
    try:
        return int(tok)
    except ValueError:
        try:
            return float(tok)
        except ValueError:
            return tok


def parse_tuples(sql, i, rows):
    """Parse `(v,v,...),(...),...;` starting at index i; append to rows.
    Returns the index just past the terminating semicolon."""
    n = len(sql)
    while True:
        while i < n and sql[i] in ",\n\r\t ":
            i += 1
        if i >= n or sql[i] == ";":
            return i + 1
        i += 1                                    # consume '('
        row = []
        while True:
            c = sql[i]
            if c == "'":
                i += 1
                parts = []
                while True:
                    c = sql[i]
                    if c == "\\":
                        parts.append(_ESC.get(sql[i + 1], sql[i + 1]))
                        i += 2
                    elif c == "'":
                        if sql[i + 1] == "'":
                            parts.append("'")
                            i += 2
                        else:
                            i += 1
                            break
                    else:                          # bulk-copy plain span
                        j = i
                        while sql[j] != "\\" and sql[j] != "'":
                            j += 1
                        parts.append(sql[i:j])
                        i = j
                row.append("".join(parts))
            else:
                j = i
                while sql[j] != "," and sql[j] != ")":
                    j += 1
                tok = sql[i:j]
                row.append(None if tok == "NULL" else _num(tok))
                i = j
            if sql[i] == ",":
                i += 1
            else:                                  # ')'
                i += 1
                break
        rows.append(row)


def read_table(sql, table):
    """All rows of `table` as list of dicts (column name -> value)."""
    cols = table_columns(sql, table)
    marker = f"INSERT INTO `{table}` VALUES"   # whitespace/newline follows; the
                                               # tuple parser skips it itself
    rows = []
    pos = 0
    while True:
        pos = sql.find(marker, pos)
        if pos < 0:
            break
        pos = parse_tuples(sql, pos + len(marker), rows)
    bad = [r for r in rows if len(r) != len(cols)]
    if bad:
        raise SystemExit(f"`{table}`: {len(bad)} rows with wrong arity "
                         f"(expected {len(cols)} cols) -- parser bug?")
    return [dict(zip(cols, r)) for r in rows]


def clean_npc_name(raw):
    """DB name -> the display name the log uses: a_gnoll_watcher -> 'a gnoll
    watcher', #Gynok_Moltor -> 'Gynok Moltor'. Trailing digit variants
    (orc_pawn00 style) lose the digits too."""
    name = raw.lstrip("#").replace("_", " ").strip()
    return re.sub(r"\d+$", "", name).strip()


def is_named(display_name):
    """Advisory 'rare/named' flag: a proper name -- no leading article AND
    capitalized. Plenty of commons ship article-less lowercase names
    ('ice boned skeleton'); without the capitalization test they flood
    the map with fake named pins. (Corroborated in-game by the Atlas
    overlay, never trusted.)"""
    low = display_name.lower()
    if low.startswith(("a ", "an ", "the ")):
        return False
    return display_name[:1].isupper()


def build(dump_path, out_path):
    t0 = time.time()
    print(f"reading {dump_path} ...")
    with open(dump_path, "r", encoding="utf-8", errors="replace") as f:
        sql = f.read()

    t = {}
    for name in TABLES:
        t[name] = read_table(sql, name)
        print(f"  {name}: {len(t[name])} rows")
    del sql

    # -- zones -------------------------------------------------------------
    zones = {}
    for z in t["zone"]:
        short = (z["short_name"] or "").strip().lower()
        if short:
            zones[short] = {"long": z["long_name"],
                            "era": int(z.get("expansion") or 0)}

    # zone connections (which zones border which -- for cross-zone "find"
    # and the guide's routing). zone_points covers walk-over borders;
    # DOORS cover the rest (clickable zone doors like Befallen's entrance
    # live in the doors table, not zone_points) -- merge both, both ways
    # (a passable connection is passable in either direction).
    conns = {}
    id_to_short = {z["id"]: (z["short_name"] or "").lower() for z in t["zone"]}
    for zp in t["zone_points"]:
        src = (zp["zone"] or "").lower()
        tgt = id_to_short.get(zp.get("target_zone_id") or 0, "")
        if src and tgt and src != tgt:
            conns.setdefault(src, set()).add(tgt)
            conns.setdefault(tgt, set()).add(src)
    for d in t["doors"]:
        src = (d.get("zone") or "").lower()
        tgt = (d.get("dest_zone") or "").lower()
        if src and tgt and src != tgt and tgt != "none":
            conns.setdefault(src, set()).add(tgt)
            conns.setdefault(tgt, set()).add(src)
    for short, adj in conns.items():
        if short in zones:
            zones[short]["adj"] = sorted(s for s in adj if s in zones)

    # -- items (id -> name; the collector builds the reverse map) ----------
    items = {str(it["id"]): it["Name"] for it in t["items"]}

    # -- loot: loottable_id -> [(item_id, eff_pct)], plus cash range -------
    # EQEmu roll: each loottable entry runs `multiplier` times; each run
    # succeeds with `probability`%; a success picks ONE lootdrop entry
    # weighted by its `chance` (of 100). Effective per-kill rate for an
    # item is therefore probability% * chance% * multiplier (capped 100).
    drops_by_lootdrop = {}
    for e in t["lootdrop_entries"]:
        drops_by_lootdrop.setdefault(e["lootdrop_id"], []).append(e)
    table_entries = {}
    for e in t["loottable_entries"]:
        table_entries.setdefault(e["loottable_id"], []).append(e)
    cash = {lt["id"]: (lt["mincash"], lt["maxcash"]) for lt in t["loottable"]}

    loot_of = {}
    for lt_id, entries in table_entries.items():
        acc = {}
        for te in entries:
            prob = (te["probability"] or 0) / 100.0
            mult = te["multiplier"] or 1
            for de in drops_by_lootdrop.get(te["lootdrop_id"], ()):
                eff = min(100.0, prob * (de["chance"] or 0) * mult)
                if eff <= 0:
                    continue
                iid = de["item_id"]
                acc[iid] = min(100.0, acc.get(iid, 0.0) + eff)
        if acc:
            loot_of[lt_id] = sorted(
                ([iid, round(pct, 2)] for iid, pct in acc.items()),
                key=lambda p: -p[1])

    # -- spawns: zone -> npc -> points -------------------------------------
    groups_points = {}                    # spawngroupID -> [(zone, x,y,z, respawn)]
    for s in t["spawn2"]:
        if not s["enabled"]:
            continue
        zone = (s["zone"] or "").lower()
        if not zone:
            continue
        groups_points.setdefault(s["spawngroupID"], []).append(
            (zone, round(s["x"], 1), round(s["y"], 1), round(s["z"], 1),
             s["respawntime"]))
    group_npcs = {}                       # spawngroupID -> [(npcID, chance)]
    for e in t["spawnentry"]:
        group_npcs.setdefault(e["spawngroupID"], []).append(
            (e["npcID"], e["chance"]))

    npc_by_id = {n["id"]: n for n in t["npc_types"]}

    npcs = {}                             # zone -> lowername -> record
    for gid, points in groups_points.items():
        for npc_id, chance in group_npcs.get(gid, ()):
            n = npc_by_id.get(npc_id)
            if n is None:
                continue
            disp = clean_npc_name(n["name"])
            if not disp:
                continue
            key = disp.lower()
            for (zone, x, y, z, respawn) in points:
                zrec = npcs.setdefault(zone, {})
                rec = zrec.get(key)
                if rec is None:
                    rec = zrec[key] = {"name": disp,
                                       "level": [n["level"], n["level"]],
                                       "named": 1 if is_named(disp) else 0,
                                       "spawns": [], "loot": {}, "cash": [0, 0]}
                rec["level"][0] = min(rec["level"][0], n["level"])
                rec["level"][1] = max(rec["level"][1], n["level"])
                rec["spawns"].append([x, y, z, chance, respawn])
                # same display name can cover several npc_type variants --
                # merge loot keeping the highest variant's rate (the log
                # can't tell variants apart, so this is the honest ceiling)
                for iid, pct in loot_of.get(n["loottable_id"], ()):
                    if pct > rec["loot"].get(iid, 0.0):
                        rec["loot"][iid] = pct
                lo, hi = cash.get(n["loottable_id"], (0, 0))
                rec["cash"][0] = max(rec["cash"][0], lo)
                rec["cash"][1] = max(rec["cash"][1], hi)

    for zrec in npcs.values():
        for rec in zrec.values():
            rec["loot"] = sorted(([iid, pct] for iid, pct in rec["loot"].items()),
                                 key=lambda p: -p[1])

    # only keep item names that something can actually drop (halves the size;
    # the collector falls back to +N-stripped name matching for the rest)
    dropped_ids = {str(iid) for zrec in npcs.values()
                   for rec in zrec.values() for iid, _ in rec["loot"]}
    items = {iid: name for iid, name in items.items() if iid in dropped_ids}

    out = {
        "format": 1,
        "source": os.path.basename(dump_path),
        "built": time.strftime("%Y-%m-%d"),
        "credit": ("Baseline data: Project Quarm / TAKP (EQMacEmu) database "
                   "-- github.com/SecretsOTheP/EQMacEmu -- browseable at "
                   "pqdi.cc. Corroborated in-game by EQL Log Reader."),
        "zones": zones,
        "items": items,
        "npcs": npcs,
    }
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))

    n_npcs = sum(len(z) for z in npcs.values())
    print(f"\nwrote {out_path}")
    print(f"  zones: {len(zones)}  npc entries: {n_npcs}  "
          f"dropped items: {len(items)}")
    print(f"  size: {os.path.getsize(out_path):,} bytes  "
          f"({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python eql_atlas_baseline_build.py <quarm_dump.sql>")
    here = os.path.dirname(os.path.abspath(__file__))
    build(sys.argv[1], os.path.join(here, OUT_NAME))
