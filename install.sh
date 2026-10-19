#!/usr/bin/env bash
# Instala Pulso como aplicación de tu usuario: copia el programa a ~/.local/share/pulso
# y añade el lanzador al menú de GNOME. Vuelve a ejecutarlo para actualizar.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
dest="$data/pulso"
apps="$data/applications"
icons="$data/icons/hicolor/scalable/apps"

mkdir -p "$dest" "$apps" "$icons"
# lanzador e icono del ID anterior (com.diego.Pulso), de instalaciones previas
rm -f "$apps/com.diego.Pulso.desktop" "$icons/com.diego.Pulso.svg"
rm -rf "$dest/pulso" "$dest/data"
cp -r "$here/pulso" "$here/data" "$dest/"
find "$dest" -name __pycache__ -prune -exec rm -rf {} +

sed "s|@APPDIR@|$dest|" "$here/data/io.github.ariot117.Pulso.desktop.in" > "$apps/io.github.ariot117.Pulso.desktop"
cp "$here/data/io.github.ariot117.Pulso.svg" "$icons/io.github.ariot117.Pulso.svg"
update-desktop-database "$apps" 2>/dev/null || true
gtk-update-icon-cache -f -t "$data/icons/hicolor" 2>/dev/null || true
echo "Pulso instalado en $dest. Búscalo en Actividades como «Pulso»."
