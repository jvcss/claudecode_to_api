"""Execução de queries no Codex via openai-codex (assinatura ChatGPT).

Produz exatamente os mesmos eventos normalizados que claude_runner.py, que
openai_stream.py consome sem saber quem os produziu:
    ("text", str)                     — delta de texto
    ("tool", name, summary)           — atividade de ferramenta
    ("done", finish_reason, usage, model, cost) — fim do turno

O 5º campo (custo em USD) é sempre None: o Codex roda sobre assinatura e não
reporta custo por request. openai_stream.py nunca lê esse campo.
"""
import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

from openai_codex import (
    ApprovalMode,
    AsyncCodex,
    CodexConfig,
    CodexError,
    InvalidParamsError,
    InvalidRequestError,
    RetryLimitExceededError,
    Sandbox,
    ServerBusyError,
    TransportClosedError,
)
from openai_codex.types import ReasoningEffort

from . import providers
from .config import Settings
from .errors import GatewayError
from .schemas import ClaudeOptionsExt, ResponseFormat

logger = logging.getLogger("gateway.codex")

# O gateway monta o schema JSON direto no turno em vez de pedir JSON por prompt.
NATIVE_JSON_SCHEMA = True

CHAT_BASE_PROMPT = (
    "You are a helpful assistant. Answer directly in plain conversation. "
    "Do not use tools, do not read or write files, and do not run commands."
)

GwEvent = tuple[Any, ...]

# Comparação por VALOR de string, não pelo enum: MessagePhase e
# CodexErrorInfoValue não são reexportados por `openai_codex.types` (só vivem em
# `generated.v2_all`, que é interno). Os valores fazem parte do protocolo e são
# estáveis; o caminho de import do pacote não é.
_PHASE_COMMENTARY = "commentary"
_PHASE_FINAL_ANSWER = "final_answer"

_TOOL_ITEM_TYPES = frozenset(
    {"commandExecution", "fileChange", "mcpToolCall", "webSearch", "dynamicToolCall"}
)

_client: AsyncCodex | None = None
_client_lock = asyncio.Lock()
_cleanup_tasks: set[asyncio.Task] = set()


def _value(enum_or_str: Any) -> Any:
    """Valor de um enum do SDK, ou o próprio objeto se já for primitivo."""
    return getattr(enum_or_str, "value", enum_or_str)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


async def get_client(settings: Settings) -> AsyncCodex:
    """Client único e longevo.

    Um app-server do Codex multiplexa vários turns por turn_id, então subir um
    processo de ~120 MB por request seria absurdo. Criado sob demanda (e não no
    lifespan) para que o gateway suba sem credencial do Codex e para que quem
    só usa Claude nunca pague o custo do processo.
    """
    global _client
    async with _client_lock:
        if _client is None:
            settings.codex_home.mkdir(parents=True, exist_ok=True)
            settings.codex_chat_cwd.mkdir(parents=True, exist_ok=True)
            client = AsyncCodex(
                config=CodexConfig(
                    # CODEX_HOME próprio: o refresh token é rotativo e de uso
                    # único, então compartilhar o diretório com o ~/.codex do
                    # host invalidaria os dois.
                    env={"CODEX_HOME": str(settings.codex_home)},
                    client_name="claudecode_to_api",
                    client_title="OpenAI-compatible gateway",
                )
            )
            await client.__aenter__()
            _client = client
    return _client


async def reset_client() -> None:
    """Derruba o app-server. Necessário quando a credencial muda: o auth.json
    é lido na inicialização do processo."""
    global _client
    async with _client_lock:
        client, _client = _client, None
    if client is not None:
        with contextlib.suppress(BaseException):
            await client.__aexit__(None, None, None)


# --------------------------------------------------------------------------- #
# Options
# --------------------------------------------------------------------------- #


@dataclass
class CodexOptions:
    model: str
    developer_instructions: str | None
    cwd: str
    sandbox: Sandbox
    approval_mode: ApprovalMode
    effort: ReasoningEffort | None
    output_schema: dict[str, Any] | None


def _resolve_effort(settings: Settings) -> ReasoningEffort | None:
    raw = (settings.codex_reasoning_effort or "").strip()
    if not raw:
        return None
    try:
        return ReasoningEffort(raw)
    except ValueError:
        logger.warning(
            "CODEX_REASONING_EFFORT=%r inválido; use um de %s. Usando o padrão do modelo.",
            raw,
            [e.value for e in ReasoningEffort],
        )
        return None


def build_options(
    mode: str,
    model: str,
    system_text: str | None,
    co: ClaudeOptionsExt | None,
    settings: Settings,
    response_format: ResponseFormat | None = None,
) -> CodexOptions:
    if mode != "chat":
        # Falha ruidosa em vez de degradar em silêncio para chat. O SDK não
        # permite negar aprovações — ApprovalMode.deny_all significa "não
        # perguntar", não "negar", e o handler padrão aceita tudo —, então
        # habilitar ferramentas aqui seria aceitação cega de escrita em disco.
        raise GatewayError(
            403,
            "Agent mode is not supported for OpenAI/Codex models on this gateway. "
            "Use a Claude model for agent mode.",
            "invalid_request_error",
            "agent_mode_unsupported_for_provider",
            param="model",
        )

    schema: dict[str, Any] | None = None
    if response_format is not None and response_format.type == "json_schema":
        schema = response_format.json_schema
    elif response_format is not None and response_format.type == "json_object":
        # Sem schema declarado: o Codex exige um, então cai no mínimo aceitável.
        schema = {"type": "object"}

    instructions = CHAT_BASE_PROMPT + (f"\n\n{system_text}" if system_text else "")
    return CodexOptions(
        model=model,
        # developer_instructions, NUNCA base_instructions: o primeiro é sempre
        # um item `developer` dentro de `input`, enquanto o segundo pode virar
        # o campo `instructions` da Responses API, que é validado server-side e
        # responde 400 "Instructions are not valid" para prompt arbitrário.
        developer_instructions=instructions,
        cwd=str(settings.codex_chat_cwd),
        sandbox=Sandbox.read_only,
        approval_mode=ApprovalMode.deny_all,
        effort=_resolve_effort(settings),
        output_schema=schema,
    )


# --------------------------------------------------------------------------- #
# Mapeamentos
# --------------------------------------------------------------------------- #


def _map_finish(status: Any) -> str:
    value = _value(status)
    if value == "completed":
        return "stop"
    if value == "interrupted":
        # Interrupção é sempre nossa (timeout ou desconexão do cliente); o
        # texto já emitido é válido.
        return "stop"
    return "error"


def _map_usage(usage: Any) -> dict[str, Any]:
    """ThreadTokenUsage -> bloco `usage` da OpenAI.

    Usa `total_tokens` do próprio payload em vez de somar prompt+completion: a
    convenção do Codex sobre o que já está incluído em input_tokens é o oposto
    da Anthropic (onde claude_runner._map_usage SOMA os campos de cache), e
    somar aqui inventaria um número.
    """
    breakdown = getattr(usage, "last", None) if usage is not None else None
    if breakdown is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "prompt_tokens": breakdown.input_tokens,
        "completion_tokens": breakdown.output_tokens,
        "total_tokens": breakdown.total_tokens,
        "prompt_tokens_details": {"cached_tokens": breakdown.cached_input_tokens},
        "completion_tokens_details": {"reasoning_tokens": breakdown.reasoning_output_tokens},
    }


_RATE_LIMIT_INFO = frozenset({"usageLimitExceeded", "serverOverloaded"})
_AUTH_INFO = frozenset({"unauthorized"})
_CLIENT_FAULT_INFO = {
    "contextWindowExceeded": ("context_length_exceeded", "messages"),
    "badRequest": ("invalid_request", None),
}


def _error_info_value(err: Any) -> str | None:
    info = getattr(err, "codex_error_info", None)
    root = getattr(info, "root", None) if info is not None else None
    if root is None:
        return None
    value = _value(root)
    return value if isinstance(value, str) else None


def _http_status_from_info(err: Any) -> int | None:
    """As variantes de falha de conexão carregam o status HTTP num campo aninhado."""
    info = getattr(err, "codex_error_info", None)
    root = getattr(info, "root", None) if info is not None else None
    if root is None or isinstance(_value(root), str):
        return None
    for field in getattr(root, "model_fields", {}):
        nested = getattr(root, field, None)
        status = getattr(nested, "http_status_code", None)
        if status is not None:
            return int(status)
    return None


def classify_run_error(err: Any) -> GatewayError:
    """TurnError -> GatewayError.

    Falha de credencial do UPSTREAM vira 502, nunca 401: a chave do cliente já
    passou; um 401 faria o cliente achar que a chave dele está errada. Mesma
    doutrina de claude_runner.classify_run_error.
    """
    message = str(getattr(err, "message", err) or "codex turn failed")
    detail = message[:300]
    info = _error_info_value(err)
    status = _http_status_from_info(err)

    if info in _RATE_LIMIT_INFO or status in (429, 529):
        return GatewayError(
            429,
            f"Upstream Codex is rate limited or overloaded: {detail}",
            "rate_limit_error",
            "upstream_rate_limited",
            headers={"Retry-After": "30"},
        )
    if info in _AUTH_INFO or status in (401, 403):
        return GatewayError(
            502,
            "The gateway's upstream Codex credential is invalid or expired. "
            "The operator must re-authenticate via POST /codex/auth/device-code.",
            "upstream_error",
            "upstream_not_authenticated",
        )
    if info in _CLIENT_FAULT_INFO:
        code, param = _CLIENT_FAULT_INFO[info]
        return GatewayError(400, detail, "invalid_request_error", code, param=param)
    return GatewayError(
        500, f"Codex run failed: {message[:500]}", "server_error", "codex_execution_error"
    )


def classify_sdk_error(exc: BaseException) -> GatewayError:
    """Exceções do próprio SDK (transporte/RPC), fora do fluxo de TurnError."""
    if isinstance(exc, (ServerBusyError, RetryLimitExceededError)):
        return GatewayError(
            429,
            f"Upstream Codex is busy: {str(exc)[:300]}",
            "rate_limit_error",
            "upstream_rate_limited",
            headers={"Retry-After": "30"},
        )
    if isinstance(exc, TransportClosedError):
        return GatewayError(
            502,
            "The Codex app-server connection dropped.",
            "upstream_error",
            "codex_transport_closed",
        )
    if isinstance(exc, (InvalidParamsError, InvalidRequestError)):
        return GatewayError(400, str(exc)[:300], "invalid_request_error", "invalid_request")
    if isinstance(exc, FileNotFoundError):
        return GatewayError(
            500,
            "The bundled Codex CLI was not found. Reinstall openai-codex.",
            "server_error",
            "codex_runtime_missing",
        )
    return GatewayError(
        500, f"Codex run failed: {str(exc)[:500]}", "server_error", "codex_execution_error"
    )


def _summarize_item(item: Any, limit: int = 120) -> str:
    for attr in ("command", "query", "tool", "path"):
        value = getattr(item, attr, None)
        if value:
            text = str(value)
            return text if len(text) <= limit else text[: limit - 1] + "…"
    changes = getattr(item, "changes", None)
    if changes:
        text = ", ".join(str(getattr(c, "path", c)) for c in changes)
        return text if len(text) <= limit else text[: limit - 1] + "…"
    text = json.dumps(getattr(item, "id", ""), ensure_ascii=False, default=str)
    return text[:limit]


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #


def _detach_cleanup(turn: Any, stream: Any, step: "asyncio.Task | None") -> None:
    """Encerra o turno em background.

    Cancelar o `to_thread` do SDK NÃO solta a worker thread: ela está parada num
    queue.Queue.get() sem timeout, e só a CHEGADA de um notification a libera.
    turn.interrupt() provoca esse notification. Sem isso, cada timeout ou
    desconexão de cliente vazaria uma worker do executor permanentemente.
    """

    async def _cleanup() -> None:
        with contextlib.suppress(BaseException):
            await turn.interrupt()
        if step is not None:
            step.cancel()
            with contextlib.suppress(BaseException):
                await step
        await _aclose_quietly(stream)

    task = asyncio.ensure_future(_cleanup())
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_tasks.discard)


async def _aclose_quietly(stream: Any) -> None:
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(aclose(), timeout=10)


async def run_events(
    prompt: str,
    options: CodexOptions,
    settings: Settings,
    timeout_seconds: int | None = None,
) -> AsyncIterator[GwEvent]:
    """Roda o turno e emite os eventos normalizados."""
    timeout = min(
        timeout_seconds or settings.request_timeout_seconds, settings.request_timeout_seconds
    )
    deadline = time.monotonic() + timeout

    def _timeout() -> GatewayError:
        return GatewayError(
            504, f"Upstream Codex run exceeded {timeout}s.", "timeout", "request_timeout"
        )

    def _remaining() -> float:
        left = deadline - time.monotonic()
        if left <= 0:
            raise _timeout()
        return left

    phases: dict[str, Any] = {}
    usage: Any = None
    resolved_model = options.model
    emitted_text = False
    tool_since_text = False
    final_text: str | None = None
    got_result = False
    completed = False

    semaphore = providers.get_semaphore("openai", settings.codex_max_concurrency)
    async with semaphore:
        client = await get_client(settings)
        try:
            thread = await asyncio.wait_for(
                client.thread_start(
                    model=options.model,
                    cwd=options.cwd,
                    sandbox=options.sandbox,
                    approval_mode=options.approval_mode,
                    developer_instructions=options.developer_instructions,
                    # ephemeral: sem isso o rollout de cada request ficaria em
                    # $CODEX_HOME/sessions e o volume /data cresceria sem limite.
                    ephemeral=True,
                ),
                timeout=_remaining(),
            )
            turn = await asyncio.wait_for(
                thread.turn(prompt, effort=options.effort, output_schema=options.output_schema),
                timeout=_remaining(),
            )
        except asyncio.TimeoutError:
            raise _timeout() from None
        except GatewayError:
            raise
        except BaseException as exc:  # noqa: BLE001
            raise classify_sdk_error(exc) from exc

        stream = turn.stream()
        iterator = stream.__aiter__()
        step: asyncio.Task | None = None
        try:
            while True:
                remaining = _remaining()
                # asyncio.wait (não wait_for): respeita o deadline SEM esperar o
                # __anext__ responder ao cancelamento. Mesma razão documentada
                # em claude_runner.run_events.
                step = asyncio.ensure_future(iterator.__anext__())
                done, _ = await asyncio.wait({step}, timeout=remaining)
                if not done:
                    raise _timeout()
                try:
                    event = step.result()
                except StopAsyncIteration:
                    completed = True
                    break

                method = event.method
                payload = event.payload

                if method == "item/started":
                    item = payload.item.root
                    if getattr(item, "type", None) == "agentMessage":
                        phases[item.id] = _value(getattr(item, "phase", None))

                elif method == "item/agentMessage/delta":
                    # Os modelos novos emitem comentário intermediário TAMBÉM
                    # como agentMessage, e o delta não carrega a fase — só o
                    # item_id. Sem este filtro, comentário e resposta final se
                    # concatenam no chat.
                    if phases.get(payload.item_id) == _PHASE_COMMENTARY:
                        continue
                    text = payload.delta
                    if not text:
                        continue
                    if emitted_text and tool_since_text:
                        text = "\n\n" + text
                    emitted_text = True
                    tool_since_text = False
                    yield ("text", text)

                elif method == "item/completed":
                    item = payload.item.root
                    item_type = getattr(item, "type", None)
                    if item_type == "agentMessage":
                        phase = _value(getattr(item, "phase", None))
                        phases[item.id] = phase
                        if phase != _PHASE_COMMENTARY:
                            final_text = item.text
                    elif item_type in _TOOL_ITEM_TYPES:
                        tool_since_text = True
                        yield ("tool", item_type, _summarize_item(item))

                elif method == "thread/tokenUsage/updated":
                    usage = payload.token_usage

                elif method == "error":
                    if not payload.will_retry:
                        raise classify_run_error(payload.error)
                    logger.warning("Codex vai retentar: %s", payload.error.message)

                elif method == "turn/completed":
                    turn_obj = payload.turn
                    got_result = True
                    completed = True
                    if _value(turn_obj.status) == "failed":
                        raise classify_run_error(turn_obj.error)
                    # Fallback: nenhum delta chegou mas há texto no resultado.
                    if not emitted_text and final_text:
                        emitted_text = True
                        yield ("text", final_text)
                    yield (
                        "done",
                        _map_finish(turn_obj.status),
                        _map_usage(usage),
                        resolved_model,
                        None,
                    )
                    break
        finally:
            if step is not None and not step.done():
                _detach_cleanup(turn, iterator, step)
            else:
                if step is not None:
                    # Consome a exceção guardada (evita "Task exception was
                    # never retrieved").
                    with contextlib.suppress(BaseException):
                        step.result()
                if completed:
                    await _aclose_quietly(iterator)
                else:
                    _detach_cleanup(turn, iterator, None)

    if not got_result:
        raise GatewayError(
            502, "Codex run ended without a result.", "server_error", "codex_protocol_error"
        )
