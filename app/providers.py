"""Registry de providers: resolve qual runner atende cada modelo.

Registry explícito em vez de if/else no router — adicionar um terceiro provider
não toca chat.py. Cada runner expõe o mesmo par de funções:

    build_options(mode, model, system_text, co, settings) -> <options opaco>
    run_events(prompt, options, settings, timeout_seconds) -> AsyncIterator[tuple]
"""
import asyncio

from .model_registry import owner_of

_semaphores: dict[str, asyncio.Semaphore] = {}


def get_semaphore(provider: str, limit: int) -> asyncio.Semaphore:
    """Um semáforo POR provider.

    Upstreams diferentes têm limites diferentes, e um Claude saturado não deve
    segurar um request do Codex atrás do mesmo contador.
    """
    sem = _semaphores.get(provider)
    if sem is None:
        sem = _semaphores[provider] = asyncio.Semaphore(limit)
    return sem


def runner_for(model: str):
    """Módulo runner do provider dono do modelo.

    Import tardio: os runners importam este módulo para pegar o próprio
    semáforo, então um import no topo criaria ciclo.
    """
    if owner_of(model) == "openai":
        # Import só aqui: puxa o openai-codex (e o binário embutido). Quem usa
        # apenas o Claude nunca paga esse custo.
        from . import codex_runner

        return codex_runner
    from . import claude_runner

    return claude_runner
