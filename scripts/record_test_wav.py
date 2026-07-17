"""Record a 16 kHz mono s16le test WAV on Windows via sounddevice/WASAPI.

Usage:
  python scripts/record_test_wav.py --seconds 5 --output audio/test_query.wav
"""
from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

RATE = 16000
CHANNELS = 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a 16kHz mono test WAV")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--output", default="audio/test_query.wav")
    args = parser.parse_args()

    print(f"Recording {args.seconds:.0f}s at {RATE} Hz mono... speak now.")
    frames = sd.rec(int(args.seconds * RATE), samplerate=RATE, channels=CHANNELS, dtype=np.int16)
    sd.wait()
    print("Done.")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(frames.tobytes())

    peak = int(np.abs(frames).max()) if frames.size else 0
    print(f"Saved {out} ({out.stat().st_size:,} bytes, peak amplitude {peak}/32767)")
    if peak < 500:
        print("WARNING: very low signal — check the microphone input device.")


if __name__ == "__main__":
    main()
