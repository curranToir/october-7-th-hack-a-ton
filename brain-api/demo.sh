#!/bin/bash
# Live access story: same question, two users, then a grant closes the gap (and a revoke reopens it).
# Usage: ./demo.sh [base_url]   (run from brain-api/; reads BRAIN_API_TOKEN from .env)
set -euo pipefail
cd "$(dirname "$0")"
BASE=${1:-http://100.87.113.122:8200}
TOKEN=$(grep '^BRAIN_API_TOKEN=' .env | cut -d= -f2-)
LEAD=jared@neptuneops.com
ENG=curran@toirinc.com
Q="Who owns the Acme rollout and what is blocking go-live?"

call() { curl -sf -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' "$@"; }
ask() {
  echo; echo "== $1 asks: $Q"
  call -d "{\"as_user\":\"$1\",\"question\":\"$Q\",\"mode\":\"answer\"}" "$BASE/recall" |
    python3 -c 'import json,sys; r=json.load(sys.stdin); print("answer:  ", r["answer"]); print("sources: ", r["sources"]); print("searched:", r["datasets_searched"]); print("withheld:", r["withheld"])'
}
grant() {
  echo; echo "== $LEAD ${1}s acme-eng for $ENG"
  call -d "{\"owner\":\"$LEAD\",\"grantee\":\"$ENG\",\"dataset\":\"acme-eng\"}" "$BASE/$1" |
    python3 -c 'import json,sys; print("readable:", json.load(sys.stdin)["readable"])'
}

ask "$LEAD"
ask "$ENG"
grant grant
ask "$ENG"
grant revoke
