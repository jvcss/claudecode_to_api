"""Execução de queries no Claude Code via claude-agent-sdk.

Produz eventos normalizados consumidos por openai_stream.py:
    ("text", str)                     — delta de texto
    ("tool", name, summary)           — uso de ferramenta (modo agente)
    ("done", finish_reason, usage, model, cost) — fim do turno
"""
import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    query,
)
from claude_agent_sdk.types import StreamEvent, ToolUseBlock

from . import providers
from .config import Settings
from .credentials import CredentialStore
from .errors import GatewayError, not_authenticated
from .schemas import ClaudeOptionsExt

logger = logging.getLogger("gateway.runner")

CHAT_BASE_PROMPT = (
    "You are a helpful assistant. Answer directly in plain conversation. "
    "You have no tools, no filesystem access, and no ability to run commands."
)

GwEvent = tuple[Any, ...]

_cleanup_tasks: set[asyncio.Task] = set()


def _detach_cleanup(step: "asyncio.Task | None", agen: Any) -> None:
    """Encerra em background um gerador com leitura em voo.

    Cancelar o __anext__ do SDK e esperá-lo é lento (o CLI demora a responder ao
    cancel); fazer isso em foreground travaria a resposta. A task destacada mata
    o subprocesso sem bloquear o cliente.
    """
    async def _cleanup() -> None:
        try:
            if step is not None:
                step.cancel()
                with contextlib.suppress(BaseException):
                    await step
        finally:
            with contextlib.suppress(BaseException):
                await agen.aclose()

    task = asyncio.ensure_future(_cleanup())
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_tasks.discard)


def resolve_agent_cwd(co: ClaudeOptionsExt | None, settings: Settings) -> str:
    root = settings.agent_root.resolve()
    requested = (co.cwd if co and co.cwd else None) or str(root)
    path = Path(requested)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    if not (resolved == root or root in resolved.parents):
        raise GatewayError(
            400,
            f"claude_options.cwd must be inside the agent root ({root}).",
            "invalid_request_error",
            "invalid_cwd",
            param="claude_options.cwd",
        )
    resolved.mkdir(parents=True, exist_ok=True)
    return str(resolved)


def build_options(
    mode: str,
    model: str,
    system_text: str | None,
    co: ClaudeOptionsExt | None,
    settings: Settings,
    creds_env: dict[str, str] | None = None,
) -> ClaudeAgentOptions:
    # A credencial é resolvida aqui, não no router: injetar env no subprocesso é
    # específico do Claude e não tem análogo nos outros providers. `creds_env`
    # continua aceito como override para /auth/validate, que precisa isolar o
    # subprocesso de um login pré-existente em ~/.claude.
    if creds_env is None:
        _, creds_env = CredentialStore(settings.credentials_path).resolve()
    common: dict[str, Any] = {
        "model": model,
        "include_partial_messages": True,
        "setting_sources": [],
        "env": creds_env,
    }
    if settings.claude_cli_path:
        common["cli_path"] = settings.claude_cli_path

    if mode == "chat":
        system = CHAT_BASE_PROMPT + (f"\n\n{system_text}" if system_text else "")
        settings.chat_cwd.mkdir(parents=True, exist_ok=True)
        return ClaudeAgentOptions(
            system_prompt=system,
            tools=[],
            max_turns=1,
            cwd=str(settings.chat_cwd),
            **common,
        )

    # Modo agente
    if not settings.agent_mode_enabled:
        raise GatewayError(
            403,
            "Agent mode is disabled on this gateway (set AGENT_MODE_ENABLED=true).",
            "invalid_request_error",
            "agent_mode_disabled",
        )
    max_turns = min(co.max_turns or settings.agent_max_turns, settings.agent_max_turns) if co else settings.agent_max_turns
    # Budget: cliente nunca ultrapassa o teto do operador (clamp, não override).
    client_budget = co.max_budget_usd if co else None
    server_budget = settings.max_budget_usd_per_request
    if client_budget is not None and server_budget is not None:
        budget = min(client_budget, server_budget)
    else:
        budget = client_budget if client_budget is not None else server_budget
    # Sem system message do cliente, ainda usa o preset do Claude Code (senão o
    # agente rodaria com system prompt vazio e perderia a competência de tools).
    preset: dict[str, Any] = {"type": "preset", "preset": "claude_code"}
    if system_text:
        preset["append"] = system_text
    system_prompt: Any = preset
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        tools={"type": "preset", "preset": "claude_code"},
        permission_mode=(co.permission_mode if co else None) or settings.agent_default_permission_mode,
        allowed_tools=(co.allowed_tools if co else None) or [],
        disallowed_tools=(co.disallowed_tools if co else None) or [],
        max_turns=max_turns,
        max_budget_usd=budget,
        cwd=resolve_agent_cwd(co, settings),
        **common,
    )


def _summarize_tool_input(tool_input: dict[str, Any], limit: int = 120) -> str:
    for key in ("command", "file_path", "path", "pattern", "url", "prompt", "description"):
        if key in tool_input:
            text = str(tool_input[key])
            return text if len(text) <= limit else text[: limit - 1] + "…"
    text = json.dumps(tool_input, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


_GRACEFUL_ERROR_SUBTYPES = ("error_max_turns", "error_max_budget_usd")


def _map_finish(subtype: str) -> str:
    if subtype == "success":
        return "stop"
    if subtype in _GRACEFUL_ERROR_SUBTYPES:
        return "length"
    return "error"


def _map_usage(usage: dict[str, Any] | None) -> dict[str, Any]:
    usage = usage or {}
    input_tokens = usage.get("input_tokens", 0) or 0
    cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    prompt_tokens = input_tokens + cache_creation + cache_read
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": prompt_tokens + output_tokens,
        "prompt_tokens_details": {"cached_tokens": cache_read},
    }


_AUTH_ERROR_MARKERS = ("api key", "oauth", "log in", "login", "authentication", "authenticate", "credential", "bearer token", "/login")
_RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "overloaded", "usage limit")


def classify_run_error(detail: str, api_error_status: int | None = None) -> GatewayError:
    """Mapeia uma falha de execução do Claude para um erro do gateway.

    Falha de credencial do UPSTREAM vira 502 (não 401): a chave do cliente já
    passou; o problema é do gateway, e um 401 faria o cliente achar que a chave
    dele está errada.
    """
    lowered = detail.lower()
    if api_error_status in (429, 529) or any(m in lowered for m in _RATE_LIMIT_MARKERS):
        return GatewayError(
            429,
            f"Upstream Claude is rate limited or overloaded: {detail[:300]}",
            "rate_limit_error",
            "upstream_rate_limited",
            headers={"Retry-After": "30"},
        )
    if api_error_status in (401, 403) or any(m in lowered for m in _AUTH_ERROR_MARKERS):
        return GatewayError(
            502,
            "The gateway's upstream Claude credential is invalid or expired. "
            "The operator must refresh it via POST /auth/token.",
            "upstream_error",
            "upstream_not_authenticated",
        )
    return GatewayError(500, f"Claude run failed: {detail[:500]}", "server_error", "claude_execution_error")


async def run_events(
    prompt: str,
    options: ClaudeAgentOptions,
    settings: Settings,
    timeout_seconds: int | None = None,
) -> AsyncIterator[GwEvent]:
    """Roda a query e emite eventos normalizados. Garante cleanup do subprocesso."""
    timeout = min(timeout_seconds or settings.request_timeout_seconds, settings.request_timeout_seconds)
    resolved_model = options.model or ""
    emitted_text = False
    tool_since_text = False
    result_text: str | None = None
    got_result = False

    # Deadline monotônico absoluto: robusto mesmo em streaming, onde o
    # padrão de read-ahead (cada anext numa task própria) faz o asyncio.timeout
    # tradicional não cancelar de forma confiável entre os yields.
    deadline = time.monotonic() + timeout

    def _timeout() -> GatewayError:
        return GatewayError(
            504, f"Upstream Claude run exceeded {timeout}s.", "timeout", "request_timeout"
        )

    semaphore = providers.get_semaphore("anthropic", settings.max_concurrency)
    async with semaphore:
        agen = query(prompt=prompt, options=options)
        sdk_iter = agen.__aiter__()
        step: asyncio.Task | None = None
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _timeout()
                # asyncio.wait (não wait_for): respeita o deadline SEM esperar o
                # __anext__ do SDK responder ao cancelamento — que é lento (~1
                # burst de geração). O finally chama aclose() e mata o subprocesso.
                step = asyncio.ensure_future(sdk_iter.__anext__())
                done, _ = await asyncio.wait({step}, timeout=remaining)
                if not done:
                    step.cancel()
                    raise _timeout()
                try:
                    msg = step.result()
                except StopAsyncIteration:
                    break

                if isinstance(msg, StreamEvent):
                    if msg.parent_tool_use_id is not None:
                        continue  # texto de subagentes não vai para o cliente
                    event = msg.event
                    if (
                        event.get("type") == "content_block_delta"
                        and event.get("delta", {}).get("type") == "text_delta"
                    ):
                        text = event["delta"].get("text", "")
                        if not text:
                            continue
                        if emitted_text and tool_since_text:
                            text = "\n\n" + text
                        emitted_text = True
                        tool_since_text = False
                        yield ("text", text)
                elif isinstance(msg, SystemMessage):
                    if msg.subtype == "init":
                        resolved_model = msg.data.get("model", resolved_model)
                elif isinstance(msg, AssistantMessage):
                    if msg.parent_tool_use_id is not None:
                        continue
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            tool_since_text = True
                            yield ("tool", block.name, _summarize_tool_input(block.input))
                    # TextBlocks são ignorados: já chegaram como deltas.
                elif isinstance(msg, ResultMessage):
                    got_result = True
                    result_text = msg.result
                    # Falha real da API: o CLI marca is_error=True; em falha de
                    # chamada de API o subtype vem "success" com api_error_status.
                    if msg.is_error and msg.subtype not in _GRACEFUL_ERROR_SUBTYPES:
                        detail = msg.result or msg.subtype
                        status = getattr(msg, "api_error_status", None)
                        raise classify_run_error(str(detail), status)
                    # Fallback: nenhum delta chegou mas há texto no resultado.
                    if not emitted_text and result_text and not msg.is_error:
                        emitted_text = True
                        yield ("text", result_text)
                    yield (
                        "done",
                        _map_finish(msg.subtype),
                        _map_usage(msg.usage),
                        resolved_model,
                        msg.total_cost_usd,
                    )
                    # ResultMessage é a última mensagem útil: encerra sem esperar
                    # o StopAsyncIteration (evita corrida timeout vs. sucesso).
                    break
        finally:
            if step is not None and not step.done():
                # __anext__ em voo (timeout/desconexão do cliente): cancelar +
                # aclose é lento; faz em background para não travar a resposta.
                _detach_cleanup(step, agen)
            else:
                # Caminho normal: consome a exceção guardada da última leitura
                # (evita "Task exception was never retrieved") e fecha o gerador,
                # que aqui está ocioso e encerra rápido.
                if step is not None:
                    with contextlib.suppress(BaseException):
                        step.result()
                with contextlib.suppress(BaseException):
                    await agen.aclose()

    if not got_result:
        raise GatewayError(502, "Claude run ended without a result.", "server_error", "cli_protocol_error")
