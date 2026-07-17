"""Transport-agnostic pipeline core: PCM in -> STT -> OpenClaw -> TTS -> WAV out.

Shared by the Phase 0a round-trip CLI and the Phase 0b Wyoming server.
"""
from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from .audio import PcmAudio, build_wav_bytes, parse_wav_bytes
from .config import Config
from .metrics import TurnMetrics
from .openclaw_client import OpenClawClient
from .providers import create_stt_client, create_tts_client


@dataclass
class PipelineResult:
    transcript: str
    reply: str
    tts_wav: bytes  # complete WAV file bytes (self-describing header)
    tts_content_type: str
    metrics: TurnMetrics


class Orchestrator:
    def __init__(self, config: Config, session: aiohttp.ClientSession):
        self._cfg = config
        self._stt = create_stt_client(config, session)
        self._tts = create_tts_client(config, session)
        self._openclaw = OpenClawClient(config, session)

    async def run_query(self, audio: PcmAudio, phase: str = "0a") -> PipelineResult:
        metrics = TurnMetrics(phase=phase)

        wav_in = build_wav_bytes(audio)
        with metrics.stage(f"stt ({self._cfg.stt_provider})"):
            stt = await self._stt.stt(wav_in)
        metrics.transcript = stt.text
        metrics.stt_audio_seconds = stt.duration_seconds or audio.duration_seconds
        if not stt.text.strip():
            raise RuntimeError("STT returned an empty transcript")

        with metrics.stage("chat (OpenClaw)"):
            reply = await self._openclaw.chat(stt.text)
        metrics.reply_chars = len(reply)
        if not reply.strip():
            raise RuntimeError("OpenClaw returned an empty reply")

        with metrics.stage(f"tts ({self._cfg.tts_provider})"):
            tts = await self._tts.tts(reply)
        metrics.tts_audio_seconds = tts.duration_seconds or parse_wav_bytes(tts.audio).duration_seconds

        return PipelineResult(
            transcript=stt.text,
            reply=reply,
            tts_wav=tts.audio,
            tts_content_type=tts.content_type,
            metrics=metrics,
        )
