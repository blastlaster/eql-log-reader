#!/bin/sh
# EQL Log Reader -- Linux per-user installer
# ============================================
# Ships at the root of eql-log-reader-<version>-linux.tar.gz. Installs the
# suite for the CURRENT USER only -- no root required:
#
#   files    -> ~/.local/share/eql-log-reader   (XDG data home)
#   command  -> ~/.local/bin/eql-log-reader     (starts the Launcher)
#   menu     -> ~/.local/share/applications/eql-log-reader.desktop
#
# Settings/rosters/records live next to the installed files (the folder is
# user-writable, same behavior as running from source). Requires python3
# with tkinter -- on Debian/Ubuntu: sudo apt install python3-tk
#
#   ./install.sh              install or upgrade in place
#   ./install.sh --uninstall  remove the suite (per-user data files that
#                             live in the install folder are kept unless
#                             you delete the folder yourself)

set -e

APP=eql-log-reader
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/$APP"
BIN="$HOME/.local/bin"
APPS="$DATA/applications"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$1" = "--uninstall" ]; then
    echo "Removing $BIN/$APP and $APPS/$APP.desktop ..."
    rm -f "$BIN/$APP" "$APPS/$APP.desktop"
    if [ -d "$DEST" ]; then
        echo "Removing program files from $DEST ..."
        for f in "$DEST"/eql_*.py "$DEST"/*.json.gz "$DEST"/icon.png \
                 "$DEST"/LICENSE "$DEST"/README.md "$DEST"/RELEASE_NOTES.md; do
            [ -e "$f" ] && rm -f "$f"
        done
        rmdir "$DEST" 2>/dev/null || \
            echo "Kept $DEST (it still holds your settings/data files)."
    fi
    echo "Uninstalled."
    exit 0
fi

command -v python3 >/dev/null 2>&1 || {
    echo "ERROR: python3 not found. Install Python 3.8+ first." >&2
    exit 1
}
python3 -c 'import tkinter' 2>/dev/null || {
    echo "ERROR: python3 is missing tkinter." >&2
    echo "  Debian/Ubuntu/Mint:  sudo apt install python3-tk" >&2
    echo "  Fedora:              sudo dnf install python3-tkinter" >&2
    echo "  Arch:                sudo pacman -S tk" >&2
    exit 1
}

echo "Installing to $DEST ..."
mkdir -p "$DEST" "$BIN" "$APPS"
cp "$HERE"/eql_*.py "$DEST"/
cp "$HERE"/*.json.gz "$DEST"/ 2>/dev/null || true
cp "$HERE"/icon.png "$HERE"/LICENSE "$HERE"/README.md \
   "$HERE"/RELEASE_NOTES.md "$DEST"/ 2>/dev/null || true

cat > "$BIN/$APP" <<WRAP
#!/bin/sh
exec python3 "$DEST/eql_launcher.py" "\$@"
WRAP
chmod 755 "$BIN/$APP"

cat > "$APPS/$APP.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=EQL Log Reader
Comment=Overlay tools for EverQuest Legends, driven by the game's log file
Exec=$BIN/$APP
Icon=$DEST/icon.png
Terminal=false
Categories=Game;Utility;
DESK

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$APPS" 2>/dev/null || true

echo ""
echo "Installed. Start it with:  $APP"
echo "(or from your application menu: 'EQL Log Reader')"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "NOTE: $BIN is not on your PATH -- add it, or run:"
       echo "      python3 $DEST/eql_launcher.py" ;;
esac
