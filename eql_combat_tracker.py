#!/usr/bin/env python3
"""
EQL Combat Tracker
===================
Parses combat-related lines out of an EverQuest Legends log for a single
character. Real logs show that EQL DOES log third-party combat in full
(groupmates, their pets, even mobs fighting each other) -- those lines are
matched-and-ignored on purpose, with ONE exception: your own pet, which is
tracked as its OWN actor ("Pet") with its own DPS/DTPS, swing stats, and
melee/skill/spell breakdown, separate from yours. Pets are recognized two
ways: "<Charname>`s warder"-style possessive names by pattern, and proper-
named pets (necro/mage style) from the "/pet leader" say line ("Jenann
says, 'My leader is Monomate.'"). Everything else stays scoped to "you",
both to keep the meter personal and so nearby fights can't stretch your
fight window. Produces both:

  * live per-fight stats (for the retro DPS/HPS meter overlay)
  * session-wide totals (for the session report): damage split eight ways
    (Melee / Skill / Ranged / Spell / Poison / Song / Damage Shield /
    Pet), heal totals, kill rate, and a
    per-ability breakdown showing which spell, skill, or plain melee is
    contributing the most damage/healing.

Data model: a SESSION starts at the "Welcome to EverQuest Legends!" login
banner and is the top-level boundary -- all totals, kills, fights, and
rolling windows reset there, so a log file holding many days of play never
skews the numbers. Within a session, a COMBAT starts the moment damage is
dealt or received and ends when no damage has been dealt/received for
IDLE_TIMEOUT seconds (default 45; the DPS meter exposes a 5-60s selector)
(heals, misses, and casts neither start nor extend it; heals still count
toward HPS and session totals). Rates divide by ACTIVE combat time: the
gap between consecutive damage events counts in full only up to
ACTIVE_GAP_CAP (10s); anything longer -- looting, medding, walking to the
next mob inside the 45s idle window -- is capped. Without this, chained
pulls diluted DPS badly (real logs showed 264s "fights" whose actual
swinging time was a quarter of that, reading 1 DPS while landing 50-point
hits). Swing gaps in real logs cluster at 1-7s, so the 10s cap never
clips normal weapon delay.

Melee vs. Skill split (best-effort)
--------------------------------------
Physical damage lines all share one format ("You <verb> X for N points of
damage."), so the only signal for "was this a basic weapon swing or an
activated skill" is the verb itself. MELEE_VERBS covers ordinary weapon-type
swings (slash/crush/pierce/hit/bite/claw/etc.); SKILL_VERBS covers the
handful of classic-EQ activated combat skills that share this same log
format (kick, bash, backstab, frenzy, slam). This is the standard
classic-EverQuest convention, not independently re-confirmed against EQL's
specific skill list -- same caveat tier as the Stance/Invocation descriptor
guesses below. If EQL has activated skills that use a verb not in either
set, they'll currently fall through unmatched and land in
`CombatTracker.unmatched` for calibration, same as any other unrecognized
line.

Calibration note (checked against a real eqlog_*.txt)
-------------------------------------------------------
    You slash a spiderling for 39 points of damage. (Critical)
        -- crits are an inline "(Critical)" suffix, not a separate
           "You deliver a critical blow!" announcement line.
    A spiderling bites YOU for 1 point of damage.
    You try to slash a spiderling, but a spiderling dodges!
    A spiderling tries to bite YOU, but misses!
        -- misses/avoidance are phrased "tries to <verb> <target>, but ...!"
    A spiderling has taken 3 damage from your Chords of Dissonance.
        -- spell/DoT damage names the caster and spell directly.
    You healed Miranda for 2 (20) hit points by Minor Healing.
        -- heals use "hit points", include overheal in parens, name the
           spell, and refer to the caster's own character by name when
           self-targeted rather than "yourself".
    a darkweed snake hit you for 11 points of poison damage by Poison.
        -- direct spell/elemental damage on you: lowercase "you", an
           element word before "damage", and "by <spell>" at the end.
    You have taken 10 damage from Poison by a darkweed snake.
        -- DoT ticks on you use this separate phrasing.
    An orc warrior has taken 6 damage by Weak Poison.
        -- your own applied weapon poison/proc ticks; no caster is named
           ("by" not "from your"), attributed to You.
    An orc warrior hits YOU for 4 points of damage. (Riposte)
        -- "(Riposte)" and other parenthesized tags can follow any combat
           line, same style as "(Critical)"; all tags are stripped before
           matching, only Critical affects stats.
    YOU are pierced by a gnoll's thorns for 5 points of non-melee damage!
        -- a MOB's damage shield burning you when your hit lands. Note the
           "YOU are" phrasing and the trailing "!" (third-party DS reads
           "An asp is pierced by Becca's thorns ... damage." with a ".").

Stance/Invocation switch lines (confirmed against a real log, screenshots
provided by the user):

    You begin to change your stance.
    You assume an offensive stance.

    You begin to change your invocation.
    You begin reciting the recovery invocation.

The "begin to change your ___" line is just an announcement (no useful
info) and is matched-and-ignored. The second line names the new stance/
invocation, but as an adjective/descriptor ("offensive", "recovery")
rather than the canonical name used elsewhere ("Offense Stance",
"Recover") -- STANCE_DESCRIPTORS/INVOCATION_DESCRIPTORS below map the
wording that's actually been observed to the canonical name. Only
"offensive" (-> Offense Stance) and "recovery" (-> Recover) have been seen
in a real log; "defensive"/"mage hunter" and "over channel"/"spell blade"
are still guesses at the descriptor word game will use for the other two
stances/invocations -- the *line format* is confirmed, just not every
descriptor. An unrecognized descriptor still gets tracked (under its own
raw name) rather than silently dropped, so it'll show up distinctly in the
session report if the guess is wrong, rather than disappearing.

The known stance/invocation *effects* (hardcoded in STANCES/INVOCATIONS)
come from eqlwiki.com, since that's a small, fixed, documented set
regardless of log wording.

How spells_us.txt is (and isn't) used here
---------------------------------------------
The spell file's full 173-column layout is now documented (see
eql_spell_db.py, which follows github.com/Amerzel/eql-info's reverse-
engineered SPELL_FORMAT.md), so this tracker leans on it for
CLASSIFICATION: song-vs-spell (casting skill), lifetap recognition
(target_type flag), and buff/debuff message attribution (spells_us_str.txt
maps each spell to the "cast on you" / "fades" strings the client logs,
which carry no spell name of their own). Actual damage/heal AMOUNTS still
always come from the log, which is ground truth -- it already reflects
resists, crits, and level scaling that static spell data plus estimate
formulas can only approximate.
"""

import re
import time
from collections import deque
from datetime import datetime

from eql_spell_db import SPELL_DB, EQL_LEVEL_CAP

TS_RE = r"\[(?P<ts>[A-Za-z]{3} [A-Za-z]{3} \d{2} \d{2}:\d{2}:\d{2} \d{4})\] "
TS_ONLY_RE = re.compile(TS_RE)
LOG_TS_FMT = "%a %b %d %H:%M:%S %Y"

# session boundary -- confirmed login banner
SESSION_START_RE = re.compile(TS_RE + r"Welcome to EverQuest Legends!\s*$")

# zoning -- songs are silently stripped when you zone (no fade lines), so
# this is a buff-list cleanup point; see _clear_stale_buffs_on_zone
ZONE_RE = re.compile(TS_RE + r"LOADING, PLEASE WAIT\.\.\.\s*$")

# spell housekeeping -- confirmed formats; matched-and-ignored because spell
# NAMES ("Spell: Flowering Heal") contain combat keywords and would land in
# the calibration list otherwise. The discipline lines (learning/being
# granted/activating rogue poisons etc. -- confirmed in a real rogue log)
# are here for the same reason: "discipline" is itself a calibration
# keyword.
SPELL_HOUSEKEEPING_RE = re.compile(
    TS_RE + r"(?:You purchased \d|Beginning to (?:scribe|memorize) "
            r"|You have finished (?:scribing|memorizing) "
            r"|You have been granted the following discipline: "
            r"|You have learned "
            r"|You activate "
            # EQL's spell-upgrade merging ("...create a new item: Sprouting
            # Heal I") -- the created ITEM name is a spell name, which
            # would trip the calibration keywords
            r"|You have successfully merged )")

# Seed-heal (Budding/Sprouting/Flowering Heal) messages, confirmed in a
# real log: "You feel a heal sprouting within you." (the seed landing) and
# "The heal within you blooms." (its trigger firing). Normally consumed by
# the buff tables via spells_us_str.txt; this fallback keeps them out of
# the calibration list when that file is unavailable (e.g. a log synced to
# a machine without the game installed). The healing itself is unaffected
# either way -- it arrives via its own "You healed ... by Sprouting Heal"
# lines.
SEED_HEAL_FALLBACK_RE = re.compile(
    TS_RE + r"(?:You feel a heal (?:budding|sprouting|flowering) within you"
            r"|The heal within you blooms)[.!]?\s*$")

# Data model (per the user's spec):
#   SESSION -- starts at "Welcome to EverQuest Legends!" (login). ALL
#     session-scoped state resets there, so numbers never mix play sessions
#     that happen to share a log file.
#   COMBAT  -- within a session: starts the moment damage is dealt or
#     received, ends when no damage has been dealt/received for
#     IDLE_TIMEOUT seconds. Heals, misses, and casts neither start nor
#     extend a Combat (heals still count toward HPS/session totals).
#     Rates divide by ACTIVE combat time (see Fight.touch/ACTIVE_GAP_CAP).
IDLE_TIMEOUT = 45.0
ACTIVE_GAP_CAP = 10.0       # a gap between damage events counts toward active
                            # combat time only up to this many seconds --
                            # downtime between chained pulls (loot/med/run,
                            # < IDLE_TIMEOUT) must not dilute DPS. Real swing
                            # gaps cluster at 1-7s, so this never clips
                            # ordinary weapon delay or short miss streaks.
ROLLING_MAX_WINDOW = 60.0   # largest rolling-rate window the meter offers
                            # (30s picker windows, plus the tri layout's
                            # last-60s per-minute column)
MAX_FIGHT_HISTORY = 20
MAX_UNMATCHED = 200
CRIT_WINDOW = 2.0           # seconds a "critical hit" announcement stays live
                            # (log timestamps only have 1s resolution, so this
                            # needs slack wider than wall-clock would)
CAST_WINDOW = 4.0           # seconds a "begins casting" stays attributable
SONG_FALLBACK_SECS = 600    # worst-case lifetime for a SONG whose duration
                            # the spell file can't supply -- songs are short
                            # by nature (a few ticks past the last note), so
                            # even an unknown one must not outlive this; keeps
                            # unresolvable song entries from sticking on the
                            # BUFFS list forever

MELEE_VERBS = {
    "hit", "hits", "slash", "slashes", "pierce", "pierces", "crush", "crushes",
    "claw", "claws", "bite", "bites", "sting", "stings", "punch", "punches",
    "gore", "gores", "maul", "mauls", "rend", "rends", "smash", "smashes",
    "strike", "strikes", "slice", "slices", "gouge", "gouges", "burn", "burns",
    "smite", "smites",     # confirmed in a real log (179 hits)
    "reave", "reaves",     # confirmed in a real log (21 hits)
}
SKILL_VERBS = {
    "kick", "kicks", "bash", "bashes", "backstab", "backstabs",
    "frenzy", "frenzies", "slam", "slams",
    # Cleave is an ACTIVATED skill on EQL, not a weapon-swing verb --
    # confirmed in a real log where the same character lands both
    # "You slash ..." (3,976 ordinary swings) and "You cleave ..."
    # (1,008 skill uses); mobs use it too, same as kick/bash
    "cleave", "cleaves",
}
# Ranged (bow) hits use "shoot" -- confirmed in a real log (archery, 780
# hits). Caveat: some bows log their damage with the generic weapon-type
# verb instead (pierce/slash/crush, indistinguishable from melee); those
# still land in Melee -- only lines the log itself marks as ranged (the
# shoot verb, and the thrown-weapon lines below) can be split out.
RANGED_VERBS = {"shoot", "shoots"}
ATTACK_VERBS = MELEE_VERBS | SKILL_VERBS | RANGED_VERBS

SKILL_VERB_NAMES = {
    "kick": "Kick", "kicks": "Kick",
    "bash": "Bash", "bashes": "Bash",
    "backstab": "Backstab", "backstabs": "Backstab",
    "frenzy": "Frenzy", "frenzies": "Frenzy",
    "slam": "Slam", "slams": "Slam",
    "cleave": "Cleave", "cleaves": "Cleave",
}


def _clean_verb_target(verb, target):
    """Frenzy phrases its target with a preposition -- "You frenzy on a
    gnoll for 12 points of damage." -- so the bare-regex target arrives as
    "on a gnoll". Strip it, or the frenzy damage lands on a phantom."""
    if verb in ("frenzy", "frenzies") and target.startswith("on "):
        return target[3:]
    return target

MELEE_ABILITY = "Melee"
RANGED_ABILITY = "Archery"
CATEGORY_LABELS = {"melee": "Melee", "skill": "Skill", "ranged": "Ranged",
                   "spell": "Spell", "poison": "Poison", "song": "Song",
                   "ds": "Dmg Shield", "pet": "Pet"}
CATEGORIES = ("melee", "skill", "ranged", "spell", "poison", "song",
              "ds", "pet")


def _attack_category_and_ability(verb):
    """Classify a physical-attack verb into
    ("melee"|"skill"|"ranged", ability_name)."""
    if verb in SKILL_VERBS:
        return "skill", SKILL_VERB_NAMES.get(verb, verb.strip("s").capitalize())
    if verb in RANGED_VERBS:
        return "ranged", RANGED_ABILITY
    return "melee", MELEE_ABILITY

# ----------------------------------------------------------------------------
# Known Stance/Invocation effects (from eqlwiki.com -- a small fixed set,
# not per-character data, so hardcoding is reliable where log detection is
# not). Swapping either has a short cooldown; exactly one of each is active
# at a time.
# ----------------------------------------------------------------------------
STANCES = {
    "Defense Stance": "-50% physical damage taken, -20% magic damage taken",
    "Offense Stance": "2x melee damage, +25% crit chance",
    "Mage Hunter Stance": "-50% magic damage taken, -20% physical damage taken",
    "Channeler": "-40% damage taken, improved channeling; half the "
                 "mitigated damage charged to mana+endurance (reduced by "
                 "Strategy skill)",
}
INVOCATIONS = {
    "Recover": "2x mana regen, -5% spell cost",
    "Over Channel": "severely reduces target's spell resistance; bonuses per caster class",
    "Spell Blade": "chance to trigger a chosen spell on melee attack",
    "Unyielding": "2x in-combat health regen, +25% resist to fear/mez/"
                  "charm control loss; no upkeep cost",
}

# -- trailing tag suffixes: "...damage. (Critical)" / "...misses! (Riposte)"
# The client appends parenthesized tags after the sentence-final ./! --
# "(Critical)" and "(Riposte)" are both confirmed in real logs. Tags are
# stripped in a loop (in case of stacking) before any other matching; only
# "(Critical)" changes stats, the rest just must not break the line match.
TAG_SUFFIX_RE = re.compile(r"^(?P<body>.*[.!])\s*\((?P<tag>[A-Za-z][A-Za-z ]*)\)\s*$")

# -- self as source ----------------------------------------------------------
# Plain melee: "You slash a spiderling for 39 points of damage."
# Elemental/proc: "You hit an orc for 4 points of fire damage by Burst of Flame."
SELF_HIT_RE = re.compile(
    TS_RE + r"You (?P<verb>[a-z]+) (?P<target>.+?) for (?P<amount>\d+) points? of "
            r"(?:(?P<element>[a-z]+) )?damage(?: by (?P<spell>.+?))?\.?\s*$")

# -- life-tap style combo spells: deal damage AND heal the caster for the
# same amount. Confirmed against a real log: when someone ELSE casts one of
# these on themselves, both halves print ("Gobn hit a crab spiderling for 4
# points of magic damage by Lifetap." followed by "Gobn healed itself for 0
# (4) hit points by Lifetap." -- the parenthesized number is the spell's
# fixed heal magnitude, always equal to its damage). For YOUR OWN casts,
# though, only the damage line ever appears in the log -- zero "You healed
# ... Lifetap" lines exist even though "You hit ... Lifetap" appears
# repeatedly. _maybe_record_lifetap_heal() synthesizes the missing self-heal
# 1:1 with the damage actually logged, so these spells show up as healing
# instead of silently vanishing.
#
# Recognition is primarily DATA-DRIVEN: the spell file flags the mechanic
# itself (target_type 13 = Lifetap / 20 = targeted-AE lifetap, per
# github.com/Amerzel/eql-info's SPELL_FORMAT.md), so SPELL_DB.lifetap_names()
# covers every tap in the game -- necro/SK spell lines, bard's Ancient
# Warsong twist, item procs -- with no list to maintain. The hand-checked
# names below are kept only as a fallback for when spells_us.txt isn't
# found (they're the ones confirmed against real logs).
LIFETAP_SPELLS = {"lifetap", "lifespike", "lifedraw", "siphon life",
                  "spirit tap"}

# -- thrown weapons (Ranged) -------------------------------------------------
# Per the user's confirmed formats: a landing throw logs the throw
# announcement and the hit message on ONE line ("You throw your <Item> at
# <Target>! <Hit Message> for <N> damage."), a miss logs "You try to throw
# your <Item> at <Target>, but miss!". The hit regex is permissive about
# the middle sentence and about "points of" so both phrasings land; the
# announce-only form (in case the client ever splits the two sentences
# onto separate lines) arms a short window that re-categorizes the next
# self-hit as ranged.
THROWN_HIT_RE = re.compile(
    TS_RE + r"You throw your (?P<item>.+?) at (?P<target>.+?)!"
            r"\s*.*? for (?P<amount>\d+)(?: points? of)? damage\.?\s*$")
THROWN_ANNOUNCE_RE = re.compile(
    TS_RE + r"You throw your (?P<item>.+?) at (?P<target>.+?)!\s*$")
THROWN_MISS_RE = re.compile(
    TS_RE + r"You try to throw your (?P<item>.+?) at (?P<target>.+?), "
            r"but miss!\s*$")
# out-of-range / line-of-sight refusals -- no combat info, matched-and-
# ignored so they can never land in the calibration list
RANGED_REFUSAL_RE = re.compile(
    TS_RE + r"(?:Your target is out of range, get closer\.?"
            r"|You cannot see your target\.?)\s*$")
THROWN_WINDOW = 2.0   # seconds an announce-only throw stays attributable

# -- self as target ------------------------------------------------------------
# Melee uses uppercase YOU ("An orc warrior cleaves YOU for 19 points of
# damage."); spell/elemental hits use lowercase you and name the spell
# ("a darkweed snake hit you for 11 points of poison damage by Poison.").
SELF_TAKEN_RE = re.compile(
    TS_RE + r"(?P<source>.+?) (?P<verb>[a-z]+) (?:YOU|you) for (?P<amount>\d+) points? of "
            r"(?:(?P<element>[a-z]+) )?damage(?: by (?P<spell>.+?))?\.?\s*$")

# Words that can land in the verb slot of the two patterns above but are
# never attacks (e.g. the classic heal fallback "You have healed X for N
# points of damage" must keep falling through to the heal regexes).
NON_ATTACK_VERBS = {"have", "has", "had", "healed", "heals", "heal"}

# -- DoT ticks on you -- confirmed: "You have taken 10 damage from Poison by
# a darkweed snake."  ("by <source>" may be absent for untraceable sources)
INCOMING_DOT_RE = re.compile(
    TS_RE + r"You have taken (?P<amount>\d+) damage from (?P<spell>.+?)"
            r"(?: by (?P<source>.+?))?\.?\s*$")

# -- your own weapon-poison/proc ticks -- confirmed: "An orc warrior has
# taken 6 damage by Weak Poison."  No caster is named; the client only
# echoes procs you applied, so this is attributed to You. (If EQL turns out
# to echo other players' procs too, this will over-credit -- calibrate via
# the session report's per-ability list, where it shows under the poison's
# own name.)
PROC_DOT_RE = re.compile(
    TS_RE + r"(?P<target>.+?) has taken (?P<amount>\d+) damage by (?P<spell>.+?)\.?\s*$")

# -- misses / avoidance -- confirmed real format: "tries to VERB X, but ...!"
SELF_MISS_RE = re.compile(
    TS_RE + r"You try to [a-z]+ (?P<target>.+?), but .+!\s*$")
OTHER_MISS_ON_SELF_RE = re.compile(
    TS_RE + r"(?P<source>.+?) tries to [a-z]+ YOU, but .+!\s*$")
# classic-EQ fallback phrasing, kept in case it shows up too
SELF_MISS_FALLBACK_RE = re.compile(TS_RE + r"You miss (?P<target>.+?)\.?\s*$")
SELF_DODGED_FALLBACK_RE = re.compile(TS_RE + r"(?P<source>.+?) misses? YOU\.?\s*$")

# -- non-melee (spell/DoT) damage -- confirmed real format names the caster --
NONMELEE_ATTR_RE = re.compile(
    TS_RE + r"(?P<target>.+?) has taken (?P<amount>\d+) damage from "
            r"(?:your|(?P<caster>[A-Za-z]+)'s) (?P<spell>.+?)\.?\s*$")
# classic-EQ fallback (unattributed) -- kept in case it shows up too
NONMELEE_FALLBACK_RE = re.compile(
    TS_RE + r"(?P<target>You|[A-Za-z][A-Za-z' -]*?) (?:was|were) hit by non-melee for (?P<amount>\d+) damage\.?\s*$")

# -- casting, used to attribute the fallback non-melee message, to log
#    non-damage spell casts (buffs/utility), and to learn whether an ability
#    is a spell or a Bard song ("casting" vs "singing") so its damage lands
#    in the right category later (DoT ticks name only the ability) ----------
#    The spell group is deliberately permissive (.+?): checked against every
#    name in the game's own spells_us.txt, ~30% contain characters beyond
#    letters/apostrophe/space (digits, colons, hyphens, parens -- "Illusion:
#    Werewolf", "Yaulp III"...), which the old [A-Za-z' ] class rejected.
CASTING_RE = re.compile(
    TS_RE + r"(?P<who>You|[A-Za-z]+) begin(?:s)? (?P<how>casting|singing) (?P<spell>.+?)\.?\s*$")

# -- cast outcomes: resists / fizzles / interrupts ---------------------------
#    EQL's real phrasings, confirmed from calibration-tab lines (2026-07-11
#    log) -- all three carry the SPELL NAME, unlike the nameless classic-EQ
#    forms, so outcomes attribute per spell:
#      A dry bone skeleton resisted your Fingers of Fire!
#      Your Cascade of Hail spell fizzles!
#      Your Force Snap spell is interrupted.
#      You resist a lesser mummy's Rabies!      (incoming resist)
#      Your melody has been interrupted!        (bard song interrupt)
#    Third parties' outcomes are also logged ("Henelope's Convoke Shadow
#    spell fizzles!") -- matched-and-ignored, except that they cancel that
#    caster's pending-cast attribution window. The classic nameless forms
#    stay as fallback alternations (harmless if EQL never emits them).
RESIST_OUT_RE = re.compile(       # a mob shrugging off YOUR spell
    TS_RE + r"(?P<target>.+?) resisted your (?P<spell>.+?)!\s*$")
RESIST_OUT_CLASSIC_RE = re.compile(
    TS_RE + r"Your target resisted the (?P<spell>.+?) spell\.?\s*$")
RESIST_IN_RE = re.compile(        # you shrugging off a mob's spell --
    TS_RE + r"You resist (?P<source>.+?)[`']s (?P<spell>.+?)!\s*$")  # confirmed
RESIST_IN_CLASSIC_RE = re.compile(
    TS_RE + r"You resist the (?P<spell>.+?) spell!?\s*$")
FIZZLE_RE = re.compile(
    TS_RE + r"(?:Your (?:(?P<spell>.+?) )?spell fizzles!?"
            r"|You miss a note, bringing your song to a close!?)\s*$")
OTHER_FIZZLE_RE = re.compile(
    TS_RE + r"(?P<who>.+?)[`']s (?:.+? )?spell fizzles!?\s*$")
INTERRUPT_RE = re.compile(
    TS_RE + r"(?:Your (?:(?P<spell>.+?) )?spell is interrupted\.?"
            r"|Your melody has been interrupted!?"      # confirmed (songs)
            r"|Your casting has been interrupted!?)\s*$")
OTHER_INTERRUPT_RE = re.compile(
    TS_RE + r"(?P<who>.+?)[`']s (?:.+? )?spell is interrupted\.?\s*$")
# The Budding Heal line's delayed-heal trigger firing on someone else
# ("Dizzi is healed from within.") -- no amount, no caster; on YOU it logs
# "The heal within you blooms." instead (handled by the buff-message
# tables). Matched-and-ignored so it can't pollute `unmatched`.
HEAL_WITHIN_RE = re.compile(
    TS_RE + r".+? is healed from within\.?\s*$")

# -- third-party combat (confirmed logged in EQL: group members, their pets,
#    and mobs fighting each other all appear in full). Only lines involving
#    YOUR OWN pet ("<Charname>`s warder" etc.) are recorded; the rest are
#    matched-and-ignored so they can't pollute fight timing or `unmatched`.
OTHER_HIT_RE = re.compile(
    TS_RE + r"(?P<source>.+?) (?P<verb>[a-z]+) (?P<target>.+?) for (?P<amount>\d+) points? of "
            r"(?:(?P<element>[a-z]+) )?damage(?: by (?P<spell>.+?))?\.?\s*$")
# Strict variant for lines that get RECORDED (group members' damage): the
# loose verb of OTHER_HIT_RE mis-splits multi-word names ("Fatereaver`s
# warder bites ..." reads as source "Fatereaver`s", verb "warder"), which
# didn't matter while every match was discarded. Anchoring the verb to the
# real attack-verb list makes the non-greedy source swallow the full name.
_ATTACK_VERB_ALT = None    # built lazily from ATTACK_VERBS below
OTHER_HIT_STRICT_RE = None


def _other_hit_strict():
    global OTHER_HIT_STRICT_RE
    if OTHER_HIT_STRICT_RE is None:
        alt = "|".join(sorted(ATTACK_VERBS, key=len, reverse=True))
        OTHER_HIT_STRICT_RE = re.compile(
            TS_RE + rf"(?P<source>.+?) (?P<verb>{alt}) (?P<target>.+?) "
                    r"for (?P<amount>\d+) points? of "
                    r"(?:(?P<element>[a-z]+) )?damage"
                    r"(?: by (?P<spell>.+?))?\.?\s*$")
    return OTHER_HIT_STRICT_RE
OTHER_DOT_RE = re.compile(
    TS_RE + r"(?P<target>.+?) has taken (?P<amount>\d+) damage from (?P<spell>.+?)"
            r" by (?P<source>.+?)\.?\s*$")
OTHER_SLAIN_RE = re.compile(
    TS_RE + r"(?P<target>.+?) has been slain by (?P<source>.+?)!?\s*$")
# third-party heals ("Miranda healed herself for 12 hit points by Minor
# Healing.", "Cryze healed Krudd over time for 8 hit points by Flowering
# Heal.") -- matched-and-ignored so they stay out of `unmatched`
OTHER_HEAL_RE = re.compile(
    TS_RE + r"(?P<source>.+?) healed (?P<target>.+?)(?: over time)? for (?P<amount>\d+)"
            r"(?:\s*\((?P<overheal>\d+)\))? hit points?(?: by (?P<spell>.+?))?\.?\s*$")

# -- Mend (monk) -- confirmed format prints NO amount ("You mend your wounds
#    and heal some damage."), so it's tracked as an ability use with 0 healing
#    rather than silently dropped; there is nothing numeric to add to HPS.
MEND_RE = re.compile(TS_RE + r"You mend your wounds and heal some damage\.?\s*$")

# -- "Your wounds begin to heal." is NOT a passive-regen announcement: it's
#    the cast-on-you message of the Hymn of Restoration / Elixir / Pact
#    heal-over-time lines (per spells_us_str.txt), so the buff tables must
#    get it first. This fallback only ignores it when the client's str file
#    is unavailable, keeping it out of `unmatched` either way.
REGEN_RE = re.compile(TS_RE + r"Your wounds begin to heal\.?\s*$")

# -- pet ownership -- confirmed: "Jenann says, 'My leader is Monomate.'"
#    (the /pet leader command). This is how named pets (necro/mage style,
#    whose random names carry no owner information) get attributed; warder-
#    style pets ("<Charname>`s warder") are recognized by pattern alone.
#    CHARM pets keep their multi-word mob name -- confirmed: "An abhorrent
#    says, 'My leader is Urgar.'" -- so the name is not a single word.
PET_LEADER_RE = re.compile(
    TS_RE + r"(?P<pet>[A-Za-z][A-Za-z`' -]{0,40}?) says, "
            r"'My leader is (?P<owner>[A-Za-z]+)\.'\s*$")

# -- pet attack announcement -- confirmed (from the OWNER's own log):
#    "Becca`s warder told you, 'Attacking an elite gnoll fighter
#    Master.'"  The pet tells its MASTER and only its master -- so a pet
#    announcing an attack "to you" in YOUR log is YOURS. This registers
#    pets (charm pets especially) automatically, without /pet leader:
#    it fires on every /pet attack, which happens far more often.
PET_ATTACK_RE = re.compile(
    TS_RE + r"(?P<pet>.+?) told you, 'Attacking (?P<target>.+?) Master\.'\s*$")

# -- /who entries -- confirmed EQL format (same one the Friends Overlay
#    parses): "[21 DRU] Miranda (Wood Elf)  ZONE: ...". When the name is
#    this character, it reveals the player's LEVEL, which buff-duration
#    estimates scale by (e.g. Shield of Barbs, duration formula 10 =
#    3*level+10 ticks: 7:18 at L21 vs 15:00 at L50 -- confirmed against
#    the in-game buff window). All who entries are consumed either way so
#    they can never be misread as combat.
WHO_ENTRY_RE = re.compile(
    TS_RE + r"\s*\[(?P<level>\d+) (?P<classes>[A-Z]{1,4}(?:/[A-Z]{1,4})*)\]"
            r" (?P<name>[A-Za-z]+) \(")

# -- level up -- classic-EQ phrasing, NOT yet confirmed for EQL (same
#    caveat tier as the *_FALLBACK_RE patterns); a /who refresh will catch
#    the level anyway even if this never matches.
GAIN_LEVEL_RE = re.compile(
    TS_RE + r"You have gained a level! Welcome to level (?P<level>\d+)!\s*$")

# -- chat lines -- players quoting combat words ("the damage shields
#    really rip") were landing in the calibration list. Any tell/say/
#    shout/auction line is chat, never combat -- matched-and-ignored.
#    Checked AFTER PET_LEADER_RE, which is itself a "says" line.
CHAT_RE = re.compile(
    TS_RE + r"(?:You|[A-Za-z][A-Za-z` ]{0,40}?) "
            r"(?:say|says|tells|tell|told|shout|shouts|auction|auctions)\b"
            r"[^,]{0,40}, '")

# -- group membership -- the strongest "this name is a PLAYER" signal
#    (better than /who: groupmates are the allies whose damage the
#    encounter analytics attribute). Confirmed formats: "Fatereaver has
#    joined the group.", "<Name> has left the group.", and group chat
#    "<Name> tells the group, '...'".
GROUP_PLAYER_RE = re.compile(
    TS_RE + r"(?P<name>[A-Z][a-z]+) (?:has (?:joined|left) the group\.?"
            r"|tells the group,)")

# -- damage shields -- confirmed third-party format: "A rattlesnake is
#    pierced by Miranda's thorns for 14 points of non-melee damage."  Your
#    own reads "by YOUR thorns" (classic-EQ convention, capital YOUR).
DS_SELF_RE = re.compile(
    TS_RE + r"(?P<target>.+?) is [a-z]+ by YOUR (?P<kind>[A-Za-z ]+?) "
            r"for (?P<amount>\d+) points? of non-melee damage\.?\s*$")
DS_OTHER_RE = re.compile(
    TS_RE + r"(?P<target>.+?) is [a-z]+ by (?P<owner>.+?)'s (?P<kind>[A-Za-z ]+?) "
            r"for (?P<amount>\d+) points? of non-melee damage\.?\s*$")
# a MOB's damage shield burning YOU -- confirmed: "YOU are pierced by a
# gnoll's thorns for 5 points of non-melee damage!" ("YOU are" + trailing
# "!", unlike the third-party form's "is ... damage.")
DS_TAKEN_RE = re.compile(
    TS_RE + r"YOU are [a-z]+ by (?P<owner>.+?)[`']s (?P<kind>[A-Za-z ]+?) "
            r"for (?P<amount>\d+) points? of non-melee damage[.!]?\s*$")

# -- critical hit announcements (classic-EQ fallback; this client uses the
#    inline "(Critical)" suffix above instead) ------------------------------
CRIT_RE = re.compile(
    TS_RE + r"(?:You deliver a critical blow!|(?P<source>[A-Za-z]+) (?:scores a critical hit|delivers a critical blow)!?)\s*$")

# -- healing -- confirmed real format names the spell + overheal total ------
HEAL_DEALT_ATTR_RE = re.compile(
    TS_RE + r"You healed (?P<target>.+?)(?: over time)? for (?P<amount>\d+)"
            r"(?:\s*\((?P<overheal>\d+)\))? hit points?(?: by (?P<spell>.+?))?\.?\s*$")
HEAL_RECEIVED_ATTR_RE = re.compile(
    TS_RE + r"(?P<source>.+?) healed you(?: over time)? for (?P<amount>\d+)"
            r"(?:\s*\((?P<overheal>\d+)\))? hit points?(?: by (?P<spell>.+?))?\.?\s*$")
# classic-EQ fallback phrasing -- kept in case it shows up too
HEAL_DEALT_FALLBACK_RE = re.compile(
    TS_RE + r"You have healed (?P<target>.+?) for (?P<amount>\d+) points? of damage\.?\s*$")
HEAL_RECEIVED_FALLBACK_RE = re.compile(
    TS_RE + r"(?P<source>.+?) has healed you for (?P<amount>\d+) points? of damage\.?\s*$")

# -- deaths ---------------------------------------------------------------------
SELF_DEATH_RE = re.compile(TS_RE + r"You have been slain by (?P<source>.+?)!?\s*$")
SELF_KILL_RE = re.compile(TS_RE + r"You have slain (?P<target>.+?)!?\s*$")

# -- stance / invocation switches -- confirmed format, see module docstring -
STANCE_CHANGING_RE = re.compile(TS_RE + r"You begin to change your stance\.?\s*$")
INVOCATION_CHANGING_RE = re.compile(TS_RE + r"You begin to change your invocation\.?\s*$")

STANCE_ASSUME_RE = re.compile(
    TS_RE + r"You assume (?:a|an) (?P<descriptor>[A-Za-z][A-Za-z '-]*?) stance\.?\s*$")
INVOCATION_RECITE_RE = re.compile(
    TS_RE + r"You begin reciting the (?P<descriptor>[A-Za-z][A-Za-z '-]*?) invocation\.?\s*$")

# descriptor word (as it appears in the log line) -> canonical name (as it
# appears in STANCES/INVOCATIONS above). Only the mappings marked "confirmed"
# have actually been seen in a real log; the others are guesses.
STANCE_DESCRIPTORS = {
    "offensive": "Offense Stance",       # confirmed
    "defensive": "Defense Stance",       # confirmed
    "mage hunter": "Mage Hunter Stance",  # guess
    # channeler / striker / evasive (confirmed in a real log) fall through
    # to the title-cased descriptor, which is already their display name
}
INVOCATION_DESCRIPTORS = {
    "recovery": "Recover",             # confirmed
    "overchannel": "Over Channel",     # confirmed ("...reciting the
                                       # overchannel invocation.")
    "over channel": "Over Channel",    # guess
    "overchanneling": "Over Channel",  # guess (alternate wording)
    "spell blade": "Spell Blade",      # guess
    # divine / inversion / unyielding (confirmed) title-case cleanly
}


def _resolve_descriptor(descriptor, table):
    """Map a log-line descriptor word to its canonical Stance/Invocation
    name. Falls back to the descriptor itself (title-cased) if it's not one
    of the recognized wordings, so an unexpected switch still gets tracked
    under a distinct name instead of silently disappearing."""
    return table.get(descriptor.strip().lower(), descriptor.strip().title())

YOU_LABEL = "You"
PET_LABEL = "Pet"   # your pet is its own actor, with its own DPS/DTPS


def _norm(name, self_name=None):
    """Collapse 'a bat' / 'A bat' (sentence-initial capital) / 'the bat' to
    one stable display label, and map the player's own character name (and
    literal "You"/"YOU") to YOU_LABEL."""
    name = name.strip()
    if name.upper() == "YOU":
        return YOU_LABEL
    if self_name and name.lower() == self_name.lower():
        return YOU_LABEL
    first, _, rest = name.partition(" ")
    if first.lower() in ("a", "an", "the"):
        return first.lower() + (" " + rest if rest else "")
    return name


def _blank_actor():
    d = {
        "dmg_out": 0, "dmg_in": 0, "heal_out": 0, "heal_in": 0,
        "hits": 0, "misses": 0, "crits": 0, "biggest_hit": 0,
        "last_hit_wall": 0.0, "last_crit_wall": 0.0,
    }
    # per-category live breakdown, for the DPS meter's segmented display
    for c in CATEGORIES:
        d[f"{c}_dmg_out"] = 0
        d[f"{c}_dmg_in"] = 0
    return d


def _blank_ability():
    # "proc": True when this looks like an automatic trigger rather than a
    # cast -- the spell file lists it as proc-granted (SPELL_DB.proc_names)
    # AND you were never seen casting it. Re-evaluated on every hit, so
    # later cast evidence flips it off (log evidence wins over static data).
    return {"total": 0, "hits": 0, "crits": 0, "biggest": 0,
            "category": "melee", "proc": False}


class Fight:
    def __init__(self, start_wall, friendly=None):
        self.start_wall = start_wall
        self.last_wall = start_wall
        self.active_secs = 0.0
        self.ended = False
        self.actors = {}   # name -> stat dict
        # live reference to the tracker's known-player set: names that are
        # PLAYERS (from /who and group lines), so enemies() never mistakes
        # a groupmate whose damage was recorded for the mob
        self.friendly = friendly if friendly is not None else set()
        self.stance = None
        self.invocation = None
        # spell -> times a mob resisted it DURING this fight (the meter's
        # RESISTED block; session-wide counts live on the tracker)
        self.spell_resists = {}
        # spell -> times YOU resisted it this fight ("You resist a necro
        # acolyte's Cancelling of Life!")
        self.you_resisted = {}
        # per-fight detail for encounter analytics (the Session Report's
        # Encounters tab and the meter's fight-summary popup): YOUR
        # ability/heal breakdowns, casts, and kill/death counts scoped to
        # THIS fight (session-wide equivalents live on the tracker)
        self.abilities_dmg = {}
        self.abilities_heal = {}
        self.spell_casts = {}
        self.kills = 0
        self.deaths = 0
        # stance/invocation shares within THIS fight: active seconds spent
        # in each (fed by touch) and YOUR damage dealt while in each
        self.stance_secs = {}
        self.stance_dmg = {}
        self.invocation_secs = {}
        self.invocation_dmg = {}

    def is_friendly(self, name):
        """A known player, or a known player's pet ("Fatereaver`s warder")."""
        low = name.lower()
        return low in self.friendly or low.split("`", 1)[0] in self.friendly

    def main_stance(self):
        """The stance active LONGEST during this fight -- fights get
        labeled by where the time actually went, not by whatever happened
        to be active at the first swing (players often open in one stance
        and settle into another). Falls back to the starting stance."""
        if self.stance_secs:
            best = max(self.stance_secs.items(), key=lambda kv: kv[1])
            if best[0] and best[0] != "?":   # "?" = touch()'s unknown key
                return best[0]
        return self.stance

    def main_invocation(self):
        if self.invocation_secs:
            best = max(self.invocation_secs.items(), key=lambda kv: kv[1])
            if best[0] and best[0] != "?":
                return best[0]
        return self.invocation

    def enemies(self):
        """(name, actor-dict) for every non-you/non-pet/non-ally actor,
        biggest involvement first -- the mobs this fight was against.
        "Spell" is the placeholder source for unattributed incoming spell
        damage, not a mob."""
        out = [(n, a) for n, a in self.actors.items()
               if n not in (YOU_LABEL, PET_LABEL, "Spell")
               and not self.is_friendly(n)]
        out.sort(key=lambda kv: -(kv[1]["dmg_in"] + kv[1]["dmg_out"]))
        return out

    def allies(self):
        """(name, damage dealt) for group members (and their pets) whose
        damage was recorded during this fight, biggest first."""
        out = [(n, a["dmg_out"]) for n, a in self.actors.items()
               if n not in (YOU_LABEL, PET_LABEL, "Spell")
               and self.is_friendly(n) and a["dmg_out"] > 0]
        out.sort(key=lambda kv: -kv[1])
        return out

    def actor(self, name):
        return self.actors.setdefault(name, _blank_actor())

    def touch(self, wall_time, stance=None, invocation=None):
        # Accumulate ACTIVE combat time: the gap since the previous damage
        # event counts in full only up to ACTIVE_GAP_CAP. Longer gaps
        # (looting, medding, running to the next mob -- anything under the
        # 45s idle timeout chains into the same Fight) are capped so
        # downtime between pulls can't dilute DPS. Each increment is also
        # attributed to the CURRENT stance/invocation, giving the fight
        # summary its per-stance time shares.
        gap = wall_time - self.last_wall
        if gap > 0:
            inc = min(gap, ACTIVE_GAP_CAP)
            self.active_secs += inc
            st = stance or "?"
            self.stance_secs[st] = self.stance_secs.get(st, 0.0) + inc
            inv = invocation or "?"
            self.invocation_secs[inv] = \
                self.invocation_secs.get(inv, 0.0) + inc
            self.last_wall = wall_time

    def elapsed(self):
        # ACTIVE combat time (see touch) -- the denominator for all rates.
        # Floor of 1s: log stamps have 1s resolution, and a fight whose only
        # hit just landed shouldn't divide by ~0.
        return max(self.active_secs, 1.0)

    def span(self):
        """Wall-clock span (first damage to last damage), gaps included --
        for the fight-timer display only; rates use elapsed()."""
        return max(self.last_wall - self.start_wall, 1.0)

    def total_dmg_out(self):
        return sum(a["dmg_out"] for a in self.actors.values())

    def total_heal_out(self):
        return sum(a["heal_out"] for a in self.actors.values())


class CombatTracker:
    """Consumes log lines for a single character; maintains the current
    fight (for live display) plus session-wide totals (for the report)."""

    def __init__(self, on_change=None, idle_timeout=IDLE_TIMEOUT,
                 self_name=None, history_maxlen=MAX_FIGHT_HISTORY):
        self.on_change = on_change
        self.idle_timeout = idle_timeout
        self.self_name = self_name
        # fight-history depth: the live meter caps it (memory in a
        # long-running overlay), but a session-report REPLAY must keep
        # every fight -- the per-fight stance/invocation tables and the
        # DPS-per-fight chart aggregate over history, and a capped deque
        # silently dropped a long session's earliest fights (None = keep
        # everything)
        self._history_maxlen = history_maxlen
        # called with each COMPLETED Fight (see _end_fight) -- encounter
        # analytics hook for the Session Report and the meter's popup
        self.fight_listeners = []

        # persistent across sessions: calibration aids only ------------------
        self.unmatched = deque(maxlen=MAX_UNMATCHED)
        # attack verbs seen that aren't in MELEE_VERBS/SKILL_VERBS -- they're
        # counted as melee, but noted here (and once in `unmatched`) so
        # calibration can promote them to the right set later
        self.unknown_verbs = {}      # verb -> count
        # ability -> "song"|"spell", learned from "You begin singing/casting
        # X" lines, so DoT ticks (which only name the ability) categorize
        # correctly. Abilities never announced that way (melody auto-play
        # logs no song name) are resolved via the spell DB in
        # _spell_category; anything still unknown defaults to "spell".
        self.ability_kind = {}

        # everything the meter/report shows is SESSION-scoped and resets on
        # the "Welcome to EverQuest Legends!" login banner
        self._reset_session_state(None)

        # The player's level, learned from /who self-entries (and, if EQL
        # uses the classic phrasing, level-up lines). Persists across
        # session resets like pet names -- relogging doesn't change your
        # level. None until first seen; buff-duration estimates fall back
        # to L50 then.
        self.player_level = None
        # The player's class combination as /who prints it ("DRU",
        # "WAR/ENC", ...). Persists across session resets like level --
        # relogging doesn't respec you; an actual respec shows up in the
        # next /who. None until first seen. Drives the meter's per-build
        # all-time data and DMG SOURCES visibility.
        self.player_classes = None

        # Your pet(s): "<Charname>`s warder"-style names match by pattern
        # alone; named pets (necro/mage style, e.g. "Jenann") are learned
        # from the pet-leader say line ("Jenann says, 'My leader is
        # Monomate.'" -- use /pet leader after summoning). Learned names
        # persist across session resets: a resummoned pet announces again,
        # and stale names are harmless.
        self.pet_names = set()
        self._pet_names_lower = set()
        self._pet_hit_out_re = self._pet_hit_in_re = None
        self._rebuild_pet_res()
        # every player name seen in a /who entry (lowercased). Persistent
        # like pet_names: players stay players across sessions. Lets pet
        # heuristics tell mobs from players (a /who name can never be a
        # charm pet), and is the substrate for future mob-vs-mob analysis.
        self.known_players = set()

    def _reset_session_state(self, wall_time):
        """Start a fresh play session -- called at init and every time the
        "Welcome to EverQuest Legends!" login banner appears. Sessions are
        the top-level data boundary: nothing the meter or report shows may
        mix data across sessions."""
        self.current = None
        self.history = deque(maxlen=self._history_maxlen)
        self._pending_crit_until = 0.0
        self._pending_crit_source = None
        self._pending_casts = {}     # name -> (spell, expires_wall)
        self._pending_thrown = None  # (item, expires_wall) -- announce-only throw
        # last spell damage that hit YOU -- an enemy cast lands its damage
        # line and its cast-on-you emote in the same instant ("Asaka L`Rei
        # hit you for 12 ... by Lifespike." + "You feel your life force
        # drain away."), so this names otherwise-ambiguous buff messages
        self._last_spell_on_you = None   # (spell, wall_time)
        self._last_activity_wall = 0.0
        self._last_line_wall = wall_time or 0.0   # newest log timestamp seen
        self.session_start_wall = wall_time

        # session totals ------------------------------------------------------
        # physical_dmg_in (melee + skill taken) is a derived property below,
        # used by the session report's Overview tab.
        self.melee_dmg_out = 0
        self.melee_dmg_in = 0
        self.skill_dmg_out = 0
        self.skill_dmg_in = 0
        self.ranged_dmg_out = 0   # bow ("shoot") + thrown weapons
        self.ranged_dmg_in = 0
        self.spell_dmg_out = 0
        self.spell_dmg_in = 0
        self.poison_dmg_out = 0   # applied weapon poisons (rogue vials)
        self.poison_dmg_in = 0
        self.song_dmg_out = 0
        self.song_dmg_in = 0
        self.ds_dmg_out = 0      # your damage shield burning attackers
        self.ds_dmg_in = 0       # mobs' damage shields burning you
        self.pet_dmg_out = 0
        self.pet_dmg_in = 0      # damage YOUR PET took (not part of your DTPS)
        self.heal_out_total = 0
        self.heal_in_total = 0
        self.kills = []              # wall timestamps of "You have slain X!"
        self.deaths = []             # wall timestamps of your own deaths
        # session-wide swing stats for YOU (feed the meter's ALL TIME
        # visualizer and anything else needing session accuracy): swings
        # that landed / missed, crit count, biggest single hit. DS ticks
        # (swing=False) are excluded, same as the per-fight actor stats.
        self.swings_hit = 0
        self.swings_missed = 0
        self.crit_count = 0
        self.biggest_hit = 0
        # completed-fight accumulators (session comparison / avg combat DPS):
        # YOUR damage and active seconds summed over every completed fight,
        # so the whole session's combat performance survives even though
        # `history` only keeps the last MAX_FIGHT_HISTORY fights.
        self.fights_completed = 0
        self.combat_active_secs = 0.0
        self.combat_dmg_out = 0
        self.combat_dmg_in = 0
        self.abilities_dmg = {}      # ability name -> _blank_ability()
        self.abilities_heal = {}     # ability name -> _blank_ability()
        self.spell_casts = {}        # spell name -> cast count
        # cast outcomes (see RESIST_OUT_RE etc.)
        self.spell_resists = {}      # spell name -> times a mob resisted it
        self.resists_incoming = 0    # spells YOU shrugged off
        self.fizzles = 0             # session totals...
        self.interrupts = 0
        self.spell_fizzles = {}      # ...and per-spell, from the named
        self.spell_interrupts = {}   # EQL phrasings
        # buffs/debuffs on YOU, recognized by exact match against the
        # spell file's own "cast on you" / "fades" messages (see
        # SpellDB.buff_landed_candidates). Labels are a spell name when the
        # message is unambiguous (or a recent cast resolves it), otherwise
        # the raw message text in quotes -- honest about ambiguity.
        self.active_buffs = {}       # label -> wall time it landed
        self._active_buff_cands = {}  # label -> set of candidate spell names
        self._buff_self_expiry = {}  # label -> estimated end, SELF-cast only
        self._buff_worst_end = {}    # label -> latest possible end (L50 caster)
        self.buff_gains = {}         # label -> times gained this session
        self.buff_fades = {}         # label -> times faded this session
        self.buff_uptime = {}        # label -> completed-stretch seconds
        self.buff_events = deque(maxlen=500)   # (wall, label, "gained"|"faded")

        self.stance = None           # unknown until re-asserted this session
        self.invocation = None
        self.stance_history = []     # (wall_time, name)
        self.invocation_history = []  # (wall_time, name)

        # rolling rate support: recent (wall_time, amount, category) events
        # for You and your pet, trimmed to ROLLING_MAX_WINDOW
        self._recent = {"dmg_out": deque(), "heal_out": deque(),
                        "dmg_in": deque(),
                        "pet_out": deque(), "pet_in": deque()}
        self._notify()

    @property
    def physical_dmg_in(self):
        return self.melee_dmg_in + self.skill_dmg_in

    def _n(self, name):
        return _norm(name, self.self_name)

    def _is_pet(self, name):
        """True if `name` is this character's own pet: either a
        "<Charname>`s warder"-style possessive name, or a proper name
        learned from the "My leader is <Charname>" say line."""
        n = name.strip().lower()
        if self.self_name and n.startswith(self.self_name.lower() + "`s "):
            return True
        return n in self._pet_names_lower

    def _register_pet(self, name):
        """A pet announced this character as its leader -- remember it and
        recompile the pet combat-line regexes to include it."""
        if name.lower() in self._pet_names_lower:
            return
        self.pet_names.add(name)
        self._pet_names_lower.add(name.lower())
        self._rebuild_pet_res()
        self._notify()

    def _unregister_pet(self, name):
        """A registered pet was SLAIN -- forget the name. Vital for CHARM
        pets, whose generic mob names ("an abhorrent") would otherwise
        credit every same-named mob as your pet for the rest of the
        session. Resummoned/recharmed pets announce again via /pet
        leader."""
        n = name.strip().lower()
        if n not in self._pet_names_lower:
            return
        self._pet_names_lower.discard(n)
        self.pet_names = {p for p in self.pet_names if p.lower() != n}
        self._rebuild_pet_res()

    def _rebuild_pet_res(self):
        """(Re)compile pet combat-line regexes anchored on the literal pet
        name(s). The generic third-party regex can't be reused here: its
        lazy groups mis-split possessive or multi-word names (the verb slot
        lands on "warder"), silently dropping pet damage."""
        alts = []
        if self.self_name:
            alts.append(re.escape(self.self_name) + r"`s [A-Za-z]+")
        alts.extend(re.escape(p) for p in sorted(self.pet_names))
        if not alts:
            self._pet_hit_out_re = self._pet_hit_in_re = None
            return
        pet = "(?:" + "|".join(alts) + ")"
        tail = (r" for (?P<amount>\d+) points? of "
                r"(?:(?P<element>[a-z]+) )?damage(?: by (?P<spell>.+?))?\.?\s*$")
        self._pet_hit_out_re = re.compile(
            TS_RE + r"(?P<source>" + pet + r") (?P<verb>[a-z]+) "
                    r"(?P<target>.+?)" + tail, re.IGNORECASE)
        self._pet_hit_in_re = re.compile(
            TS_RE + r"(?P<source>.+?) (?P<verb>[a-z]+) "
                    r"(?P<target>" + pet + r")" + tail, re.IGNORECASE)

    def _spell_category(self, ability):
        """"song" if we saw the ability started with "begin singing", else
        "spell". When the log never named the kind -- twisting via melody
        auto-play logs only "You whistle an ancient warsong.", with no song
        name, so a meter started mid-session has no "begin singing" line to
        learn from -- fall back to the game's own spell data: an ability
        only a Bard can use is a song. A later "begin singing/casting" line
        still overrides this (log evidence wins over static data)."""
        kind = self.ability_kind.get(ability)
        if kind is None:
            kind = "song" if ability.strip().lower() \
                in SPELL_DB.bard_song_names() else "spell"
            self.ability_kind[ability] = kind
        return kind

    def _is_applied_poison(self, ability):
        """True when `ability` reads as an APPLIED weapon poison rather
        than a cast spell. EQL's rogue poisons are activated disciplines
        ("You activate Asp Venom.") whose procs are "* Strike" spells --
        confirmed from a real rogue log (Monomate, 2026-07): direct procs
        log "You hit X for 22 points of poison damage by Asp Venom
        Strike.", DoT procs "A zombie has taken 16 damage from your Blood
        Siphon Strike." (attributed!), and Blood Siphon's leech heal is
        NATIVELY logged ("You healed Monomate for 16 hit points by Blood
        Siphon Strike.") so no lifetap synthesis applies.

        Recognition: never seen cast by you (cast poison-resist spells
        like necro DoTs have a "You begin casting" line, so the
        spell_casts guard keeps them in Spell), AND either poison resist
        type per the spell file, or a proc-granted "* Strike" spell --
        the latter catches Stunning Strike, the one rogue poison proc
        whose resist type is Magic. Without a spell file, fall back to
        the name ("poison" substring or the "* Strike" suffix)."""
        if not ability or ability in self.spell_casts:
            return False
        name = ability.strip().lower()
        info = SPELL_DB.lookup(ability)
        if info is not None:
            if info.is_beneficial:
                return False
            if info.resist_label == "Poison":
                return True
            return name.endswith(" strike") \
                and name in SPELL_DB.proc_names()
        return "poison" in name or name.endswith(" strike")

    # -- fight lifecycle -------------------------------------------------------
    def _ensure_fight(self, wall_time):
        if self.session_start_wall is None:
            self.session_start_wall = wall_time
        if self.current is not None and \
           wall_time - self._last_activity_wall > self.idle_timeout:
            self._end_fight()
        if self.current is None:
            self.current = Fight(wall_time, friendly=self.known_players)
            self.current.stance = self.stance
            self.current.invocation = self.invocation
        self._last_activity_wall = wall_time
        self.current.touch(wall_time, self.stance, self.invocation)

    def _end_fight(self):
        if self.current and self.current.total_dmg_out() + self.current.total_heal_out() > 0:
            self.current.ended = True
            self.history.appendleft(self.current)
            self.fights_completed += 1
            self.combat_active_secs += self.current.elapsed()
            you = self.current.actors.get(YOU_LABEL)
            if you:
                self.combat_dmg_out += you["dmg_out"]
                self.combat_dmg_in += you["dmg_in"]
            # encounter analytics: hand the completed fight to listeners
            # (Session Report encounter collection, meter fight popup)
            for fn in self.fight_listeners:
                try:
                    fn(self.current)
                except Exception:
                    pass
        self.current = None

    def avg_combat_dps(self):
        """YOUR damage per second of ACTIVE combat time, across every
        completed fight this session -- the honest 'how hard am I hitting
        when I'm actually fighting' number for comparing sessions."""
        return self.combat_dmg_out / self.combat_active_secs \
            if self.combat_active_secs > 0 else 0.0

    def avg_combat_dtps(self):
        return self.combat_dmg_in / self.combat_active_secs \
            if self.combat_active_secs > 0 else 0.0

    def _record_ally_damage(self, source, target, amount):
        """Third-party damage where a KNOWN PLAYER (or their pet) is the
        source or target -- group members fighting alongside you. Recorded
        into the CURRENT fight's actor table only: it never starts a
        fight, extends active time, or touches your session totals, so
        strangers' fights still can't pollute your numbers."""
        f = self.current
        if f is None or f.ended or amount <= 0:
            return
        if not (f.is_friendly(source) or f.is_friendly(target)):
            return
        f.actor(source)["dmg_out"] += amount
        f.actor(target)["dmg_in"] += amount

    def force_end_fight(self):
        self._end_fight()
        self._notify()

    def maybe_timeout(self):
        """Call periodically (e.g. from the UI tick) so a fight visibly ends
        even if no new lines arrive to trigger _ensure_fight. Also sweeps
        expired buffs on the same clock: without this, a song whose end
        was never logged (zoning already handled; quitting the game or
        closing the meter is not) stayed on the BUFFS list ticking up
        forever, because the sweep used to run only when new log lines
        arrived."""
        self.sweep_expired_buffs(time.time())
        if self.current and \
           time.time() - self._last_activity_wall > self.idle_timeout:
            self._end_fight()
            self._notify()

    def session_elapsed(self):
        if self.session_start_wall is None:
            return 0.001
        # the newest LOG timestamp is the session's end. Falling back to
        # real wall-clock time is only for the degenerate no-lines case --
        # it used to be the fallback whenever a session had no COMBAT,
        # which made an idle historical session look like it lasted until
        # the report was generated (a 9-hour session read as 228 hours).
        end = (self._last_line_wall or self._last_activity_wall
               or time.time())
        return max(end - self.session_start_wall, 0.001)

    def kills_per_hour(self):
        elapsed_hr = self.session_elapsed() / 3600.0
        return len(self.kills) / elapsed_hr if elapsed_hr > 0 else 0.0

    # -- rolling rates ---------------------------------------------------------
    def _push_recent(self, key, wall_time, amount, category="melee"):
        dq = self._recent[key]
        dq.append((wall_time, amount, category))
        cutoff = wall_time - ROLLING_MAX_WINDOW
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def rolling_sum(self, key, window, now=None, cats=None):
        """Total for You over the last `window` seconds (dmg_out / heal_out /
        dmg_in), optionally restricted to a tuple of categories (e.g.
        ("melee", "skill")). `now` defaults to wall-clock, which matches log
        timestamps during live tailing."""
        now = now if now is not None else time.time()
        dq = self._recent[key]
        while dq and dq[0][0] < now - ROLLING_MAX_WINDOW:
            dq.popleft()
        cutoff = now - window
        return sum(a for t, a, c in dq
                   if t >= cutoff and (cats is None or c in cats))

    def _note_verb(self, verb, line):
        if verb not in ATTACK_VERBS and verb not in self.unknown_verbs:
            self.unmatched.append(
                f"(new attack verb {verb!r}, counted as melee) {line}")
        if verb not in ATTACK_VERBS:
            self.unknown_verbs[verb] = self.unknown_verbs.get(verb, 0) + 1

    # -- recording helpers -------------------------------------------------------
    def _record_damage(self, wall_time, source, target, amount, crit=False,
                       category="melee", ability=None, swing=True):
        self._ensure_fight(wall_time)
        src_label = self._n(source)
        if self._is_pet(src_label):
            # Your pet is its OWN actor with its own DPS/DTPS and swing
            # stats, separate from yours. Its damage keeps whatever category
            # it was parsed as (melee / skill / spell), so all pet damage
            # types are captured.
            src_label = PET_LABEL
        src = self.current.actor(src_label)
        src["dmg_out"] += amount
        src["last_hit_wall"] = wall_time
        if swing:
            src["hits"] += 1
            src["biggest_hit"] = max(src["biggest_hit"], amount)
            if crit:
                src["crits"] += 1
                src["last_crit_wall"] = wall_time
        src[f"{category}_dmg_out"] += amount
        tgt_label = self._n(target)
        if self._is_pet(tgt_label):
            tgt_label = PET_LABEL
        tgt = self.current.actor(tgt_label)
        tgt["dmg_in"] += amount
        tgt["last_hit_wall"] = max(tgt["last_hit_wall"], wall_time)
        tgt[f"{category}_dmg_in"] += amount

        if src_label == YOU_LABEL:
            self._push_recent("dmg_out", wall_time, amount, category)
            setattr(self, f"{category}_dmg_out",
                    getattr(self, f"{category}_dmg_out") + amount)
            # damage attributed to the stance/invocation active RIGHT NOW
            # (fight summary's per-stance damage shares)
            st = self.stance or "?"
            self.current.stance_dmg[st] = \
                self.current.stance_dmg.get(st, 0) + amount
            inv = self.invocation or "?"
            self.current.invocation_dmg[inv] = \
                self.current.invocation_dmg.get(inv, 0) + amount
            if swing:
                self.swings_hit += 1
                self.biggest_hit = max(self.biggest_hit, amount)
                if crit:
                    self.crit_count += 1
            for dst in (self.abilities_dmg, self.current.abilities_dmg):
                ab = dst.setdefault(ability or MELEE_ABILITY,
                                    _blank_ability())
                ab["total"] += amount
                ab["hits"] += 1
                ab["biggest"] = max(ab["biggest"], amount)
                ab["category"] = category
                if crit:
                    ab["crits"] += 1
                if ability and category == "spell":
                    ab["proc"] = ability not in self.spell_casts \
                        and ability.lower() in SPELL_DB.proc_names()
        elif src_label == PET_LABEL:
            self._push_recent("pet_out", wall_time, amount, category)
            self.pet_dmg_out += amount
            for dst in (self.abilities_dmg, self.current.abilities_dmg):
                ab = dst.setdefault(
                    f"Pet: {ability or MELEE_ABILITY}", _blank_ability())
                ab["total"] += amount
                ab["hits"] += 1
                ab["biggest"] = max(ab["biggest"], amount)
                ab["category"] = "pet"
                if crit:
                    ab["crits"] += 1
        elif tgt_label == YOU_LABEL:
            self._push_recent("dmg_in", wall_time, amount, category)
            setattr(self, f"{category}_dmg_in",
                    getattr(self, f"{category}_dmg_in") + amount)
        if tgt_label == PET_LABEL:
            self._push_recent("pet_in", wall_time, amount, category)
            self.pet_dmg_in += amount
        self._notify()

    def _record_miss(self, wall_time, source, target):
        # Misses count toward accuracy but never start or extend a fight --
        # combat runs on damage/heal accumulation only (whiffing at a mob
        # for 30s with no damage landing in either direction isn't a fight).
        if self.current is None or \
           wall_time - self._last_activity_wall > self.idle_timeout:
            return
        src_label = self._n(source)
        src = self.current.actor(src_label)
        src["misses"] += 1
        if src_label == YOU_LABEL:
            self.swings_missed += 1
        self._notify()

    def _record_heal(self, wall_time, source, target, amount, ability=None):
        # Combat is damage-bounded: heals never start or extend one. They
        # attach to the live Combat's actors if one exists, and always count
        # toward session totals and the rolling HPS window.
        src_label = self._n(source)
        tgt_label = self._n(target)
        in_combat = self.current is not None and \
            wall_time - self._last_activity_wall <= self.idle_timeout
        if in_combat:
            src = self.current.actor(src_label)
            src["heal_out"] += amount
            src["last_hit_wall"] = wall_time
            self.current.actor(tgt_label)["heal_in"] += amount

        if src_label == YOU_LABEL:
            self._push_recent("heal_out", wall_time, amount)
            self.heal_out_total += amount
            dsts = [self.abilities_heal]
            if in_combat:
                dsts.append(self.current.abilities_heal)
            for dst in dsts:
                ab = dst.setdefault(ability or "Heal", _blank_ability())
                ab["total"] += amount
                ab["hits"] += 1
                ab["biggest"] = max(ab["biggest"], amount)
        if tgt_label == YOU_LABEL:
            self.heal_in_total += amount
        self._notify()

    def _maybe_record_lifetap_heal(self, wall_time, ability, amount):
        """See LIFETAP_SPELLS: your own casts of these log the damage half
        but never the heal half, unlike everyone else's. Synthesize the
        missing self-heal 1:1 with the damage that WAS logged. Taps are
        recognized from the spell file's own target_type flag (with the
        hand-confirmed name set as fallback when no spell file is found)."""
        if not ability:
            return
        name = ability.lower()
        if name in SPELL_DB.lifetap_names() or name in LIFETAP_SPELLS:
            self._record_heal(wall_time, YOU_LABEL, YOU_LABEL, amount,
                              ability=ability)

    # -- buff/debuff messages on YOU ---------------------------------------------
    # A buff landing on you ("You feel armored.") or wearing off ("Your
    # armor fades.") logs only the spell's message text -- no spell name,
    # no caster. spells_us_str.txt maps those messages back to spells (see
    # SpellDB), which is the only way to track buff coverage from the log.

    # Buff cast times routinely exceed CAST_WINDOW (which is tuned for
    # damage attribution), so "You begin casting X" stays usable for
    # naming an ambiguous landed-message for this much longer.
    BUFF_CAST_SLACK = 8.0

    def _resolve_buff_label(self, candidates, wall_time):
        """Pick a display label for a landed buff message that maps to one
        or more spells. Unambiguous -> the spell name. Ambiguous -> a
        recent cast of ours that's among the candidates, else a candidate
        already tracked as active (recast of the same thing), else None
        (the caller falls back to quoting the message text -- honest,
        never a guess)."""
        if len(candidates) == 1:
            return candidates[0]
        pending = self._pending_casts.get(YOU_LABEL)
        if pending and pending[1] + self.BUFF_CAST_SLACK >= wall_time \
           and pending[0] in candidates:
            return pending[0]
        # an enemy cast logs its damage line in the same instant as its
        # cast-on-you emote -- if the spell that just hit you is among the
        # candidates, that's the one (confirmed: Lifespike's damage/heal
        # lines bracket "You feel your life force drain away.")
        hit = self._last_spell_on_you
        if hit and abs(wall_time - hit[1]) <= 2.0 and hit[0] in candidates:
            return hit[0]
        active = [c for c in candidates if c in self.active_buffs]
        if len(active) == 1:
            return active[0]
        return None

    def _buff_est_end(self, label, cand_filter=None):
        """Estimated natural-expiry wall time of an active buff label, or
        None when unknowable (not active, permanent, or candidates
        disagree). Quoted-message labels use their candidates' shared
        duration estimate -- ambiguity doesn't matter when every candidate
        would expire at the same time. `cand_filter` narrows a quoted
        label's candidates (e.g. to the ones a fade message could mean)."""
        start = self.active_buffs.get(label)
        if start is None:
            return None
        lvl = self.player_level or 50
        info = SPELL_DB.lookup(label)
        if info:
            dur = info.duration_seconds(lvl)
        else:
            cands = self._active_buff_cands.get(label) or set()
            if cand_filter:
                cands = cands & set(cand_filter)
            durs = {(i.duration_seconds(lvl) if i else None)
                    for i in (SPELL_DB.lookup(c) for c in cands)}
            dur = durs.pop() if len(durs) == 1 else None
        if not dur or dur <= 0:      # unknown, instant, or permanent
            return None
        return start + dur

    def _worst_case_end(self, label, start):
        """Latest moment ANY candidate reading of this buff could still be
        active, assuming a level-50 caster (EQL's cap). None when
        unknowable (a candidate is missing from the spell file or
        permanent) -- those entries only close on a fade line, death, or
        zone. Lets debuffs whose ending is never logged ("You feel your
        skin freeze.") drop off instead of clogging the BUFFS list."""
        names = [label] if not label.startswith('"') \
            else self._active_buff_cands.get(label) or ()
        worst = 0
        for n in names:
            info = SPELL_DB.lookup(n)
            if info is None:
                # unknown spell -- but a SONG can't run long no matter
                # what it is (see SONG_FALLBACK_SECS); everything else
                # stays unknowable
                if self.ability_kind.get(n) == "song" \
                   or n.strip().lower() in SPELL_DB.bard_song_names():
                    worst = max(worst, SONG_FALLBACK_SECS)
                    continue
                return None
            dur = info.duration_seconds(EQL_LEVEL_CAP)
            if dur == -1:
                return None
            worst = max(worst, dur)
        return start + worst if worst > 0 else None

    def sweep_expired_buffs(self, now=None):
        """Close active buffs past their estimated end. SELF-cast ends are
        reliable (you are the caster; 2s of slack absorbs timestamp
        rounding); everything else closes only past its WORST-CASE end --
        the longest any candidate could run for a level-50 caster, plus a
        tick of slack. Uptime banks at the estimated end, not the sweep
        moment. Called on every log line AND periodically from the UI tick
        (maybe_timeout) so buffs expire even while the log is quiet -- e.g.
        songs seeded from an old log tail after the meter was relaunched."""
        now = now if now is not None else time.time()
        for src, grace in ((self._buff_self_expiry, 2.0),
                           (self._buff_worst_end, 6.0)):
            for lbl, end in list(src.items()):
                if now > end + grace:
                    self._close_buff(lbl, end)
                    self.buff_events.append((end, lbl, "faded"))
                    self._notify()

    def _close_buff(self, label, wall_time):
        start = self.active_buffs.pop(label, None)
        self._active_buff_cands.pop(label, None)
        self._buff_self_expiry.pop(label, None)
        self._buff_worst_end.pop(label, None)
        if start is not None:
            self.buff_uptime[label] = \
                self.buff_uptime.get(label, 0.0) + max(wall_time - start, 0.0)

    def _close_fade_trigger_parent(self, trigger_ids, wall_time):
        """An instant "trigger" landing can mark the END of the buff that
        spawned it: seed heals (the Budding Heal line) cast their "* Heal
        Trigger" via SPA 289 exactly when the seed expires, and the seed
        itself prints no fade line of its own. Close the active buff that
        links to one of these trigger ids."""
        def links(label):
            names = [label] if not label.startswith('"') \
                else self._active_buff_cands.get(label) or ()
            return any(
                (info := SPELL_DB.lookup(n)) and
                info.fade_trigger_ids() & trigger_ids
                for n in names)
        matches = [lbl for lbl in self.active_buffs if links(lbl)]
        if not matches:
            return
        if len(matches) > 1:   # several seeds up -- the one nearest its end
            matches.sort(key=lambda l: abs(
                wall_time - (self._buff_est_end(l) or float("inf"))))
        label = matches[0]
        self._close_buff(label, wall_time)
        self.buff_fades[label] = self.buff_fades.get(label, 0) + 1
        self.buff_events.append((wall_time, label, "faded"))
        self._notify()

    def _clear_stale_buffs_on_zone(self, wall_time):
        """Zoning silently strips bard songs (regular buffs persist), and
        the log prints no fade lines for them. Close an active entry when
        NO reading of it could still be up after the zone: every candidate
        is a song, instant, or already past its own duration estimate.
        Anything that might legitimately persist (permanent, unknown
        duration, still inside its estimate) stays."""
        lvl = self.player_level or 50
        song_names = SPELL_DB.bard_song_names()

        def could_survive(name, start):
            if name.lower() in song_names:
                return False         # songs never survive a zone
            info = SPELL_DB.lookup(name)
            if info is None:
                return True          # unknown spell -- assume it can
            dur = info.duration_seconds(lvl)
            if dur == -1:
                return True          # permanent until removed
            if dur <= 0:
                return False         # instant -- was never really up
            return start + dur > wall_time   # inside its estimate?

        for label in list(self.active_buffs):
            start = self.active_buffs[label]
            if label.startswith('"'):
                names = self._active_buff_cands.get(label) or ()
                survives = any(could_survive(n, start) for n in names) \
                    if names else True
            else:
                survives = could_survive(label, start)
            if not survives:
                self._close_buff(label, wall_time)
                self.buff_events.append((wall_time, label, "faded"))

    def _clear_all_buffs(self, wall_time):
        """Death strips every buff and debuff, but the log prints no fade
        messages for them -- close them all here so the BUFFS list doesn't
        carry ghosts past the respawn. Uptime stretches are banked as
        usual; buff_fades counts stay untouched (they count actual fade
        messages, and death isn't one)."""
        for label in list(self.active_buffs):
            self._close_buff(label, wall_time)
            self.buff_events.append((wall_time, label, "faded"))

    def _handle_buff_message(self, wall_time, body):
        """Try `body` (line text after the timestamp, tags stripped)
        against the spell file's cast-on-you / fade message tables.
        Returns True if the line was recognized as a buff event."""
        landed = SPELL_DB.buff_landed_candidates(body)
        faded = SPELL_DB.buff_faded_candidates(body)
        if landed and faded:
            # The same text can be one spell's landed message AND another's
            # fade message ("You slow down." = a snare landing OR the
            # Selo's-line run speed fading). If something currently active
            # could be the fading spell, read it as the fade; otherwise
            # nothing matching is up, so it must be the landing.
            fade_set = set(faded)
            could_fade = any(
                lbl in fade_set
                or (self._active_buff_cands.get(lbl) or set()) & fade_set
                for lbl in self.active_buffs)
            if could_fade:
                landed = None
            else:
                # Nothing active matches the fade reading -- but that does
                # NOT make it a landing: Selo's "You slow down." wear-off
                # can print after zoning has already stripped the song's
                # entry. Which meaning is right is unknowable, and tracking
                # a possibly-phantom debuff clogs the BUFFS list -- drop it.
                return True
        if landed:
            label = self._resolve_buff_label(landed, wall_time) \
                or f'"{body.strip()}"'
            # Instant spells (direct heals, lifetaps, ...) log a "cast on
            # you" message too, but nothing lands that could ever fade --
            # tracking them would leave immortal entries on the BUFFS list.
            # MAJORITY of candidates instant is enough to read the message
            # as the instant: "You feel your life force drain away." maps
            # to ~180 spells, 137 of them instant enemy lifetaps (target
            # type 13) vs a handful of drain-over-time variants (and one
            # permanent self-only necro buff that used to immortalize the
            # row). Confirmed by play: the message fires per TAP. A DoT
            # variant's damage still records through its own damage lines.
            # (An unambiguous or pending-resolved label has ONE candidate,
            # so named duration'd spells are unaffected.)
            info = SPELL_DB.lookup(label)
            infos = [info] if info else \
                [SPELL_DB.lookup(c) for c in landed]
            n_inst = sum(1 for i in infos if i and i.duration_ticks() == 0)
            if infos and all(infos) and n_inst * 2 > len(infos):
                # ...but an instant can still be the death knell of a buff
                # that spawns it on expiry (seed heals' "* Heal Trigger")
                self._close_fade_trigger_parent(
                    {i.id for i in infos}, wall_time)
                return True
            if label in self.active_buffs:  # recast before it faded --
                self._close_buff(label, wall_time)  # bank the stretch
            self.active_buffs[label] = wall_time
            self._active_buff_cands[label] = set(landed)
            # Buffs YOU cast expire on their own clock (see the sweep in
            # handle_line): the caster's level is your own, so the duration
            # estimate doesn't suffer the unknown-caster-level problem, and
            # some (seed heals at full health) print nothing when they end.
            pending = self._pending_casts.get(YOU_LABEL)
            dur = info.duration_seconds(self.player_level or 50) \
                if info else 0
            if pending and pending[0] == label and dur and dur > 0 \
               and pending[1] + self.BUFF_CAST_SLACK >= wall_time:
                self._buff_self_expiry[label] = wall_time + dur
                self._buff_worst_end.pop(label, None)
            else:
                self._buff_self_expiry.pop(label, None)
                # not provably yours -- cap it at the longest any candidate
                # could run for a max-level caster (see _worst_case_end)
                end = self._worst_case_end(label, wall_time)
                if end is not None:
                    self._buff_worst_end[label] = end
                else:
                    self._buff_worst_end.pop(label, None)
            self.buff_gains[label] = self.buff_gains.get(label, 0) + 1
            self.buff_events.append((wall_time, label, "gained"))
            self._notify()
            return True
        if faded:
            cand_set = set(faded)
            # Active labels this fade could close: tracked under a candidate
            # name, or under a quoted landed-message whose candidate set
            # intersects ours (a fade and its landed message name the same
            # spell(s)).
            matches = [lbl for lbl in self.active_buffs
                       if lbl in cand_set
                       or (self._active_buff_cands.get(lbl) or set()) & cand_set]
            if len(matches) == 1:
                label = matches[0]
            elif matches:
                # Several active buffs share this fade message ("Your surge
                # of strength fades." = Anthem de Arms AND Yaulp) -- only
                # one actually ended: the one nearest its estimated natural
                # end. Permanent/unknown-duration matches score last; if no
                # match has a usable estimate, close nothing and record the
                # fade under the quoted text (honest, never a guess).
                def _score(lbl):
                    end = self._buff_est_end(lbl, cand_set)
                    return abs(wall_time - end) if end is not None \
                        else float("inf")
                best = min(matches, key=_score)
                label = best if _score(best) != float("inf") \
                    else f'"{body.strip()}"'
            else:
                # nothing active matches -- still count the fade
                label = faded[0] if len(faded) == 1 else f'"{body.strip()}"'
            self._close_buff(label, wall_time)
            self.buff_fades[label] = self.buff_fades.get(label, 0) + 1
            self.buff_events.append((wall_time, label, "faded"))
            self._notify()
            return True
        return False

    def buff_rows(self, now=None):
        """Sorted (label, stats) pairs for every buff/debuff message seen
        on you this session: gained/faded counts and total uptime seconds
        (still-active buffs count up to `now`, defaulting to the newest
        log timestamp seen -- NOT wall-clock, so replaying an old log
        doesn't inflate active buffs). Sorted by uptime, longest first."""
        now = now if now is not None else \
            (self._last_line_wall or time.time())
        rows = {}
        for label in set(self.buff_gains) | set(self.buff_fades):
            up = self.buff_uptime.get(label, 0.0)
            start = self.active_buffs.get(label)
            if start is not None:
                up += max(now - start, 0.0)
            rows[label] = {"gained": self.buff_gains.get(label, 0),
                           "faded": self.buff_fades.get(label, 0),
                           "uptime": up,
                           "active": start is not None}
        return sorted(rows.items(), key=lambda kv: -kv[1]["uptime"])

    def _notify(self):
        if self.on_change:
            self.on_change()

    def _drop_pending_cast(self, who, spell=None):
        """Cancel `who`'s begin-casting attribution window -- their cast
        fizzled or was interrupted, so nothing can land from it. A named
        outcome line only cancels a matching pending spell; the nameless
        classic forms cancel whatever is pending."""
        pending = self._pending_casts.get(who)
        if pending and (spell is None or pending[0] == spell):
            del self._pending_casts[who]

    def _consume_crit(self, source_label, wall_time):
        if source_label == self._pending_crit_source and \
           wall_time <= self._pending_crit_until:
            self._pending_crit_source = None
            self._pending_crit_until = 0.0
            return True
        return False

    def _set_stance(self, wall_time, name):
        if name != self.stance:
            self.stance = name
            self.stance_history.append((wall_time, name))
            if self.current:
                self.current.stance = name
            self._notify()

    def _set_invocation(self, wall_time, name):
        if name != self.invocation:
            self.invocation = name
            self.invocation_history.append((wall_time, name))
            if self.current:
                self.current.invocation = name
            self._notify()

    # -- line handling -------------------------------------------------------
    def handle_line(self, line):
        # Use the log line's own timestamp as the clock, not real wall-clock
        # time. For live tailing the two are nearly identical, but the
        # session report replays an entire historical log in a fraction of
        # a second -- wall-clock time would make the whole session look like
        # it took milliseconds (and kills/hour would be nonsense). Log
        # timestamps only have 1-second resolution, which is fine for fight
        # durations and session totals.
        wall_time = time.time()
        m_ts = TS_ONLY_RE.match(line)
        stamped = False
        if m_ts:
            try:
                wall_time = datetime.strptime(m_ts.group("ts"), LOG_TS_FMT).timestamp()
                stamped = True
            except ValueError:
                pass
        if self.session_start_wall is None:
            self.session_start_wall = wall_time
        if stamped:
            # only REAL log timestamps advance the session-end clock: the
            # time.time() fallback for stray unstamped lines would make a
            # replayed historical session end "now"
            self._last_line_wall = max(self._last_line_wall, wall_time)

        # sweep expired buffs (see sweep_expired_buffs)
        self.sweep_expired_buffs(wall_time)

        # session boundary: login banner -> wipe ALL session-scoped state
        if SESSION_START_RE.match(line):
            self._reset_session_state(wall_time)
            return

        if ZONE_RE.match(line):
            self._clear_stale_buffs_on_zone(wall_time)
            return

        # pet ownership announcement ("/pet leader")
        m = PET_LEADER_RE.match(line)
        if m:
            if self.self_name and \
               m.group("owner").lower() == self.self_name.lower() \
               and m.group("pet").lower() not in self.known_players:
                # known_players guard: a charm pet is a MOB; a name we've
                # seen in /who is a player and can never be your pet
                self._register_pet(m.group("pet"))
            return

        # pet attack announcement: a pet that tells YOU "Attacking X
        # Master." is YOUR pet (tells go only to the master -- see
        # PET_ATTACK_RE) -- register it, /pet leader not required
        m = PET_ATTACK_RE.match(line)
        if m:
            pet = m.group("pet")
            if pet.lower() not in self.known_players:
                self._register_pet(pet)
            return

        # /who entries: learn the player's own level (and remember every
        # player name seen -- the mob-vs-player distinction other
        # heuristics lean on); consume all of them
        m = WHO_ENTRY_RE.match(line)
        if m:
            self.known_players.add(m.group("name").lower())
            if self.self_name and \
               m.group("name").lower() == self.self_name.lower():
                self.player_level = int(m.group("level"))
                self.player_classes = m.group("classes")
                self._notify()
            return

        m = GAIN_LEVEL_RE.match(line)
        if m:
            self.player_level = int(m.group("level"))
            self._notify()
            return

        # group join/leave/chat: remember the name as a player (groupmates
        # are the allies whose damage encounter analytics report), then
        # fall through -- group tells are also plain chat lines below
        m = GROUP_PLAYER_RE.match(line)
        if m:
            self.known_players.add(m.group("name").lower())

        # chat (tell/say/shout/auction) -- never combat; must come after
        # the pet-leader "says" line above
        if CHAT_RE.match(line):
            return

        # inline tag suffixes -- "(Critical)", "(Riposte)", ... -- strip them
        # all before matching anything else; only Critical changes stats
        is_crit = False
        while True:
            m_tag = TAG_SUFFIX_RE.match(line)
            if not m_tag:
                break
            line = m_tag.group("body")
            if m_tag.group("tag") == "Critical":
                is_crit = True

        if STANCE_CHANGING_RE.match(line) or INVOCATION_CHANGING_RE.match(line):
            return  # "You begin to change your stance/invocation." -- no info yet

        m = STANCE_ASSUME_RE.match(line)
        if m:
            self._set_stance(wall_time, _resolve_descriptor(m.group("descriptor"), STANCE_DESCRIPTORS))
            return

        m = INVOCATION_RECITE_RE.match(line)
        if m:
            self._set_invocation(wall_time, _resolve_descriptor(m.group("descriptor"), INVOCATION_DESCRIPTORS))
            return

        m = CRIT_RE.match(line)
        if m:
            self._pending_crit_until = wall_time + CRIT_WINDOW
            self._pending_crit_source = self._n(m.group("source") or YOU_LABEL)
            return

        m = CASTING_RE.match(line)
        if m:
            who = self._n(m.group("who"))
            spell = m.group("spell")
            self.ability_kind[spell] = \
                "song" if m.group("how") == "singing" else "spell"
            self._pending_casts[who] = (spell, wall_time + CAST_WINDOW)
            if who == YOU_LABEL:
                self.spell_casts[spell] = self.spell_casts.get(spell, 0) + 1
                if self.current is not None and \
                   wall_time - self._last_activity_wall <= self.idle_timeout:
                    fc = self.current.spell_casts
                    fc[spell] = fc.get(spell, 0) + 1
            return

        # cast outcomes: resists / fizzles / interrupts (EQL phrasings --
        # see the regex block). A fizzled/interrupted cast never lands, so
        # the caster's pending-cast window is cancelled too: later damage
        # or an ambiguous buff-landed line must not attribute to it.
        m = RESIST_OUT_RE.match(line) or RESIST_OUT_CLASSIC_RE.match(line)
        if m:
            spell = m.group("spell")
            self.spell_resists[spell] = self.spell_resists.get(spell, 0) + 1
            if self.current is not None:
                # per-fight tally for the meter's RESISTED block; resists
                # don't start or extend a Combat, same as misses/casts
                self.current.spell_resists[spell] = \
                    self.current.spell_resists.get(spell, 0) + 1
            self._notify()
            return
        m = RESIST_IN_RE.match(line) or RESIST_IN_CLASSIC_RE.match(line)
        if m:
            self.resists_incoming += 1
            if self.current is not None:
                # per-fight tally for the fight summary's "you resisted"
                fr = self.current.you_resisted
                spell = m.group("spell")
                fr[spell] = fr.get(spell, 0) + 1
            self._notify()
            return
        m = FIZZLE_RE.match(line)
        if m:
            self.fizzles += 1
            spell = m.group("spell")
            if spell:
                self.spell_fizzles[spell] = \
                    self.spell_fizzles.get(spell, 0) + 1
            self._drop_pending_cast(YOU_LABEL, spell)
            self._notify()
            return
        m = INTERRUPT_RE.match(line)
        if m:
            self.interrupts += 1
            spell = m.group("spell")
            if spell:
                self.spell_interrupts[spell] = \
                    self.spell_interrupts.get(spell, 0) + 1
            self._drop_pending_cast(YOU_LABEL, spell)
            self._notify()
            return
        m = OTHER_FIZZLE_RE.match(line) or OTHER_INTERRUPT_RE.match(line)
        if m:
            # third party's failed cast -- not counted, but their pending
            # cast can't be allowed to soak up later attribution
            self._drop_pending_cast(self._n(m.group("who")))
            return
        if HEAL_WITHIN_RE.match(line):
            return   # delayed-heal trigger on someone else -- no amount to record

        # thrown weapons -- must run before SELF_HIT/SELF_MISS: the generic
        # patterns would otherwise mis-split "your <Item> at <Target>"
        m = THROWN_HIT_RE.match(line)
        if m:
            crit = is_crit or self._consume_crit(YOU_LABEL, wall_time)
            self._record_damage(wall_time, YOU_LABEL, m.group("target"),
                                int(m.group("amount")), crit=crit,
                                category="ranged",
                                ability=f"Thrown: {m.group('item')}")
            return
        m = THROWN_ANNOUNCE_RE.match(line)
        if m:
            # announce-only form: the damage line (if any) follows on its
            # own -- remember the throw so that hit counts as ranged
            self._pending_thrown = (m.group("item"),
                                    wall_time + THROWN_WINDOW)
            return
        m = THROWN_MISS_RE.match(line)
        if m:
            self._record_miss(wall_time, YOU_LABEL, m.group("target"))
            return
        if RANGED_REFUSAL_RE.match(line):
            return   # out of range / no line of sight -- nothing to count

        m = SELF_HIT_RE.match(line)
        if m and m.group("verb") not in NON_ATTACK_VERBS:
            if m.group("spell") or m.group("element"):
                # "...for N points of fire damage by Burst of Flame."
                ability = m.group("spell") or \
                    f"{m.group('element').capitalize()} damage"
                category = "poison" if self._is_applied_poison(ability) \
                    else self._spell_category(ability)
            else:
                self._note_verb(m.group("verb"), line)
                category, ability = _attack_category_and_ability(m.group("verb"))
                pt = self._pending_thrown
                if pt is not None and category == "melee":
                    # a plain-verb hit right after an announce-only throw
                    # IS the throw landing
                    if wall_time <= pt[1]:
                        category, ability = "ranged", f"Thrown: {pt[0]}"
                    self._pending_thrown = None
            crit = is_crit or self._consume_crit(YOU_LABEL, wall_time)
            self._record_damage(wall_time, YOU_LABEL,
                                _clean_verb_target(m.group("verb"),
                                                   m.group("target")),
                                int(m.group("amount")), crit=crit,
                                category=category, ability=ability)
            self._maybe_record_lifetap_heal(wall_time, ability,
                                            int(m.group("amount")))
            return

        m = SELF_TAKEN_RE.match(line)
        if m and m.group("verb") not in NON_ATTACK_VERBS:
            if m.group("spell") or m.group("element"):
                category = "spell"
                if m.group("spell"):
                    self._last_spell_on_you = (m.group("spell"), wall_time)
            else:
                self._note_verb(m.group("verb"), line)
                category, _ability = _attack_category_and_ability(m.group("verb"))
            crit = is_crit or self._consume_crit(self._n(m.group("source")), wall_time)
            self._record_damage(wall_time, m.group("source"), YOU_LABEL,
                                int(m.group("amount")), crit=crit, category=category)
            return

        m = INCOMING_DOT_RE.match(line)
        if m:
            self._last_spell_on_you = (m.group("spell"), wall_time)
            self._record_damage(wall_time, m.group("source") or m.group("spell"),
                                YOU_LABEL, int(m.group("amount")),
                                category="spell")
            return

        m = SELF_MISS_RE.match(line) or SELF_MISS_FALLBACK_RE.match(line)
        if m:
            self._record_miss(wall_time, YOU_LABEL, m.group("target"))
            return

        m = OTHER_MISS_ON_SELF_RE.match(line) or SELF_DODGED_FALLBACK_RE.match(line)
        if m:
            self._record_miss(wall_time, m.group("source"), YOU_LABEL)
            return

        m = NONMELEE_ATTR_RE.match(line)
        if m:
            target = m.group("target")
            amount = int(m.group("amount"))
            caster = m.group("caster")
            source = YOU_LABEL if caster is None else caster
            spell = m.group("spell")
            crit = is_crit or self._consume_crit(self._n(source), wall_time)
            if self._n(source) == YOU_LABEL:
                # your poison procs tick ATTRIBUTED too ("...from your
                # Blood Siphon Strike.") -- same poison-vs-spell split as
                # the caster-less form
                cat = "poison" if self._is_applied_poison(spell) \
                    else self._spell_category(spell)
            else:
                cat = "spell"
            self._record_damage(wall_time, source, target, amount, crit=crit,
                                category=cat, ability=spell)
            if self._n(source) == YOU_LABEL:
                self._maybe_record_lifetap_heal(wall_time, spell, amount)
            return

        m = PROC_DOT_RE.match(line)
        if m:
            # "An orc warrior has taken 6 damage by Weak Poison." -- no
            # caster is named. When the target is a MOB, this is your own
            # applied poison/proc. When the target is YOUR PET (confirmed in
            # a real log: mobs' poisons tick on pets with this same line),
            # it's incoming damage -- attributing it to You would inflate
            # your spell output. Confirmed in a real log (2026-07): the
            # same caster-less line also prints for a MOB's DoT ticking on
            # ANOTHER PLAYER ("Lekn has taken 10 damage by Poison Bolt.")
            # -- a /who-known player target is never your poison, and a
            # tick on YOU is incoming.
            target = m.group("target")
            tgt_label = self._n(target)
            if self._is_pet(tgt_label):
                self._record_damage(wall_time, "Unknown", target,
                                    int(m.group("amount")), category="spell")
            elif tgt_label == YOU_LABEL:
                self._last_spell_on_you = (m.group("spell"), wall_time)
                self._record_damage(wall_time, m.group("spell"), YOU_LABEL,
                                    int(m.group("amount")), category="spell")
            elif tgt_label.lower() in self.known_players \
                    or re.fullmatch(r"[A-Z][a-z]+", tgt_label):
                # someone else's incoming DoT -- not yours. Player names
                # are a single capitalized word; mobs are article-prefixed
                # ("an orc warrior") or multi-word named mobs ("Priest
                # Amiaz"). Trade-off: a single-word named MOB you poison
                # would be skipped here -- rarer and cheaper than crediting
                # every groupmate's incoming DoT to you.
                pass
            else:
                spell = m.group("spell")
                cat = "poison" if self._is_applied_poison(spell) \
                    else self._spell_category(spell)
                self._record_damage(wall_time, YOU_LABEL, target,
                                    int(m.group("amount")),
                                    category=cat, ability=spell)
                self._maybe_record_lifetap_heal(wall_time, spell,
                                                int(m.group("amount")))
            return

        # -- third-party combat: only your own pet matters; everything else
        #    is matched-and-ignored so groupmates' fights can't extend your
        #    fight window or spam `unmatched` --------------------------------
        if self._pet_hit_out_re:
            m = self._pet_hit_out_re.match(line)
            if m:
                # pet damage keeps its real category (melee/skill/spell) so
                # every pet damage type is captured; _record_damage routes
                # pet sources to the PET actor via _is_pet()
                if m.group("spell") or m.group("element"):
                    category = "spell"
                    ability = m.group("spell") or \
                        f"{m.group('element').capitalize()} damage"
                else:
                    category, ability = \
                        _attack_category_and_ability(m.group("verb"))
                self._record_damage(wall_time, m.group("source"),
                                    _clean_verb_target(m.group("verb"),
                                                       m.group("target")),
                                    int(m.group("amount")),
                                    crit=is_crit, category=category,
                                    ability=ability)
                return
            m = self._pet_hit_in_re.match(line)
            if m:
                category = "spell" if (m.group("spell") or m.group("element")) \
                    else _attack_category_and_ability(m.group("verb"))[0]
                self._record_damage(wall_time, m.group("source"),
                                    m.group("target"), int(m.group("amount")),
                                    category=category)
                return

        m = OTHER_DOT_RE.match(line)
        if m:
            if self._is_pet(self._n(m.group("target"))):
                self._record_damage(wall_time, m.group("source"),
                                    m.group("target"),
                                    int(m.group("amount")), category="spell")
            elif self._is_pet(self._n(m.group("source"))):
                # DoT ticking FROM your pet (charm pets especially) --
                # "A gnoll has taken 10 damage from Tainted Breath by an
                # abhorrent."
                self._record_damage(wall_time, m.group("source"),
                                    m.group("target"),
                                    int(m.group("amount")),
                                    category="spell",
                                    ability=m.group("spell"))
            else:
                self._record_ally_damage(m.group("source"),
                                         m.group("target"),
                                         int(m.group("amount")))
            return

        m = OTHER_HIT_RE.match(line)
        if m and m.group("verb") not in NON_ATTACK_VERBS:
            # someone else's swing. If a GROUP MEMBER is involved, record
            # the numbers into the current fight's actor table so the
            # encounter analytics can report their contribution -- but
            # never start a fight or extend combat time over it: fight
            # boundaries stay driven by YOUR OWN combat alone.
            ms = _other_hit_strict().match(line)
            if ms:
                self._record_ally_damage(
                    ms.group("source"),
                    _clean_verb_target(ms.group("verb"),
                                       ms.group("target")),
                    int(ms.group("amount")))
            return

        m = OTHER_SLAIN_RE.match(line)
        if m:
            if self._is_pet(self._n(m.group("source"))):
                self.kills.append(wall_time)   # your pet's kill is your kill
                if self.current is not None:
                    self.current.kills += 1
                self._notify()
            if self._is_pet(self._n(m.group("target"))):
                self._unregister_pet(m.group("target"))   # pet died
            return

        m = MEND_RE.match(line)
        if m:
            # Mend prints no amount, so it can't contribute to HPS -- but
            # it's still recorded as an ability use (shows in the session
            # report with 0 healing rather than vanishing).
            self._record_heal(wall_time, YOU_LABEL, YOU_LABEL, 0,
                              ability="Mend")
            return

        m = DS_SELF_RE.match(line)
        if m:
            # your damage shield ("... is pierced by YOUR thorns for N ...")
            self._record_damage(wall_time, YOU_LABEL, m.group("target"),
                                int(m.group("amount")), category="ds",
                                ability=f"Damage Shield ({m.group('kind')})",
                                swing=False)
            return

        m = DS_TAKEN_RE.match(line)
        if m:
            # a mob's damage shield burning you when your hit lands
            self._record_damage(wall_time, m.group("owner"), YOU_LABEL,
                                int(m.group("amount")), category="ds",
                                swing=False)
            return

        m = DS_OTHER_RE.match(line)
        if m:
            return   # someone else's damage shield -- matched-and-ignored

        m = NONMELEE_FALLBACK_RE.match(line)
        if m:
            target = m.group("target")
            amount = int(m.group("amount"))
            source = "Spell"
            spell = None
            for name, (cast_spell, expires) in list(self._pending_casts.items()):
                if wall_time <= expires and self._n(target) != name:
                    source = name
                    spell = cast_spell
                    del self._pending_casts[name]
                    break
            crit = is_crit or self._consume_crit(self._n(source), wall_time)
            self._record_damage(wall_time, source, target, amount, crit=crit,
                                category="spell", ability=spell or "Unknown spell")
            return

        m = HEAL_DEALT_ATTR_RE.match(line) or HEAL_DEALT_FALLBACK_RE.match(line)
        if m:
            target = m.group("target")
            if target.strip().lower() in ("yourself", "you"):
                target = YOU_LABEL
            spell = m.groupdict().get("spell")
            self._record_heal(wall_time, YOU_LABEL, target, int(m.group("amount")),
                              ability=spell)
            return

        m = HEAL_RECEIVED_ATTR_RE.match(line) or HEAL_RECEIVED_FALLBACK_RE.match(line)
        if m:
            spell = m.groupdict().get("spell")
            self._record_heal(wall_time, m.group("source"), YOU_LABEL,
                              int(m.group("amount")), ability=spell)
            return

        m = OTHER_HEAL_RE.match(line)
        if m:
            return   # someone healing someone else -- matched-and-ignored

        m = SELF_DEATH_RE.match(line)
        if m:
            # the killing blow already landed via its own damage line; a
            # death never needs to spawn a Combat by itself
            self.deaths.append(wall_time)
            if self.current is not None:
                self.current.deaths += 1
            self._clear_all_buffs(wall_time)
            self._notify()
            return

        m = SELF_KILL_RE.match(line)
        if m:
            # kill confirmations don't carry damage numbers themselves (they
            # already landed via the hit lines that preceded this) -- just
            # log the timestamp for the kills/hour rate.
            self.kills.append(wall_time)
            if self.current is not None:
                self.current.kills += 1
            if self._is_pet(self._n(m.group("target"))):
                # you killed your own (ex-)charm pet -- forget the name so
                # same-named mobs stop counting as your pet
                self._unregister_pet(m.group("target"))
            self._notify()
            return

        # buff/debuff landed-on-you & fade messages -- exact match against
        # the spell file's own message strings, checked only after every
        # combat pattern has had its chance (cheap dict lookups)
        m_body = TS_ONLY_RE.match(line)
        if m_body and self._handle_buff_message(wall_time, line[m_body.end():]):
            return

        if REGEN_RE.match(line):
            return   # HoT tick with spells_us_str.txt unavailable -- see REGEN_RE

        if SEED_HEAL_FALLBACK_RE.match(line):
            return   # seed-heal messages with spells_us_str.txt unavailable

        if SPELL_HOUSEKEEPING_RE.match(line):
            return   # buying/scribing/memorizing -- spell NAMES ("Flowering
                     # Heal") would otherwise trip the calibration keywords

        # nothing matched -- keep combat-flavored lines around for calibration
        if re.search(r"\bdamage\b|\bheal(?:s|ed)?\b|\bslain\b|\bcritical\b"
                     r"|\bstance\b|\binvocation\b|\bresist(?:s|ed)?\b"
                     r"|\bfizzle|\binterrupt|\bdiscipline\b|\bdispel",
                     line, re.IGNORECASE) \
           and TS_RE and re.match(TS_RE, line):
            self.unmatched.append(line)

    # -- views -----------------------------------------------------------------
    def snapshot(self):
        """Return (fight_or_None, is_live) for rendering."""
        if self.current is not None:
            return self.current, True
        if self.history:
            return self.history[0], False
        return None, False

    def ability_rows(self, metric="total", kind="dmg"):
        """Sorted (name, stats) pairs across the whole session, ranking
        which spell/Melee contributed the most damage or healing."""
        src = self.abilities_dmg if kind == "dmg" else self.abilities_heal
        return sorted(src.items(), key=lambda kv: -kv[1][metric])

    def stance_performance(self):
        """DPS/DTPS grouped by each *completed* fight's DOMINANT stance
        (the one active longest during the fight -- see Fight.main_stance)."""
        return self._group_fight_metric(lambda f: f.main_stance())

    def invocation_performance(self):
        return self._group_fight_metric(lambda f: f.main_invocation())

    def _group_fight_metric(self, key_fn):
        groups = {}
        for f in self.history:
            key = key_fn(f) or "Unknown"
            you = f.actors.get(YOU_LABEL)
            if not you:
                continue
            elapsed = f.elapsed()
            g = groups.setdefault(key, {"fights": 0, "dps_sum": 0.0, "dtps_sum": 0.0})
            g["fights"] += 1
            g["dps_sum"] += you["dmg_out"] / elapsed
            g["dtps_sum"] += you["dmg_in"] / elapsed
        out = {}
        for key, g in groups.items():
            n = g["fights"]
            out[key] = {"fights": n, "avg_dps": g["dps_sum"] / n, "avg_dtps": g["dtps_sum"] / n}
        return out
