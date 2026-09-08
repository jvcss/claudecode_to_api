"""Catálogo de modelos do Codex: descoberto pelo SDK, cacheado em disco.

Descoberta dinâmica em vez de lista fixa no código: os ids do Codex são
aposentados rápido (gpt-5-codex e gpt-5.4 já saíram). O cache em disco existe
para que GET /v1/models responda sem subir o app-server e sem exigir
credencial — o endpoint é chamado por clientes antes de cada sessão e não pode
depender de rede.

Estado de módulo (e não um objeto injetado) porque `is_known`/`owner_of` são
chamados de lugares que não carregam `Settings` — ConfigStore, por exemplo.
Enquanto ninguém chamar `configure()`, o catálogo é vazio e o gateway se
comporta exatamente como antes de existir provider OpenAI.
"""
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("gateway.codex.catalog")

_path: Path | None = None
_enabled: bool = False
_models: list[dict[str, Any]] = []
_ids: frozenset[str] = frozenset()


def configure(settings: Any) -> None:
    """Aponta o catálogo para o data_dir e carrega o cache de disco."""
    global _path, _enabled
    _path = settings.codex_models_path
    _enabled = settings.codex_enabled
    if _enabled:
        _load()


def _load() -> None:
    global _models, _ids
    if _path is None:
        return
    try:
        data = json.loads(_path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return
    if isinstance(data, list):
        _set(data, persist=False)


def _set(models: list[dict[str, Any]], persist: bool = True) -> None:
    global _models, _ids
    _models = [m for m in models if isinstance(m, dict) and m.get("id")]
    _ids = frozenset(m["id"] for m in _models)
    if persist and _path is not None:
        _write(_models)


def _write(models: list[dict[str, Any]]) -> None:
    assert _path is not None
    _path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_path.parent, prefix=".codex-models-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(models, f, indent=2)
        os.replace(tmp, _path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ids() -> frozenset[str]:
    """Ids conhecidos. Vazio quando o provider está desligado."""
    return _ids if _enabled else frozenset()


def models() -> list[dict[str, Any]]:
    return list(_models) if _enabled else []


def is_enabled() -> bool:
    return _enabled


async def refresh(client: Any) -> list[dict[str, Any]]:
    """Repopula o catálogo a partir do SDK e persiste o cache.

    Recebe o client pronto em vez de criá-lo: quem chama (lifespan, rotas de
    auth) já tem um, e assim este módulo não importa o runner.
    """
    resp = await client.models(include_hidden=False)
    found = [
        {
            "id": m.id,
            "display_name": m.display_name,
            "is_default": m.is_default,
            "default_reasoning_effort": getattr(m.default_reasoning_effort, "value", None),
        }
        for m in resp.data
    ]
    _set(found)
    logger.info("Catálogo do Codex atualizado: %d modelos", len(found))
    return found
