"""The global hotkey parser and the optional voice transcriber."""

from __future__ import annotations

import pytest

from ai_os.hotkey import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    HotkeyError,
    HotkeyListener,
    parse_hotkey,
)
from ai_os.voice import (
    INSTALL_HINT,
    SpeechRecognitionTranscriber,
    UnavailableTranscriber,
    build_transcriber,
)


def test_hotkey_parses_modifiers_and_key():
    hotkey = parse_hotkey("ctrl+alt+space")
    assert hotkey.modifiers == MOD_NOREPEAT | MOD_CONTROL | MOD_ALT
    assert hotkey.key_code == 0x20


def test_hotkey_accepts_letters_and_function_keys():
    assert parse_hotkey("ctrl+shift+k").key_code == ord("K")
    assert parse_hotkey("alt+f4").key_code == 0x73
    assert parse_hotkey("ctrl+shift+k").modifiers & MOD_SHIFT


def test_hotkey_accepts_dashes_as_separators():
    dashed, plussed = parse_hotkey("ctrl-alt-space"), parse_hotkey("ctrl+alt+space")
    assert (dashed.modifiers, dashed.key_code) == (plussed.modifiers, plussed.key_code)


@pytest.mark.parametrize("text", ["", "space", "ctrl+nope", "ctrl+", "meta+space"])
def test_bad_hotkeys_are_rejected_with_a_reason(text):
    with pytest.raises(HotkeyError):
        parse_hotkey(text)


def test_a_hotkey_without_a_modifier_explains_itself():
    with pytest.raises(HotkeyError) as excinfo:
        parse_hotkey("space")
    assert "modifier" in str(excinfo.value)


def test_listener_reports_unsupported_platforms_instead_of_failing(monkeypatch):
    monkeypatch.setattr("ai_os.hotkey.is_supported", lambda: False)
    listener = HotkeyListener("ctrl+alt+space", lambda: None)
    assert listener.start() is False
    assert "only available on Windows" in listener.error
    assert not listener.running
    listener.stop()   # must be safe even though it never started


def test_listener_validates_the_hotkey_up_front():
    with pytest.raises(HotkeyError):
        HotkeyListener("nonsense", lambda: None)


# -- voice --------------------------------------------------------------
def test_voice_is_a_no_op_when_turned_off():
    transcriber = build_transcriber(False)
    assert isinstance(transcriber, UnavailableTranscriber)
    result = transcriber.transcribe()
    assert not result.ok and "turned off" in result.error


def test_enabling_voice_selects_the_real_transcriber():
    assert isinstance(build_transcriber(True), SpeechRecognitionTranscriber)


def test_missing_speech_libraries_produce_an_install_hint(monkeypatch):
    transcriber = SpeechRecognitionTranscriber()
    monkeypatch.setattr(transcriber, "available", lambda: False)
    result = transcriber.transcribe()
    assert not result.ok
    assert result.error == INSTALL_HINT


def test_transcription_failures_are_returned_not_raised(monkeypatch):
    transcriber = SpeechRecognitionTranscriber()
    monkeypatch.setattr(transcriber, "available", lambda: True)

    def explode(*args, **kwargs):
        raise RuntimeError("microphone on fire")

    monkeypatch.setitem(__import__("sys").modules, "speech_recognition",
                        type("Fake", (), {"Recognizer": explode, "Microphone": explode}))
    result = transcriber.transcribe()
    assert not result.ok and "microphone on fire" in result.error
