#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
#
# The 3djlchb edition as a .deb for Debian and its derivatives (LMDE,
# Ubuntu, Mint): the PyInstaller bundle build-appimage.sh already made,
# installed under /opt with a launcher, icons and the .igz type.
#
#   packaging/build-deb.sh [BUNDLE] [OUT]
#
# BUNDLE defaults to build/pyi/ingetrazo (left by build-appimage.sh), OUT to
# dist/. Installs beside the official IngeTrazo: every name is the edition's
# (core/edition.py). Built on ubuntu-22.04, so glibc 2.35 or newer.
#
# No maintainer scripts: the desktop, MIME and icon caches are refreshed by
# the dpkg triggers of desktop-file-utils, shared-mime-info and
# hicolor-icon-theme.
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BUNDLE=${1:-$ROOT/build/pyi/ingetrazo}
OUT=${2:-$ROOT/dist}
WORK=${INGETRAZO_BUILD_DIR:-$ROOT/build}
PYTHON=${PYTHON:-$ROOT/venv/bin/python}

PKG=ingetrazo-3djlchb               # core.edition.DESKTOP_ID
NAME="IngeTrazo 3djlchb"            # core.edition.DISPLAY_NAME
ARCH=amd64
cd "$ROOT"
VERSION=${DEB_VERSION:-$("$PYTHON" -c 'from core.version import __version__; print(__version__)')}
DEB="$OUT/${PKG}_${VERSION}_${ARCH}.deb"

[ -x "$BUNDLE/ingetrazo" ] || {
    echo "!! no bundle at $BUNDLE — run packaging/build-appimage.sh first" >&2
    exit 1
}
echo "==> $NAME $VERSION -> $DEB"

STAGE="$WORK/deb/$PKG"
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/opt/$PKG" "$STAGE/usr/bin" \
         "$STAGE/usr/share/applications" "$STAGE/usr/share/mime/packages" \
         "$STAGE/usr/share/doc/$PKG"
cp -a "$BUNDLE/." "$STAGE/opt/$PKG/"
# Empty on purpose: GIO stops scanning the host's modules, built against
# another libglib (the AppImage's AppRun does the same).
mkdir -p "$STAGE/opt/$PKG/gio-modules"

cat > "$STAGE/usr/bin/$PKG" <<SH
#!/bin/sh
# $NAME, installed by the $PKG package.
[ -z "\$QT_QPA_PLATFORM" ] && export QT_QPA_PLATFORM="wayland;xcb"
export GIO_MODULE_DIR="/opt/$PKG/gio-modules"
exec "/opt/$PKG/ingetrazo" "\$@"
SH

cat > "$STAGE/usr/share/applications/$PKG.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=$NAME
GenericName=3D Modeler
GenericName[es]=Modelador 3D
Comment=Free 3D modeler for civil engineering and architecture
Comment[es]=Modelador 3D libre para ingeniería civil y arquitectura
Exec=$PKG %f
TryExec=$PKG
Icon=$PKG
Terminal=false
Categories=Graphics;3DGraphics;Engineering;
MimeType=application/x-ingetrazo;model/vnd.collada+xml;application/vnd.sketchup.skp;
StartupWMClass=$PKG
DESKTOP

for size in 16 32 48 64 128 256 512; do
    d="$STAGE/usr/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$d"
    cp "resources/icons/ingetrazo_$size.png" "$d/$PKG.png"
done
# The document icons under the edition's own file names; the MIME package
# names them, so the official build's copies are never overwritten.
for d in resources/icons/hicolor/*/mimetypes; do
    size=$(basename "$(dirname "$d")")
    dest="$STAGE/usr/share/icons/hicolor/$size/mimetypes"
    mkdir -p "$dest"
    for f in "$d"/*.png; do
        cp "$f" "$dest/$PKG-$(basename "$f")"
    done
done
sed -E "s|<icon name=\"([^\"]+)\"/>|<icon name=\"$PKG-\\1\"/>|" \
    resources/mime/ingetrazo.xml > "$STAGE/usr/share/mime/packages/$PKG.xml"

cp LICENSE "$STAGE/usr/share/doc/$PKG/copyright"

# The libraries the bundle leaves to the host (ingetrazo.spec _HOST_ONLY),
# the GL/EGL driver side and what Qt's xcb and Wayland plugins open.
SIZE=$(du -sk "$STAGE/opt" "$STAGE/usr" | awk '{s += $1} END {print s}')
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: $PKG
Version: $VERSION
Architecture: $ARCH
Maintainer: 3djlchb <204752941+3djlchb@users.noreply.github.com>
Installed-Size: $SIZE
Depends: libc6 (>= 2.35), libstdc++6, libgcc-s1, libglib2.0-0t64 | libglib2.0-0, libx11-6, libx11-xcb1, libxcb-glx0, libgl1, libegl1, libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-keysyms1, libxcb-shape0, libfontconfig1, libdbus-1-3
Section: graphics
Priority: optional
Homepage: https://github.com/3djlchb/ingetrazo
Description: $NAME — free 3D modeler for engineering and architecture
 The 3djlchb edition of IngeTrazo, installed beside the official one: its
 own preferences, launcher and file names. Includes the Animation and
 Timeline extensions of this fork.
CONTROL

# Owned by root, readable by all; only the launchers run.
find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE" -type f -exec chmod 644 {} +
chmod 755 "$STAGE/usr/bin/$PKG" "$STAGE/opt/$PKG/ingetrazo"
[ -e "$STAGE/opt/$PKG/ingetrazo-mcp" ] && chmod 755 "$STAGE/opt/$PKG/ingetrazo-mcp"
# The bundle's native libraries and helper binaries keep their x bit.
(cd "$BUNDLE" && find . -type f -perm -u+x -print0) \
    | (cd "$STAGE/opt/$PKG" && xargs -0 -r chmod 755)

mkdir -p "$OUT"
rm -f "$DEB"
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$DEB"
printf '%s  (%s)\n' "$DEB" "$(du -h "$DEB" | cut -f1)"
