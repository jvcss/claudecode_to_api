"""Modelos Pydantic dos requests (compatíveis com o wire format da OpenAI)."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PermissionModeStr = Literal["default", "acceptEdits", "bypassPermissions", "plan", "dontAsk"]


class ClaudeOptionsExt(BaseModel):
    """Extensão do gateway, enviada via extra_body dos clientes OpenAI."""

    model_config = ConfigDict(extra="ignore")

    mode: Literal["chat", "agent"] | None = None
    cwd: str | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    max_turns: int | None = Field(None, ge=1)
    permission_mode: PermissionModeStr | None = None
    max_budget_usd: float | None = Field(None, gt=0)
    emit_tool_activity: bool = False
    timeout_seconds: int | None = Field(None, ge=1)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: str
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None


class StreamOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    include_usage: bool = False


class ResponseFormat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["text", "json_object", "json_schema"] = "text"
    json_schema: dict[str, Any] | None = None


class ChatCompletionRequest(BaseModel):
    # Campos OpenAI sem equivalente (temperature, max_tokens, top_p, ...) são
    # aceitos e ignorados para manter compatibilidade com clientes existentes.
    model_config = ConfigDict(extra="ignore")

    model: str | None = None
    messages: list[ChatMessage]
    stream: bool = False
    stream_options: StreamOptions | None = None
    user: str | None = None
    claude_options: ClaudeOptionsExt | None = None
    # Campos que exigem tratamento explícito (não podem ser silenciosamente ignorados):
    n: int | None = None
    response_format: ResponseFormat | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    functions: list[dict[str, Any]] | None = None


class AuthTokenRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: str = Field(min_length=8)
    type: Literal["oauth", "api_key"] | None = None
    validate_token: bool = Field(True, alias="validate")


class SetModelRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = Field(min_length=1)
