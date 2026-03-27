#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${SCENE_WIKI_CONFIG:-${ROOT_DIR}/.scene-wiki.env}"

if [[ -f "${CONFIG_PATH}" ]]; then
  set -a
  source "${CONFIG_PATH}"
  set +a
fi

PROJECT_NAME="${PROJECT_NAME:-scene-wiki}"
OUTPUT_DIR="${OUTPUT_DIR:-${ROOT_DIR}/dist/wiki}"
BUILD_MODE="auto"
FORCE_BUILD=0
SKIP_BUILD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      BUILD_MODE="${2:-}"
      shift 2
      ;;
    --force-build)
      FORCE_BUILD=1
      shift
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -f "${ROOT_DIR}/.dev.vars" ]]; then
  set -a
  source "${ROOT_DIR}/.dev.vars"
  set +a
fi

if [[ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
  echo "Set CLOUDFLARE_ACCOUNT_ID before deploying." >&2
  exit 1
fi

if (( SKIP_BUILD == 0 )); then
  build_args=(--mode "${BUILD_MODE}")
  if (( FORCE_BUILD == 1 )); then
    build_args+=(--force)
  fi
  "${ROOT_DIR}/scripts/build_wiki_site.sh" "${build_args[@]}"
fi

cd "${ROOT_DIR}"
if [[ -n "${GOOGLE_API_KEY:-}" ]]; then
  printf '%s' "${GOOGLE_API_KEY}" | npx wrangler pages secret put GOOGLE_API_KEY --project-name "${PROJECT_NAME}" >/dev/null
fi
if [[ -n "${GEMINI_API_KEY:-}" ]]; then
  printf '%s' "${GEMINI_API_KEY}" | npx wrangler pages secret put GEMINI_API_KEY --project-name "${PROJECT_NAME}" >/dev/null
fi
npx wrangler pages deploy "${OUTPUT_DIR}" --project-name "${PROJECT_NAME}" --commit-dirty=true
