"""Autenticação Bearer do gateway (formato de erro idêntico ao da OpenAI)."""
import hmac
import logging

from fastapi import Header

from .config import get_settings
from .errors import GatewayError

logger = logging.getLogger("gateway.security")


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _check(authorization: str | None, keys: list[str]) -> None:
    if not keys:
        return  # modo aberto (dev) — warning emitido no startup
    provided = _extract_bearer(authorization)
    if provided is not None:
        # Compara em bytes: um token com caractere não-ASCII faria
        # hmac.compare_digest levantar TypeError (→ 500 em vez de 401).
        provided_b = provided.encode("utf-8", "surrogatepass")
        if any(hmac.compare_digest(provided_b, k.encode("utf-8")) for k in keys):
            return
    raise GatewayError(
        401,
        "Incorrect API key provided. You can find your API key with the gateway operator.",
        "invalid_request_error",
        "invalid_api_key",
    )


async def require_gateway_key(authorization: str | None = Header(None)) -> None:
    _check(authorization, get_settings().gateway_keys)


async def require_admin_key(authorization: str | None = Header(None)) -> None:
    _check(authorization, get_settings().admin_keys)
