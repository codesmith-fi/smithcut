"""Kielinormalisoinnin ja chat-stubin testit."""

from __future__ import annotations

from controller.editor import chat_stub_reply, normalize_lang


def test_normalize_lang():
    assert normalize_lang("fi") == "fi"
    assert normalize_lang("EN") == "en"
    assert normalize_lang("de") == "fi"
    assert normalize_lang(None) == "fi"


def test_chat_stub_reply_languages():
    fi = chat_stub_reply("fi")
    en = chat_stub_reply("en")
    assert "kytketty" in fi.lower()
    assert "not connected" in en.lower()
    assert chat_stub_reply("xx") == fi
