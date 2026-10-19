#!/usr/bin/env bash
# Construye dist/pulso_<versión>_all.deb sin dependencias de compilación (solo dpkg-deb).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
version="$(python3 -P -c "import sys; sys.path.insert(0, '$here'); import pulso; print(pulso.__version__)")"
id="io.github.ariot117.Pulso"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
root="$stage/pulso"

install -d "$root/DEBIAN" "$root/usr/share/doc/pulso"
"$here/packaging/install-files.sh" "$root"
install -m 644 "$here/packaging/copyright" "$root/usr/share/doc/pulso/copyright"
gzip -9n -c "$here/packaging/changelog" > "$root/usr/share/doc/pulso/changelog.Debian.gz"
chmod 644 "$root/usr/share/doc/pulso/changelog.Debian.gz"

size="$(du -sk --exclude=DEBIAN "$root" | cut -f1)"
sed -e "s/@VERSION@/$version/" -e "s/@SIZE@/$size/" "$here/packaging/control.in" > "$root/DEBIAN/control"

mkdir -p "$here/dist"
out="$here/dist/pulso_${version}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$root" "$out" >/dev/null
echo "$out"
