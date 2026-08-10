# Claude Code → API compatível com OpenAI

Transforme sua assinatura **Claude Pro/Max** (preço fixo mensal) em um endpoint
`/v1/chat/completions` compatível com **OpenAI**, e aponte para ele qualquer
ferramenta que hoje paga **por token** — IDE, CLI de coding agent, chat UI ou
agente autônomo. Sem trocar de modelo, sem reescrever integrações: elas já
falam o protocolo OpenAI, só muda o `base_url`.

## Por que isso importa

O Claude Code já vem incluído na assinatura Pro/Max — mas a maioria das
ferramentas de IA (extensões de IDE, CLIs de coding agent, chat UIs, agentes
autônomos) só sabe falar com uma **API paga por token**. Sem este gateway,
você paga duas vezes: a assinatura *e* o consumo de API de cada ferramenta.
Com ele, essas mesmas ferramentas passam a usar a sua assinatura já paga.

Referência de custo (Claude Sonnet 5, preço público em
[claude.com/pricing](https://claude.com/pricing); estimativas, seu consumo real varia):

| Cenário | Via API paga por token | Via assinatura + este gateway |
|---|---|---|
| Uso leve — ~1M tokens/dia | ~US$4/dia → **US$60–130/mês** | Incluso no **Pro (US$20/mês)** |
| Coding agent o dia todo — ~10M tokens/dia | ~US$40/dia → **US$1.000+/mês** | Incluso no **Max (a partir de US$100/mês)** |

Quanto mais intensivo o uso — o caso típico de um coding agent rodando em
loop —, maior a diferença entre pagar por token e pagar assinatura fixa.

## Como funciona

- Usa o [`claude-agent-sdk`](https://pypi.org/project/claude-agent-sdk/), que **embute o
  CLI do Claude Code** no wheel — não precisa de Node.js.
- **Modo padrão = chat puro**: sem ferramentas, comportamento idêntico a um LLM de API.
- **Modo agente (opt-in)**: ferramentas completas do Claude Code (Bash, arquivos, etc.)
  num diretório confinado.
- **Stateless**: o cliente manda o histórico completo em `messages` a cada request
  (padrão OpenAI); o gateway reconstrói o contexto.

## Endpoints

| Método | Rota | Auth | Função |
|---|---|---|---|
| POST | `/v1/chat/completions` | Bearer | Chat compatível OpenAI (stream e não-stream) |
| GET | `/v1/models` | Bearer | Lista modelos (aliases, IDs e variantes `-agent`) |
| GET | `/v1/models/{id}` | Bearer | Detalhe de um modelo |
| POST | `/auth/token` | Admin | Grava o token do `claude setup-token` (ou API key) |
| GET | `/auth/status` | Admin | Fonte de credencial ativa |
| DELETE | `/auth/token` | Admin | Remove o token gravado |
| POST | `/auth/validate` | Admin | Re-valida a credencial (query barata com haiku) |
| GET/POST | `/config/model` | Admin | Lê/define o modelo padrão |
| GET | `/healthz` | — | Liveness |

## Subindo com Docker (produção)

```bash
cp .env.example .env       # defina GATEWAY_API_KEYS e ADMIN_API_KEY!
mkdir -p data workspace    # senão o Docker os cria como root e o container (uid 1000) não escreve
docker compose up -d --build
```

Os bind mounts `./data` e `./workspace` precisam pertencer ao uid 1000 (o usuário
`app` da imagem). Se o gateway subir com `PermissionError: [Errno 13] ... '/data/chat_cwd'`,
corrija com:

```bash
sudo chown -R 1000:1000 data workspace
```

A imagem também é publicada automaticamente no GHCR a cada push em `main`
(ver [.github/workflows/build.yml](.github/workflows/build.yml)) — útil se
quiser fazer pull em vez de build local.

Autentique o Claude Code no container (uma única vez; o token dura ~1 ano e
persiste no volume `./data`):

```bash
# Na sua máquina (fluxo OAuth no navegador; requer assinatura Pro/Max):
claude setup-token
# Cole o token no gateway:
curl -X POST http://SEU_HOST:8000/auth/token \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"token":"sk-ant-oat01-..."}'
# → {"stored": true, "valid": true, "error": null}
```

Também é aceita uma API key do Claude Console (`ANTHROPIC_API_KEY`) no mesmo endpoint.

## Rodando local (dev)

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000
```

Se a máquina já está logada no Claude Code (`~/.claude`), funciona sem token.

## Conecte suas ferramentas

Qualquer cliente que fale o protocolo OpenAI aponta pra cá trocando só
`base_url` e `api_key`. Abaixo, o passo a passo para as integrações mais
comuns — todas assumindo o gateway já rodando (`docker compose up -d`) e uma
`GATEWAY_API_KEYS` configurada.

> **Aviso de compatibilidade:** o gateway roda em modo chat por padrão e
> **rejeita** requests que mandam os campos `tools`/`tool_choice`/`functions`
> (function-calling nativo do cliente) com erro 400 — as ferramentas do
> Claude Code rodam **server-side**, via `claude_options.mode="agent"` (veja
> [docs.md](docs.md), Passo 4). Isso funciona sem atrito com ferramentas que
> implementam o próprio loop de ferramentas via *prompt* (Cline, Aider, e o
> modo `openai-completions` do OpenClaw); ferramentas que dependem de
> function-calling nativo do modelo para orquestrar suas próprias ações
> (browsing, exec, etc.) não vão funcionar em modo chat puro.

### Cline (VS Code)

1. Instale a extensão [Cline](https://marketplace.visualstudio.com/items?itemName=saoudrizwan.claude-dev).
2. Abra as configurações da Cline (ícone de engrenagem) → **API Provider** →
   `OpenAI Compatible`.
3. Preencha:
   - **Base URL**: `http://SEU_HOST:8099/v1`
   - **API Key**: sua `GATEWAY_API_KEYS`
   - **Model ID**: `sonnet` (ou `opus`, `haiku`)
4. Clique em **Verify** para confirmar a conexão.

Use o alias simples (`sonnet`), **não** o sufixo `-agent`: a Cline já executa
suas próprias ferramentas (ler/escrever arquivo, terminal) localmente no seu
workspace via parsing de texto — ativar o modo agente do gateway junto seria
redundante e rodaria num diretório confinado separado (`AGENT_ROOT`).

### Continue.dev (VS Code / JetBrains)

Edite `~/.continue/config.yaml` (ou `.continue/config.yaml` do projeto):

```yaml
models:
  - name: claude-gateway
    provider: openai
    model: sonnet
    apiBase: http://SEU_HOST:8099/v1
    apiKey: SUA_GATEWAY_API_KEY
    roles: [chat, edit, apply, autocomplete]
```

Funciona bem para chat/edit/autocomplete. O **Agent mode** do Continue pode
enviar `tools` nativo ao modelo — se topar com erro 400 `tools_not_supported`,
fique nos roles acima em vez do modo agente da extensão.

### Aider (CLI)

```bash
export OPENAI_API_BASE="http://SEU_HOST:8099/v1"
export OPENAI_API_KEY="SUA_GATEWAY_API_KEY"

aider --model openai/sonnet
```

O Aider edita código com blocos de diff no próprio texto da resposta (não usa
function-calling), então funciona nativamente bem com o modo chat do gateway.

### Open WebUI

1. Suba o Open WebUI (`docker run` ou compose, ver
   [docs oficiais](https://docs.openwebui.com/)).
2. **Admin Panel → Settings → Connections** → adicione uma conexão `OpenAI API`:
   - **API Base URL**: `http://SEU_HOST:8099/v1`
   - **API Key**: sua `GATEWAY_API_KEY`
3. Os modelos (`sonnet`, `opus`, `haiku`, ...) aparecem no seletor do chat.

Deixe os **Tools/Functions** nativos do Open WebUI desligados para o modelo —
eles usam function-calling do lado do cliente, que o gateway rejeita.

### OpenClaw

O [OpenClaw](https://openclaw.ai/) aceita provedores customizados via
`~/.openclaw/openclaw.json`, usando `api: "openai-completions"` — o modo
pensado justamente para backends OpenAI-compatible sem function-calling
nativo, o que casa bem com este gateway:

```json5
{
  models: {
    mode: "merge",
    providers: {
      "ccode-gateway": {
        baseUrl: "http://SEU_HOST:8099/v1",
        apiKey: "${CCODE_GATEWAY_API_KEY}",
        api: "openai-completions",
        models: [
          {
            id: "sonnet",
            name: "Claude Sonnet (via assinatura)",
            reasoning: false,
            input: ["text"],
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
            contextWindow: 200000,
            contextTokens: 180000,
            maxTokens: 8192,
          },
        ],
      },
    },
  },
  agents: {
    defaults: {
      model: { primary: "ccode-gateway/sonnet" },
    },
  },
}
```

```bash
export CCODE_GATEWAY_API_KEY="SUA_GATEWAY_API_KEY"
```

Repare no `cost: { input: 0, ... }` — é literal: como o gateway roda sobre a
sua assinatura fixa, não há custo marginal por token para o OpenClaw rastrear.
Funcionalidades do OpenClaw que dependem de function-calling nativo (não do
modo `openai-completions`) continuam sujeitas ao mesmo limite descrito no
aviso de compatibilidade acima.

## Usando via SDK (qualquer cliente OpenAI)

```python
from openai import OpenAI
client = OpenAI(base_url="http://SEU_HOST:8000/v1", api_key="SUA_GATEWAY_API_KEY")

resp = client.chat.completions.create(
    model="sonnet",  # sonnet | opus | haiku | fable | claude-sonnet-5 | ...
    messages=[{"role": "user", "content": "Olá!"}],
    stream=True,
)
for chunk in resp:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

### Modo agente (ferramentas do Claude Code)

Requer `AGENT_MODE_ENABLED=true`. Duas formas de ativar por request:

```python
# 1) Campo extra (controle completo):
client.chat.completions.create(
    model="sonnet",
    messages=[{"role": "user", "content": "Crie um script hello.py"}],
    extra_body={"claude_options": {
        "mode": "agent",
        "cwd": "meu-projeto",        # relativo ao AGENT_ROOT (confinado)
        "emit_tool_activity": True,   # mostra `> [tool: Bash] ...` na resposta
        "max_turns": 20,
    }},
)

# 2) Sufixo no modelo (para clientes que só configuram a string do modelo):
client.chat.completions.create(model="sonnet-agent", messages=[...])
```

`claude_options` aceita: `mode`, `cwd`, `allowed_tools`, `disallowed_tools`,
`max_turns`, `permission_mode`, `max_budget_usd`, `emit_tool_activity`,
`timeout_seconds`.

### Observações de compatibilidade

- `temperature`, `max_tokens`, `top_p`, `logprobs`, `stop` etc. são **aceitos e
  ignorados** (o Claude Code não os expõe).
- `response_format` `json_object`/`json_schema` → o gateway instrui o modelo a
  responder só com JSON (best-effort; o schema é anexado ao system prompt).
- `n > 1` → erro 400 (o gateway retorna uma única escolha).
- `tools`/`tool_choice`/`functions` (function-calling do cliente) → erro 400: as
  ferramentas rodam server-side; use `claude_options.mode="agent"`.
- Conteúdo de imagem/áudio → erro 400 `images_not_supported`.
- Roles `tool`/`function` → erro 400 (as ferramentas rodam server-side).
- Modelo desconhecido (ex.: `gpt-4o`) → cai no modelo padrão (`MODEL_STRICT=true`
  transforma em erro 404). Sufixo `-agent` em modelo desconhecido **não** ativa o
  modo agente.
- `usage` vem do Claude Code: `prompt_tokens` inclui tokens de cache
  (`prompt_tokens_details.cached_tokens` = lidos do cache).
- Erro no meio de um stream é emitido como evento `data: {"error": ...}` (sem um
  `finish_reason:"stop"` antes), seguindo a convenção da OpenAI.
- Falha da credencial do gateway com o upstream → 502 `upstream_not_authenticated`
  (não 401), para o cliente não confundir com a própria chave dele.

## Variáveis de ambiente

Ver [.env.example](.env.example). As principais:

| Var | Default | Função |
|---|---|---|
| `GATEWAY_API_KEYS` | *(vazio = aberto, só dev)* | Chaves Bearer dos clientes |
| `ADMIN_API_KEY` | cai nas chaves do gateway | Chave dos endpoints admin |
| `DEFAULT_MODEL` | `sonnet` | Modelo padrão |
| `MAX_CONCURRENCY` | `3` | Requests simultâneos ao Claude (1 subprocesso cada) |
| `REQUEST_TIMEOUT_SECONDS` | `300` | Teto por request |
| `AGENT_MODE_ENABLED` | `false` | Habilita o modo agente |
| `AGENT_ROOT` | `/workspace` | Raiz confinada do modo agente |

## Smoke test

```bash
BASE_URL=http://localhost:8000 API_KEY=sua-chave ./scripts/smoke_test.sh
```

## Limitações conhecidas

- Latência de ~5–15s por request (inicialização do CLI a cada chamada) — inerente
  ao design do Claude Code; use streaming para TTFB melhor.
- `completion_tokens` inclui tokens internos do Claude Code (system/thinking),
  não apenas o texto visível.
- Sem suporte a `tool_calls`/`function_calling` do lado do cliente — as ferramentas
  executam dentro do Claude Code (modo agente).
- Uma resposta por request (`n=1`); `logprobs` sempre `null`.

Guia completo de uso via `curl`, endpoint por endpoint: [docs.md](docs.md).
