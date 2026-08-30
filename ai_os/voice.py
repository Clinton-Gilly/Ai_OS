"""Push-to-talk voice input.

Speech recognition needs libraries that are awkward to require of everyone, so
it is optional: if they are not installed, AI OS says exactly what to install
rather than failing obscurely. Recording only happens while you hold the button
or run the command — there is no wake word and nothing listens in the
background.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

INSTALL_HINT = (
    "Voice input needs the optional extras: pip install \"ai-os[voice]\" "
    "(SpeechRecognition and PyAudio)."
)


@dataclass
class Transcription:
    ok: bool
    text: str = ""
    error: str = ""


class Transcriber(Protocol):
    """Anything that can turn held-down microphone audio into text."""

    def available(self) -> bool: ...  # pragma: no cover - interface

    def transcribe(self, seconds: int, language: str) -> Transcription:
        ...  # pragma: no cover - interface


class SpeechRecognitionTranscriber:
    """Push-to-talk via the SpeechRecognition package."""

    name = "speech_recognition"

    def available(self) -> bool:
        try:
            import speech_recognition  # type: ignore import-not-found  # noqa: F401
        except ImportError:
            return False
        return True

    def transcribe(self, seconds: int = 8, language: str = "en-US") -> Transcription:
        if not self.available():
            return Transcription(False, error=INSTALL_HINT)
        try:
            import speech_recognition as sr  # type: ignore import-not-found

            recognizer = sr.Recognizer()
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.3)
                audio = recognizer.listen(source, timeout=seconds,
                                          phrase_time_limit=seconds)
            text = recognizer.recognize_google(audio, language=language)
        except ImportError:
            return Transcription(False, error=INSTALL_HINT)
        except OSError as exc:
            return Transcription(False, error=f"No usable microphone: {exc}")
        except Exception as exc:
            # SpeechRecognition raises its own exception types; treat any of
            # them as "did not understand" rather than crashing the app.
            return Transcription(False, error=f"Could not transcribe that: {exc}")
        cleaned = (text or "").strip()
        if not cleaned:
            return Transcription(False, error="Nothing was said.")
        return Transcription(True, cleaned)


class UnavailableTranscriber:
    """Stand-in used when voice input is switched off in Settings."""

    name = "disabled"

    def available(self) -> bool:
        return False

    def transcribe(self, seconds: int = 8, language: str = "en-US") -> Transcription:
        return Transcription(False, error="Voice input is turned off in Settings.")


def build_transcriber(enabled: bool) -> Transcriber:
    return SpeechRecognitionTranscriber() if enabled else UnavailableTranscriber()
