"""Energy-based end-of-speech detection for 16-bit mono PCM.

The ESP32 streams mic audio until the pipeline says the utterance is over
(the STT_END event), so the bridge needs its own end-of-speech decision.
Simple mean-|amplitude| gating is enough for a quiet room MVP; swap for
webrtcvad or xAI streaming STT endpointing if it proves too crude.
"""
from __future__ import annotations

from array import array


class EndOfSpeechDetector:
    def __init__(
        self,
        rate: int = 16000,
        width: int = 2,
        threshold: int = 500,
        silence_seconds: float = 0.8,
        max_seconds: float = 10.0,
        min_speech_seconds: float = 0.3,
    ):
        if width != 2:
            raise ValueError("EndOfSpeechDetector only supports 16-bit PCM")
        self._rate = rate
        self._width = width
        self._threshold = threshold
        self._silence_seconds = silence_seconds
        self._max_seconds = max_seconds
        self._min_speech_seconds = min_speech_seconds
        self._buffer = bytearray()
        self._speech_seconds = 0.0
        self._trailing_silence = 0.0
        self.speech_started = False
        # Level stats for threshold tuning against the real device/room
        self.max_level = 0.0
        self._level_sum = 0.0
        self._level_count = 0

    @property
    def pcm(self) -> bytes:
        return bytes(self._buffer)

    @property
    def duration_seconds(self) -> float:
        return len(self._buffer) / (self._rate * self._width)

    def stats(self) -> str:
        mean = self._level_sum / self._level_count if self._level_count else 0.0
        return (
            f"levels mean={mean:.0f} max={self.max_level:.0f} threshold={self._threshold} | "
            f"speech={self._speech_seconds:.1f}s trailing_silence={self._trailing_silence:.1f}s"
        )

    def feed(self, chunk: bytes) -> bool:
        """Append a PCM chunk. Returns True once the utterance is complete."""
        self._buffer.extend(chunk)
        samples = array("h", chunk[: len(chunk) - (len(chunk) % 2)])
        if not samples:
            return False
        level = sum(abs(s) for s in samples) / len(samples)
        seconds = len(samples) / self._rate
        self.max_level = max(self.max_level, level)
        self._level_sum += level
        self._level_count += 1

        if level >= self._threshold:
            self.speech_started = True
            self._speech_seconds += seconds
            self._trailing_silence = 0.0
        elif self.speech_started:
            self._trailing_silence += seconds

        if self.duration_seconds >= self._max_seconds:
            return True
        return (
            self.speech_started
            and self._speech_seconds >= self._min_speech_seconds
            and self._trailing_silence >= self._silence_seconds
        )
