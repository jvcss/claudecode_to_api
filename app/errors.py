"""Erros no envelope da OpenAI: {"error": {"message", "type", "param", "code"}}."""
from typing import Any

from fastapi.responses import JSONResponse


class GatewayError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        err_type: str = "invalid_request_error",
        code: str | None = None,
        param: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.err_type = err_type
        self.code = code
        self.param = param
        self.headers = headers or {}


def error_body(message: str, err_type: str, code: str | None = None, param: str | None = None) -> dict[str, Any]:
    return {"error": {"message": message, "type": err_type, "param": param, "code": code}}


def error_response(exc: GatewayError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.message, exc.err_type, exc.code, exc.param),
        headers=exc.headers or None,
    )


def invalid_request(message: str, code: str | None = None, param: str | None = None) -> GatewayError:
    return GatewayError(400, message, "invalid_request_error", code, param)


def not_authenticated() -> GatewayError:
    return GatewayError(
        401,
        "Claude Code is not authenticated on this gateway. "
        "Run `claude setup-token` and store the token via POST /auth/token.",
        "authentication_error",
        "gateway_not_authenticated",
    )
