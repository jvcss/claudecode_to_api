"""Roteamento entre providers — o que protege quem já usa o gateway."""
from app import codex_catalog, providers
from app.config import Settings
from app.model_registry import is_known, model_ids, model_object, owner_of, parse_model


def _catalog(*ids: str, enabled: bool = True) -> None:
    """Popula o catálogo em memória (persist=False não toca o disco)."""
    codex_catalog.configure(Settings(data_dir="/tmp/gwtest_codex", codex_enabled=enabled))
    codex_catalog._set([{"id": i} for i in ids], persist=False)


# --- ANTI-REGRESSÃO: gpt-* desconhecido continua caindo no modelo padrão ---
# Clientes que hardcodam gpt-4o funcionam hoje porque o modelo é desconhecido.
# Uma allowlist por regex `gpt-*` os roteria ao Codex, que rejeita o id.
def test_gpt4o_continua_no_provider_padrao():
    _catalog("gpt-5.6-sol", "gpt-6-astra")
    assert owner_of("gpt-4o") == "anthropic"
    assert is_known("gpt-4o") is False
    assert parse_model("gpt-4o", "sonnet", strict=False) == ("sonnet", False)


def test_modelo_do_catalogo_vai_para_openai():
    _catalog("gpt-5.6-sol")
    assert owner_of("gpt-5.6-sol") == "openai"
    assert is_known("gpt-5.6-sol") is True
    assert parse_model("gpt-5.6-sol", "sonnet", strict=False) == ("gpt-5.6-sol", False)


def test_catalogo_desligado_nao_registra_modelos():
    _catalog("gpt-5.6-sol", enabled=False)
    assert codex_catalog.ids() == frozenset()
    assert owner_of("gpt-5.6-sol") == "anthropic"
    assert "gpt-5.6-sol" not in model_ids(False)


def test_model_ids_lista_os_dois_providers():
    _catalog("gpt-5.6-sol")
    ids = model_ids(False)
    assert "sonnet" in ids and "gpt-5.6-sol" in ids


def test_sufixo_agent_so_para_aliases_do_claude():
    # Anunciar gpt-*-agent em /v1/models daria 403 na primeira chamada.
    _catalog("gpt-5.6-sol")
    ids = model_ids(True)
    assert "sonnet-agent" in ids
    assert "gpt-5.6-sol-agent" not in ids


def test_owned_by_segue_o_provider():
    _catalog("gpt-5.6-sol")
    assert model_object("sonnet")["owned_by"] == "anthropic"
    assert model_object("gpt-5.6-sol")["owned_by"] == "openai"
    assert model_object("sonnet-agent")["owned_by"] == "anthropic"


def test_sufixo_agent_em_modelo_codex_resolve_o_base():
    # parse_model devolve agent=True; quem recusa é build_options, com 403.
    _catalog("gpt-5.6-sol")
    assert parse_model("gpt-5.6-sol-agent", "sonnet", strict=False) == ("gpt-5.6-sol", True)


def test_runner_for_escolhe_o_modulo_certo():
    _catalog("gpt-5.6-sol")
    from app import claude_runner, codex_runner

    assert providers.runner_for("sonnet") is claude_runner
    assert providers.runner_for("gpt-5.6-sol") is codex_runner
    assert providers.runner_for("gpt-4o") is claude_runner


def test_semaforos_sao_independentes_por_provider():
    # Um Claude saturado não deve segurar um request do Codex.
    assert providers.get_semaphore("openai", 3) is not providers.get_semaphore("anthropic", 3)
    assert providers.get_semaphore("openai", 3) is providers.get_semaphore("openai", 3)
