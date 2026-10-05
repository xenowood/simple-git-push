#!/bin/bash
# Builds dist/simple-git-push_<version>_all.deb
set -euo pipefail
cd "$(dirname "$0")"

VERSION=$(python3 -c "import re;print(re.search(r'__version__ = \"(.+?)\"', open('simple_git_push/__init__.py').read()).group(1))")
PKG=simple-git-push
ROOT=build/${PKG}_${VERSION}
OUT=dist/${PKG}_${VERSION}_all.deb
APPID=org.simplegitpush.SimpleGitPush
REPO_URL=$(python3 -c "import re;print(re.search(r'REPO_URL = \"(.+?)\"', open('simple_git_push/__init__.py').read()).group(1))")

rm -rf "$ROOT"
mkdir -p "$ROOT/DEBIAN" "$ROOT/usr/bin" "$ROOT/usr/lib/$PKG/simple_git_push" \
         "$ROOT/usr/share/applications" "$ROOT/usr/share/metainfo" \
         "$ROOT/usr/share/doc/$PKG" "$ROOT/usr/share/icons/hicolor/scalable/apps" dist

install -m 644 simple_git_push/*.py "$ROOT/usr/lib/$PKG/simple_git_push/"
install -m 755 data/simple-git-push "$ROOT/usr/bin/simple-git-push"
install -m 644 data/$APPID.desktop "$ROOT/usr/share/applications/"
install -m 644 data/$APPID.metainfo.xml "$ROOT/usr/share/metainfo/"
install -m 644 data/simple-git-push.svg "$ROOT/usr/share/icons/hicolor/scalable/apps/"
for s in 48 128 256; do
  mkdir -p "$ROOT/usr/share/icons/hicolor/${s}x${s}/apps"
  install -m 644 data/icons/simple-git-push-$s.png "$ROOT/usr/share/icons/hicolor/${s}x${s}/apps/simple-git-push.png"
done
install -m 644 README.md CLAUDE.md RELEASE_NOTES.md "$ROOT/usr/share/doc/$PKG/"
install -m 644 LICENSE "$ROOT/usr/share/doc/$PKG/copyright"

SIZE=$(du -sk "$ROOT/usr" | cut -f1)
cat > "$ROOT/DEBIAN/control" <<CTL
Package: $PKG
Version: $VERSION
Section: devel
Priority: optional
Architecture: all
Depends: python3 (>= 3.11), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, git
Recommends: gh
Installed-Size: $SIZE
Maintainer: Simple Git Push contributors <noreply@example.com>
Homepage: $REPO_URL
Description: Unpack a zip, commit, push and create a GitHub release
 Simple Git Push imports a zip file with source code, readme, release notes
 and a .deb, unpacks it into a development folder, commits and pushes it,
 and creates a GitHub release with gh. Several projects can be stored.
CTL

for f in postinst postrm; do
cat > "$ROOT/DEBIAN/$f" <<'SH'
#!/bin/sh
set -e
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications 2>/dev/null || true
exit 0
SH
chmod 755 "$ROOT/DEBIAN/$f"
done

find "$ROOT" -type d -exec chmod 755 {} +
dpkg-deb --root-owner-group --build "$ROOT" "$OUT"
echo "Built $OUT"
