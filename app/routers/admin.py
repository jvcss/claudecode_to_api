"""Configuração runtime do gateway (modelo padrão)."""
from fastapi import APIRouter, Depends

from ..config import get_settings
from ..model_registry import ConfigStore
from ..schemas import SetModelRequest
from ..security import require_admin_key

router = APIRouter(prefix="/config", dependencies=[Depends(require_admin_key)])


def _config() -> ConfigStore:
    settings = get_settings()
    return ConfigStore(settings.config_path, settings.default_model)


@router.get("/model")
async def get_default_model():
    return {"default_model": _config().default_model}


@router.post("/model")
async def set_default_model(body: SetModelRequest):
    config = _config()
    config.set_default_model(body.model)
    return {"default_model": config.default_model}
