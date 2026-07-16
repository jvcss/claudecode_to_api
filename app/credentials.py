"""Armazenamento e resolução das credenciais do Claude Code.

Ordem de resolução: token gravado via /auth/token → env do processo →
login de assinatura da máquina (~/.claude/.credentials.json) → nenhuma.

O token gravado é injetado no subprocesso do CLI via ClaudeAgentOptions.env;
as variáveis concorrentes são esvaziadas porque o env do processo é herdado
pelo subprocesso e uma ANTHROPIC_API_KEY residual do host venceria o token.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("gateway.credentials")

OAUTH_PREFIX = "sk-ant-oat"
_AUTH_ENV_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def detect_type(token: str) -> str:
    return "oauth" if token.startswith(OAUTH_PREFIX) else "api_key"


class CredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            logger.exception("credentials.json ilegível em %s", self.path)
            return None
        if not isinstance(data, dict) or not data.get("token"):
            return None
        return data

    def save(self, token: str, type_: str | None = None) -> dict[str, Any]:
        record = {
            "type": type_ or detect_type(token),
            "token": token,
            "created_at": _now(),
            "validated_at": None,
            "last_error": None,
        }
        self._write(record)
        return record

    def update(self, **fields: Any) -> dict[str, Any] | None:
        record = self.load()
        if record is None:
            return None
        record.update(fields)
        self._write(record)
        return record

    def delete(self) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False

    def _write(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".credentials-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(record, f, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def resolve(self) -> tuple[str, dict[str, str]]:
        """Retorna (source, env_para_o_subprocesso)."""
        record = self.load()
        if record:
            env = {var: "" for var in _AUTH_ENV_VARS}
            if record["type"] == "oauth":
                env["CLAUDE_CODE_OAUTH_TOKEN"] = record["token"]
                return "stored_oauth", env
            env["ANTHROPIC_API_KEY"] = record["token"]
            return "stored_api_key", env
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return "process_env", {}
        if (Path.home() / ".claude" / ".credentials.json").exists():
            return "machine_login", {}
        return "none", {}

    def status(self) -> dict[str, Any]:
        source, _ = self.resolve()
        record = self.load()
        info: dict[str, Any] = {"configured": source != "none", "source": source}
        if record:
            token = record["token"]
            info.update(
                {
                    "type": record["type"],
                    "token_suffix": token[-4:] if len(token) >= 8 else "****",
                    "created_at": record.get("created_at"),
                    "validated_at": record.get("validated_at"),
                    "last_error": record.get("last_error"),
                }
            )
        return info
