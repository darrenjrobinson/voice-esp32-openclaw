"""ElevenLabs API client: STT (POST /v1/speech-to-text) and TTS
(POST /v1/text-to-speech/{voice_id}).

API shapes (docs.elevenlabs.io, 2026-07):
  - Auth is an `xi-api-key` header (not a Bearer token)
  - TTS body: {text, model_id}; output format goes in the `output_format`
    query param (`pcm_24000` = raw s16le mono). The /stream variant returns
    a chunked body of the same raw audio.
  - PCM rates above 24000 need a paid tier (44100 requires Pro), so we request
    ELEVENLABS_OUTPUT_RATE (default 24000) and resample locally to whatever
    the caller asked for (the BOX-3 wants 48000).
  - STT is multipart/form-data with `file` + `model_id`; the response has
    `text` and `language_code` but no duration (callers fall back to the
    locally measured audio length).

Smoke tests:
  python -m voicepipe.elevenlabs_client --stt audio/test_query.wav
  python -m voicepipe.elevenlabs_client --tts "Hello, I am Marvin."
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import aiohttp

from .audio import PcmAudio, StreamResampler, build_wav_bytes, resample_pcm
from .config import Config
from .xai_client import STTResult, TTSResult

log = logging.getLogger("voicepipe.elevenlabs")


class ElevenLabsClient:
    """Drop-in provider with the same stt/tts/tts_stream surface as XAIClient."""

    def __init__(self, config: Config, session: aiohttp.ClientSession):
        self._cfg = config
        self._session = session

    @property
    def _headers(self) -> dict:
        return {"xi-api-key": self._cfg.elevenlabs_api_key}

    async def stt(self, audio_bytes: bytes, filename: str = "query.wav") -> STTResult:
        form = aiohttp.FormData()
        form.add_field("file", audio_bytes, filename=filename, content_type="audio/wav")
        form.add_field("model_id", self._cfg.elevenlabs_stt_model)
        if self._cfg.xai_tts_language and self._cfg.xai_tts_language != "auto":
            form.add_field("language_code", self._cfg.xai_tts_language)

        async with self._session.post(
            f"{self._cfg.elevenlabs_base_url}/v1/speech-to-text",
            headers=self._headers,
            data=form,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                raise RuntimeError(f"ElevenLabs STT HTTP {resp.status}: {body}")

        result = STTResult(
            text=body.get("text", ""),
            language=body.get("language_code", ""),
            duration_seconds=0.0,  # not reported; callers use the local audio length
            raw=body,
        )
        log.info("STT: -> %r", result.text)
        return result

    def _tts_url(self, voice: str | None, stream: bool) -> str:
        voice_id = voice or self._cfg.elevenlabs_voice_id
        suffix = "/stream" if stream else ""
        return f"{self._cfg.elevenlabs_base_url}/v1/text-to-speech/{voice_id}{suffix}"

    async def _tts_request(self, text: str, voice: str | None, stream: bool) -> aiohttp.ClientResponse:
        """POST the synthesis request; one retry on 429 (free tier allows only
        2 concurrent requests)."""
        url = self._tts_url(voice, stream)
        params = {"output_format": f"pcm_{self._cfg.elevenlabs_output_rate}"}
        payload = {"text": text, "model_id": self._cfg.elevenlabs_tts_model}
        for attempt in (1, 2):
            resp = await self._session.post(
                url,
                params=params,
                headers={**self._headers, "Content-Type": "application/json"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            )
            if resp.status == 429 and attempt == 1:
                delay = float(resp.headers.get("Retry-After", 1) or 1)
                log.warning("ElevenLabs TTS 429; retrying in %.1fs", delay)
                resp.release()
                await asyncio.sleep(delay)
                continue
            if resp.status != 200:
                detail = await resp.text()
                resp.release()
                raise RuntimeError(f"ElevenLabs TTS HTTP {resp.status}: {detail}")
            return resp
        raise AssertionError("unreachable")

    async def tts_stream(
        self,
        text: str,
        codec: str = "pcm",
        sample_rate: int | None = None,
        voice: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Streaming TTS — yields raw s16le mono PCM at the requested rate,
        resampling from ELEVENLABS_OUTPUT_RATE on the fly when they differ."""
        if codec != "pcm":
            raise ValueError(f"ElevenLabs streaming supports codec='pcm' only, not {codec!r}")
        target = sample_rate or self._cfg.tts_sample_rate
        source = self._cfg.elevenlabs_output_rate
        resampler = StreamResampler(source, target) if source != target else None

        resp = await self._tts_request(text, voice, stream=True)
        total = 0
        try:
            async with resp:
                async for chunk in resp.content.iter_any():
                    out = resampler.feed(chunk) if resampler else chunk
                    if out:
                        total += len(out)
                        yield out
            if resampler:
                out = resampler.flush()
                if out:
                    total += len(out)
                    yield out
        finally:
            log.info("TTS stream: %d chars -> %d bytes (pcm @ %d Hz)", len(text), total, target)

    async def tts(
        self,
        text: str,
        codec: str = "wav",
        sample_rate: int | None = None,
        voice: str | None = None,
    ) -> TTSResult:
        target = sample_rate or self._cfg.tts_sample_rate
        source = self._cfg.elevenlabs_output_rate

        resp = await self._tts_request(text, voice, stream=False)
        async with resp:
            raw = await resp.read()
        pcm = resample_pcm(PcmAudio(pcm=raw, rate=source, width=2, channels=1), target)

        if codec == "wav":
            audio, content_type = build_wav_bytes(pcm), "audio/wav"
        elif codec == "pcm":
            audio, content_type = pcm.pcm, "audio/L16"
        else:
            raise ValueError(f"ElevenLabs TTS supports codec 'wav' or 'pcm', not {codec!r}")

        result = TTSResult(
            audio=audio,
            content_type=content_type,
            duration_seconds=pcm.duration_seconds,
        )
        log.info(
            "TTS: %d chars -> %d bytes (%s, %.1fs)",
            len(text), len(result.audio), result.content_type, result.duration_seconds,
        )
        return result


async def _smoke() -> None:
    import argparse

    from .config import load_config

    parser = argparse.ArgumentParser(description="ElevenLabs client smoke test")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stt", metavar="WAV", help="transcribe a WAV file")
    group.add_argument("--tts", metavar="TEXT", help="synthesize text to out/smoke_tts_elevenlabs.wav")
    parser.add_argument("--rate", type=int, default=None, help="target sample rate (--tts)")
    args = parser.parse_args()

    cfg = load_config()
    async with aiohttp.ClientSession() as session:
        client = ElevenLabsClient(cfg, session)
        if args.stt:
            with open(args.stt, "rb") as f:
                result = await client.stt(f.read())
            print(f"Transcript ({result.language}): {result.text}")
        else:
            result = await client.tts(args.tts, sample_rate=args.rate)
            from pathlib import Path

            out = Path("out")
            out.mkdir(exist_ok=True)
            path = out / "smoke_tts_elevenlabs.wav"
            path.write_bytes(result.audio)
            print(f"Saved {len(result.audio):,} bytes ({result.content_type}) to {path}")


if __name__ == "__main__":
    asyncio.run(_smoke())
