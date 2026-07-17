"""Phase 1: Wyoming TTS server — Home Assistant's TTS provider for Grok voice.

HA's Wyoming integration connects here (port 10200), sends `synthesize`
with the reply text, and receives audio-start / audio-chunk... / audio-stop.
Registers exactly like Piper does, so the pipeline TTS is a config swap.

By default synthesis is STREAMED from xAI (wss /v1/tts, codec=pcm): audio
chunks are forwarded to HA as they arrive, so time-to-first-audio is a few
hundred ms instead of the full batch synthesis time. Set TTS_STREAMING=false
to fall back to batch WAV mode; a streaming failure also falls back per-request.

Run:
  python -m server.tts_server
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time
from functools import partial

import aiohttp
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.ping import Ping, Pong
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.tts import Synthesize

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.wyoming_common import build_tts_program
from voicepipe.audio import parse_wav_bytes
from voicepipe.config import Config, load_config
from voicepipe.providers import VoiceClient, create_tts_client

log = logging.getLogger("voicepipe.tts_server")

SAMPLE_WIDTH = 2  # both providers deliver s16le mono PCM
CHANNELS = 1


class TtsEventHandler(AsyncEventHandler):
    def __init__(self, config: Config, tts: VoiceClient, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cfg = config
        self._tts = tts

    async def handle_event(self, event: Event) -> bool:
        try:
            return await self._handle(event)
        except Exception as exc:  # noqa: BLE001 — never crash the accept loop
            log.exception("Synthesis failed")
            await self.write_event(Error(text=str(exc), code="tts-error").event())
            return True

    async def _handle(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(Info(tts=[build_tts_program(self._cfg)]).event())
        elif Ping.is_type(event.type):
            await self.write_event(Pong().event())
        elif Synthesize.is_type(event.type):
            synthesize = Synthesize.from_event(event)
            voice = synthesize.voice.name if synthesize.voice and synthesize.voice.name else None
            await self._synthesize(synthesize.text, voice)
        else:
            log.debug("ignoring event: %s", event.type)
        return True

    async def _synthesize(self, text: str, voice: str | None) -> None:
        started = time.perf_counter()
        default_voice = (
            self._cfg.elevenlabs_voice_id
            if self._cfg.tts_provider == "elevenlabs"
            else self._cfg.xai_voice
        )
        log.info("synthesize: %d chars (voice=%s)", len(text), voice or default_voice)

        if self._cfg.tts_streaming:
            try:
                await self._synthesize_streaming(text, voice, started)
                return
            except Exception:  # noqa: BLE001
                log.exception("Streaming TTS failed; falling back to batch")

        await self._synthesize_batch(text, voice, started)

    async def _synthesize_streaming(self, text: str, voice: str | None, started: float) -> None:
        rate = self._cfg.tts_sample_rate
        await self.write_event(AudioStart(rate=rate, width=SAMPLE_WIDTH, channels=CHANNELS).event())
        first_chunk_at: float | None = None
        remainder = b""
        total = 0
        async for chunk in self._tts.tts_stream(text, codec="pcm", voice=voice):
            if first_chunk_at is None:
                first_chunk_at = time.perf_counter()
            data = remainder + chunk
            aligned = len(data) - (len(data) % SAMPLE_WIDTH)  # keep frames whole
            remainder = data[aligned:]
            if aligned:
                total += aligned
                await self.write_event(
                    AudioChunk(audio=data[:aligned], rate=rate, width=SAMPLE_WIDTH, channels=CHANNELS).event()
                )
        if remainder:
            log.warning("dropping %d trailing non-frame-aligned byte(s)", len(remainder))
        await self.write_event(AudioStop().event())
        log.info(
            "streamed %d bytes (%.1fs audio): first audio in %.3fs, done in %.3fs",
            total,
            total / (rate * SAMPLE_WIDTH * CHANNELS),
            (first_chunk_at or started) - started,
            time.perf_counter() - started,
        )

    async def _synthesize_batch(self, text: str, voice: str | None, started: float) -> None:
        result = await self._tts.tts(text, codec="wav", voice=voice)
        audio = parse_wav_bytes(result.audio)
        await self.write_event(
            AudioStart(rate=audio.rate, width=audio.width, channels=audio.channels).event()
        )
        step = 1024 * audio.width * audio.channels
        for i in range(0, len(audio.pcm), step):
            await self.write_event(
                AudioChunk(
                    audio=audio.pcm[i : i + step],
                    rate=audio.rate,
                    width=audio.width,
                    channels=audio.channels,
                ).event()
            )
        await self.write_event(AudioStop().event())
        log.info(
            "batch synthesized %.1fs audio in %.3fs",
            audio.duration_seconds,
            time.perf_counter() - started,
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Wyoming Grok TTS server (HA integration)")
    parser.add_argument("--host", default=None, help="override WYOMING_HOST")
    parser.add_argument("--port", type=int, default=None, help="override TTS_PORT")
    args = parser.parse_args()

    cfg = load_config()
    host = args.host or cfg.wyoming_host
    port = args.port or cfg.tts_port

    async with aiohttp.ClientSession() as session:
        tts = create_tts_client(cfg, session)
        server = AsyncServer.from_uri(f"tcp://{host}:{port}")
        voice = cfg.elevenlabs_voice_id if cfg.tts_provider == "elevenlabs" else cfg.xai_voice
        log.info(
            "Wyoming TTS server listening on tcp://%s:%d (provider=%s, streaming=%s, voice=%s, %d Hz)",
            host, port, cfg.tts_provider, cfg.tts_streaming, voice, cfg.tts_sample_rate,
        )
        await server.run(partial(TtsEventHandler, cfg, tts))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
