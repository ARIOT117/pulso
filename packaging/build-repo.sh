#!/usr/bin/env bash
# Genera un repositorio APT «plano» y firmado en repo/ a partir de los .deb de dist/.
# Se publica tal cual en GitHub Pages (rama gh-pages) → https://ariot117.github.io/pulso
#
# Uso: PULSO_GPG_KEY=<id o correo de la clave> packaging/build-repo.sh
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
key="${PULSO_GPG_KEY:?Indica la clave de firma: PULSO_GPG_KEY=<id> $0}"
repo="$here/repo"

mkdir -p "$repo"
cp "$here"/dist/*.deb "$repo/"
cd "$repo"

dpkg-scanpackages --multiversion . /dev/null > Packages 2>/dev/null
gzip -9n -k -f Packages

python3 -P - <<'PY'
import hashlib, os, time
files = ["Packages", "Packages.gz"]
lines = [
    "Origin: Pulso",
    "Label: Pulso",
    "Suite: stable",
    "Codename: stable",
    "Architectures: all amd64 arm64",
    "Components: main",
    "Description: Repositorio APT de Pulso (monitor de recursos para GNOME)",
    "Date: " + time.strftime("%a, %d %b %Y %H:%M:%S UTC", time.gmtime()),
]
for algo, name in (("md5", "MD5Sum"), ("sha256", "SHA256")):
    lines.append(name + ":")
    for f in files:
        data = open(f, "rb").read()
        lines.append(f" {hashlib.new(algo, data).hexdigest()} {len(data)} {f}")
open("Release", "w").write("\n".join(lines) + "\n")
PY

rm -f InRelease Release.gpg
gpg --batch --yes --local-user "$key" --clearsign -o InRelease Release
gpg --batch --yes --local-user "$key" --armor --detach-sign -o Release.gpg Release
gpg --batch --yes --export "$key" > pulso-archive-keyring.gpg
cp "$here/packaging/repo-index.html" index.html
touch .nojekyll
echo "Repositorio listo en $repo"
