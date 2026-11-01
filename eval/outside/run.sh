#!/bin/sh
# Reproduces the outside pass. Fetches the five APG example pages (W3C
# content, never committed) into .cache/apg, serves them on loopback, walks
# them (no model call) and, with META_API_KEY set, reviews each twice.
set -eu
root="${1:-.cache/apg}"
out="${2:-runs/outside}"
mkdir -p "$root" "$out"
pages="dialog-modal/examples/dialog:dialog
menu-button/examples/menu-button-actions:menu-actions
menu-button/examples/menu-button-links:menu-links
disclosure/examples/disclosure-faq:disclosure-faq
disclosure/examples/disclosure-navigation:disclosure-nav"
for entry in $pages; do
  (cd "$root" && wget -q -p -k -nH -E -H -D www.w3.org \
    "https://www.w3.org/WAI/ARIA/apg/patterns/${entry%%:*}/")
done
python3 -m http.server 8765 --bind 127.0.0.1 --directory "$root" >/dev/null 2>&1 &
server=$!
trap 'kill $server' EXIT
sleep 1
dirs=""
for entry in $pages; do
  dir="$out/${entry##*:}"
  uv run nomouse walk \
    "http://127.0.0.1:8765/WAI/ARIA/apg/patterns/${entry%%:*}/index.html" --out "$dir"
  if [ -n "${META_API_KEY:-}" ]; then
    uv run nomouse review "$dir" --run outside1
    uv run nomouse review "$dir" --run outside2
  fi
  dirs="$dirs $dir"
done
[ -n "${META_API_KEY:-}" ] && python3 eval/outside/score.py $dirs
