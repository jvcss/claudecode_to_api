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

# Opt-in: só roda se CODEX_MODEL for definido, porque exige o provider OpenAI
# habilitado (CODEX_ENABLED=true) e credencial do Codex gravada. É aqui que as
# suposições sobre o SDK caem — deltas incrementais, usage e cwd sem repo git.
if [ -n "${CODEX_MODEL:-}" ]; then
  echo "6) Codex: modelo aparece em /v1/models"
  curl -sf "${AUTH[@]}" "$BASE_URL/v1/models" | grep -q "\"$CODEX_MODEL\"" || fail "codex: modelo ausente em /v1/models"

  echo "7) Codex: chat completion não-stream"
  RESP=$(curl -sf "${AUTH[@]}" "$BASE_URL/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$CODEX_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with just: ok\"}]}")
  echo "$RESP" | grep -q '"chat.completion"' || fail "codex não-stream: objeto errado"
  echo "$RESP" | grep -q '"finish_reason":"stop"' || fail "codex não-stream: finish_reason"
  echo "$RESP" | grep -q '"total_tokens"' || fail "codex não-stream: sem usage"

  echo "8) Codex: streaming com deltas incrementais"
  STREAM=$(curl -sfN "${AUTH[@]}" "$BASE_URL/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$CODEX_MODEL\",\"stream\":true,\"stream_options\":{\"include_usage\":true},\"messages\":[{\"role\":\"user\",\"content\":\"Count from 1 to 20, one number per line.\"}]}")
  echo "$STREAM" | grep -q 'data: \[DONE\]' || fail "codex stream: sem [DONE]"
  echo "$STREAM" | grep -q '"total_tokens"' || fail "codex stream: sem usage"
  # Mais de um chunk com conteúdo prova que o streaming é token-a-token e não
  # um bloco único no fim — a garantia que motivou escolher o SDK.
  CHUNKS=$(echo "$STREAM" | grep -c '"content":"[^"]' || true)
  [ "$CHUNKS" -ge 2 ] || fail "codex stream: só $CHUNKS chunk(s) com texto — sem deltas"
  echo "   deltas de texto recebidos: $CHUNKS"

  echo "9) Codex: modo agente é recusado"
  CODE=$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "$BASE_URL/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$CODEX_MODEL\",\"claude_options\":{\"mode\":\"agent\"},\"messages\":[{\"role\":\"user\",\"content\":\"oi\"}]}")
  [ "$CODE" = "403" ] || fail "codex agente: esperava 403, veio $CODE"
fi

echo "TODOS OS TESTES PASSARAM ✔"
