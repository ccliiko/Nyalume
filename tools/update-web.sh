#!/usr/bin/env bash
# Run on the Linux server from any working directory. No destructive volume operations.
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-build}"
if [[ "$mode" != build && "$mode" != pull ]]; then
  echo 'Usage: bash tools/update-web.sh [build|pull]' >&2
  exit 2
fi
compose=(docker compose --env-file .env.web -f compose.web.yaml)
mkdir -p web-backups
chmod 700 web-backups
umask 077
if [[ -n "$("${compose[@]}" ps -a -q postgres)" ]]; then
  if ! "${compose[@]}" exec -T postgres pg_isready -U nyalume -d nyalume >/dev/null 2>&1; then
    echo 'Existing database is not ready. Start/recover it before updating so a backup can be made.' >&2
    exit 1
  fi
  backup="web-backups/nyalume-$(date -u +%Y%m%dT%H%M%SZ).dump"
  "${compose[@]}" exec -T postgres pg_dump -U nyalume -d nyalume -Fc > "$backup"
  test -s "$backup"
  echo "Backup: $backup"
fi
if [[ "$mode" == build ]]; then
  "${compose[@]}" build api
else
  "${compose[@]}" pull api
fi
"${compose[@]}" up -d --no-build --wait --wait-timeout 180
"${compose[@]}" exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5); print('Nyalume web is healthy')"
echo 'Update complete. Keep the previous image tag and matching database backup for rollback.'
