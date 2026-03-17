#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="nightshift-runner"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BRANCH_NAME="chore/nightshift-cleanup-${TIMESTAMP}"
LEDGER_FILE=".nightshift_ledger.txt"
PY_PROMPT_FILE="nightshift/prompts/strict_cleanup_python.txt"
TS_PROMPT_FILE="nightshift/prompts/strict_cleanup_ts.txt"
MODEL="ollama/qwen2.5-coder:7b"

# --- Safety check: abort if working directory is not clean ---
if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: Working directory is not clean. Commit or stash changes before running NightShift."
  git status --short
  exit 1
fi

git checkout -b "${BRANCH_NAME}"

docker build -t "${IMAGE_NAME}" -f nightshift/Dockerfile .

touch "${LEDGER_FILE}"

cleanup_on_exit() {
  echo ""
  echo "NightShift interrupted. Ledger preserved at ${LEDGER_FILE}."
  exit 0
}
trap cleanup_on_exit INT

BACKEND_FILES="$(
  find backend/app -type f -name '*.py' \
    -not -path '*/__pycache__/*' \
    -not -name '__init__.py' \
    || true
)"

FRONTEND_FILES="$(
  find frontend/src -type f \( -name '*.ts' -o -name '*.tsx' \) \
    | grep -v '/components/ui/' \
    | grep -v '/test/' \
    || true
)"

MASTER_QUEUE="$(printf "%s\n%s\n" "${BACKEND_FILES}" "${FRONTEND_FILES}" | sed '/^$/d')"

if [[ -z "${MASTER_QUEUE}" ]]; then
  echo "No backend/frontend files found. Nothing to do."
  exit 0
fi

while IFS= read -r FILE; do
  if [[ -z "${FILE}" ]]; then
    continue
  fi

  if grep -Fxq "${FILE}" "${LEDGER_FILE}"; then
    echo "Skipping (in ledger): ${FILE}"
    continue
  fi

  echo ""
  echo "Processing: ${FILE}"

  PROMPT_FILE="${PY_PROMPT_FILE}"
  if [[ "${FILE}" == *.ts || "${FILE}" == *.tsx ]]; then
    PROMPT_FILE="${TS_PROMPT_FILE}"
  fi

  PIPELINE_EXIT=0
  docker run --rm \
    --network host \
    -v "$(pwd)":/app \
    -w /app \
    "${IMAGE_NAME}" \
    bash -lc "
      set -euo pipefail
      aider --model '${MODEL}' --map-tokens 0 --yes --message-file '${PROMPT_FILE}' '${FILE}'

      if [[ '${FILE}' == *.py ]]; then
        PYTHONPATH=/app pytest backend/tests/
        ruff check '${FILE}'
        mypy '${FILE}'
      else
        cd frontend
        npm install
        npx tsc --noEmit
      fi
    " || PIPELINE_EXIT=$?

  if [[ "${PIPELINE_EXIT}" -ne 0 ]]; then
    echo "FAILED checks for ${FILE}. Resetting working tree."
    git reset --hard
    exit 1
  fi

  git add "${FILE}"
  git commit -m "chore(nightshift): cleanup ${FILE}"
  echo "${FILE}" >> "${LEDGER_FILE}"
  echo "Done with $FILE. Pausing for 5 seconds..."
  sleep 5
done <<< "${MASTER_QUEUE}"

echo ""
echo "Queue complete. Ledger written to ${LEDGER_FILE}."
echo "Push and create a PR manually in the morning."

