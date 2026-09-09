"""Aliases e sanitização de modelos + modelo padrão persistido."""
import json
import logging
import re
import tempfile
import os
from pathlib import Path

from . import codex_catalog
from .errors import GatewayError

logger = logging.getLogger("gateway.models")

# Aliases entendidos pelo Claude Code — repassados como estão para o SDK.
ALIASES = ["sonnet", "opus", "haiku", "fable", "sonnet[1m]", "opus[1m]"]
KNOWN_FULL = [
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-haiku-4-5",
    "claude-fable-5",
]
CLAUDE_ID_RE = re.compile(r"^claude-[a-z0-9\[\].-]+$")
AGENT_SUFFIX = "-agent"
_MODELS_CREATED = 1751328000  # timestamp estático para o campo `created`


def is_known(model: str) -> bool:
    if model in codex_catalog.ids():
        return True
    return model in ALIASES or model in KNOWN_FULL or bool(CLAUDE_ID_RE.match(model))


def owner_of(model: str) -> str:
    """Provider dono do modelo, no vocabulário do campo `owned_by` da OpenAI.

    A adesão ao Codex é por allowlist EXATA vinda do catálogo — nunca um regex
    `gpt-*`. Um regex capturaria `gpt-4o`, que hoje é desconhecido e cai no
    modelo padrão (é o caso que o comentário de `parse_model` cita); ele
    passaria a ser roteado ao Codex, que rejeita o id. Seria uma regressão
    silenciosa disparada por upgrade de imagem, sem ação do usuário.
    """
    return "openai" if model in codex_catalog.ids() else "anthropic"


def parse_model(
    requested: str | None,
    default_model: str,
    strict: bool,
) -> tuple[str, bool]:
    """Resolve o modelo do request. Retorna (modelo, sufixo_agent_presente)."""
    if not requested:
        return default_model, False
    agent = requested.endswith(AGENT_SUFFIX)
    base = requested[: -len(AGENT_SUFFIX)] if agent else requested
    if is_known(base):
        return base, agent
    if strict:
        raise GatewayError(
            404,
            f"The model `{requested}` does not exist or you do not have access to it.",
            "invalid_request_error",
            "model_not_found",
            param="model",
        )
    # Muitos clientes OpenAI hardcodam modelos gpt-*; cai no padrão configurado.
    # NÃO herda o modo agente de um sufixo -agent em modelo desconhecido (ex.:
    # "gpt-4o-agent" não deve, silenciosamente, habilitar ferramentas).
    logger.warning("Modelo desconhecido %r; usando o padrão %r", requested, default_model)
    return default_model, False


def model_ids(agent_enabled: bool) -> list[str]:
    ids = ALIASES + KNOWN_FULL
    if agent_enabled:
        # Sufixo -agent só para os aliases do Claude: o modo agente não existe
        # para o Codex, e anunciá-lo daria 403 na primeira chamada.
        ids = ids + [f"{a}{AGENT_SUFFIX}" for a in ALIASES if "[" not in a]
    return ids + sorted(codex_catalog.ids())


def model_object(model_id: str) -> dict:
    base = model_id[: -len(AGENT_SUFFIX)] if model_id.endswith(AGENT_SUFFIX) else model_id
    return {
        "id": model_id,
        "object": "model",
        "created": _MODELS_CREATED,
        "owned_by": owner_of(base),
    }


class ConfigStore:
    """Configuração runtime persistida (por ora, só o modelo padrão)."""

    def __init__(self, path: Path, fallback_model: str) -> None:
        self.path = path
        self.fallback_model = fallback_model

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}

    @property
    def default_model(self) -> str:
        return self._load().get("default_model") or self.fallback_model

    def set_default_model(self, model: str) -> None:
        if not is_known(model):
            raise GatewayError(
                400,
                f"Unknown model `{model}`. Use an alias ({', '.join(ALIASES)}) or a claude-* model id.",
                "invalid_request_error",
                "model_not_found",
                param="model",
            )
        data = self._load()
        data["default_model"] = model
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".config-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
