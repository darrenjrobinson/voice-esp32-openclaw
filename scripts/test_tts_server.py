"""Exercise the Wyoming TTS server the way Home Assistant does.

Connects to the TTS server (port 10200), verifies describe/info, sends a
`synthesize` request, and measures time-to-first-audio-chunk (the number
streaming TTS exists to improve) plus total synthesis time. Saves the audio.

Usage:
  python scripts/test_tts_server.py --text "Hello from Grok voice." [--voice eve] [--play]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.error import Error
from wyoming.info import Describe, Info
from wyoming.tts import Synthesize, SynthesizeVoice

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicepipe.audio import PcmAudio, build_wav_bytes
from voicepipe.config import load_config


async def run(args: argparse.Namespace) -> None:
    cfg = load_config(require_xai_key=False)
    host = args.server or cfg.wyoming_host
    port = args.port or cfg.tts_port

    t_start = time.perf_counter()
    async with AsyncTcpClient(host, port) as client:
        await client.write_event(Describe().event())
        event = await client.read_event()
        if event is None or not Info.is_type(event.type):
            raise RuntimeError(f"Expected info after describe, got: {event and event.type}")
        info = Info.from_event(event)
        voices = [v.name for p in info.tts for v in (p.voices or [])]
        print(f"Server info: tts=[{', '.join(p.name for p in info.tts)}] voices={voices}")

        voice = SynthesizeVoice(name=args.voice) if args.voice else None
        t_request = time.perf_counter()
        await client.write_event(Synthesize(text=args.text, voice=voice).event())

        pcm = bytearray()
        audio_format: AudioStart | None = None
        t_first_chunk: float | None = None
        while True:
            event = await client.read_event()
            if event is None:
                raise RuntimeError("Server disconnected before audio-stop")
            if Error.is_type(event.type):
                err = Error.from_event(event)
                raise RuntimeError(f"Server error: {err.text} ({err.code})")
            if AudioStart.is_type(event.type):
                audio_format = AudioStart.from_event(event)
            elif AudioChunk.is_type(event.type):
                if t_first_chunk is None:
                    t_first_chunk = time.perf_counter()
                pcm.extend(AudioChunk.from_event(event).audio)
            elif AudioStop.is_type(event.type):
                break
    t_done = time.perf_counter()

    if audio_format is None or not pcm:
        raise RuntimeError("No audio received")
    audio = PcmAudio(
        pcm=bytes(pcm),
        rate=audio_format.rate,
        width=audio_format.width,
        channels=audio_format.channels,
    )
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"tts_server_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    out_path.write_bytes(build_wav_bytes(audio))

    print(f"Text ({len(args.text)} chars): {args.text}")
    print(
        f"Audio: {out_path} ({audio.rate} Hz, {audio.width * 8}-bit, "
        f"{audio.channels}ch, {audio.duration_seconds:.1f}s)"
    )
    print(f"Time to first audio : {(t_first_chunk or t_done) - t_request:.3f}s")
    print(f"Total               : {t_done - t_request:.3f}s")

    if args.play:
        import winsound

        winsound.PlaySound(str(out_path), winsound.SND_FILENAME)


def main() -> None:
    parser = argparse.ArgumentParser(description="Wyoming TTS server test client (mimics HA)")
    parser.add_argument("--text", default="Hello, I am Marvin, speaking with Grok voice through Wyoming.")
    parser.add_argument("--voice", default=None, help="voice name to request (e.g. eve)")
    parser.add_argument("--server", default=None, help="override WYOMING_HOST")
    parser.add_argument("--port", type=int, default=None, help="override TTS_PORT")
    parser.add_argument("--output-dir", default="out")
    parser.add_argument("--play", action="store_true", help="play result via winsound")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
