"""Configurações do gateway via variáveis de ambiente (e .env)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

GATEWAY_VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Segurança do gateway
    gateway_api_keys: str = ""  # separadas por vírgula; vazio = aberto (dev)
    admin_api_key: str = ""  # vazio = usa as chaves do gateway

    # Persistência
    data_dir: Path = Path("./data")

    # Modelos
    default_model: str = "sonnet"
    model_strict: bool = False

    # Robustez
    max_concurrency: int = 3
    request_timeout_seconds: int = 300

    # Modo agente
    agent_mode_enabled: bool = False
    agent_root: Path = Path("/workspace")
    agent_default_permission_mode: str = "bypassPermissions"
    agent_max_turns: int = 30
    max_budget_usd_per_request: float | None = None

    # CLI (opcional; por padrão o SDK usa o binário embutido no wheel)
    claude_cli_path: str | None = None

    # Provider OpenAI/Codex (assinatura ChatGPT)
    codex_enabled: bool = False
    codex_max_concurrency: int = 3
    codex_reasoning_effort: str = "medium"

    cors_origins: str = ""
    log_level: str = "info"

    @property
    def gateway_keys(self) -> list[str]:
        return [k.strip() for k in self.gateway_api_keys.split(",") if k.strip()]

    @property
    def admin_keys(self) -> list[str]:
        if self.admin_api_key.strip():
            return [self.admin_api_key.strip()]
        return self.gateway_keys

    @property
    def chat_cwd(self) -> Path:
        return self.data_dir / "chat_cwd"

    @property
    def credentials_path(self) -> Path:
        return self.data_dir / "credentials.json"

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.json"

    @property
    def codex_home(self) -> Path:
        """CODEX_HOME próprio, isolado do ~/.codex do host.

        O refresh token do Codex é rotativo e de uso único: compartilhar este
        diretório com outro processo que também roda `codex` invalida os dois.
        """
        return self.data_dir / "codex"

    @property
    def codex_chat_cwd(self) -> Path:
        return self.data_dir / "codex_cwd"

    @property
    def codex_models_path(self) -> Path:
        return self.data_dir / "codex_models.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
