#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXAMPLE_DIR="${ROOT_DIR}/examples/mediawiki"

DO_HOST="${DO_HOST:-}"
DO_USER="${DO_USER:-root}"
DO_SSH_PORT="${DO_SSH_PORT:-22}"
DO_REMOTE_DIR="${DO_REMOTE_DIR:-/opt/scene-wiki-mediawiki}"
EXPORT_DIR="${EXPORT_DIR:-}"
MEDIAWIKI_SITE_TITLE="${MEDIAWIKI_SITE_TITLE:-Scene Wiki}"
MEDIAWIKI_SITE_SERVER="${MEDIAWIKI_SITE_SERVER:-}"
MEDIAWIKI_DB_NAME="${MEDIAWIKI_DB_NAME:-mediawiki}"
MEDIAWIKI_DB_USER="${MEDIAWIKI_DB_USER:-mediawiki}"
MEDIAWIKI_DB_PASSWORD="${MEDIAWIKI_DB_PASSWORD:-}"
MEDIAWIKI_DB_ROOT_PASSWORD="${MEDIAWIKI_DB_ROOT_PASSWORD:-}"
MEDIAWIKI_ADMIN_USER="${MEDIAWIKI_ADMIN_USER:-admin}"
MEDIAWIKI_ADMIN_PASS="${MEDIAWIKI_ADMIN_PASS:-}"
MEDIAWIKI_HTTP_PORT="${MEDIAWIKI_HTTP_PORT:-80}"
SCENE_SEARCH_BASE="${SCENE_SEARCH_BASE:-/scene-search}"
SCENE_SEARCH_APP_SCRIPT="${SCENE_SEARCH_APP_SCRIPT:-${SCENE_SEARCH_BASE}/scene-search-app.js}"

if [[ -z "${DO_HOST}" ]]; then
  echo "Set DO_HOST to the droplet hostname or IP." >&2
  exit 1
fi

if [[ -z "${EXPORT_DIR}" ]]; then
  echo "Set EXPORT_DIR to a MediaWiki export directory containing mediawiki-import.xml." >&2
  exit 1
fi

if [[ ! -f "${EXPORT_DIR}/mediawiki-import.xml" ]]; then
  echo "Missing export dump: ${EXPORT_DIR}/mediawiki-import.xml" >&2
  exit 1
fi

if [[ -z "${MEDIAWIKI_DB_PASSWORD}" || -z "${MEDIAWIKI_DB_ROOT_PASSWORD}" || -z "${MEDIAWIKI_ADMIN_PASS}" ]]; then
  echo "Set MEDIAWIKI_DB_PASSWORD, MEDIAWIKI_DB_ROOT_PASSWORD, and MEDIAWIKI_ADMIN_PASS." >&2
  exit 1
fi

if [[ -z "${MEDIAWIKI_SITE_SERVER}" ]]; then
  MEDIAWIKI_SITE_SERVER="http://${DO_HOST}"
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

mkdir -p \
  "${TMP_DIR}/import" \
  "${TMP_DIR}/images" \
  "${TMP_DIR}/db" \
  "${TMP_DIR}/scene-search"

cp "${EXAMPLE_DIR}/docker-compose.yml" "${TMP_DIR}/docker-compose.yml"
cp "${EXPORT_DIR}/mediawiki-import.xml" "${TMP_DIR}/import/mediawiki-import.xml"
if [[ -d "${EXPORT_DIR}/static" ]]; then
  rsync -a "${EXPORT_DIR}/static/" "${TMP_DIR}/scene-search/"
fi

cat > "${TMP_DIR}/.env" <<EOF
MEDIAWIKI_HTTP_PORT=${MEDIAWIKI_HTTP_PORT}
MEDIAWIKI_DB_NAME=${MEDIAWIKI_DB_NAME}
MEDIAWIKI_DB_USER=${MEDIAWIKI_DB_USER}
MEDIAWIKI_DB_PASSWORD=${MEDIAWIKI_DB_PASSWORD}
MEDIAWIKI_DB_ROOT_PASSWORD=${MEDIAWIKI_DB_ROOT_PASSWORD}
EOF

SITE_NAME="${MEDIAWIKI_SITE_TITLE}" \
SITE_SERVER="${MEDIAWIKI_SITE_SERVER}" \
DB_NAME="${MEDIAWIKI_DB_NAME}" \
DB_USER="${MEDIAWIKI_DB_USER}" \
DB_PASSWORD="${MEDIAWIKI_DB_PASSWORD}" \
SEARCH_BASE="${SCENE_SEARCH_BASE}" \
SEARCH_APP_SCRIPT="${SCENE_SEARCH_APP_SCRIPT}" \
perl -0pe '
  s/__SITE_NAME__/$ENV{SITE_NAME}/g;
  s/__SITE_SERVER__/$ENV{SITE_SERVER}/g;
  s/__DB_NAME__/$ENV{DB_NAME}/g;
  s/__DB_USER__/$ENV{DB_USER}/g;
  s/__DB_PASSWORD__/$ENV{DB_PASSWORD}/g;
  s#__SEARCH_BASE__#$ENV{SEARCH_BASE}#g;
  s#__SEARCH_APP_SCRIPT__#$ENV{SEARCH_APP_SCRIPT}#g;
' "${EXAMPLE_DIR}/LocalSettings.php.example" > "${TMP_DIR}/LocalSettings.php"

REMOTE="${DO_USER}@${DO_HOST}"
SSH=(ssh -p "${DO_SSH_PORT}")

"${SSH[@]}" "${REMOTE}" "mkdir -p '${DO_REMOTE_DIR}'"
rsync -az --delete -e "ssh -p ${DO_SSH_PORT}" "${TMP_DIR}/" "${REMOTE}:${DO_REMOTE_DIR}/"

"${SSH[@]}" "${REMOTE}" bash -s <<EOF
set -euo pipefail
cd "${DO_REMOTE_DIR}"

docker compose up -d db
until docker compose exec -T db mariadb-admin ping -h 127.0.0.1 -u root -p"${MEDIAWIKI_DB_ROOT_PASSWORD}" --silent; do
  sleep 2
done

if ! docker compose exec -T db mariadb -u root -p"${MEDIAWIKI_DB_ROOT_PASSWORD}" -N -e "USE ${MEDIAWIKI_DB_NAME}; SHOW TABLES LIKE 'page';" | grep -q page; then
  grep -v 'LocalSettings.php' docker-compose.yml > docker-compose.bootstrap.yml
  docker compose -f docker-compose.bootstrap.yml up -d mediawiki
  docker compose -f docker-compose.bootstrap.yml exec -T mediawiki php maintenance/run.php install \
    --confpath /tmp/scene-wiki-install \
    --dbname "${MEDIAWIKI_DB_NAME}" \
    --dbserver db \
    --dbuser "${MEDIAWIKI_DB_USER}" \
    --dbpass "${MEDIAWIKI_DB_PASSWORD}" \
    --server "${MEDIAWIKI_SITE_SERVER}" \
    --scriptpath "" \
    --pass "${MEDIAWIKI_ADMIN_PASS}" \
    "${MEDIAWIKI_SITE_TITLE}" \
    "${MEDIAWIKI_ADMIN_USER}"
  docker compose -f docker-compose.bootstrap.yml down
  rm -f docker-compose.bootstrap.yml
fi

docker compose up -d
docker compose exec -T mediawiki php maintenance/run.php update --quick --conf /var/www/html/LocalSettings.php
docker compose exec -T mediawiki php maintenance/run.php importDump --conf /var/www/html/LocalSettings.php < import/mediawiki-import.xml
EOF

echo "Deployed MediaWiki bundle to ${MEDIAWIKI_SITE_SERVER}"
