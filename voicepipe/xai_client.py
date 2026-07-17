"""xAI Grok Voice API client: STT (POST /v1/stt) and TTS (POST /v1/tts).

API shapes verified against docs.x.ai 2026-07-17 and live probing:
  - TTS body: {text, language, voice_id, output_format: {codec, sample_rate}}
  - TTS response: raw audio bytes (Content-Type: audio/wav) in practice,
    though the docs describe JSON with base64 "audio" — both are handled
  - STT is multipart/form-data with a "file" field; "language" optional

Smoke tests:
  python -m voicepipe.xai_client --stt audio/test_query.wav
  python -m voicepipe.xai_client --tts "Hello, I am Marvin."
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator

import aiohttp

from .config import Config

log = logging.getLogger("voicepipe.xai")


@dataclass
class STTResult:
    text: str
    language: str
    duration_seconds: float
    raw: dict


@dataclass
class TTSResult:
    audio: bytes
    content_type: str
    duration_seconds: float


class XAIClient:
    def __init__(self, config: Config, session: aiohttp.ClientSession):
        self._cfg = config
        self._session = session

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._cfg.xai_api_key}"}

    async def stt(self, audio_bytes: bytes, filename: str = "query.wav") -> STTResult:
        form = aiohttp.FormData()
        form.add_field("file", audio_bytes, filename=filename, content_type="audio/wav")
        if self._cfg.xai_tts_language and self._cfg.xai_tts_language != "auto":
            form.add_field("language", self._cfg.xai_tts_language)

        async with self._session.post(
            f"{self._cfg.xai_base_url}/v1/stt",
            headers=self._headers,
            data=form,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                raise RuntimeError(f"xAI STT HTTP {resp.status}: {body}")

        result = STTResult(
            text=body.get("text", ""),
            language=body.get("language", ""),
            duration_seconds=float(body.get("duration", 0.0)),
            raw=body,
        )
        log.info("STT: %.1fs audio -> %r", result.duration_seconds, result.text)
        return result

    async def tts_stream(
        self,
        text: str,
        codec: str = "pcm",
        sample_rate: int | None = None,
        voice: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Streaming TTS over wss://api.x.ai/v1/tts — yields audio chunks as they
        are synthesized, so playback can start long before synthesis finishes.

        Config goes in query params; messages are text.delta/text.done in,
        audio.delta (base64) / audio.done out. codec=pcm is raw s16le mono
        (verified live: the WAV wrapper the batch endpoint adds declares 16-bit mono).
        """
        params = {
            "voice": voice or self._cfg.xai_voice,
            "language": self._cfg.xai_tts_language,
            "codec": codec,
            "sample_rate": str(sample_rate or self._cfg.tts_sample_rate),
            # Smaller first chunk -> lower time-to-first-audio (minor quality
            # tradeoff at chunk boundaries per docs)
            "optimize_streaming_latency": "1",
        }
        url = self._cfg.xai_base_url.replace("https://", "wss://", 1) + "/v1/tts"
        total = 0
        async with self._session.ws_connect(
            url, params=params, headers=self._headers, heartbeat=30
        ) as ws:
            await ws.send_json({"type": "text.delta", "delta": text})
            await ws.send_json({"type": "text.done"})
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    raise RuntimeError(f"xAI TTS stream: unexpected WS message {msg.type}")
                data = json.loads(msg.data)
                kind = data.get("type")
                if kind == "audio.delta":
                    chunk = base64.b64decode(data["delta"])
                    total += len(chunk)
                    yield chunk
                elif kind == "audio.done":
                    break
                elif kind == "error":
                    raise RuntimeError(f"xAI TTS stream error: {data}")
        log.info("TTS stream: %d chars -> %d bytes (%s)", len(text), total, codec)

    async def tts(
        self,
        text: str,
        codec: str = "wav",
        sample_rate: int | None = None,
        voice: str | None = None,
    ) -> TTSResult:
        payload = {
            "text": text,
            "language": self._cfg.xai_tts_language,
            "voice_id": voice or self._cfg.xai_voice,
            "output_format": {
                "codec": codec,
                "sample_rate": sample_rate or self._cfg.tts_sample_rate,
            },
        }
        async with self._session.post(
            f"{self._cfg.xai_base_url}/v1/tts",
            headers={**self._headers, "Content-Type": "application/json"},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if resp.status != 200:
                raise RuntimeError(f"xAI TTS HTTP {resp.status}: {await resp.text()}")
            if content_type.startswith("application/json"):
                body = await resp.json()
                audio = base64.b64decode(body["audio"])
                content_type = body.get("content_type", content_type)
                duration = float(body.get("duration", 0.0))
            else:
                audio = await resp.read()
                duration = 0.0  # computed downstream from the WAV header

        result = TTSResult(
            audio=audio,
            content_type=content_type,
            duration_seconds=duration,
        )
        log.info(
            "TTS: %d chars -> %d bytes (%s, %.1fs)",
            len(text), len(result.audio), result.content_type, result.duration_seconds,
        )
        return result


async def _smoke() -> None:
    import argparse

    from .config import load_config

    parser = argparse.ArgumentParser(description="xAI client smoke test")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stt", metavar="WAV", help="transcribe a WAV file")
    group.add_argument("--tts", metavar="TEXT", help="synthesize text to out/smoke_tts.wav")
    args = parser.parse_args()

    cfg = load_config()
    async with aiohttp.ClientSession() as session:
        client = XAIClient(cfg, session)
        if args.stt:
            with open(args.stt, "rb") as f:
                result = await client.stt(f.read())
            print(f"Transcript ({result.language}, {result.duration_seconds:.1f}s): {result.text}")
        else:
            result = await client.tts(args.tts)
            from pathlib import Path

            out = Path("out")
            out.mkdir(exist_ok=True)
            path = out / "smoke_tts.wav"
            path.write_bytes(result.audio)
            print(f"Saved {len(result.audio):,} bytes ({result.content_type}) to {path}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_smoke())
