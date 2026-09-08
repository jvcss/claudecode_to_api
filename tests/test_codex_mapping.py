"""Tradução Codex → wire format da OpenAI: usage, finish_reason e erros."""
import pytest

from app.codex_runner import (
    _map_finish,
    _map_usage,
    _summarize_item,
    classify_run_error,
    classify_sdk_error,
)
from app.errors import GatewayError


def _usage(**over):
    from openai_codex.generated.v2_all import ThreadTokenUsage

    breakdown = {
        "inputTokens": 100,
        "cachedInputTokens": 80,
        "outputTokens": 20,
        "reasoningOutputTokens": 12,
        "totalTokens": 120,
    }
    breakdown.update(over)
    return ThreadTokenUsage.model_validate({"last": breakdown, "total": breakdown})


def _turn_error(info=None, message="boom"):
    from openai_codex.generated.v2_all import TurnError

    payload = {"message": message}
    if info is not None:
        payload["codexErrorInfo"] = info
    return TurnError.model_validate(payload)


# --- usage: total_tokens vem do payload, não de uma soma ---
# A convenção do Codex sobre o que já está incluído em input_tokens é oposta à
# da Anthropic (onde claude_runner._map_usage SOMA os campos de cache).
def test_usage_usa_total_do_payload():
    u = _map_usage(_usage())
    assert u["prompt_tokens"] == 100
    assert u["completion_tokens"] == 20
    assert u["total_tokens"] == 120
    assert u["prompt_tokens_details"]["cached_tokens"] == 80
    assert u["completion_tokens_details"]["reasoning_tokens"] == 12


def test_usage_ausente_vira_zeros():
    assert _map_usage(None) == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


# --- finish_reason ---
def test_map_finish():
    assert _map_finish("completed") == "stop"
    # Interrupção é sempre nossa (timeout/desconexão): o texto já emitido vale.
    assert _map_finish("interrupted") == "stop"
    assert _map_finish("failed") == "error"


# --- erros: credencial do upstream é 502, nunca 401 ---
# Espelha test_fixes.py::test_classify_auth_error_is_502_not_401: um 401 faria
# o cliente achar que a chave DELE está errada.
def test_credencial_do_upstream_vira_502():
    err = classify_run_error(_turn_error("unauthorized"))
    assert err.status_code == 502
    assert err.code == "upstream_not_authenticated"


def test_limite_de_uso_vira_429_com_retry_after():
    for info in ("usageLimitExceeded", "serverOverloaded"):
        err = classify_run_error(_turn_error(info))
        assert err.status_code == 429
        assert err.headers.get("Retry-After") == "30"


def test_contexto_estourado_e_culpa_do_cliente():
    err = classify_run_error(_turn_error("contextWindowExceeded"))
    assert err.status_code == 400
    assert err.code == "context_length_exceeded"
    assert err.param == "messages"


def test_erro_generico_vira_500():
    assert classify_run_error(_turn_error("other")).status_code == 500
    assert classify_run_error(_turn_error()).status_code == 500


def test_falha_de_conexao_usa_o_status_http_aninhado():
    err = classify_run_error(
        _turn_error({"httpConnectionFailed": {"httpStatusCode": 429}})
    )
    assert err.status_code == 429


# --- exceções do próprio SDK ---
def test_classify_sdk_error():
    from openai_codex import ServerBusyError, TransportClosedError

    assert classify_sdk_error(ServerBusyError(-32000, "busy")).status_code == 429
    dropped = classify_sdk_error(TransportClosedError("gone"))
    assert dropped.status_code == 502 and dropped.code == "codex_transport_closed"
    missing = classify_sdk_error(FileNotFoundError("codex"))
    assert missing.code == "codex_runtime_missing"


# --- resumo de atividade de ferramenta ---
def test_summarize_item_trunca():
    class Item:
        command = "x" * 300

    assert len(_summarize_item(Item())) == 120
