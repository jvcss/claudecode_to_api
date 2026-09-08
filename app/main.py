"""App factory do gateway Claude Code → API compatível com OpenAI."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import codex_catalog, codex_credentials
from .config import GATEWAY_VERSION, get_settings
from .credentials import CredentialStore
from .errors import GatewayError, error_body, error_response
from .routers import admin, auth, chat, codex_auth, models

logger = logging.getLogger("gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.chat_cwd.mkdir(parents=True, exist_ok=True)

    codex_catalog.configure(settings)
    if settings.codex_enabled:
        settings.codex_home.mkdir(parents=True, exist_ok=True)
        settings.codex_chat_cwd.mkdir(parents=True, exist_ok=True)

    if not settings.gateway_keys:
        logger.warning(
            "*** GATEWAY EM MODO ABERTO (sem GATEWAY_API_KEYS) — use apenas em dev ***"
        )
    if not settings.admin_keys:
        logger.warning(
            "*** ENDPOINTS ADMIN ABERTOS (/auth/*, /config/*) — defina ADMIN_API_KEY "
            "ou GATEWAY_API_KEYS antes de expor o gateway ***"
        )
    source, _ = CredentialStore(settings.credentials_path).resolve()
    if source == "none":
        logger.warning(
            "Nenhuma credencial do Claude Code encontrada. "
            "Rode `claude setup-token` e grave o token via POST /auth/token."
        )
    else:
        logger.info("Credencial do Claude Code ativa: %s", source)

    if settings.codex_enabled:
        codex_status = codex_credentials.status(settings)
        if codex_status["configured"]:
            logger.info(
                "Credencial do Codex ativa: %s (%s), %d modelos em cache",
                codex_status.get("account_id") or "?",
                codex_status.get("plan") or "?",
                len(codex_catalog.ids()),
            )
        else:
            logger.warning(
                "Provider Codex habilitado sem credencial. "
                "Rode POST /codex/auth/device-code para autenticar."
            )
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Claude Code OpenAI Gateway", version=GATEWAY_VERSION, lifespan=lifespan)

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError):
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        detail = "; ".join(
            f"{'.'.join(str(loc) for loc in err.get('loc', []))}: {err.get('msg')}"
            for err in exc.errors()[:5]
        )
        return JSONResponse(
            status_code=400,
            content=error_body(f"Invalid request: {detail}", "invalid_request_error"),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("Erro não tratado em %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_body("Internal gateway error.", "server_error", "internal_error"),
        )

    if settings.cors_origins.strip():
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    app.include_router(chat.router)
    app.include_router(models.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(codex_auth.router)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "version": GATEWAY_VERSION}

    return app


app = create_app()
