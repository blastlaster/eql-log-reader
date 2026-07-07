#!/usr/bin/env python3
"""
EQL Spell DB
=============
Looks up spell facts (mana cost, cast time, recast time, range/AoE radius,
per-class availability, and per-effect magnitude estimates) from the game's
own `spells_us.txt`, by name or id.

Where this data comes from
-----------------------------
This module originally only trusted the first ~15 fields of `spells_us.txt`
(name/mana/cast-time/recast-time/range) because the field layout wasn't
independently confirmed past that point, and a wrong guess at an effect
magnitude field would silently produce a confidently-wrong number.

That's no longer true for everything below: the community project
"EQL Spell Explorer" (github.com/Amerzel/eql-info, browsable at
amerzel.github.io/eql-info) reverse-engineered the *entire* field layout by
statistically diffing EQL's `spells_us.txt` against the modern Live
EverQuest client's copy of the same file (which has public documentation).
Their `SPELL_FORMAT.md` documents:

  * EQL's `spells_us.txt` is 172 caret-delimited columns (0-171): columns
    0-169 are scalar fields (mostly identical to Live EQ's layout), column
    170 is an EQL-only "ritual_eligible" flag added by a 2026-05 patch, and
    the final column is a variable-length pipe-delimited "effects" blob
    (up to 41 effects per spell, each a 5-field group: effect_id/base_value/
    limit_value/formula/max_value).
  * The per-effect `effect_id` is a "SPA" (Spell Affect) number from EQ's
    public SPA enum -- e.g. SPA 0 = "HP" (the generic current-hitpoints
    effect used for direct heals, heal-over-time, direct damage, *and*
    damage-over-time, distinguished only by the sign of base_value).
  * Each effect's `formula` field says how `base_value` scales with caster
    level (and, for ticking effects, elapsed duration) into the actual
    applied number, clamped by `max_value`.

Since our earlier fields-0-14 mapping (id/name/range/aoe_range/cast_time/
recovery_time/recast_time/buff_duration_formula/buff_duration/ae_duration/
mana) matches Amerzel's independently-derived column map exactly, this
module now trusts their fuller layout too -- see CLASS_NAMES, IDX_* below,
and `parse_effects_blob()` / `estimate_effect_value()`.

**Important caveat, carried over honestly from the source project**: the
formula-to-value math below (`estimate_effect_value`) is EQEmu's *classic-era
reference implementation* (`spell_effects.cpp ::
Mob::CalcSpellEffectValue_formula`), not EQL's actual server code -- formula
application is server-side logic, not client data, so there's no way to
directly confirm EQL computes it identically. Treat any number this produces
as an **estimate**, not a guaranteed-exact value, especially for anything
beyond the common static/per-level cases. The EQEmu source itself flags
several formulas as unverified even for classic EQ. Confirmed spot-check:
Minor Healing (SPA 0, base=10, formula=2, max=20) estimates 12 at level 1
and 20 at level 5+ -- consistent with it being a small early heal that caps
out fast. Not every damage/heal-looking spell uses SPA 0, though -- e.g.
Chords of Dissonance's only effect is SPA 334, not SPA 0, so this technique
finds likely candidates, not a complete guarantee.

This estimation exists specifically to help with cases the combat log
can't show directly -- e.g. a Bard's passive regen/heal-over-time song
ticking on a group member produces no "healed X for N hit points" log line
at all, so there's nothing to parse; the only way to get *any* number for
that contribution is to compute what the spell's own data says it should do.
"""

import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Validated field indices (columns 0-51), cross-confirmed against
# github.com/Amerzel/eql-info's SPELL_FORMAT.md.
IDX_ID = 0
IDX_NAME = 1
IDX_RANGE = 4
IDX_AOE_RANGE = 5
IDX_CAST_TIME_MS = 8
IDX_RECOVERY_TIME_S = 9
IDX_RECAST_TIME_S = 10
IDX_BUFF_DURATION_FORMULA = 11
IDX_BUFF_DURATION = 12
IDX_AE_DURATION = 13
IDX_MANA = 14
IDX_GOOD_EFFECT = 28     # 0=detrimental, 1=beneficial, 2=beneficial group-only
IDX_RESIST_TYPE = 29
IDX_TARGET_TYPE = 30
IDX_CLASSES_START = 36   # 16 consecutive fields, one per class (min level;
                         # 255=unavailable to that class, 254=available/no min)
IDX_CLASSES_COUNT = 16
MIN_FIELDS = IDX_CLASSES_START + IDX_CLASSES_COUNT

# Standard classic-EverQuest class ordering used by the classes[16] array.
# (Inferred from the well-known EQ class-ID enumeration that SPELL_FORMAT.md's
# "WARRIORMIN...BERSERKERMIN" column comment lines up with -- EQL keeps the
# same 16 classes as classic/Live EQ, so this hasn't needed independent
# re-verification.)
CLASS_NAMES = [
    "Warrior", "Cleric", "Paladin", "Ranger", "ShadowKnight", "Druid",
    "Monk", "Bard", "Rogue", "Shaman", "Necromancer", "Wizard", "Magician",
    "Enchanter", "Beastlord", "Berserker",
]
BARD_CLASS_INDEX = CLASS_NAMES.index("Bard")

# A trimmed set of SPA (Spell Affect) names relevant to damage/healing
# analysis. Full list of ~500 is documented at github.com/Amerzel/eql-info
# (spa_data.py) if more are ever needed; these are the ones this toolkit
# actually reasons about today.
SPA_NAMES = {
    0: "HP",                 # generic current-HP effect: heals, HoTs, DDs, DoTs
    79: "InstantHp",
    100: "Healdot",          # heal-over-time
    101: "Completeheal",
    120: "Healmod",
    124: "FocusDamageMod",
    125: "FocusHealMod",
    147: "PercentHeal",
    273: "Dotcrit",
    274: "Healcrit",
    275: "Mendcrit",
    392: "FocusHealAmt",
    393: "FocusHealModBeneficial",
    394: "FocusHealAmtBeneficial",
    395: "FocusHealModCrit",
    396: "FocusHealAmtCrit",
}


def spa_name(effect_id):
    return SPA_NAMES.get(effect_id, f"SE #{effect_id}")


class Effect:
    __slots__ = ("effect_id", "base_value", "limit_value", "formula", "max_value")

    def __init__(self, effect_id, base_value, limit_value, formula, max_value):
        self.effect_id = effect_id
        self.base_value = base_value
        self.limit_value = limit_value
        self.formula = formula
        self.max_value = max_value

    def __repr__(self):
        return (f"Effect({spa_name(self.effect_id)}, base={self.base_value}, "
               f"formula={self.formula}, max={self.max_value})")


def parse_effects_blob(blob):
    """Parse the trailing pipe-delimited effects field:
    `1|<eff1>$2|<eff2>$3|...$N|<effN>` where each `<effK>` is 5 `|`-joined
    subfields (effect_id, base_value, limit_value, formula, max_value), and
    every subfield except the very last of the whole blob has a trailing
    `$N` marking the next effect's 1-based index (stripped here). Returns a
    list of Effect. Format confirmed via github.com/Amerzel/eql-info."""
    if not blob:
        return []
    parts = blob.split("|")
    if not parts or parts[0] != "1":
        return []
    cleaned = []
    for tok in parts[1:]:
        if "$" in tok:
            tok = tok.split("$", 1)[0]
        cleaned.append(tok)
    if len(cleaned) % 5 != 0:
        return []
    effects = []
    for i in range(0, len(cleaned), 5):
        try:
            effects.append(Effect(
                effect_id=int(cleaned[i]),
                base_value=int(cleaned[i + 1]),
                limit_value=int(cleaned[i + 2]),
                formula=int(cleaned[i + 3]),
                max_value=int(cleaned[i + 4]),
            ))
        except ValueError:
            continue
    return effects


def estimate_effect_value(effect, level):
    """Estimate the magnitude an effect applies at a given caster level.

    ESTIMATE ONLY -- see module docstring caveat. Implements the documented
    EQEmu classic-era formula table (SPELL_FORMAT.md, sourced from
    spell_effects.cpp). For decrementing/ticking effects (heal-over-time,
    damage-over-time that fades) this returns the *per-tick* magnitude, not
    a duration-multiplied total -- multiply by however many ticks you expect
    (buff_duration is in the same units the server ticks on, typically ~6s
    ticks in classic EQ, but that tick length isn't independently confirmed
    for EQL either) if you want a total.
    """
    lvl = max(int(level or 1), 1)
    base = effect.base_value
    max_v = effect.max_value
    f = effect.formula

    if f in (0, 100):
        result = base
    elif 1 <= f <= 99:
        result = base + lvl * f
    elif f == 101:
        result = base + lvl // 2
    elif f == 102:
        result = base + lvl
    elif f in (103, 104, 105):
        result = base + lvl * {103: 2, 104: 3, 105: 4}[f]
    elif f in (109, 110, 119, 121):
        result = base + lvl // {109: 4, 110: 6, 119: 8, 121: 3}[f]
    elif f == 143:
        result = base + (3 * lvl) // 4
    elif f in (107, 108, 120, 122):
        # decrementing-per-tick (HoT/DoT): this is the *starting* per-tick
        # value, not a running total -- see docstring.
        result = base
    elif 1001 <= f <= 1998:
        result = base   # generalized "splurt" -- starting per-tick value
    elif f in (111, 112, 113, 114):
        thresh = {111: 16, 112: 24, 113: 34, 114: 44}[f]
        rate = {111: 6, 112: 8, 113: 10, 114: 15}[f]
        result = base + max(0, lvl - thresh) * rate
    elif 124 <= f <= 132:
        rate = {124: 1, 125: 2, 126: 3, 127: 4, 128: 5,
               129: 10, 130: 15, 131: 20, 132: 25}.get(f, 1)
        result = base + max(0, lvl - 50) * rate
    elif f in (60, 70):
        result = base / 100
    elif f == 123:
        # random integer in base..|max| -- no RNG here, report the base as
        # a floor estimate rather than guessing a roll.
        result = base
    elif 2000 <= f <= 2650:
        result = base * (lvl * (f - 2000) + 1)
    else:
        # Unknown/out-of-range formula (EQEmu itself doesn't handle every
        # value EQL's data contains, e.g. formulas around 3000/3500 -- see
        # SPELL_FORMAT.md). Falling back to the raw base is a rougher
        # estimate than 0, at least, but flag it as such upstream if this
        # matters for your use case.
        result = base

    if max_v:
        if max_v >= base:
            result = min(result, max_v)
        else:
            result = max(result, max_v)
    return result


class SpellInfo:
    __slots__ = ("id", "name", "range", "aoe_range", "cast_time_ms",
                 "recovery_time_s", "recast_time_s", "buff_duration_formula",
                 "buff_duration_raw", "ae_duration", "mana", "good_effect",
                 "resist_type", "target_type", "classes", "effects")

    def __init__(self, fields):
        self.id = int(fields[IDX_ID])
        self.name = fields[IDX_NAME]
        self.range = _to_int(fields[IDX_RANGE])
        self.aoe_range = _to_int(fields[IDX_AOE_RANGE])
        self.cast_time_ms = _to_int(fields[IDX_CAST_TIME_MS])
        self.recovery_time_s = _to_int(fields[IDX_RECOVERY_TIME_S])
        self.recast_time_s = _to_int(fields[IDX_RECAST_TIME_S])
        self.buff_duration_formula = _to_int(fields[IDX_BUFF_DURATION_FORMULA])
        self.buff_duration_raw = _to_int(fields[IDX_BUFF_DURATION])
        self.ae_duration = _to_int(fields[IDX_AE_DURATION])
        self.mana = _to_int(fields[IDX_MANA])
        self.good_effect = _to_int(fields[IDX_GOOD_EFFECT])
        self.resist_type = _to_int(fields[IDX_RESIST_TYPE])
        self.target_type = _to_int(fields[IDX_TARGET_TYPE])
        self.classes = [_to_int(fields[IDX_CLASSES_START + i])
                        for i in range(IDX_CLASSES_COUNT)]
        # Effects blob is the trailing pipe-delimited field -- located by
        # content ("1|" prefix) rather than a fixed index, since an EQL
        # patch (ritual_eligible) has already shifted this once and could
        # again; scanning from the end is cheap and future-proof.
        blob = ""
        for cell in reversed(fields):
            if cell.startswith("1|"):
                blob = cell
                break
        self.effects = parse_effects_blob(blob)

    @property
    def is_aoe(self):
        return self.aoe_range > 0

    @property
    def is_beneficial(self):
        return self.good_effect in (1, 2)

    @property
    def cast_time_s(self):
        return self.cast_time_ms / 1000.0

    def usable_by(self, class_name):
        try:
            idx = CLASS_NAMES.index(class_name)
        except ValueError:
            return False
        return 0 <= self.classes[idx] <= 125  # 254/255 sentinels mean unavailable

    def min_level_for(self, class_name):
        try:
            idx = CLASS_NAMES.index(class_name)
        except ValueError:
            return None
        lvl = self.classes[idx]
        return lvl if lvl not in (254, 255) else None

    def hp_effects(self):
        """Effects using SPA 0 (HP) -- the generic heal/damage magnitude
        slot. A spell can have more than one (rare); usually just one."""
        return [e for e in self.effects if e.effect_id == 0]

    def estimated_hp_value(self, level):
        """Estimated heal (positive) or damage (negative) magnitude from
        this spell's SPA-0 effect at the given caster level, or None if it
        has no such effect. See estimate_effect_value() caveats."""
        hp = self.hp_effects()
        if not hp:
            return None
        return estimate_effect_value(hp[0], level)

    def __repr__(self):
        return (f"SpellInfo({self.name!r}, id={self.id}, mana={self.mana}, "
               f"cast={self.cast_time_s}s, recast={self.recast_time_s}s)")


def _to_int(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


class SpellDB:
    """Loads spells_us.txt on first use; looks up by exact name (case
    insensitive) or by ID. Silently empty if the file isn't found, so
    callers can treat "no spell info available" as a normal case."""

    def __init__(self, path=None):
        self._by_name = {}
        self._by_id = {}
        self._loaded = False
        self._path = path

    def set_game_dir_hint(self, game_dir):
        """Point the lookup at a specific game install directory (e.g.
        derived from the log file's location) if the spell file wasn't
        found via the default search paths. Only takes effect before the
        first lookup -- call this right after construction."""
        if not self._loaded and game_dir:
            candidate = os.path.join(game_dir, "spells_us.txt")
            if os.path.isfile(candidate):
                self._path = candidate

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        path = self._path or _find_spells_file()
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="cp1252", errors="replace") as f:
                for line in f:
                    fields = line.rstrip("\r\n").split("^")
                    if len(fields) < MIN_FIELDS:
                        continue
                    try:
                        info = SpellInfo(fields)
                    except (ValueError, IndexError):
                        continue
                    self._by_id[info.id] = info
                    self._by_name.setdefault(info.name.lower(), info)
        except OSError:
            pass

    def lookup(self, name):
        self._ensure_loaded()
        if not name:
            return None
        return self._by_name.get(name.strip().lower())

    def lookup_id(self, spell_id):
        self._ensure_loaded()
        return self._by_id.get(spell_id)

    def find_class_heals(self, class_name, max_level=50):
        """All beneficial spells usable by `class_name` that have an SPA-0
        (HP) effect with a positive base_value -- i.e. candidate heals,
        heal-over-time songs/buffs, etc. Useful for identifying what a
        silent passive heal (no log line) might actually be, given you know
        roughly what songs/spells the caster has available."""
        self._ensure_loaded()
        out = []
        for info in self._by_id.values():
            if not info.is_beneficial or not info.usable_by(class_name):
                continue
            min_lvl = info.min_level_for(class_name)
            if min_lvl is None or min_lvl > max_level:
                continue
            hp = info.hp_effects()
            if hp and hp[0].base_value > 0:
                out.append(info)
        out.sort(key=lambda i: (i.min_level_for(class_name) or 0, i.name))
        return out

    def __len__(self):
        self._ensure_loaded()
        return len(self._by_id)


DEFAULT_INSTALL_DIR = r"C:\Users\Public\Daybreak Game Company\Installed Games"


def _find_spells_file():
    candidates = [
        os.path.join(APP_DIR, "spells_us.txt"),
        os.path.join(os.path.dirname(APP_DIR), "spells_us.txt"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    # fall back to searching a couple of levels under the default install dir
    if os.path.isdir(DEFAULT_INSTALL_DIR):
        import glob
        for depth in ("*", os.path.join("*", "*")):
            hits = glob.glob(os.path.join(DEFAULT_INSTALL_DIR, depth, "spells_us.txt"))
            if hits:
                return hits[0]
    return None


# Module-level shared instance -- overlays can just `from eql_spell_db import
# SPELL_DB` and call SPELL_DB.lookup(name) without managing their own load.
SPELL_DB = SpellDB()
