"""Credenciais do Codex (protegidas pela chave admin).

Não existe equivalente consumidor do `claude setup-token` para o ChatGPT: o
caminho headless é o device code. Import de um auth.json feito na máquina do
operador é o plano B, e a API key é o plano C — que **cobra por token e não
usa a assinatura**, ou seja, anula o propósito do gateway.
"""
import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends

from .. import codex_catalog, codex_credentials
from ..config import get_settings
from ..errors import GatewayError
from ..schemas import CodexApiKeyRequest, CodexImportRequest
from ..security import require_admin_key

logger = logging.getLogger("gateway.codex.auth")

router = APIRouter(prefix="/codex", dependencies=[Depends(require_admin_key)])

# Login em andamento. Single-slot e em memória de propósito: um login pela
# metade não deve sobreviver a um restart.
_pending: dict[str, Any] = {}
_pending_tasks: set[asyncio.Task] = set()


def _require_enabled() -> None:
    if not get_settings().codex_enabled:
        raise GatewayError(
            403,
            "The OpenAI/Codex provider is disabled on this gateway (set CODEX_ENABLED=true).",
            "invalid_request_error",
            "codex_disabled",
        )


async def _client():
    from .. import codex_runner

    return await codex_runner.get_client(get_settings())


async def _refresh_catalog() -> None:
    """Repopula o catálogo depois de um login. Best-effort: falhar aqui não
    invalida a credencial que acabou de ser gravada."""
    try:
        await codex_catalog.refresh(await _client())
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao atualizar o catálogo de modelos do Codex")


@router.post("/auth/device-code")
async def device_code_start():
    """Inicia o login por device code. O operador abre a URL na máquina dele."""
    _require_enabled()
    handle = await (await _client()).login_chatgpt_device_code()

    async def _await_login() -> None:
        try:
            await handle.wait()
            logger.info("Login do Codex concluído (login_id=%s)", handle.login_id)
            await _refresh_catalog()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Login do Codex falhou: %s", exc)
            _pending["error"] = str(exc)
        finally:
            _pending.pop("handle", None)

    task = asyncio.ensure_future(_await_login())
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)
    _pending.update({"handle": handle, "error": None})

    return {
        "login_id": handle.login_id,
        "verification_url": handle.verification_url,
        "user_code": handle.user_code,
        "next": "Abra a URL, informe o user_code e acompanhe em GET /codex/auth/status.",
    }


@router.delete("/auth/device-code")
async def device_code_cancel():
    handle = _pending.pop("handle", None)
    if handle is None:
        return {"cancelled": False}
    await handle.cancel()
    return {"cancelled": True}


@router.post("/auth/import")
async def import_auth(body: CodexImportRequest):
    """Grava um auth.json produzido por `codex login` na máquina do operador."""
    _require_enabled()
    settings = get_settings()
    codex_credentials.import_auth_json(settings, body.auth_json)
    # O app-server lê o auth.json na inicialização: sem derrubá-lo, a
    # credencial nova só valeria no próximo restart do container.
    from .. import codex_runner

    await codex_runner.reset_client()
    await _refresh_catalog()
    return {"stored": True, **codex_credentials.status(settings)}


@router.post("/auth/api-key")
async def login_api_key(body: CodexApiKeyRequest):
    """⚠️ Cobra por token e NÃO usa a assinatura — só para quem quer isso."""
    _require_enabled()
    await (await _client()).login_api_key(body.api_key)
    await _refresh_catalog()
    logger.warning("Codex autenticado por API key: o consumo será cobrado por token.")
    return {"stored": True, "billing": "per_token"}


@router.get("/auth/status")
async def auth_status():
    settings = get_settings()
    info = codex_credentials.status(settings)
    info["enabled"] = settings.codex_enabled
    info["pending_login"] = "handle" in _pending
    info["last_login_error"] = _pending.get("error")
    info["models_cached"] = len(codex_catalog.ids())
    return info


@router.post("/auth/validate")
async def validate_now():
    """Barato: `account()` não gasta tokens (o ping do lado Claude gasta)."""
    _require_enabled()
    try:
        account = await (await _client()).account()
    except Exception as exc:  # noqa: BLE001
        return {"valid": False, "error": str(exc)[:300]}
    return {"valid": not account.requires_openai_auth, "error": None}


@router.delete("/auth/token")
async def delete_token():
    settings = get_settings()
    removed = codex_credentials.delete(settings)
    from .. import codex_runner

    await codex_runner.reset_client()
    return {"removed": removed}


@router.post("/models/refresh")
async def refresh_models():
    _require_enabled()
    models = await codex_catalog.refresh(await _client())
    return {"count": len(models), "models": [m["id"] for m in models]}
