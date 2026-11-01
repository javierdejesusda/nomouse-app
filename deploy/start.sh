#!/bin/sh
# Container entrypoint. The W3C demo is fetched from its official zip at
# start, never baked into the image, and walked with no model call; then the
# replay is exported and served with live mode on $PORT.
set -eu

data="${NOMOUSE_DATA:-/data}"

# Started as root (the image has no USER): hosts such as Fly.io mount volumes
# owned by root, and files uploaded over ssh are root-owned too. Hand the data
# directory to the unprivileged user, then re-run this script as that user.
if [ "$(id -u)" = "0" ]; then
  chown -R nomouse:nomouse "$data"
  exec setpriv --reuid=nomouse --regid=nomouse --init-groups \
    env HOME=/home/nomouse "$0" "$@"
fi

runs="${NOMOUSE_RUNS:-$data/runs}"
cache="$data/cache"
site="$data/site"

nomouse mirror --cache "$cache"
for page in before/home.html before/tickets.html before/survey.html \
    after/home.html after/tickets.html after/survey.html; do
  out="$runs/eval/w3c-$(echo "${page%.html}" | tr / -)"
  [ -f "$out/verdicts.json" ] || nomouse walk "$page" --cache "$cache" --out "$out"
done
for file in builder/*.html; do
  out="$runs/builder/$(basename "$file" .html)"
  [ -f "$out/verdicts.json" ] || nomouse walk "$file" --out "$out"
done

rm -rf "$site"
nomouse export "$site" --runs-dir "$runs" --runs eval1 eval2 live \
  --audit eval/audit-corrections.json

set -- --live --host 0.0.0.0 --port "${PORT:-8000}" \
  --state-dir "$data/state" --cache "$cache"
if [ "${NOMOUSE_TRUST_PROXY:-0}" = "1" ]; then
  set -- "$@" --trust-proxy
fi
exec nomouse serve-replay "$site" "$@"
