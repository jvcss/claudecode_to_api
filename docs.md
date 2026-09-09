# Guia de uso dos endpoints (via curl)

Este documento mostra, **na ordem correta**, como operar o gateway do Claude Code
com `curl`: subir, autenticar, escolher modelo, conversar (com e sem streaming) e
usar o modo agente. Cada passo explica o que faz, o que esperar de volta e os erros
mais comuns.

> **Portas:** com Docker Compose o gateway responde em **`http://localhost:8099`**
> (mapeado de `8099:8000`). Rodando local com `uvicorn`, o padrão é **`:8000`**.
> Ajuste `BASE` abaixo conforme o seu caso.

## Convenções usadas aqui

Para não repetir chaves e URL em cada comando, defina estas variáveis no seu shell:

```bash
export BASE="http://localhost:8099"          # URL do gateway
export GW_KEY="troque-esta-chave"             # uma das GATEWAY_API_KEYS (clientes)
export ADMIN_KEY="troque-esta-chave-admin"    # ADMIN_API_KEY (endpoints de admin)
```

Há **dois níveis de autenticação**, ambos via header `Authorization: Bearer <chave>`:

| Nível | Chave | Usado em |
|---|---|---|
| Cliente | `GATEWAY_API_KEYS` | `/v1/chat/completions`, `/v1/models` |
| Admin | `ADMIN_API_KEY` (cai nas do gateway se não definida) | `/auth/*`, `/config/*` |

Todos os erros seguem o formato da OpenAI:
`{"error": {"message": "...", "type": "...", "param": ..., "code": "..."}}`.

---

## Passo 0 — Subir o gateway

Antes de tudo, configure e suba o serviço.

```bash
cp .env.example .env
# edite o .env e defina GATEWAY_API_KEYS e ADMIN_API_KEY (obrigatório em produção!)

docker compose up -d --build
```

Confirme que está no ar (este endpoint **não** exige autenticação):

```bash
curl -s "$BASE/healthz"
```

Resposta esperada:

```json
{"status": "ok", "version": "0.1.0"}
```

Se não responder, veja os logs: `docker compose logs -f gateway`.

---

## Passo 1 — Autenticar o Claude Code (uma vez)

O gateway precisa de uma credencial do Claude para falar com o modelo. O caminho
recomendado é gerar um **token de longa duração** (dura ~1 ano) na sua máquina e
colá-lo no gateway.

### 1.1 Gerar o token (na sua máquina, fora do servidor)

```bash
claude setup-token
```

Isso abre um fluxo OAuth no navegador (requer assinatura Claude Pro/Max). Ao final,
ele imprime um token começando com `sk-ant-oat01-...`. Copie-o.

### 1.2 Gravar o token no gateway

```bash
curl -s -X POST "$BASE/auth/token" \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"token": "sk-ant-oat01-COLE_SEU_TOKEN_AQUI"}'
```

Resposta esperada (o gateway valida o token com um ping barato ao modelo):

```json
{"stored": true, "valid": true, "error": null}
```

- `stored: true` — o token foi salvo (em `/data/credentials.json`, permissão `0600`).
- `valid: true` — o ping autenticou com sucesso.
- Se vier `"valid": false`, o token está errado/expirado — a mensagem em `error`
  explica. O token é salvo mesmo assim, para você poder inspecionar; basta regravar
  o correto.

**Alternativas:**

- Você também pode gravar uma **API key** do Claude Console (`sk-ant-api...`): o
  gateway detecta o tipo automaticamente.
- Para pular a validação (grava sem testar): adicione `"validate": false` ao corpo.

### 1.3 Conferir o estado da credencial

```bash
curl -s "$BASE/auth/status" -H "Authorization: Bearer $ADMIN_KEY"
```

Resposta (o token **nunca** é retornado, só os 4 últimos caracteres):

```json
{
  "configured": true,
  "source": "stored_oauth",
  "type": "oauth",
  "token_suffix": "o123",
  "created_at": "2026-07-09T12:00:00+00:00",
  "validated_at": "2026-07-09T12:00:07+00:00",
  "last_error": null
}
```

O campo `source` diz de onde vem a credencial ativa:

| `source` | Significado |
|---|---|
| `stored_oauth` / `stored_api_key` | Token que você gravou via `/auth/token` |
| `process_env` | `ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN` no ambiente |
| `machine_login` | Login de assinatura montado em `~/.claude` (modo dev) |
| `none` | **Sem credencial** — o chat vai falhar até você gravar uma |

### 1.4 (Opcional) Revalidar depois

Para checar se o token ainda funciona (ex.: suspeita de expiração):

```bash
curl -s -X POST "$BASE/auth/validate" -H "Authorization: Bearer $ADMIN_KEY"
```

```json
{"valid": true, "error": null, "source": "stored_oauth"}
```

---

## Passo 2 — Descobrir e escolher o modelo

### 2.1 Listar modelos disponíveis

```bash
curl -s "$BASE/v1/models" -H "Authorization: Bearer $GW_KEY"
```

Retorna a lista no formato OpenAI. Os `id` válidos incluem os **aliases**
(`sonnet`, `opus`, `haiku`, `fable`, `sonnet[1m]`, `opus[1m]`), os **IDs completos**
(`claude-sonnet-5`, `claude-opus-4-8`, `claude-haiku-4-5`, ...) e, se o modo agente
estiver habilitado, as variantes `-agent` (`sonnet-agent`, etc.).

### 2.2 Ver o modelo padrão atual

Usado quando o cliente não manda um `model` (ou manda um desconhecido).

```bash
curl -s "$BASE/config/model" -H "Authorization: Bearer $ADMIN_KEY"
```

```json
{"default_model": "sonnet"}
```

### 2.3 Trocar o modelo padrão

```bash
curl -s -X POST "$BASE/config/model" \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "haiku"}'
```

```json
{"default_model": "haiku"}
```

A configuração é persistida em `/data/config.json` e sobrevive a reinícios. Um
modelo desconhecido aqui retorna erro 400 (diferente do chat, que cai no padrão).

---

## Passo 3 — Conversar (chat)

Este é o endpoint principal, compatível com `POST /v1/chat/completions` da OpenAI.
Ele é **stateless**: em toda requisição você manda o histórico completo em `messages`.

### 3.1 Resposta simples (sem streaming)

```bash
curl -s "$BASE/v1/chat/completions" \
  -H "Authorization: Bearer $GW_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "haiku",
    "messages": [
      {"role": "user", "content": "Explique o que é uma API em uma frase."}
    ]
  }'
```

Resposta (formato `chat.completion` da OpenAI):

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1783600000,
  "model": "claude-haiku-4-5-20251001",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "Uma API é ...", "refusal": null},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 187, "completion_tokens": 42, "total_tokens": 229,
            "prompt_tokens_details": {"cached_tokens": 0}}
}
```

### 3.2 Conversa com histórico (memória)

Como o gateway é stateless, para dar "memória" você reenvia todo o diálogo. O
gateway reconstrói o contexto a partir do array `messages`:

```bash
curl -s "$BASE/v1/chat/completions" \
  -H "Authorization: Bearer $GW_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "haiku",
    "messages": [
      {"role": "system", "content": "Responda em português, de forma concisa."},
      {"role": "user", "content": "Meu nome é Alice."},
      {"role": "assistant", "content": "Prazer, Alice!"},
      {"role": "user", "content": "Qual é o meu nome?"}
    ]
  }'
```

A resposta mencionará "Alice", provando que o histórico foi considerado. As
mensagens `system` viram a instrução de sistema; as anteriores viram um bloco de
histórico; a última mensagem `user` é a pergunta atual.

### 3.3 Resposta em streaming (SSE)

Para receber a resposta token a token (menor tempo até o primeiro caractere),
mande `"stream": true` e use `curl -N` (sem buffer):

```bash
curl -sN "$BASE/v1/chat/completions" \
  -H "Authorization: Bearer $GW_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "haiku",
    "stream": true,
    "stream_options": {"include_usage": true},
    "messages": [{"role": "user", "content": "Conte de 1 a 5, um por linha."}]
  }'
```

O corpo é uma sequência de eventos `data:` (Server-Sent Events):

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"1\n2\n3"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":194,"completion_tokens":18,"total_tokens":212,"prompt_tokens_details":{"cached_tokens":0}}}

data: [DONE]
```

Ordem dos eventos:
1. **role chunk** — `delta.role="assistant"` (chega na hora, marca o início);
2. **content chunks** — um por pedaço de texto (`delta.content`);
3. **finish chunk** — `delta` vazio + `finish_reason`;
4. **usage chunk** — só se você mandou `stream_options.include_usage: true`; vem com
   `choices: []` e o `usage` preenchido;
5. **`data: [DONE]`** — fim do stream.

Se ocorrer um erro no meio do stream, ele vem como um evento
`data: {"error": {...}}` (sem um `finish_reason:"stop"` antes), seguido de `[DONE]`.

### 3.4 Selecionar o modelo por request

Basta mudar `"model"`. Aceita aliases ou IDs completos:

```bash
# ... -d '{"model": "sonnet", "messages": [...]}'
# ... -d '{"model": "claude-opus-4-8", "messages": [...]}'
```

Se mandar um modelo desconhecido (ex.: `gpt-4o`, comum em clientes que assumem
OpenAI), o gateway usa o **modelo padrão** em vez de dar erro — a menos que
`MODEL_STRICT=true`, aí retorna 404.

---

## Passo 4 — Modo agente (ferramentas do Claude Code)

Por padrão o gateway roda em **chat puro** (sem ferramentas, igual a um LLM comum).
O **modo agente** libera as ferramentas do Claude Code (rodar comandos, ler/escrever
arquivos) dentro de um diretório de trabalho confinado.

> **Pré-requisito:** o servidor precisa ter sido iniciado com `AGENT_MODE_ENABLED=true`
> (no `.env`). Caso contrário, qualquer request em modo agente retorna **403**
> `agent_mode_disabled`.

### 4.1 Ativar via campo extra `claude_options`

```bash
curl -s "$BASE/v1/chat/completions" \
  -H "Authorization: Bearer $GW_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sonnet",
    "messages": [{"role": "user", "content": "Crie um arquivo hello.txt com o texto: oi"}],
    "claude_options": {
      "mode": "agent",
      "cwd": "meu-projeto",
      "emit_tool_activity": true,
      "max_turns": 20
    }
  }'
```

Campos de `claude_options`:

| Campo | Descrição |
|---|---|
| `mode` | `"chat"` (padrão) ou `"agent"` |
| `cwd` | Diretório de trabalho, **relativo ao `AGENT_ROOT`** (`/workspace`). Confinado: um caminho fora do root retorna 400 |
| `emit_tool_activity` | Se `true`, cada uso de ferramenta aparece na resposta como `> [tool: Bash] ...` |
| `max_turns` | Máximo de turnos do agente (limitado por `AGENT_MAX_TURNS`) |
| `permission_mode` | `bypassPermissions` (padrão), `acceptEdits`, `default`, `plan` |
| `max_budget_usd` | Teto de custo do request (limitado pelo teto do servidor, se houver) |
| `timeout_seconds` | Teto de tempo do request (nunca ultrapassa `REQUEST_TIMEOUT_SECONDS`) |

### 4.2 Ativar via sufixo no modelo

Para clientes que só permitem configurar a string do modelo (sem `extra_body`),
use o sufixo `-agent`:

```bash
curl -s "$BASE/v1/chat/completions" \
  -H "Authorization: Bearer $GW_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sonnet-agent",
    "messages": [{"role": "user", "content": "Liste os arquivos do diretório atual."}]
  }'
```

Isso ativa o modo agente com os padrões do servidor (cwd = `AGENT_ROOT`).

---

## Passo 5 — Encerrar / rotacionar credenciais

Para remover o token gravado (o gateway volta a usar `process_env`/`machine_login`
se existirem, senão fica sem credencial):

```bash
curl -s -X DELETE "$BASE/auth/token" -H "Authorization: Bearer $ADMIN_KEY"
```

```json
{"removed": true}
```

Para **rotacionar**, basta gravar um novo token com o Passo 1.2 (sobrescreve o
anterior).

---

## Passo 6 — Backend OpenAI/Codex (assinatura ChatGPT)

Opcional. Só vale a pena se você tem uma assinatura ChatGPT Plus/Pro/Business e
quer usá-la pelas mesmas ferramentas. Suba com `CODEX_ENABLED=true`.

### 6.1 Autenticar (device code)

Não existe equivalente do `claude setup-token` para o plano de consumidor: não
há token longo de copiar e colar. O caminho headless é o *device code*, e o CLI
do Codex já vem embutido na imagem — não precisa instalar nada:

```bash
docker compose exec -e CODEX_HOME=/data/codex gateway \
  /usr/local/lib/python3.13/site-packages/codex_cli_bin/bin/codex login --device-auth
```

Abra a URL que ele imprime, informe o código, e o `auth.json` é gravado em
`./data/codex/`. Pule para o 6.3.

Se você não tem shell no host (VPS gerenciado por outra pessoa), a rota admin
faz o mesmo — mas exige `CODEX_ENABLED=true` antes:

```bash
curl -s -X POST "$BASE/codex/auth/device-code" \
  -H "Authorization: Bearer $ADMIN_KEY"
```

```json
{
  "login_id": "...",
  "verification_url": "https://auth.openai.com/activate",
  "user_code": "ABCD-EFGH",
  "next": "Abra a URL, informe o user_code e acompanhe em GET /codex/auth/status."
}
```

Abra a URL no navegador da **sua** máquina, informe o `user_code` e acompanhe:

```bash
curl -s "$BASE/codex/auth/status" -H "Authorization: Bearer $ADMIN_KEY"
```

```json
{
  "configured": true,
  "source": "auth_json",
  "auth_mode": "chatgpt",
  "account_id": "...",
  "email": "voce@exemplo.com",
  "plan": "plus",
  "enabled": true,
  "pending_login": false,
  "models_cached": 6
}
```

### 6.2 Alternativa: importar um `auth.json`

Se o device code estiver desabilitado no seu workspace, rode `codex login` na sua
máquina e importe o resultado:

```bash
curl -s -X POST "$BASE/codex/auth/import" \
  -H "Authorization: Bearer $ADMIN_KEY" -H "Content-Type: application/json" \
  -d "{\"auth_json\": $(cat ~/.codex/auth.json)}"
```

> Depois de importar, **pare de usar o `codex` naquela máquina com essa mesma
> cadeia de tokens**. O `refresh_token` é rotativo e de uso único: os dois lados
> se invalidam e será preciso refazer o login.

### 6.3 Conversar

Os modelos entram sozinhos em `/v1/models` assim que a credencial existe. Daí em
diante é o mesmo endpoint de sempre — só muda o `model`:

```bash
curl -s "$BASE/v1/chat/completions" \
  -H "Authorization: Bearer $GW_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.6-sol","messages":[{"role":"user","content":"Olá!"}]}'
```

Para redescobrir o catálogo depois de a OpenAI publicar modelos novos:

```bash
curl -s -X POST "$BASE/codex/models/refresh" -H "Authorization: Bearer $ADMIN_KEY"
```

### 6.4 Encerrar

```bash
curl -s -X DELETE "$BASE/codex/auth/token" -H "Authorization: Bearer $ADMIN_KEY"
```

---

## Referência rápida dos erros

| HTTP | `code` | Quando |
|---|---|---|
| 400 | `images_not_supported` | Mandou conteúdo de imagem/áudio |
| 400 | `unsupported_message_role` | Usou role `tool`/`function` |
| 400 | `unsupported_parameter` | Mandou `n` > 1 |
| 400 | `context_length_exceeded` | (Codex) Histórico maior que a janela do modelo |
| 403 | `agent_mode_unsupported_for_provider` | Pediu modo agente num modelo do Codex |
| 403 | `codex_disabled` | Rota `/codex/*` com `CODEX_ENABLED=false` |
| 400 | `tools_not_supported` | Mandou `tools`/`tool_choice`/`functions` (function-calling do cliente) |
| 400 | `invalid_cwd` | `claude_options.cwd` fora do `AGENT_ROOT` |
| 401 | `invalid_api_key` | Chave do gateway (Bearer) errada ou ausente |
| 403 | `agent_mode_disabled` | Pediu modo agente sem `AGENT_MODE_ENABLED=true` |
| 404 | `model_not_found` | Modelo desconhecido com `MODEL_STRICT=true` |
| 429 | `upstream_rate_limited` | Claude sobrecarregado/limite de uso (tem `Retry-After`) |
| 502 | `upstream_not_authenticated` | A credencial do **gateway** com o Claude é inválida/expirou |
| 504 | `request_timeout` | O request passou do tempo limite |

> A diferença entre **401** e **502** é importante: **401** significa que a *sua*
> chave de cliente está errada; **502** significa que a chave está certa, mas a
> credencial do *gateway* com o Claude precisa ser renovada pelo operador (Passo 1).

---

## Fluxo completo, do zero (resumo)

```bash
export BASE="http://localhost:8099"
export GW_KEY="troque-esta-chave"
export ADMIN_KEY="troque-esta-chave-admin"

# 0. subir e checar
docker compose up -d --build
curl -s "$BASE/healthz"

# 1. autenticar (token vem de `claude setup-token` na sua máquina)
curl -s -X POST "$BASE/auth/token" -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" -d '{"token":"sk-ant-oat01-..."}'
curl -s "$BASE/auth/status" -H "Authorization: Bearer $ADMIN_KEY"

# 2. (opcional) escolher modelo padrão
curl -s -X POST "$BASE/config/model" -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" -d '{"model":"sonnet"}'

# 3. conversar
curl -s "$BASE/v1/chat/completions" -H "Authorization: Bearer $GW_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"haiku","messages":[{"role":"user","content":"Olá!"}]}'
```

Pronto — a partir daqui qualquer cliente compatível com a API da OpenAI pode apontar
`base_url` para `"$BASE/v1"` e usar `GW_KEY` como `api_key`.
