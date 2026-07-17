"""Phase 0a: raw API round trip — WAV -> xAI STT -> OpenClaw -> xAI TTS -> play.

Usage:
  python scripts/phase0a_roundtrip.py --audio audio/test_query.wav [--no-play]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicepipe.audio import build_wav_bytes, parse_wav_bytes, read_wav_file
from voicepipe.config import load_config
from voicepipe.metrics import append_metrics
from voicepipe.orchestrator import Orchestrator


async def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 0a raw API round trip")
    parser.add_argument("--audio", required=True, help="input query WAV (16kHz mono s16le recommended)")
    parser.add_argument("--output-dir", default="out")
    parser.add_argument("--no-play", action="store_true", help="skip winsound playback")
    args = parser.parse_args()

    cfg = load_config()
    audio_in = read_wav_file(args.audio)
    print(
        f"Input: {args.audio} ({audio_in.duration_seconds:.1f}s, "
        f"{audio_in.rate} Hz, {audio_in.width * 8}-bit, {audio_in.channels}ch)"
    )

    async with aiohttp.ClientSession() as session:
        result = await Orchestrator(cfg, session).run_query(audio_in, phase="0a")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"response_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    # Rebuild the WAV: xAI's streamed writer leaves placeholder RIFF/data sizes
    tts_audio = parse_wav_bytes(result.tts_wav)
    out_path.write_bytes(build_wav_bytes(tts_audio))
    print()
    print(f"Transcript : {result.transcript}")
    print(f"Reply      : {result.reply}")
    print(
        f"TTS audio  : {out_path} ({len(result.tts_wav):,} bytes, {result.tts_content_type}, "
        f"{tts_audio.rate} Hz, {tts_audio.width * 8}-bit, {tts_audio.channels}ch, "
        f"{tts_audio.duration_seconds:.1f}s)"
    )
    print()
    print(result.metrics.table())
    metrics_file = append_metrics(result.metrics, args.output_dir)
    print(f"\nMetrics appended to {metrics_file}")

    if not args.no_play:
        import winsound

        print("Playing response...")
        winsound.PlaySound(str(out_path), winsound.SND_FILENAME)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
