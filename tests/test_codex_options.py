"""Opções do turno do Codex: modo chat, schema nativo e recusa do modo agente."""
import dataclasses

import pytest

from app.codex_runner import CHAT_BASE_PROMPT, CodexOptions, build_options
from app.config import Settings
from app.errors import GatewayError
from app.schemas import ClaudeOptionsExt, ResponseFormat


def _settings(**over):
    return Settings(data_dir="/tmp/gwtest_codex", codex_enabled=True, **over)


def test_chat_usa_sandbox_somente_leitura():
    from openai_codex import ApprovalMode, Sandbox

    opts = build_options("chat", "gpt-5.6-sol", None, None, _settings())
    assert opts.sandbox == Sandbox.read_only
    assert opts.approval_mode == ApprovalMode.deny_all
    assert opts.cwd.endswith("codex_cwd")


def test_system_do_cliente_vai_para_developer_instructions():
    opts = build_options("chat", "gpt-5.6-sol", "Seja formal.", None, _settings())
    assert CHAT_BASE_PROMPT in opts.developer_instructions
    assert "Seja formal." in opts.developer_instructions


# --- trava a decisão: NUNCA base_instructions ---
# base_instructions pode virar o campo `instructions` da Responses API, que é
# validado server-side e responde 400 "Instructions are not valid" para prompt
# arbitrário. developer_instructions é sempre um item dentro de `input`.
def test_nunca_usa_base_instructions():
    campos = {f.name for f in dataclasses.fields(CodexOptions)}
    assert "developer_instructions" in campos
    assert "base_instructions" not in campos


# --- modo agente falha ruidosamente, não degrada para chat ---
def test_modo_agente_e_recusado():
    with pytest.raises(GatewayError) as exc:
        build_options("agent", "gpt-5.6-sol", None, None, _settings())
    assert exc.value.status_code == 403
    assert exc.value.code == "agent_mode_unsupported_for_provider"


def test_modo_agente_recusado_tambem_via_claude_options():
    co = ClaudeOptionsExt(mode="agent")
    with pytest.raises(GatewayError):
        build_options("agent", "gpt-5.6-sol", None, co, _settings())


# --- response_format nativo em vez de pedir JSON por prompt ---
def test_json_schema_vai_para_output_schema():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    opts = build_options(
        "chat", "gpt-5.6-sol", None, None, _settings(),
        response_format=ResponseFormat(type="json_schema", json_schema=schema),
    )
    assert opts.output_schema["properties"] == schema["properties"]


# O upstream exige additionalProperties:false em todo objeto e required com
# TODAS as propriedades; senão responde 400 invalid_json_schema.
def test_schema_e_normalizado_para_o_modo_estrito():
    schema = {
        "type": "object",
        "properties": {
            "capital": {"type": "string"},
            "dados": {"type": "object", "properties": {"pop": {"type": "integer"}}},
        },
        "required": ["capital"],
    }
    out = build_options(
        "chat", "gpt-5.6-sol", None, None, _settings(),
        response_format=ResponseFormat(type="json_schema", json_schema=schema),
    ).output_schema
    assert out["additionalProperties"] is False
    assert sorted(out["required"]) == ["capital", "dados"]
    # a normalização é recursiva
    assert out["properties"]["dados"]["additionalProperties"] is False
    assert out["properties"]["dados"]["required"] == ["pop"]


# O formato oficial da OpenAI é o invólucro {name, strict, schema}; muitos
# clientes mandam o schema cru. Os dois precisam funcionar.
def test_aceita_o_involucro_oficial_e_o_schema_cru():
    interno = {"type": "object", "properties": {"n": {"type": "integer"}}}
    com_involucro = build_options(
        "chat", "gpt-5.6-sol", None, None, _settings(),
        response_format=ResponseFormat(
            type="json_schema",
            json_schema={"name": "resposta", "strict": True, "schema": interno},
        ),
    ).output_schema
    cru = build_options(
        "chat", "gpt-5.6-sol", None, None, _settings(),
        response_format=ResponseFormat(type="json_schema", json_schema=interno),
    ).output_schema
    assert com_involucro == cru
    assert "name" not in com_involucro


def test_json_object_sem_schema_cai_no_minimo():
    opts = build_options(
        "chat", "gpt-5.6-sol", None, None, _settings(),
        response_format=ResponseFormat(type="json_object"),
    )
    assert opts.output_schema["additionalProperties"] is False


def test_response_format_texto_nao_gera_schema():
    opts = build_options(
        "chat", "gpt-5.6-sol", None, None, _settings(),
        response_format=ResponseFormat(type="text"),
    )
    assert opts.output_schema is None


# --- reasoning effort ---
def test_effort_invalido_cai_no_padrao_do_modelo():
    from openai_codex.types import ReasoningEffort

    assert build_options(
        "chat", "gpt-5.6-sol", None, None, _settings(codex_reasoning_effort="high")
    ).effort == ReasoningEffort.high
    assert build_options(
        "chat", "gpt-5.6-sol", None, None, _settings(codex_reasoning_effort="turbo")
    ).effort is None
