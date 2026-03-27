#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

DB_NAME="${1:-scene-wiki-platform}"
KV_NAME="${2:-scene-wiki-rate-limits}"

extract_first_match() {
  local text="$1"
  local pattern="$2"
  python3 - "$text" "$pattern" <<'PY'
import re
import sys

text = sys.argv[1]
pattern = sys.argv[2]
match = re.search(pattern, text)
if not match:
    raise SystemExit(1)
print(match.group(1))
PY
}

echo "Creating or reusing D1 database ${DB_NAME}..."
set +e
db_output="$(npx wrangler@latest d1 create "${DB_NAME}" 2>&1)"
db_status=$?
set -e
if [[ ${db_status} -ne 0 ]]; then
  db_output="$(npx wrangler@latest d1 list 2>&1 | python3 - "${DB_NAME}" <<'PY'
import re
import sys

target = sys.argv[1]
text = sys.stdin.read()
pattern = rf"{re.escape(target)}\s+([0-9a-f]{{8}}-[0-9a-f-]{{27}})"
match = re.search(pattern, text)
if match:
    print(match.group(1))
    raise SystemExit(0)
raise SystemExit(1)
PY
)"
  db_id="${db_output}"
else
  db_id="$(extract_first_match "${db_output}" '([0-9a-f]{8}-[0-9a-f-]{27})')"
fi

echo "Creating or reusing KV namespace ${KV_NAME}..."
set +e
kv_output="$(npx wrangler@latest kv namespace create "${KV_NAME}" 2>&1)"
kv_status=$?
set -e
if [[ ${kv_status} -ne 0 ]]; then
  kv_output="$(npx wrangler@latest kv namespace list 2>&1 | python3 - "${KV_NAME}" <<'PY'
import re
import sys

target = sys.argv[1]
text = sys.stdin.read()
pattern = rf"{re.escape(target)}\s+([0-9a-f]{{32}})"
match = re.search(pattern, text)
if match:
    print(match.group(1))
    raise SystemExit(0)
raise SystemExit(1)
PY
)"
  kv_id="${kv_output}"
else
  kv_id="$(extract_first_match "${kv_output}" '([0-9a-f]{32})')"
fi

python3 - "${ROOT_DIR}/wrangler.toml" "${db_id}" "${kv_id}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
db_id = sys.argv[2]
kv_id = sys.argv[3]
text = path.read_text()
text = text.replace("REPLACE_WITH_D1_DATABASE_ID", db_id)
text = text.replace("REPLACE_WITH_KV_NAMESPACE_ID", kv_id)
path.write_text(text)
PY

echo "Applying D1 migrations..."
npx wrangler@latest d1 migrations apply "${DB_NAME}" --remote

echo "wrangler.toml updated with:"
echo "  database_id=${db_id}"
echo "  kv_namespace_id=${kv_id}"
