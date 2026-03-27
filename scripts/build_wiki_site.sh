#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${SCENE_WIKI_CONFIG:-${ROOT_DIR}/.scene-wiki.env}"

if [[ -f "${CONFIG_PATH}" ]]; then
  set -a
  source "${CONFIG_PATH}"
  set +a
fi

RUN_DIR="${RUN_DIR:-${ROOT_DIR}/data/runs/latest}"
VAULT_DIR="${VAULT_DIR:-${ROOT_DIR}/vault/default}"
WIKI_DIR="${WIKI_DIR:-${ROOT_DIR}/wiki}"
OUTPUT_DIR="${OUTPUT_DIR:-${ROOT_DIR}/dist/wiki}"
CACHE_DIR="${CACHE_DIR:-${ROOT_DIR}/.cache/scene-wiki}"
MODE="auto"
FORCE=0
QUARTZ_CONCURRENCY="${QUARTZ_CONCURRENCY:-3}"
SEARCH_ASSET_PATH="${OUTPUT_DIR}/scene-search-index.json"
CACHED_SEARCH_ASSET_PATH="${CACHE_DIR}/scene-search-index.json"

usage() {
  cat <<'EOF'
Usage: scripts/build_wiki_site.sh [--mode auto|quartz|frontend] [--force]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

mkdir -p "${CACHE_DIR}"

fingerprint_paths() {
  python3 - "$@" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

h = hashlib.sha256()
for raw in sys.argv[1:]:
    path = Path(raw)
    if not path.exists():
        h.update(b"MISSING\0")
        h.update(raw.encode("utf-8"))
        h.update(b"\0")
        continue
    entries = [path] if path.is_file() else sorted(child for child in path.rglob("*") if child.is_file())
    for entry in entries:
        stat = entry.stat()
        h.update(str(entry.resolve()).encode("utf-8"))
        h.update(b"\0")
        h.update(str(stat.st_size).encode("utf-8"))
        h.update(b"\0")
        with entry.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        h.update(b"\0")
print(h.hexdigest())
PY
}

cache_key_path() {
  printf '%s/%s.key' "${CACHE_DIR}" "$1"
}

cache_matches() {
  [[ -f "$1" ]] && [[ "$(cat "$1")" == "$2" ]]
}

write_cache_key() {
  printf '%s' "$2" > "$1"
}

cache_file_matches() {
  [[ -f "$1" ]] || return 1
  mkdir -p "$(dirname "$2")"
  cp "$1" "$2"
}

vault_key="$(
  fingerprint_paths \
    "${RUN_DIR}/normalized" \
    "${RUN_DIR}/artifacts/${WIKI_CORPUS_FILENAME:-corpus.json}" \
    "${ROOT_DIR}/src/scene_wiki/scene_wiki.py"
)"
content_key="$(
  fingerprint_paths \
    "${VAULT_DIR}" \
    "${ROOT_DIR}/src/scene_wiki/scene_wiki.py"
)"
quartz_key="$(
  fingerprint_paths \
    "${WIKI_DIR}/content" \
    "${WIKI_DIR}/quartz" \
    "${WIKI_DIR}/quartz.config.ts" \
    "${WIKI_DIR}/package.json" \
    "${WIKI_DIR}/package-lock.json"
)"
search_key="$(
  fingerprint_paths \
    "${RUN_DIR}/normalized" \
    "${RUN_DIR}/artifacts/${WIKI_CORPUS_FILENAME:-corpus.json}" \
    "${ROOT_DIR}/src/scene_wiki/scene_search.py" \
    "${ROOT_DIR}/src/scene_wiki/scene_wiki.py"
)"
bundle_key="$(
  fingerprint_paths \
    "${WIKI_DIR}/custom" \
    "${WIKI_DIR}/package.json" \
    "${WIKI_DIR}/package-lock.json"
)"

if cache_matches "$(cache_key_path search)" "${search_key}" && [[ -f "${SEARCH_ASSET_PATH}" && ! -f "${CACHED_SEARCH_ASSET_PATH}" ]]; then
  cache_file_matches "${SEARCH_ASSET_PATH}" "${CACHED_SEARCH_ASSET_PATH}"
fi

if [[ "${MODE}" == "auto" ]]; then
  if (( FORCE == 1 )) || ! cache_matches "$(cache_key_path vault)" "${vault_key}" || [[ ! -f "${VAULT_DIR}/Home.md" ]]; then
    PYTHONPATH="${ROOT_DIR}/src" python3 -m scene_wiki.cli build-vault "${RUN_DIR}" "${VAULT_DIR}"
    write_cache_key "$(cache_key_path vault)" "${vault_key}"
  fi

  if (( FORCE == 1 )) || ! cache_matches "$(cache_key_path content)" "${content_key}" || [[ ! -f "${WIKI_DIR}/content/index.md" ]]; then
    PYTHONPATH="${ROOT_DIR}/src" python3 -m scene_wiki.cli prepare-content "${VAULT_DIR}" "${WIKI_DIR}/content"
    write_cache_key "$(cache_key_path content)" "${content_key}"
  fi
fi

if [[ "${MODE}" != "frontend" ]]; then
  if [[ "${MODE}" == "quartz" ]] || (( FORCE == 1 )) || ! cache_matches "$(cache_key_path quartz)" "${quartz_key}" || [[ ! -f "${OUTPUT_DIR}/index.html" ]]; then
    (cd "${WIKI_DIR}" && node ./quartz/bootstrap-cli.mjs build -d content -o "${OUTPUT_DIR}" --concurrency "${QUARTZ_CONCURRENCY}")
    write_cache_key "$(cache_key_path quartz)" "${quartz_key}"
  fi
fi

if [[ "${MODE}" == "auto" || "${MODE}" == "quartz" ]]; then
  if (( FORCE == 1 )) || ! cache_matches "$(cache_key_path search)" "${search_key}" || [[ ! -f "${SEARCH_ASSET_PATH}" && ! -f "${CACHED_SEARCH_ASSET_PATH}" ]]; then
    PYTHONPATH="${ROOT_DIR}/src" python3 -m scene_wiki.cli build-search-assets "${RUN_DIR}" "${OUTPUT_DIR}"
    write_cache_key "$(cache_key_path search)" "${search_key}"
    cache_file_matches "${SEARCH_ASSET_PATH}" "${CACHED_SEARCH_ASSET_PATH}"
  elif [[ ! -f "${SEARCH_ASSET_PATH}" && -f "${CACHED_SEARCH_ASSET_PATH}" ]]; then
    cp "${CACHED_SEARCH_ASSET_PATH}" "${SEARCH_ASSET_PATH}"
  fi
fi

if (( FORCE == 1 )) || [[ "${MODE}" != "auto" ]] || ! cache_matches "$(cache_key_path bundles)" "${bundle_key}" || [[ ! -f "${OUTPUT_DIR}/_worker.js" ]] || [[ ! -f "${OUTPUT_DIR}/static/scene-search-app.js" ]]; then
  node "${WIKI_DIR}/custom/build-scene-search-bundles.mjs" "${OUTPUT_DIR}"
  write_cache_key "$(cache_key_path bundles)" "${bundle_key}"
fi
