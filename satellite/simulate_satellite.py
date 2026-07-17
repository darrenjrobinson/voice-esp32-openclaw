"""Phase 0b: Wyoming satellite emulator — plays the role of the future ESP32.

Sends what a real satellite sends (run-pipeline, then mic audio), receives
transcript/synthesize/TTS audio, writes the response WAV using the format
announced in the server's audio-start event, and reports end-to-end latency.

Usage:
  python satellite/simulate_satellite.py --audio audio/test_query.wav [--realtime] [--no-play]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from wyoming.asr import Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.error import Error
from wyoming.info import Describe, Info
from wyoming.pipeline import PipelineStage, RunPipeline
from wyoming.snd import Played
from wyoming.tts import Synthesize

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicepipe.audio import PcmAudio, build_wav_bytes, chunk_pcm, read_wav_file
from voicepipe.config import load_config

CHUNK_SAMPLES = 1024


async def run(args: argparse.Namespace) -> None:
    cfg = load_config(require_xai_key=False)
    host = args.server or cfg.wyoming_host
    port = args.port or cfg.wyoming_port
    query = read_wav_file(args.audio)
    print(f"Query: {args.audio} ({query.duration_seconds:.1f}s, {query.rate} Hz)")

    t_start = time.perf_counter()
    async with AsyncTcpClient(host, port) as client:
        # Handshake: confirm the server describes ASR + TTS programs
        await client.write_event(Describe().event())
        event = await client.read_event()
        if event is None or not Info.is_type(event.type):
            raise RuntimeError(f"Expected info after describe, got: {event and event.type}")
        info = Info.from_event(event)
        asr = ", ".join(p.name for p in info.asr) or "none"
        tts = ", ".join(p.name for p in info.tts) or "none"
        print(f"Server info: asr=[{asr}] tts=[{tts}]")

        # Mirror a real satellite: announce the pipeline run, then stream mic audio
        await client.write_event(
            RunPipeline(start_stage=PipelineStage.ASR, end_stage=PipelineStage.TTS).event()
        )
        await client.write_event(
            AudioStart(rate=query.rate, width=query.width, channels=query.channels).event()
        )
        chunk_seconds = CHUNK_SAMPLES / query.rate
        for chunk in chunk_pcm(query, samples_per_chunk=CHUNK_SAMPLES):
            await client.write_event(
                AudioChunk(audio=chunk, rate=query.rate, width=query.width, channels=query.channels).event()
            )
            if args.realtime:
                await asyncio.sleep(chunk_seconds)  # emulate ESP32 mic cadence
        await client.write_event(AudioStop().event())
        t_sent = time.perf_counter()

        # Collect the server's response events
        response_pcm = bytearray()
        response_format: AudioStart | None = None
        transcript = reply = ""
        while True:
            event = await client.read_event()
            if event is None:
                raise RuntimeError("Server disconnected before audio-stop")
            if Error.is_type(event.type):
                err = Error.from_event(event)
                raise RuntimeError(f"Server error: {err.text} ({err.code})")
            if Transcript.is_type(event.type):
                transcript = Transcript.from_event(event).text
                print(f"Transcript : {transcript}")
            elif Synthesize.is_type(event.type):
                reply = Synthesize.from_event(event).text
                print(f"Reply      : {reply}")
            elif AudioStart.is_type(event.type):
                response_format = AudioStart.from_event(event)
            elif AudioChunk.is_type(event.type):
                response_pcm.extend(AudioChunk.from_event(event).audio)
            elif AudioStop.is_type(event.type):
                break

        await client.write_event(Played().event())

    t_done = time.perf_counter()
    if response_format is None or not response_pcm:
        raise RuntimeError("No TTS audio received")

    audio_out = PcmAudio(
        pcm=bytes(response_pcm),
        rate=response_format.rate,
        width=response_format.width,
        channels=response_format.channels,
    )
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"satellite_response_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    out_path.write_bytes(build_wav_bytes(audio_out))

    print(
        f"TTS audio  : {out_path} ({len(audio_out.pcm):,} PCM bytes, {audio_out.rate} Hz, "
        f"{audio_out.width * 8}-bit, {audio_out.channels}ch, {audio_out.duration_seconds:.1f}s)"
    )
    print(f"End-to-end : {t_done - t_start:.3f}s total ({t_sent - t_start:.3f}s sending audio)")
    print("Compare against the server's per-stage metrics in out/metrics.jsonl "
          "to compute Wyoming transport overhead.")

    if not args.no_play:
        import winsound

        print("Playing response...")
        winsound.PlaySound(str(out_path), winsound.SND_FILENAME)


def main() -> None:
    parser = argparse.ArgumentParser(description="Wyoming satellite emulator")
    parser.add_argument("--audio", required=True, help="query WAV to send")
    parser.add_argument("--server", default=None, help="override WYOMING_HOST")
    parser.add_argument("--port", type=int, default=None, help="override WYOMING_PORT")
    parser.add_argument("--output-dir", default="out")
    parser.add_argument("--realtime", action="store_true", help="pace chunks at mic cadence")
    parser.add_argument("--no-play", action="store_true", help="skip winsound playback")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
