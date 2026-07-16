"""Reconstrução stateless de contexto: messages[] (OpenAI) → (system_text, prompt)."""
from typing import Any

from .errors import invalid_request
from .schemas import ChatMessage

HISTORY_OPEN = "<conversation_history>"
HISTORY_CLOSE = "</conversation_history>"
HISTORY_HEADER = (
    "This is the prior conversation between the user and you (the assistant).\n"
    "Do not mention this block; simply continue the conversation naturally."
)
CONTINUE_ASK = (
    "Continue your previous (assistant) message from exactly where it stopped. "
    "Output only the continuation."
)


def _normalize_content(content: str | list[dict[str, Any]] | None, role: str) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        ptype = part.get("type")
        if ptype == "text":
            parts.append(str(part.get("text", "")))
        elif ptype in ("image_url", "input_image", "input_audio", "file"):
            raise invalid_request(
                "This gateway does not accept non-text inputs (images/audio/files).",
                code="images_not_supported",
                param="messages",
            )
        # partes desconhecidas são ignoradas
    return "\n".join(parts)


def _escape_delimiter(text: str) -> str:
    return text.replace(HISTORY_CLOSE, "<\\/conversation_history>")


def build_prompt(messages: list[ChatMessage]) -> tuple[str | None, str]:
    """Retorna (system_text|None, prompt) a partir do array messages da OpenAI."""
    if not messages:
        raise invalid_request("messages must not be empty.", param="messages")

    system_parts: list[str] = []
    turns: list[tuple[str, str]] = []
    for msg in messages:
        role = msg.role
        if role in ("tool", "function"):
            raise invalid_request(
                f"Message role `{role}` is not supported by this gateway "
                "(tools run server-side inside Claude Code).",
                code="unsupported_message_role",
                param="messages",
            )
        if role not in ("system", "developer", "user", "assistant"):
            raise invalid_request(f"Unknown message role `{role}`.", param="messages")
        text = _normalize_content(msg.content, role)
        if role in ("system", "developer"):
            if text:
                system_parts.append(text)
        else:
            turns.append((role, text))

    system_text = "\n\n".join(system_parts) or None

    if not any(role == "user" for role, _ in turns):
        raise invalid_request("messages must contain at least one user message.", param="messages")

    # Fast path: uma única mensagem de usuário, sem histórico.
    if len(turns) == 1 and turns[0][0] == "user":
        return system_text, turns[0][1]

    if turns[-1][0] == "user":
        history, ask = turns[:-1], turns[-1][1]
    else:
        # Última mensagem é do assistant: cliente pede continuação.
        history, ask = turns, CONTINUE_ASK

    blocks = [f"[{role}]\n{_escape_delimiter(text)}" for role, text in history]
    transcript = "\n\n".join(blocks)
    prompt = f"{HISTORY_OPEN}\n{HISTORY_HEADER}\n\n{transcript}\n{HISTORY_CLOSE}\n\n{ask}"
    return system_text, prompt
