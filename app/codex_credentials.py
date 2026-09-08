"""Credencial do Codex: status e importação do auth.json.

Deliberadamente NÃO reusa CredentialStore. Aquele guarda *um token string*; a
credencial do Codex é um *arquivo gerenciado pelo próprio binário*, com
id_token/access_token/refresh_token e rotação automática. Forçar os dois no
mesmo slot quebraria o formato em disco de quem já usa o gateway. O que se
reusa é o padrão de escrita atômica (mkstemp + chmod 0600 + os.replace).
"""
import base64
import binascii
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("gateway.codex.credentials")


def auth_path(settings: Any) -> Path:
    return settings.codex_home / "auth.json"


def _jwt_claims(token: str) -> dict[str, Any]:
    """Claims de um JWT sem verificar assinatura.

    Não somos o emissor — só precisamos ler account_id/email para exibir no
    /status. Isso evita uma dependência de PyJWT só para isso.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # base64url sem padding
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError):
        return {}


def _read(settings: Any) -> dict[str, Any] | None:
    try:
        data = json.loads(auth_path(settings).read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        logger.exception("auth.json do Codex ilegível em %s", auth_path(settings))
        return None
    return data if isinstance(data, dict) else None


def status(settings: Any) -> dict[str, Any]:
    """Estado da credencial. Nunca devolve tokens."""
    data = _read(settings)
    if not data:
        return {"configured": False, "source": "none"}
    tokens = data.get("tokens") or {}
    claims = _jwt_claims(tokens.get("id_token") or tokens.get("access_token") or "")
    profile = claims.get("https://api.openai.com/profile") or {}
    auth = claims.get("https://api.openai.com/auth") or {}
    return {
        "configured": bool(tokens.get("access_token") or data.get("OPENAI_API_KEY")),
        "source": "auth_json",
        "auth_mode": data.get("auth_mode"),
        "account_id": tokens.get("account_id") or auth.get("chatgpt_account_id"),
        "email": profile.get("email"),
        "plan": auth.get("chatgpt_plan_type"),
        "last_refresh": data.get("last_refresh"),
    }


def import_auth_json(settings: Any, blob: dict[str, Any]) -> None:
    """Grava um auth.json vindo de um `codex login` feito na máquina do operador."""
    tokens = blob.get("tokens") or {}
    if not (tokens.get("access_token") or blob.get("OPENAI_API_KEY")):
        from .errors import invalid_request

        raise invalid_request(
            "auth_json must contain either tokens.access_token or OPENAI_API_KEY.",
            code="invalid_auth_json",
            param="auth_json",
        )
    path = auth_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".auth-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(blob, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def delete(settings: Any) -> bool:
    try:
        auth_path(settings).unlink()
        return True
    except FileNotFoundError:
        return False
