"""Conversão dos eventos normalizados do runner para o wire format da OpenAI."""
import asyncio
import contextlib
import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

from .config import GATEWAY_VERSION
from .errors import GatewayError, error_body

logger = logging.getLogger("gateway.stream")

HEARTBEAT_SECONDS = 15
SYSTEM_FINGERPRINT = f"ccgw-{GATEWAY_VERSION}"


def new_completion_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex


def _chunk(
    completion_id: str,
    created: int,
    model: str,
    *,
    delta: dict[str, Any] | None = None,
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
    usage_field: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "system_fingerprint": SYSTEM_FINGERPRINT,
        "choices": [],
    }
    if delta is not None or finish_reason is not None:
        body["choices"] = [
            {"index": 0, "delta": delta or {}, "logprobs": None, "finish_reason": finish_reason}
        ]
    if usage_field:
        body["usage"] = usage
    return body


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


async def sse_stream(
    events: AsyncIterator[tuple],
    model: str,
    include_usage: bool,
    emit_tool_activity: bool,
) -> AsyncIterator[str]:
    completion_id = new_completion_id()
    created = int(time.time())
    resolved_model = model

    # Chunk de role imediato para TTFB rápido.
    yield _sse(
        _chunk(
            completion_id,
            created,
            resolved_model,
            delta={"role": "assistant", "content": ""},
            usage_field=include_usage,
        )
    )

    finish_reason = "stop"
    usage: dict[str, Any] | None = None
    pending: asyncio.Task | None = None
    try:
        pending = asyncio.ensure_future(anext(events))  # type: ignore[arg-type]
        while True:
            try:
                event = await asyncio.wait_for(asyncio.shield(pending), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                # Sem eventos há 15s (ferramenta longa): keep-alive SSE.
                yield ": ping\n\n"
                continue
            except StopAsyncIteration:
                break
            pending = asyncio.ensure_future(anext(events))  # type: ignore[arg-type]

            kind = event[0]
            if kind == "text":
                yield _sse(
                    _chunk(
                        completion_id,
                        created,
                        resolved_model,
                        delta={"content": event[1]},
                        usage_field=include_usage,
                    )
                )
            elif kind == "tool":
                if emit_tool_activity:
                    line = f"\n> [tool: {event[1]}] {event[2]}\n"
                    yield _sse(
                        _chunk(
                            completion_id,
                            created,
                            resolved_model,
                            delta={"content": line},
                            usage_field=include_usage,
                        )
                    )
            elif kind == "done":
                finish_reason = event[1]
                usage = event[2]
                resolved_model = event[3] or resolved_model
    except GatewayError as exc:
        # Convenção da OpenAI: erro no meio do stream é emitido SEM um chunk de
        # finish_reason:"stop" antes (senão o cliente trata como sucesso).
        yield _sse(error_body(exc.message, exc.err_type, exc.code, exc.param))
        yield "data: [DONE]\n\n"
        return
    except Exception:
        logger.exception("Erro inesperado durante o stream")
        yield _sse(error_body("Internal gateway error during streaming.", "server_error", "internal_error"))
        yield "data: [DONE]\n\n"
        return
    finally:
        # Desconexão do cliente (GeneratorExit) ou qualquer saída: cancela e
        # DRENA a task pendente (mesmo se já concluída — senão uma exceção não
        # recuperada vira log "Task exception was never retrieved") e fecha o
        # runner, cujo aclose() mata o subprocesso.
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(BaseException):
                await pending
        with contextlib.suppress(BaseException):
            await events.aclose()  # type: ignore[attr-defined]

    if finish_reason == "error":
        yield _sse(error_body("Upstream run ended with an error.", "server_error", "upstream_execution_error"))
        yield "data: [DONE]\n\n"
        return

    yield _sse(
        _chunk(
            completion_id,
            created,
            resolved_model,
            finish_reason=finish_reason,
            usage_field=include_usage,
        )
    )
    if include_usage:
        yield _sse(_chunk(completion_id, created, resolved_model, usage=usage, usage_field=True))
    yield "data: [DONE]\n\n"


async def collect_completion(
    events: AsyncIterator[tuple],
    model: str,
    emit_tool_activity: bool,
) -> dict[str, Any]:
    completion_id = new_completion_id()
    created = int(time.time())
    resolved_model = model
    parts: list[str] = []
    finish_reason = "stop"
    usage: dict[str, Any] | None = None

    async for event in events:
        kind = event[0]
        if kind == "text":
            parts.append(event[1])
        elif kind == "tool" and emit_tool_activity:
            parts.append(f"\n> [tool: {event[1]}] {event[2]}\n")
        elif kind == "done":
            finish_reason = event[1]
            usage = event[2]
            resolved_model = event[3] or resolved_model

    if finish_reason == "error":
        raise GatewayError(500, "Upstream run ended with an error.", "server_error", "upstream_execution_error")

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": resolved_model,
        "system_fingerprint": SYSTEM_FINGERPRINT,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "".join(parts), "refusal": None},
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage
        or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
