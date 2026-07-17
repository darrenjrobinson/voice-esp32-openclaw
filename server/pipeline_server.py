"""Phase 0b: Wyoming pipeline server — satellite audio in, Grok voice out.

Event flow per turn (client = satellite/emulator):
  client: describe?                -> server: info (asr + tts programs)
  client: run-pipeline (optional)  -> logged; tolerated if absent
  client: audio-start, audio-chunk..., audio-stop
  server: transcript, synthesize, audio-start, audio-chunk..., audio-stop
  client: played (logged)

MVP contract: the client sends audio-stop to mark end of utterance
(no server-side VAD yet — revisit at Phase 2 for real satellite firmware).

Run:
  python -m server.pipeline_server
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from functools import partial

import aiohttp
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.ping import Ping, Pong
from wyoming.pipeline import RunPipeline
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.snd import Played
from wyoming.tts import Synthesize

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.wyoming_common import build_asr_program, build_tts_program
from voicepipe.audio import PcmAudio, chunk_pcm, parse_wav_bytes
from voicepipe.config import Config, load_config
from voicepipe.metrics import append_metrics
from voicepipe.orchestrator import Orchestrator

log = logging.getLogger("voicepipe.server")


def build_info(cfg: Config) -> Info:
    return Info(asr=[build_asr_program(cfg)], tts=[build_tts_program(cfg)])


class PipelineEventHandler(AsyncEventHandler):
    def __init__(self, config: Config, orchestrator: Orchestrator, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cfg = config
        self._orchestrator = orchestrator
        self._buffer = bytearray()
        self._rate = 16000
        self._width = 2
        self._channels = 1

    async def handle_event(self, event: Event) -> bool:
        try:
            return await self._handle(event)
        except Exception as exc:  # noqa: BLE001 — never crash the accept loop
            log.exception("Turn failed")
            await self.write_event(Error(text=str(exc), code="pipeline-error").event())
            return True

    async def _handle(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(build_info(self._cfg).event())
        elif Ping.is_type(event.type):
            await self.write_event(Pong().event())
        elif RunPipeline.is_type(event.type):
            run = RunPipeline.from_event(event)
            log.info("run-pipeline: %s -> %s", run.start_stage, run.end_stage)
        elif Transcribe.is_type(event.type):
            log.debug("transcribe hint: %s", event.data)
        elif AudioStart.is_type(event.type):
            start = AudioStart.from_event(event)
            self._buffer.clear()
            self._rate, self._width, self._channels = start.rate, start.width, start.channels
            log.info("audio-start: %d Hz, %d-bit, %dch", start.rate, start.width * 8, start.channels)
        elif AudioChunk.is_type(event.type):
            self._buffer.extend(AudioChunk.from_event(event).audio)
        elif AudioStop.is_type(event.type):
            await self._run_turn()
        elif Played.is_type(event.type):
            log.info("satellite reported playback complete")
        else:
            log.debug("ignoring event: %s", event.type)
        return True

    async def _run_turn(self) -> None:
        if not self._buffer:
            raise RuntimeError("audio-stop received with no buffered audio")

        query = PcmAudio(
            pcm=bytes(self._buffer),
            rate=self._rate,
            width=self._width,
            channels=self._channels,
        )
        log.info("running pipeline on %.1fs of audio", query.duration_seconds)
        result = await self._orchestrator.run_query(query, phase="0b")

        await self.write_event(Transcript(text=result.transcript).event())
        await self.write_event(Synthesize(text=result.reply).event())

        tts_audio = parse_wav_bytes(result.tts_wav)
        await self.write_event(
            AudioStart(rate=tts_audio.rate, width=tts_audio.width, channels=tts_audio.channels).event()
        )
        for chunk in chunk_pcm(tts_audio, samples_per_chunk=1024):
            await self.write_event(
                AudioChunk(
                    audio=chunk, rate=tts_audio.rate, width=tts_audio.width, channels=tts_audio.channels
                ).event()
            )
        await self.write_event(AudioStop().event())

        metrics_file = append_metrics(result.metrics)
        log.info("turn complete (%.2fs total) — metrics -> %s", result.metrics.total_seconds, metrics_file)
        self._buffer.clear()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Wyoming Grok pipeline server")
    parser.add_argument("--host", default=None, help="override WYOMING_HOST")
    parser.add_argument("--port", type=int, default=None, help="override WYOMING_PORT")
    args = parser.parse_args()

    cfg = load_config()
    host = args.host or cfg.wyoming_host
    port = args.port or cfg.wyoming_port

    async with aiohttp.ClientSession() as session:
        orchestrator = Orchestrator(cfg, session)
        server = AsyncServer.from_uri(f"tcp://{host}:{port}")
        log.info("Wyoming pipeline server listening on tcp://%s:%d", host, port)
        await server.run(partial(PipelineEventHandler, cfg, orchestrator))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
