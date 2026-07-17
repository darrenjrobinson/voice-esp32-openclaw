"""Provider selection: map STT_PROVIDER / TTS_PROVIDER to client instances.

Both clients expose the same surface (stt / tts / tts_stream), so callers
hold one STT client and one TTS client and never care which provider is
behind them. Any combination works — e.g. xAI STT with ElevenLabs TTS.
"""
from __future__ import annotations

import aiohttp

from .config import Config
from .elevenlabs_client import ElevenLabsClient
from .xai_client import XAIClient

VoiceClient = XAIClient | ElevenLabsClient


def create_stt_client(cfg: Config, session: aiohttp.ClientSession) -> VoiceClient:
    return ElevenLabsClient(cfg, session) if cfg.stt_provider == "elevenlabs" else XAIClient(cfg, session)


def create_tts_client(cfg: Config, session: aiohttp.ClientSession) -> VoiceClient:
    return ElevenLabsClient(cfg, session) if cfg.tts_provider == "elevenlabs" else XAIClient(cfg, session)
