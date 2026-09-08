"""POST /v1/chat/completions — o coração do gateway."""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import providers
from ..config import get_settings
from ..errors import GatewayError
from ..model_registry import AGENT_SUFFIX, ConfigStore, parse_model
from ..openai_stream import collect_completion, sse_stream
from ..prompting import build_prompt
from ..schemas import ChatCompletionRequest
from ..security import require_gateway_key

logger = logging.getLogger("gateway.chat")

router = APIRouter(dependencies=[Depends(require_gateway_key)])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

JSON_ONLY_INSTRUCTION = (
    "You must respond with a single valid JSON value and nothing else. "
    "Do not wrap it in markdown code fences and do not add any prose before or after the JSON."
)


def _reject_unsupported(body: ChatCompletionRequest) -> None:
    """Rejeita explicitamente o que o gateway não consegue cumprir, em vez de
    ignorar em silêncio (o que faria o cliente travar/quebrar)."""
    if body.n is not None and body.n > 1:
        raise GatewayError(
            400,
            "This gateway returns a single choice; `n` > 1 is not supported.",
            "invalid_request_error",
            "unsupported_parameter",
            param="n",
        )
    # Function/tool calling do lado do cliente não é suportado — as ferramentas
    # rodam server-side dentro do Claude Code (modo agente). Rejeita para o
    # cliente não ficar esperando um `tool_calls` que nunca vem.
    tool_choice_active = body.tool_choice not in (None, "none", "auto")
    if body.functions or (body.tools and tool_choice_active) or tool_choice_active:
        raise GatewayError(
            400,
            "Client-side function/tool calling is not supported by this gateway. "
            "Use claude_options.mode='agent' to let Claude Code run tools server-side.",
            "invalid_request_error",
            "tools_not_supported",
            param="tools",
        )


def _system_with_response_format(system_text: str | None, body: ChatCompletionRequest) -> str | None:
    rf = body.response_format
    if not rf or rf.type == "text":
        return system_text
    instruction = JSON_ONLY_INSTRUCTION
    if rf.type == "json_schema" and rf.json_schema:
        import json as _json

        instruction += "\nThe JSON must strictly conform to this JSON Schema:\n" + _json.dumps(rf.json_schema)
    return f"{system_text}\n\n{instruction}" if system_text else instruction


@router.post("/v1/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request):
    settings = get_settings()
    config = ConfigStore(settings.config_path, settings.default_model)

    _reject_unsupported(body)

    model, agent_suffix = parse_model(body.model, config.default_model, settings.model_strict)

    co = body.claude_options
    if co and co.mode:
        mode = co.mode
    elif agent_suffix:
        mode = "agent"
    else:
        mode = "chat"

    system_text, prompt = build_prompt(body.messages)
    runner = providers.runner_for(model)
    if getattr(runner, "NATIVE_JSON_SCHEMA", False):
        # O provider aceita o schema no próprio turno; pedir JSON por prompt
        # seria menos confiável.
        options = runner.build_options(
            mode, model, system_text, co, settings, response_format=body.response_format
        )
    else:
        system_text = _system_with_response_format(system_text, body)
        options = runner.build_options(mode, model, system_text, co, settings)

    if body.user:
        logger.info("chat_completions user=%s mode=%s model=%s", body.user, mode, model)

    timeout = co.timeout_seconds if co else None
    events = runner.run_events(prompt, options, settings, timeout)
    emit_tools = bool(co and co.emit_tool_activity)

    if body.stream:
        include_usage = bool(body.stream_options and body.stream_options.include_usage)
        return StreamingResponse(
            sse_stream(events, model, include_usage, emit_tools),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    try:
        completion = await collect_completion(events, model, emit_tools)
    finally:
        await events.aclose()
    return JSONResponse(completion)
