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

  * EQL's `spells_us.txt` is now 173 caret-delimited columns (0-172): the
    leading columns are scalar fields (mostly identical to Live EQ's
    layout) and the final column is a variable-length pipe-delimited
    "effects" blob (up to 41 effects per spell, each a 5-field group:
    effect_id/base_value/limit_value/formula/max_value). EQL keeps
    appending/inserting columns by patch: a 2026-05 patch appended a
    "ritual_eligible" flag before the blob, and a 2026-06-29 patch INSERTED
    a placeholder column at index 103 (suspected spell-upgrade/"motes"
    hook), shifting every later column by +1. Columns 0-102 have never
    moved, so every index this module reads (all < 103) is stable across
    both revisions, and the effects blob is located by content rather than
    index -- see SpellInfo.__init__.
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
import sys

from eql_verified_spells import verified_levels

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Validated field indices (all < 103, so stable across every known EQL
# column-shuffle patch), cross-confirmed against github.com/Amerzel/
# eql-info's SPELL_FORMAT.md + parse_spells.py.
IDX_ID = 0
IDX_NAME = 1
IDX_RANGE = 4
IDX_AOE_RANGE = 5
IDX_CAST_TIME_MS = 8
IDX_RECOVERY_TIME_MS = 9   # milliseconds, like cast_time (SPELL_FORMAT.md:
IDX_RECAST_TIME_MS = 10    # RECOVERYDELAY / SPELLDELAY are uint32 ms --
                           # confirmed: Minor Healing's recast reads 1500)
IDX_BUFF_DURATION_FORMULA = 11
IDX_BUFF_DURATION = 12
IDX_AE_DURATION = 13
IDX_MANA = 14
IDX_GOOD_EFFECT = 28     # 0=detrimental, 1=beneficial, 2=beneficial group-only
IDX_RESIST_TYPE = 29
IDX_TARGET_TYPE = 30
IDX_SKILL = 32           # casting skill id (see SKILL_NAMES) -- Singing /
                         # instrument skills are the reliable "is a song" tell
IDX_CLASSES_START = 36   # 16 consecutive fields, one per class (min level;
                         # 255=unavailable to that class, 254=available/no min)
IDX_CLASSES_COUNT = 16
IDX_RECOURSE_LINK = 81   # spell id cast back on the caster when this lands
IDX_ENDURANCE_COST = 96
IDX_TIMER_ID = 97        # shared reuse-timer group for disciplines
IDX_IS_DISCIPLINE = 98   # 1 = combat-window discipline, not a spell gem
MIN_FIELDS = IDX_IS_DISCIPLINE + 1

# EQL is a level-50-capped server (see eql_verified_spells) -- player spells
# gated above this level exist in the Live-era data files but can never be
# cast there.
EQL_LEVEL_CAP = 50

# Target-type ids (column 30) -- names from eql-info's app.py (standard EQ
# SpellTargetType enum). The two that matter for log analysis:
#   13 = Lifetap (damage that heals the caster), 20 = targeted-AE lifetap.
TARGET_LIFETAP_IDS = (13, 20)
TARGET_TYPES = {
    1: "Line of Sight", 2: "Targeted AE", 3: "Group v1", 4: "PB AE",
    5: "Single Target", 6: "Self", 8: "Targeted AE", 9: "Animal",
    10: "Undead", 11: "Summoned", 13: "Lifetap", 14: "Pet",
    15: "Corpse", 16: "Plant", 17: "Uber Giants", 18: "Uber Dragons",
    20: "Targeted AE (Caster)", 24: "AE Undead", 25: "AE Summoned",
    32: "Hatelist 2", 33: "Hatelist", 34: "Chest", 35: "Special Muramite",
    36: "Group v2", 38: "Directional AE", 39: "Group Teleport",
    40: "Beam", 41: "Single in Group", 42: "Directional AE Caster",
    43: "Free Target", 44: "Beam", 45: "Pet Owner", 46: "Target Of Target",
    47: "Free Target", 50: "Tap (group)", 51: "Single Friendly (or Self)",
    52: "All Group Members",
}

# Resist-type ids (column 29) -- same source.
RESIST_TYPES = {0: "Unresistable", 1: "Magic", 2: "Fire", 3: "Cold",
                4: "Poison", 5: "Disease", 6: "Chromatic", 7: "Prismatic",
                8: "Physical", 9: "Corruption"}

# Casting-skill ids (column 32) that mark Bard songs: Brass / Singing /
# Stringed / Wind / Percussion. From EQEmu's skills.h enum (the EQL client
# uses the same numeric ids -- see eql-info's skills_data.py). This is a
# far stronger "song" signal than class-exclusivity alone, so both are used.
BARD_SKILL_IDS = {12, 41, 49, 54, 70}
SKILL_NAMES = {
    0: "1H Blunt", 1: "1H Slashing", 2: "2H Blunt", 3: "2H Slashing",
    4: "Abjuration", 5: "Alteration", 6: "Apply Poison", 7: "Archery",
    8: "Backstab", 9: "Bind Wound", 10: "Bash", 11: "Block",
    12: "Brass Instruments", 13: "Channeling", 14: "Conjuration",
    15: "Defense", 16: "Disarm", 17: "Disarm Traps", 18: "Divination",
    19: "Dodge", 20: "Double Attack", 21: "Dragon Punch / Tail Rake",
    22: "Dual Wield", 23: "Eagle Strike", 24: "Evocation",
    25: "Feign Death", 26: "Flying Kick", 27: "Forage", 28: "Hand to Hand",
    29: "Hide", 30: "Kick", 31: "Meditate", 32: "Mend", 33: "Offense",
    34: "Parry", 35: "Pick Lock", 36: "1H Piercing", 37: "Riposte",
    38: "Round Kick", 39: "Safe Fall", 40: "Sense Heading", 41: "Singing",
    42: "Sneak", 43: "Specialize Abjuration", 44: "Specialize Alteration",
    45: "Specialize Conjuration", 46: "Specialize Divination",
    47: "Specialize Evocation", 48: "Pick Pockets",
    49: "Stringed Instruments", 50: "Swimming", 51: "Throwing",
    52: "Tiger Claw", 53: "Tracking", 54: "Wind Instruments", 55: "Fishing",
    56: "Make Poison", 57: "Tinkering", 58: "Research", 59: "Alchemy",
    60: "Baking", 61: "Tailoring", 62: "Sense Traps", 63: "Blacksmithing",
    64: "Fletching", 65: "Brewing", 66: "Alcohol Tolerance", 67: "Begging",
    68: "Jewelry Making", 69: "Pottery", 70: "Percussion Instruments",
    71: "Intimidation", 72: "Berserking", 73: "Taunt", 74: "Frenzy",
    75: "Remove Traps", 76: "Triple Attack",
}

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

# The full SPA (Spell Affect) name table, from Daybreak's official
# "Enumerated SPA List" forum thread via github.com/Amerzel/eql-info
# (spa_data.py), cross-checked against EQEmu's SE_* defines in spdat.h.
# EQL extends the enum past 526 with custom ids; the one identified so far
# is 537 (Promised Renewal delayed-trigger heal). Unknown ids render as
# "SE #<id>".
_SPA_NAMES_RAW = """
0 HP|1 AC|2 AttackPower|3 MovementRate|4 STR|5 DEX|6 AGI|7 STA|8 INT|9 WIS
10 CHA|11 Haste|12 Invisibility|13 SeeInvis|14 EnduringBreath|15 MANA
16 NpcFrenzy|17 NpcAwareness|18 NpcAggro|19 NpcFaction|20 Blindness|21 Stun
22 Charm|23 Fear|24 Fatigue|25 BindAffinity|26 Gate|27 DispelMagic
28 InvisVsUndead|29 InvisVsAnimals|30 NpcAggroRadius|31 Enthrall
32 CreateItem|33 SummonPet|34 Confuse|35 Disease|36 Poison|37 DetectHostile
38 DetectMagic|39 NoTwincast|40 Invulnerability|41 Banish|42 ShadowStep
43 Berserk|44 Lycanthropy|45 Vampirism|46 ResistFire|47 ResistCold
48 ResistPoison|49 ResistDisease|50 ResistMagic|51 DetectTraps
52 DetectUndead|53 DetectSummoned|54 DetectAnimals|55 Stoneskin|56 TrueNorth
57 Levitation|58 ChangeForm|59 DamageShield|60 TransferItem|61 ItemLore
62 ItemIdentify|63 NpcWipeHateList|64 SpinStun|65 Infravision|66 Ultravision
67 EyeOfZomm|68 ReclaimEnergy|69 MaxHp|70 CorpseBomb|71 CreateUndead
72 PreserveCorpse|73 BindSight|74 FeignDeath|75 Ventriloquism|76 Sentinel
77 LocateCorpse|78 SpellShield|79 InstantHp|80 EnchantLight|81 Resurrect
82 SummonTarget|83 Portal|84 HpNpcOnly|85 AddProcSpell|86 NpcHelpRadius
87 Magnification|88 Evacuate|89 Height|90 IgnorePet|91 SummonCorpse|92 Hate
93 WeatherControl|94 Fragile|95 Sacrifice|96 Silence|97 MaxMana|98 BardHaste
99 Root|100 Healdot|101 Completeheal|102 PetFearless|103 CallPet
104 Translocate|105 NpcAntiGate|106 BeastlordPet|107 AlterPetLevel
108 Familiar|109 CreateItemInBag|110 Archery|111 ResistAll|112 FizzleSkill
113 SummonMount|114 ModifyHate|115 Cornucopia|116 Curse|117 HitMagic
118 Amplification|119 AttackSpeedMax|120 Healmod|121 Ironmaiden
122 Reduceskill|123 Immunity|124 FocusDamageMod|125 FocusHealMod
126 FocusResistMod|127 FocusCastTimeMod|128 FocusDurationMod
129 FocusRangeMod|130 FocusHateMod|131 FocusReagentMod|132 FocusManacostMod
133 FocusStuntimeMod|134 FocusLevelMax|135 FocusResistType
136 FocusTargetType|137 FocusWhichSpa|138 FocusBeneficial|139 FocusWhichSpell
140 FocusDurationMin|141 FocusInstantOnly|142 FocusLevelMin
143 FocusCasttimeMin|144 FocusCasttimeMax|145 NpcPortalWarderBanish
146 PortalLocations|147 PercentHeal|148 StackingBlock|149 StripVirtualSlot
150 DivineIntervention|151 PocketPet|152 PetSwarm|153 HealthBalance
154 CancelNegativeMagic|155 PopResurrect|156 Mirror|157 Feedback|158 Reflect
159 ModifyAllStats|160 ChangeSobriety|161 SpellGuard|162 MeleeGuard
163 AbsorbHit|164 ObjectSenseTrap|165 ObjectDisarmTrap|166 ObjectPicklock
167 FocusPet|168 Defensive|169 CriticalMelee|170 CriticalSpell
171 CripplingBlow|172 Evasion|173 Riposte|174 Dodge|175 Parry|176 DualWield
177 DoubleAttack|178 MeleeLifetap|179 Puretone|180 Sanctification
181 Fearless|182 HundredHands|183 SkillIncreaseChance|184 Accuracy
185 SkillDamageMod|186 MinDamageDoneMod|187 ManaBalance|188 Block
189 Endurance|190 IncreaseMaxEndurance|191 Amnesia|192 HateOverTime
193 SkillAttack|194 Fade|195 StunResist|196 Strikethrough1
197 SkillDamageTaken|198 InstantEndurance|199 Taunt|200 ProcChance
201 RangeAbility|202 IllusionOthers|203 MassGroupBuff|204 GroupFearImmunity
205 Rampage|206 AeTaunt|207 FleshToBone|208 PurgePoison|209 CancelBeneficial
210 ShieldCaster|211 DestructiveForce|212 FocusFrenziedDevastation
213 PetPctMaxHp|214 HpMaxHp|215 PetPctAvoidance|216 MeleeAccuracy
217 Headshot|218 PetCritMelee|219 SlayUndead|220 IncreaseSkillDamage
221 ReduceWeight|222 BlockBehind|223 DoubleRiposte|224 AddRiposte
225 GiveDoubleAttack|226 2hBash|227 ReduceSkillTimer|228 Acrobatics
229 CastThroughStun|230 ExtendedShielding|231 BashChance|232 DivineSave
233 Metabolism|234 PoisonMastery|235 FocusChanneling|236 FreePet
237 PetAffinity|238 PermIllusion|239 Stonewall|240 StringUnbreakable
241 ImproveReclaimEnergy|242 IncreaseChangeMemwipe|243 EnhancedCharm
244 EnhancedRoot|245 TrapCircumvention|246 IncreaseAirSupply
247 IncreaseMaxSkill|248 ExtraSpecialization|249 OffhandMinWeaponDamage
250 IncreaseProcChance|251 EndlessQuiver|252 BackstabFront|253 ChaoticStab
254 Nospell|255 ShieldingDurationMod|256 ShroudOfStealth|257 GivePetHold
258 TripleBackstab|259 AcLimitMod|260 AddInstrumentMod|261 SongModCap
262 IncreaseStatCap|263 TradeskillMastery|264 ReduceAaTimer|265 NoFizzle
266 Add2hAttackChance|267 AddPetCommands|268 AlchemyFailRate|269 FirstAid
270 ExtendSongRange|271 BaseRunMod|272 IncreaseCastingLevel|273 Dotcrit
274 Healcrit|275 Mendcrit|276 DualWieldAmt|277 ExtraDiChance
278 FinishingBlow|279 Flurry|280 PetFlurry|281 PetFeign
282 IncreaseBandageAmt|283 WuAttack|284 ImproveLoh|285 NimbleEvasion
286 FocusDamageAmt|287 FocusDurationAmt|288 AddProcHit|289 DoomEffect
290 IncreaseRunSpeedCap|291 Purify|292 Strikethrough|293 StunResist2
294 SpellCritChance|295 ReduceSpecialTimer|296 FocusDamageModDetrimental
297 FocusDamageAmtDetrimental|298 TinyCompanion|299 WakeDead
300 Doppelganger|301 IncreaseRangeDmg|302 FocusDamageModCrit
303 FocusDamageAmtCrit|304 SecondaryRiposteMod|305 DamageShieldMod
306 WeakDead2|307 Appraisal|308 ZoneSuspendMinion
309 TeleportCastersBindpoint|310 FocusReuseTimer|311 FocusCombatSkill
312 Observer|313 ForageMaster|314 ImprovedInvis|315 ImprovedInvisUndead
316 ImprovedInvisAnimals|317 IncreaseWornHpRegenCap
318 IncreaseWornManaRegenCap|319 CriticalHpRegen|320 ShieldBlockChance
321 ReduceTargetHate|322 GateStartingCity|323 DefensiveProc|324 HpForMana
325 NoBreakAeSneak|326 AddSpellSlots|327 AddBuffSlots
328 IncreaseNegativeHpLimit|329 ManaAbsorbPctDmg|330 CritAttackModifier
331 FailAlchemyItemRecovery|332 SummonToCorpse|333 DoomRuneEffect
334 NoMoveHp|335 FocusedImmunity|336 IllusionaryTarget|337 IncreaseExpMod
338 ExpedientRecovery|339 FocusCastingProc|340 ChanceSpell
341 WornAttackCap|342 NoPanic|343 SpellInterrupt|344 ItemChanneling
345 AssassinateMaxLevel|346 HeadshotMaxLevel|347 DoubleRangedAttack
348 FocusManaMin|349 IncreaseShieldDmg|350 Manaburn
351 SpawnInteractiveObject|352 IncreaseTrapCount|353 IncreaseSoiCount
354 DeactivateAllTraps|355 LearnTrap|356 ChangeTriggerType|357 FocusMute
358 InstantMana|359 PassiveSenseTrap|360 ProcOnKillShot|361 ProcOnDeath
362 PotionBelt|363 Bandolier|364 AddTripleAttackChance
365 ProcOnSpellKillShot|366 GroupShielding|367 ModifyBodyType
368 ModifyFaction|369 Corruption|370 ResistCorruption|371 Slow
372 GrantForaging|373 DoomAlways|374 TriggerSpell|375 CritDotDmgMod
376 Fling|377 DoomEntity|378 ResistOtherSpa|379 DirectionalTeleport
380 ExplosiveKnockback|381 FlingToward|382 Suppression
383 FocusCastingProcNormalized|384 FlingAt|385 FocusWhichGroup
386 DoomDispeller|387 DoomDispellee|388 SummonAllCorpses
389 RefreshSpellTimer|390 LockoutSpellTimer|391 FocusManaMax
392 FocusHealAmt|393 FocusHealModBeneficial|394 FocusHealAmtBeneficial
395 FocusHealModCrit|396 FocusHealAmtCrit|397 AddPetAc
398 FocusSwarmPetDuration|399 FocusTwincastChance|400 Healburn
401 ManaIgnite|402 EnduranceIgnite|403 FocusSpellClass
404 FocusSpellSubclass|405 StaffBlockChance|406 DoomLimitUse
407 DoomFocusUsed|408 LimitHp|409 LimitMana|410 LimitEndurance
411 FocusLimitClass|412 FocusLimitRace|413 FocusBaseEffects
414 FocusLimitSkill|415 FocusLimitItemClass|416 AC2|417 Mana2
418 FocusIncreaseSkillDmg2|419 ProcEffect2|420 FocusLimitUse
421 FocusLimitUseAmt|422 FocusLimitUseMin|423 FocusLimitUseType
424 Gravitate|425 Fly|426 AddExtendedTargetSlots|427 SkillProc
428 ProcSkillModifier|429 SkillProcSuccess|430 PostEffect
431 PostEffectData|432 ExpandMaxActiveTrophyBenefits
433 AddNormalizedSkillMinDmgAmt|434 AddNormalizedSkillMinDmgAmt2
435 FragileDefense|436 FreezeBuffTimer|437 TeleportToAnchor
438 TranslocateToAnchor|439 Assassinate|440 FinishingBlowMax
441 DistanceRemoval|442 RequireTargetDoom|443 RequireCasterDoom
444 ImprovedTaunt|445 AddMercSlot|446 StackerA|447 StackerB|448 StackerC
449 StackerD|450 DotGuard|451 MeleeThresholdGuard|452 SpellThresholdGuard
453 MeleeThresholdDoom|454 SpellThresholdDoom|455 AddHatePct
456 AddHateOverTimePct|457 ResourceTap|458 FactionMod|459 SkillDamageMod2
460 OverrideNotFocusable|461 FocusDamageMod2|462 FocusDamageAmt2
463 Shield|464 PcPetRampage|465 PcPetAeRampage|466 PcPetFlurry
467 DamageShieldMitigationAmt|468 DamageShieldMitigationPct
469 ChanceBestInSpellGroup|470 TriggerBestInSpellGroup
471 DoubleMeleeAttacks|472 AaBuyNextRank|473 DoubleBackstabFront
474 PetMeleeCritDmgMod|475 TriggerSpellNonItem|476 WeaponStance
477 HatelistToTop|478 HatelistToTail|479 FocusLimitMinValue
480 FocusLimitMaxValue|481 FocusCastSpellOnLand|482 SkillBaseDamageMod
483 FocusIncomingDmgMod|484 FocusIncomingDmgAmt|485 FocusLimitCasterClass
486 FocusLimitSameCaster|487 ExtendTradeskillCap|488 DefenderMeleeForcePct
489 WornEnduranceRegenCap|490 FocusMinReuseTime|491 FocusMaxReuseTime
492 FocusEnduranceMin|493 FocusEnduranceMax|494 PetAddAtk
495 FocusDurationMax|496 CritMeleeDmgModMax|497 FocusCastProcNoBypass
498 AddExtraPrimaryAttackPct|499 AddExtraSecondaryAttackPct
500 FocusCastTimeMod2|501 FocusCastTimeAmt|502 Fearstun
503 MeleeDmgPositionMod|504 MeleeDmgPositionAmt|505 DmgTakenPositionMod
506 DmgTakenPositionAmt|507 AmplifyMod|508 AmplifyAmt|509 HealthTransfer
510 FocusResistIncoming|511 FocusTimerMin|512 ProcTimerMod|513 ManaMax
514 EnduranceMax|515 AcAvoidanceMax|516 AcMitigationMax
517 AttackOffenseMax|518 AttackAccuracyMax|519 LuckAmt|520 LuckPct
521 EnduranceAbsorbPctDmg|522 InstantManaPct|523 InstantEndurancePct
524 DurationHpPct|525 DurationManaPct|526 DurationEndurancePct
537 PromisedRenewalTrigger
"""

SPA_NAMES = {}
for _entry in _SPA_NAMES_RAW.replace("\n", "|").split("|"):
    _entry = _entry.strip()
    if _entry:
        _sid, _, _sname = _entry.partition(" ")
        SPA_NAMES[int(_sid)] = _sname
del _entry, _sid, _sname

# SPAs that carry an HP magnitude: 0 is the generic current-HP slot (direct
# heals, HoTs, DDs, DoTs alike -- sign of base_value decides), 79 is the
# instant-HP variant (unfocusable direct heal/damage), 100 is the dedicated
# heal-over-time slot.
HP_SPA_IDS = (0, 79, 100)

# SPAs that GRANT an automatic trigger spell -- weapon/buff procs (85
# AddProcSpell, 419 ProcEffect2), defensive procs (323), skill procs
# (427/429), sympathetic cast-procs (339/383), and kill/death triggers
# (360/361/365). The granted spell's id sits in base_value for the classic
# slots and limit_value for the focus-style ones; both are checked when
# building proc_names(). Chance percentages (<= 100) can collide with low
# spell ids, so low values are only trusted from the slots whose base IS
# the spell id by definition.
PROC_SPA_IDS = {85, 323, 339, 360, 361, 365, 383, 419, 427, 429}
_PROC_ID_IS_BASE = {85, 419, 323, 427}


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
                 "recovery_time_ms", "recast_time_ms", "buff_duration_formula",
                 "buff_duration_raw", "ae_duration", "mana", "good_effect",
                 "resist_type", "target_type", "skill", "classes",
                 "recourse_link", "endurance_cost", "timer_id",
                 "is_discipline", "effects")

    def __init__(self, fields):
        self.id = int(fields[IDX_ID])
        self.name = fields[IDX_NAME]
        self.range = _to_int(fields[IDX_RANGE])
        self.aoe_range = _to_int(fields[IDX_AOE_RANGE])
        self.cast_time_ms = _to_int(fields[IDX_CAST_TIME_MS])
        self.recovery_time_ms = _to_int(fields[IDX_RECOVERY_TIME_MS])
        self.recast_time_ms = _to_int(fields[IDX_RECAST_TIME_MS])
        self.buff_duration_formula = _to_int(fields[IDX_BUFF_DURATION_FORMULA])
        self.buff_duration_raw = _to_int(fields[IDX_BUFF_DURATION])
        self.ae_duration = _to_int(fields[IDX_AE_DURATION])
        self.mana = _to_int(fields[IDX_MANA])
        self.good_effect = _to_int(fields[IDX_GOOD_EFFECT])
        self.resist_type = _to_int(fields[IDX_RESIST_TYPE])
        self.target_type = _to_int(fields[IDX_TARGET_TYPE])
        self.skill = _to_int(fields[IDX_SKILL])
        self.classes = [_to_int(fields[IDX_CLASSES_START + i])
                        for i in range(IDX_CLASSES_COUNT)]
        self.recourse_link = _to_int(fields[IDX_RECOURSE_LINK])
        self.endurance_cost = _to_int(fields[IDX_ENDURANCE_COST])
        self.timer_id = _to_int(fields[IDX_TIMER_ID])
        self.is_discipline = _to_int(fields[IDX_IS_DISCIPLINE]) == 1
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

    @property
    def recovery_time_s(self):
        return self.recovery_time_ms / 1000.0

    @property
    def recast_time_s(self):
        # NOTE: this field was misread as seconds until 2026-07 -- the raw
        # column is milliseconds (a 1.5s recast read as "1500s").
        return self.recast_time_ms / 1000.0

    @property
    def is_song(self):
        """True when the casting skill is a Bard skill (Singing or one of
        the four instrument skills) -- the game's own classification, far
        stronger than inferring from class-exclusivity."""
        return self.skill in BARD_SKILL_IDS

    @property
    def is_lifetap(self):
        """True for lifetap-targeted spells (target_type 13, or 20 for the
        targeted-AE variant): damage that flows back to the caster as a
        heal. This is the game's own flag for the mechanic, so it covers
        every tap regardless of name."""
        return self.target_type in TARGET_LIFETAP_IDS

    @property
    def skill_label(self):
        return SKILL_NAMES.get(self.skill, f"Skill #{self.skill}")

    @property
    def target_label(self):
        return TARGET_TYPES.get(self.target_type, f"#{self.target_type}")

    @property
    def resist_label(self):
        return RESIST_TYPES.get(self.resist_type, f"#{self.resist_type}")

    def duration_ticks(self, level=50):
        """Estimated buff duration in 6-second ticks at the given caster
        level, or 0 for instant spells, or -1 for permanent-until-removed.

        ESTIMATE: implements EQEmu's classic-era CalcBuffDuration_formula
        table (spdat.cpp) -- same caveat tier as estimate_effect_value():
        duration formulas are server-side logic, so EQL isn't guaranteed to
        match. The cap (buff_duration_raw) is what the modern client itself
        displays for most buffs, and the result is always clamped to it."""
        f = self.buff_duration_formula
        cap = self.buff_duration_raw
        lvl = max(int(level or 1), 1)
        if f == 0:
            return 0
        if f in (50, 51):     # permanent buff / permanent aura
            return -1
        if f == 1:
            ticks = max(lvl // 2, 1)
        elif f == 2:
            ticks = max(lvl // 2 + 5, 6)
        elif f == 3:
            ticks = 30 * lvl
        elif f == 4:
            ticks = 50
        elif f == 5:
            ticks = 2
        elif f == 6:
            ticks = lvl // 2 + 2
        elif f == 7:
            ticks = lvl
        elif f == 8:
            ticks = lvl + 10
        elif f == 9:
            ticks = 2 * lvl + 10
        elif f == 10:
            ticks = 3 * lvl + 10
        elif f == 11:
            ticks = 30 * (lvl + 3)
        elif f == 12:
            ticks = max(lvl // 4, 1)
        elif f == 13:
            ticks = 4 * lvl + 10
        elif f == 14:
            ticks = 5 * (lvl + 2)
        elif f == 15:
            ticks = 10 * (lvl + 10)
        else:
            # unknown formula -- fall back to the cap, which is what the
            # client shows anyway
            ticks = cap
        if cap and 0 < cap < ticks:
            ticks = cap
        return ticks

    def duration_seconds(self, level=50):
        """Estimated buff duration in seconds (1 tick = 6s), 0 if instant,
        -1 if permanent. Same estimate caveats as duration_ticks()."""
        t = self.duration_ticks(level)
        return t * 6 if t > 0 else t

    def usable_by(self, class_name):
        try:
            idx = CLASS_NAMES.index(class_name)
        except ValueError:
            return False
        return 0 <= self.classes[idx] <= 125  # 254/255 sentinels mean unavailable

    @property
    def bard_only(self):
        """True when the Bard is the ONLY class that can use this ability.
        Bard songs are never shared with other classes, so bard-exclusivity
        is a reliable way to recognize a song from its name alone (the
        combat log doesn't always say "You begin singing X" -- melody
        auto-play logs only "You whistle an ancient warsong.")."""
        usable = [i for i in range(IDX_CLASSES_COUNT)
                  if 0 <= self.classes[i] <= 125]
        return usable == [BARD_CLASS_INDEX]

    def min_level_for(self, class_name):
        try:
            idx = CLASS_NAMES.index(class_name)
        except ValueError:
            return None
        lvl = self.classes[idx]
        return lvl if lvl not in (254, 255) else None

    def min_player_level(self):
        """Lowest level at which ANY player class gets this spell, or None
        when no player class can use it (mob/proc/item-only spells)."""
        lvls = [l for l in self.classes if 0 <= l <= 125]
        return min(lvls) if lvls else None

    def fade_trigger_ids(self):
        """Spell ids this buff casts automatically when it EXPIRES (SPA 289,
        trigger-on-fade) -- e.g. every Budding Heal line seed casts its
        "* Heal Trigger" at the moment the seed ends, which is the only
        log-visible signal of that ending."""
        return {e.base_value for e in self.effects
                if e.effect_id == 289 and e.base_value > 0}

    def hp_effects(self):
        """Effects carrying an HP magnitude -- SPA 0 (the generic heal/
        damage slot), SPA 79 (instant HP), and SPA 100 (heal-over-time).
        SPA 0 effects are listed first so callers that only look at [0]
        keep preferring the classic slot. A spell can have more than one
        (rare); usually just one."""
        eff = [e for e in self.effects if e.effect_id in HP_SPA_IDS]
        eff.sort(key=lambda e: HP_SPA_IDS.index(e.effect_id))
        return eff

    def estimated_hp_value(self, level):
        """Estimated heal (positive) or damage (negative) magnitude from
        this spell's HP effect (SPA 0/79/100) at the given caster level, or
        None if it has no such effect. Per-TICK for SPA 100 heal-over-time.
        See estimate_effect_value() caveats."""
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
        self._song_names = None
        self._lifetap_names = None
        self._proc_names = None
        self._loaded = False
        self._path = path
        # spells_us_str.txt message tables (lazy, loaded on first message
        # lookup): normalized message text -> sorted list of spell names.
        self._str_loaded = False
        self._landed_on_you = None   # CASTEDMETXT ("You feel armored.")
        self._fade_msgs = None       # SPELLGONE  ("Your armor fades.")

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
        self._path = path   # remember where it was found (str file lives beside it)
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

    def bard_song_names(self):
        """Lowercased names of every song in the spell file, so a combat
        parser can recognize a song by name alone. Two independent signals,
        OR'd: the ability's casting skill is a Bard skill (Singing or an
        instrument -- the game's own classification, column 32), or the
        ability is bard-only by class mask. A name counts as a song when
        ANY entry bearing it qualifies: the file holds duplicate names
        (player/NPC/test copies) with different class masks, and
        lookup()-by-name only ever sees the first copy -- which for e.g.
        "Selo's Song of Travel" is not the bard one."""
        self._ensure_loaded()
        if self._song_names is None:
            self._song_names = {info.name.lower()
                                for info in self._by_id.values()
                                if info.is_song or info.bard_only}
        return self._song_names

    def lifetap_names(self):
        """Lowercased names of every lifetap-targeted spell (target_type
        13/20) -- damage that heals the caster. Lets the combat parser
        recognize the mechanic from the game's own data instead of a
        hardcoded name list."""
        self._ensure_loaded()
        if self._lifetap_names is None:
            self._lifetap_names = {info.name.lower()
                                   for info in self._by_id.values()
                                   if info.is_lifetap}
        return self._lifetap_names

    def proc_names(self):
        """Lowercased names of every spell that some OTHER spell grants as
        an automatic trigger (see PROC_SPA_IDS) -- i.e. names that can show
        up as "by <spell>" damage without any cast. A name appearing here
        doesn't PROVE a given hit was a proc (a few trigger spells are also
        directly castable), so callers should additionally require that the
        player never cast it -- see CombatTracker's proc tagging.

        COVERAGE LIMIT: only procs granted by SPELLS/AAs are discoverable
        here. Weapon procs granted by ITEMS live in item data the client
        spell file doesn't contain, so they can't be recognized this way --
        an unrecognized "by <spell>" hit is still recorded normally, just
        without the proc tag."""
        self._ensure_loaded()
        if self._proc_names is None:
            ids = set()
            for info in self._by_id.values():
                for e in info.effects:
                    if e.effect_id not in PROC_SPA_IDS:
                        continue
                    for val, trusted in (
                            (e.base_value, e.effect_id in _PROC_ID_IS_BASE),
                            (e.limit_value, False)):
                        if val in self._by_id and (trusted or val > 100):
                            ids.add(val)
            self._proc_names = {self._by_id[i].name.lower() for i in ids}
        return self._proc_names

    def lookup_id(self, spell_id):
        self._ensure_loaded()
        return self._by_id.get(spell_id)

    # -- spells_us_str.txt message tables --------------------------------------
    # The client logs a spell's "cast on you" message when a buff/debuff
    # lands on you and its "spell fades" message when it wears off -- as
    # bare lines carrying no spell name. spells_us_str.txt (same directory
    # as spells_us.txt, same caret-delimited format plus a header row, per
    # github.com/Amerzel/eql-info's SPELL_FORMAT.md) maps every spell id to
    # those five message strings, which is the ONLY way to attribute such a
    # line back to the spell(s) that produce it. Messages are not unique --
    # whole spell lines share one ("You feel armored.") -- so lookups
    # return a sorted list of candidate spell names, never a single guess.

    def _ensure_str_loaded(self):
        if self._str_loaded:
            return
        self._str_loaded = True
        self._landed_on_you = {}
        self._fade_msgs = {}
        self._ensure_loaded()
        if not self._path:
            return
        str_path = os.path.join(os.path.dirname(self._path),
                                "spells_us_str.txt")
        if not os.path.isfile(str_path):
            return
        landed, fades = {}, {}
        try:
            with open(str_path, "r", encoding="cp1252", errors="replace") as f:
                for line in f:
                    cols = line.rstrip("\r\n").split("^")
                    if len(cols) < 6:
                        continue
                    try:
                        sid = int(cols[0])
                    except ValueError:
                        continue    # header row ("SPELLINDEX^...") or junk
                    info = self._by_id.get(sid)
                    if info is None:
                        continue
                    # A player spell above EQL's level cap cannot occur on
                    # the L50 server -- keeping it only adds false ambiguity
                    # to shared messages (Live's L77 "Selo's Accelerating
                    # Canto" shares "Your feet move faster." with the real
                    # L5 Accelerando, wrecking the shared-duration estimate).
                    # Mob-only spells (no player class) stay: their debuff
                    # messages on you are real.
                    mpl = info.min_player_level()
                    if mpl is not None and mpl > EQL_LEVEL_CAP:
                        continue
                    # cols: 1=you cast, 2=other casts, 3=cast ON you,
                    #       4=cast on other, 5=fades
                    for msg, table in ((cols[3], landed), (cols[5], fades)):
                        msg = msg.strip()
                        # skip empties and templated messages (%1 etc.) --
                        # they can't be matched verbatim against a log line
                        if not msg or "%" in msg:
                            continue
                        table.setdefault(msg.lower(), set()).add(info.name)
        except OSError:
            return
        self._landed_on_you = {k: sorted(v) for k, v in landed.items()}
        self._fade_msgs = {k: sorted(v) for k, v in fades.items()}

    def buff_landed_candidates(self, line_body):
        """Spell names whose "cast on you" message is exactly `line_body`
        (timestamp already stripped, case-insensitive), else None. A hit
        means a buff/debuff just landed on you."""
        self._ensure_str_loaded()
        return self._landed_on_you.get(line_body.strip().lower())

    def buff_faded_candidates(self, line_body):
        """Spell names whose fade message is exactly `line_body`, else
        None. A hit means one of those spells just wore off you."""
        self._ensure_str_loaded()
        return self._fade_msgs.get(line_body.strip().lower())

    def find_class_heals(self, class_name, max_level=50, verified_only=True):
        """All beneficial spells usable by `class_name` that have an HP
        effect (SPA 0/79/100) with a positive base_value -- i.e. candidate
        heals, heal-over-time songs/buffs, etc. Useful for identifying what
        a silent passive heal (no log line) might actually be, given you
        know roughly what songs/spells the caster has available.

        `verified_only` (default): when a wiki-verified list exists for the
        class (eql_verified_spells.py), only spells confirmed obtainable on
        EQL's L1-50 server are returned -- the raw file carries ~74k
        Live-inherited entries, most of which don't exist in-game. Classes
        without verified data are returned unfiltered."""
        self._ensure_loaded()
        ver = verified_levels(class_name) if verified_only else None
        out = []
        for info in self._by_id.values():
            if not info.is_beneficial or not info.usable_by(class_name):
                continue
            min_lvl = info.min_level_for(class_name)
            if min_lvl is None or min_lvl > max_level:
                continue
            if ver is not None:
                vlvl = ver.get(info.name.lower())
                if vlvl is None or vlvl > max_level:
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
