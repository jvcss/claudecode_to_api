"""Testes de regressão dos achados do review multi-agente."""
import pytest

from app.claude_runner import _map_finish, classify_run_error
from app.errors import GatewayError
from app.model_registry import parse_model


# --- P/#20: sufixo -agent em modelo desconhecido não habilita agente ---
def test_unknown_agent_model_does_not_enable_agent():
    model, agent = parse_model("gpt-4o-agent", "sonnet", strict=False)
    assert model == "sonnet"
    assert agent is False


def test_known_agent_suffix_enables_agent():
    model, agent = parse_model("sonnet-agent", "sonnet", strict=False)
    assert model == "sonnet"
    assert agent is True


def test_strict_unknown_model_raises():
    with pytest.raises(GatewayError) as exc:
        parse_model("gpt-4o", "sonnet", strict=True)
    assert exc.value.status_code == 404


# --- K/#17: falha de API (subtype success + api_error_status) vira erro certo ---
def test_classify_auth_error_is_502_not_401():
    err = classify_run_error("Failed to authenticate. API Error: 401 Invalid bearer token", 401)
    assert err.status_code == 502
    assert err.code == "upstream_not_authenticated"


def test_classify_rate_limit_by_status():
    err = classify_run_error("something", 529)
    assert err.status_code == 429
    assert err.headers.get("Retry-After") == "30"


def test_classify_generic_error_500():
    err = classify_run_error("boom", None)
    assert err.status_code == 500


# --- L/#18: max_budget degrada como length (igual max_turns) ---
def test_map_finish_budget_is_length():
    assert _map_finish("error_max_budget_usd") == "length"
    assert _map_finish("error_max_turns") == "length"
    assert _map_finish("success") == "stop"
    assert _map_finish("error_during_execution") == "error"


# --- D/#15: clamp do budget do cliente ao teto do servidor ---
def test_budget_clamped_to_server_cap():
    from app.config import Settings
    from app.schemas import ClaudeOptionsExt
    from app.claude_runner import build_options

    settings = Settings(agent_mode_enabled=True, max_budget_usd_per_request=1.0,
                        agent_root="/tmp", data_dir="/tmp/gwtest_data")
    co = ClaudeOptionsExt(mode="agent", max_budget_usd=999.0, cwd="/tmp")
    opts = build_options("agent", "sonnet", None, co, settings)
    assert opts.max_budget_usd == 1.0  # cliente pediu 999, teto é 1.0


# --- J/#16: modo agente sem system message usa o preset claude_code ---
def test_agent_mode_uses_preset_without_system():
    from app.config import Settings
    from app.claude_runner import build_options

    settings = Settings(agent_mode_enabled=True, agent_root="/tmp", data_dir="/tmp/gwtest_data")
    opts = build_options("agent", "sonnet", None, None, settings)
    assert isinstance(opts.system_prompt, dict)
    assert opts.system_prompt.get("preset") == "claude_code"
    assert "append" not in opts.system_prompt
