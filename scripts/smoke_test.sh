#!/usr/bin/env bash
# Smoke test do gateway. Uso:
#   BASE_URL=http://localhost:8000 API_KEY=sk-... ./scripts/smoke_test.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-}"
AUTH=()
[ -n "$API_KEY" ] && AUTH=(-H "Authorization: Bearer $API_KEY")

fail() { echo "FALHOU: $1" >&2; exit 1; }

echo "1) /healthz"
curl -sf "$BASE_URL/healthz" | grep -q '"ok"' || fail "healthz"

echo "2) /v1/models"
curl -sf "${AUTH[@]}" "$BASE_URL/v1/models" | grep -q '"sonnet"' || fail "models"

echo "3) chat completion não-stream"
RESP=$(curl -sf "${AUTH[@]}" "$BASE_URL/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"haiku","messages":[{"role":"user","content":"Reply with just: ok"}]}')
echo "$RESP" | grep -q '"chat.completion"' || fail "não-stream: objeto errado"
echo "$RESP" | grep -q '"finish_reason":"stop"' || fail "não-stream: finish_reason"

echo "4) chat completion stream (SSE)"
STREAM=$(curl -sfN "${AUTH[@]}" "$BASE_URL/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"haiku","stream":true,"stream_options":{"include_usage":true},"messages":[{"role":"user","content":"Reply with just: ok"}]}')
echo "$STREAM" | grep -q '"chat.completion.chunk"' || fail "stream: sem chunks"
echo "$STREAM" | grep -q 'data: \[DONE\]' || fail "stream: sem [DONE]"
echo "$STREAM" | grep -q '"total_tokens"' || fail "stream: sem usage"

echo "5) memória stateless"
RESP=$(curl -sf "${AUTH[@]}" "$BASE_URL/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"haiku","messages":[{"role":"user","content":"My name is Alice."},{"role":"assistant","content":"Hi Alice!"},{"role":"user","content":"What is my name? Reply with the name only."}]}')
echo "$RESP" | grep -qi 'alice' || fail "memória stateless"

echo "TODOS OS TESTES PASSARAM ✔"
