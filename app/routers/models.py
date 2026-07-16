"""GET /v1/models e /v1/models/{id} no formato da OpenAI."""
from fastapi import APIRouter, Depends

from ..config import get_settings
from ..errors import GatewayError
from ..model_registry import model_ids, model_object
from ..security import require_gateway_key

router = APIRouter(dependencies=[Depends(require_gateway_key)])


@router.get("/v1/models")
async def list_models():
    settings = get_settings()
    return {
        "object": "list",
        "data": [model_object(m) for m in model_ids(settings.agent_mode_enabled)],
    }


@router.get("/v1/models/{model_id:path}")
async def get_model(model_id: str):
    settings = get_settings()
    if model_id not in model_ids(settings.agent_mode_enabled):
        raise GatewayError(
            404,
            f"The model `{model_id}` does not exist.",
            "invalid_request_error",
            "model_not_found",
            param="model",
        )
    return model_object(model_id)
