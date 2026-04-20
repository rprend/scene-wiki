#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DUMP_PATH="${1:-${ROOT_DIR}/import/mediawiki-import.xml}"

if [[ ! -f "${DUMP_PATH}" ]]; then
  echo "Dump file not found: ${DUMP_PATH}" >&2
  exit 1
fi

docker compose -f "${ROOT_DIR}/docker-compose.yml" exec -T mediawiki \
  php maintenance/run.php importDump --conf /var/www/html/LocalSettings.php < "${DUMP_PATH}"
