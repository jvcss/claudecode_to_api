# Os wheels do claude-agent-sdk e do openai-codex embutem binários nativos. O do
# claude-agent-sdk só existe para manylinux/amd64 (glibc): não há wheel musl, então
# Alpine é impossível e a plataforma precisa ser fixada — o runner pode ser multi-arch.
FROM --platform=linux/amd64 python:3.13-slim

# git é útil para o modo agente; os CLIs do Claude Code e do Codex já vêm embutidos
# nos wheels do claude-agent-sdk e do openai-codex (não é necessário Node.js).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 app && mkdir -p /data /workspace && chown app:app /data /workspace

WORKDIR /srv/app
# requirements ANTES de COPY app/: mantém a layer pesada do SDK (~270 MB) em cache
# entre builds que só mexem no código.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

USER app
# O Claude Code escreve estado em $HOME/.claude — /home/app é gravável (useradd -m).
# O Codex usa um CODEX_HOME próprio em /data/codex (ver Settings.codex_home), que já
# é volume: seu refresh token é rotativo e de uso único, então não pode ser
# compartilhado com o ~/.codex de outra máquina.
ENV HOME=/home/app \
    DATA_DIR=/data \
    AGENT_ROOT=/workspace \
    CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK=1

EXPOSE 8000

# Sem HEALTHCHECK do Docker: o kubelet o ignora. A saúde é aferida pelos
# readiness/liveness probes em GET /healthz definidos no Deployment.

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
