"""Endpoints de credenciais do Claude Code (protegidos pela chave admin)."""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from .. import claude_runner
from ..config import get_settings
from ..credentials import CredentialStore
from ..errors import GatewayError
from ..schemas import AuthTokenRequest
from ..security import require_admin_key

logger = logging.getLogger("gateway.auth")

router = APIRouter(prefix="/auth", dependencies=[Depends(require_admin_key)])


def _store() -> CredentialStore:
    return CredentialStore(get_settings().credentials_path)


async def _validate(store: CredentialStore) -> tuple[bool, str | None]:
    """Ping barato: uma query de 1 turno com haiku prova que a credencial funciona."""
    settings = get_settings()
    source, creds_env = store.resolve()
    if source.startswith("stored"):
        # Isola o CLI de QUALQUER login pré-existente (~/.claude), senão um token
        # gravado inválido validaria como bom via fallback de credenciais.
        # As credenciais são lidas de $HOME/.claude independentemente de
        # CLAUDE_CONFIG_DIR, então é preciso sobrepor os dois.
        isolated = settings.data_dir / "validate_home"
        isolated.mkdir(parents=True, exist_ok=True)
        creds_env = {
            **creds_env,
            "HOME": str(isolated),
            "CLAUDE_CONFIG_DIR": str(isolated),
        }
    options = claude_runner.build_options("chat", "haiku", None, None, settings, creds_env)
    try:
        events = claude_runner.run_events("ping", options, settings, timeout_seconds=60)
        try:
            async for event in events:
                if event[0] == "done":
                    return True, None
        finally:
            await events.aclose()
        return False, "run ended without a result"
    except GatewayError as exc:
        return False, exc.message
    except Exception as exc:  # noqa: BLE001
        logger.exception("Validação de credencial falhou")
        return False, str(exc)


@router.post("/token")
async def set_token(body: AuthTokenRequest):
    store = _store()
    store.save(body.token, body.type)
    result = {"stored": True, "valid": None, "error": None}
    if body.validate_token:
        ok, error = await _validate(store)
        store.update(
            validated_at=datetime.now(timezone.utc).isoformat() if ok else None,
            last_error=error,
        )
        result["valid"] = ok
        result["error"] = error
    return result


@router.get("/status")
async def auth_status():
    return _store().status()


@router.delete("/token")
async def delete_token():
    removed = _store().delete()
    return {"removed": removed}


@router.post("/validate")
async def validate_now():
    store = _store()
    ok, error = await _validate(store)
    if store.load():
        store.update(
            validated_at=datetime.now(timezone.utc).isoformat() if ok else None,
            last_error=error,
        )
    return {"valid": ok, "error": error, "source": store.resolve()[0]}
