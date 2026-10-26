#!/usr/bin/env bash
# Install this skill into a DSH skills directory.
#   ./install.sh                     # -> ${DSH_HOME:-$HOME/.dsh-home}/skills
#   ./install.sh /path/to/skills
#   SKIP_DEPS=1 ./install.sh
set -euo pipefail

src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "${1:-}" != "" ]; then dest="$1"; else dest="${DSH_HOME:-$HOME/.dsh-home}/skills"; fi
target="$dest/generic-pixel-svg-rebuild"

mkdir -p "$dest"
rm -rf "$target"
cp -R "$src" "$target"
find "$target" -type d -name __pycache__ -prune -exec rm -rf {} +

echo "installed -> $target"

if [ "${SKIP_DEPS:-0}" != "1" ]; then
  echo "installing Python dependencies ..."
  python3 -m pip install --quiet --disable-pip-version-check -r "$target/requirements.txt" || \
    echo "pip failed; run: python3 -m pip install -r $target/requirements.txt"
fi

python3 -c "import numpy, PIL, scipy, potrace; print('deps ok')"
echo
echo "Try it:"
echo "  python3 $target/tests/run_tests.py"
echo "  python3 $target/scripts/vectorize.py ref.png out.html 192"
