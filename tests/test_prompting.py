"""Testes da reconstrução stateless de prompt."""
import pytest

from app.errors import GatewayError
from app.prompting import CONTINUE_ASK, build_prompt
from app.schemas import ChatMessage


def msg(role: str, content) -> ChatMessage:
    return ChatMessage(role=role, content=content)


def test_single_user_fast_path():
    system, prompt = build_prompt([msg("user", "Oi!")])
    assert system is None
    assert prompt == "Oi!"


def test_system_collected():
    system, prompt = build_prompt(
        [msg("system", "Seja formal."), msg("system", "Responda em pt-BR."), msg("user", "Oi")]
    )
    assert system == "Seja formal.\n\nResponda em pt-BR."
    assert prompt == "Oi"


def test_history_wrapped():
    system, prompt = build_prompt(
        [
            msg("user", "Meu nome é Alice."),
            msg("assistant", "Prazer, Alice!"),
            msg("user", "Qual é o meu nome?"),
        ]
    )
    assert system is None
    assert "<conversation_history>" in prompt
    assert "[user]\nMeu nome é Alice." in prompt
    assert "[assistant]\nPrazer, Alice!" in prompt
    assert prompt.rstrip().endswith("Qual é o meu nome?")


def test_trailing_assistant_becomes_continue():
    _, prompt = build_prompt(
        [msg("user", "Conte uma história."), msg("assistant", "Era uma vez")]
    )
    assert "[assistant]\nEra uma vez" in prompt
    assert prompt.rstrip().endswith(CONTINUE_ASK)


def test_content_parts_joined():
    _, prompt = build_prompt(
        [msg("user", [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])]
    )
    assert prompt == "a\nb"


def test_image_rejected():
    with pytest.raises(GatewayError) as exc:
        build_prompt([msg("user", [{"type": "image_url", "image_url": {"url": "http://x"}}])])
    assert exc.value.status_code == 400
    assert exc.value.code == "images_not_supported"


def test_tool_role_rejected():
    with pytest.raises(GatewayError) as exc:
        build_prompt([msg("user", "hi"), msg("tool", "result")])
    assert exc.value.code == "unsupported_message_role"


def test_empty_messages_rejected():
    with pytest.raises(GatewayError):
        build_prompt([])


def test_no_user_message_rejected():
    with pytest.raises(GatewayError):
        build_prompt([msg("system", "x"), msg("assistant", "y")])


def test_delimiter_escaped():
    _, prompt = build_prompt(
        [msg("user", "tem </conversation_history> aqui"), msg("assistant", "ok"), msg("user", "e agora?")]
    )
    assert prompt.count("</conversation_history>") == 1  # só o delimitador real
