"""Shared Wyoming Info building blocks for the TTS and pipeline servers.

The advertised program/voice roster depends on the configured provider:
the xAI roster is the fixed set of built-in voices, while ElevenLabs
advertises the single configured voice ID (voices live in the user's
ElevenLabs account — list them with scripts/elevenlabs_voices.py).
"""
from __future__ import annotations

from wyoming.info import AsrModel, AsrProgram, Attribution, TtsProgram, TtsVoice

from voicepipe.config import Config

XAI_ATTRIBUTION = Attribution(name="xAI", url="https://docs.x.ai")
ELEVENLABS_ATTRIBUTION = Attribution(name="ElevenLabs", url="https://elevenlabs.io")
# Full roster from GET /v1/tts/voices (2026-07-17) — the docs only list 5
VOICES = [
    "altair", "ara", "atlas", "carina", "castor", "celeste", "cosmo", "eve",
    "helios", "helix", "iris", "kepler", "leo", "lumen", "luna", "lux",
    "naksh", "orion", "perseus", "rex", "rigel", "sal", "sirius", "ursa",
    "zagan", "zenith",
]


def build_tts_program(cfg: Config) -> TtsProgram:
    if cfg.tts_provider == "elevenlabs":
        return TtsProgram(
            name="elevenlabs-tts",
            description="ElevenLabs text-to-speech",
            attribution=ELEVENLABS_ATTRIBUTION,
            installed=True,
            version="1.0",
            voices=[
                TtsVoice(
                    name=cfg.elevenlabs_voice_id,
                    description="ElevenLabs voice (configured via ELEVENLABS_VOICE_ID)",
                    attribution=ELEVENLABS_ATTRIBUTION,
                    installed=True,
                    version="1.0",
                    languages=["en"],
                )
            ],
        )
    return TtsProgram(
        name="grok-voice",
        description="xAI Grok text-to-speech",
        attribution=XAI_ATTRIBUTION,
        installed=True,
        version="1.0",
        voices=[
            TtsVoice(
                name=voice,
                description=f"xAI voice '{voice}'",
                attribution=XAI_ATTRIBUTION,
                installed=True,
                version="1.0",
                languages=["en"],
            )
            for voice in VOICES
        ],
    )


def build_asr_program(cfg: Config) -> AsrProgram:
    if cfg.stt_provider == "elevenlabs":
        return AsrProgram(
            name="elevenlabs-stt",
            description="ElevenLabs speech-to-text (Scribe)",
            attribution=ELEVENLABS_ATTRIBUTION,
            installed=True,
            version="1.0",
            models=[
                AsrModel(
                    name=cfg.elevenlabs_stt_model,
                    description="ElevenLabs /v1/speech-to-text",
                    attribution=ELEVENLABS_ATTRIBUTION,
                    installed=True,
                    version="1.0",
                    languages=["en"],
                )
            ],
        )
    return AsrProgram(
        name="grok-stt",
        description="xAI Grok speech-to-text",
        attribution=XAI_ATTRIBUTION,
        installed=True,
        version="1.0",
        models=[
            AsrModel(
                name="grok-stt",
                description="xAI /v1/stt",
                attribution=XAI_ATTRIBUTION,
                installed=True,
                version="1.0",
                languages=["en"],
            )
        ],
    )
