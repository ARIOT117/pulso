#!/usr/bin/env bash
# Instala los archivos de Pulso bajo DESTDIR con el diseño de /usr. Lo usan build-deb.sh y el
# PKGBUILD del AUR, así ambos paquetes contienen exactamente lo mismo.
# Uso: packaging/install-files.sh <destdir>
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
dest="${1:?Uso: $0 <destdir>}"
id="io.github.ariot117.Pulso"

install -d "$dest/usr/lib/pulso/data"
cp -r "$here/pulso" "$dest/usr/lib/pulso/"
find "$dest/usr/lib/pulso" -name __pycache__ -prune -exec rm -rf {} +
find "$dest/usr/lib/pulso" -type d -exec chmod 755 {} +
find "$dest/usr/lib/pulso" -type f -exec chmod 644 {} +
install -Dm644 "$here/data/$id.svg" "$dest/usr/lib/pulso/data/$id.svg"

install -Dm755 "$here/packaging/pulso" "$dest/usr/bin/pulso"
install -Dm644 "$here/packaging/$id.desktop" "$dest/usr/share/applications/$id.desktop"
install -Dm644 "$here/packaging/$id.metainfo.xml" "$dest/usr/share/metainfo/$id.metainfo.xml"
install -Dm644 "$here/data/$id.svg" "$dest/usr/share/icons/hicolor/scalable/apps/$id.svg"
