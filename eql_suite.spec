# -*- mode: python ; coding: utf-8 -*-
# ============================================================
#  EQL Log Reader -- eql_suite.spec
# ============================================================
#  Custom PyInstaller spec that builds all five tools as ONE
#  onedir bundle: a single shared "_internal" folder plus five
#  sibling .exe files sitting directly in dist\EQL Log Reader\ --
#  the flat layout eql_launcher.py already expects (see its
#  `frozen` / APP_DIR checks, and how it launches sibling tools
#  by exe name).
#
#  Onedir instead of onefile, deliberately: onefile builds
#  self-extract to a fresh temp folder on every launch, which is
#  both slower and the far more common target of antivirus
#  "helpfully" patching or quarantining part of the packed exe
#  after the fact -- which is what produces symptoms like
#  "ordinal not found" errors and blank icons on a build that
#  compiled without error. Onedir ships the real files directly;
#  there's nothing to self-extract or falsely flag as a dropper.
#
#  UPX compression is also turned off below for the same reason
#  -- UPX-packed binaries are disproportionately likely to trip
#  AV heuristics.
#
#  Build with:  pyinstaller --noconfirm --clean eql_suite.spec
#  (build_exe.bat does this for you.)
# ============================================================

import os

ICON = "icon.ico" if os.path.exists("icon.ico") else None

TOOLS = ["eql_launcher", "eql_friend_overlay", "eql_dps_meter",
         "eql_session_report", "eql_atlas"]

# The Atlas ships its optional "pre-discovered" baseline (distilled Project
# Quarm database -- see eql_atlas_baseline_build.py) as a data file. It lands
# in _internal, which eql_atlas.py checks via sys._MEIPASS.
ATLAS_DATAS = ([("eql_atlas_baseline.json.gz", ".")]
               if os.path.exists("eql_atlas_baseline.json.gz") else [])

analyses = []
for tool in TOOLS:
    a = Analysis(
        [f"{tool}.py"],
        pathex=[],
        binaries=[],
        datas=ATLAS_DATAS if tool == "eql_atlas" else [],
        hiddenimports=[],
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        noarchive=False,
    )
    analyses.append(a)

# Dedupe shared modules (eql_overlay_common, eql_combat_tracker, eql_spell_db,
# stdlib, tkinter/tcl) across the four tools so they're stored once in the
# shared _internal folder instead of once per tool.
MERGE(*[(a, tool, tool) for a, tool in zip(analyses, TOOLS)])

exes = []
for a, tool in zip(analyses, TOOLS):
    pyz = PYZ(a.pure, a.zipped_data)
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=tool,
        icon=ICON,
        console=False,
        upx=False,
    )
    exes.append(exe)

collect_args = []
for exe, a in zip(exes, analyses):
    collect_args.extend([exe, a.binaries, a.zipfiles, a.datas])

coll = COLLECT(
    *collect_args,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EQL Log Reader",
)
