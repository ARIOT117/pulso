#!/usr/bin/env bash
# Construye dist/pulso_<versión>_all.deb sin dependencias de compilación (solo dpkg-deb).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
version="$(python3 -P -c "import sys; sys.path.insert(0, '$here'); import pulso; print(pulso.__version__)")"
id="io.github.ariot117.Pulso"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
root="$stage/pulso"

install -d "$root/DEBIAN" "$root/usr/lib/pulso" "$root/usr/bin" \
  "$root/usr/share/applications" "$root/usr/share/metainfo" \
  "$root/usr/share/icons/hicolor/scalable/apps" "$root/usr/share/doc/pulso"

# programa (sin cachés de Python)
cp -r "$here/pulso" "$root/usr/lib/pulso/"
install -d "$root/usr/lib/pulso/data"
install -m 644 "$here/data/$id.svg" "$root/usr/lib/pulso/data/"
find "$root/usr/lib/pulso" -name __pycache__ -prune -exec rm -rf {} +
find "$root/usr/lib/pulso" -type f -exec chmod 644 {} +

install -m 755 "$here/packaging/pulso" "$root/usr/bin/pulso"
install -m 644 "$here/packaging/$id.desktop" "$root/usr/share/applications/"
install -m 644 "$here/packaging/$id.metainfo.xml" "$root/usr/share/metainfo/"
install -m 644 "$here/data/$id.svg" "$root/usr/share/icons/hicolor/scalable/apps/"
install -m 644 "$here/packaging/copyright" "$root/usr/share/doc/pulso/copyright"
gzip -9n -c "$here/packaging/changelog" > "$root/usr/share/doc/pulso/changelog.Debian.gz"
chmod 644 "$root/usr/share/doc/pulso/changelog.Debian.gz"

size="$(du -sk --exclude=DEBIAN "$root" | cut -f1)"
sed -e "s/@VERSION@/$version/" -e "s/@SIZE@/$size/" "$here/packaging/control.in" > "$root/DEBIAN/control"

mkdir -p "$here/dist"
out="$here/dist/pulso_${version}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$root" "$out" >/dev/null
echo "$out"
