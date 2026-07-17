"""WAV parse/build and PCM chunking helpers."""
from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from typing import Iterator


@dataclass
class PcmAudio:
    pcm: bytes
    rate: int
    width: int  # bytes per sample
    channels: int

    @property
    def duration_seconds(self) -> float:
        frame_size = self.width * self.channels
        if frame_size == 0:
            return 0.0
        return len(self.pcm) / frame_size / self.rate


def read_wav_file(path: str) -> PcmAudio:
    with wave.open(path, "rb") as wav:
        return PcmAudio(
            pcm=wav.readframes(wav.getnframes()),
            rate=wav.getframerate(),
            width=wav.getsampwidth(),
            channels=wav.getnchannels(),
        )


def parse_wav_bytes(data: bytes) -> PcmAudio:
    with wave.open(io.BytesIO(data), "rb") as wav:
        return PcmAudio(
            pcm=wav.readframes(wav.getnframes()),
            rate=wav.getframerate(),
            width=wav.getsampwidth(),
            channels=wav.getnchannels(),
        )


def build_wav_bytes(audio: PcmAudio) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(audio.channels)
        wav.setsampwidth(audio.width)
        wav.setframerate(audio.rate)
        wav.writeframes(audio.pcm)
    return buf.getvalue()


def normalize_pcm(audio: PcmAudio, target_peak: float = 0.90, max_gain: float = 20.0) -> PcmAudio:
    """Peak-normalize 16-bit PCM. xAI TTS output is quiet; boost it so the
    speaker plays at a healthy level regardless of device volume."""
    import numpy as np

    if audio.width != 2 or not audio.pcm:
        return audio
    samples = np.frombuffer(audio.pcm, dtype=np.int16).astype(np.float32)
    peak = float(np.max(np.abs(samples)))
    if peak == 0:
        return audio
    gain = min(target_peak * 32767.0 / peak, max_gain)
    if gain <= 1.0:
        return audio
    boosted = np.clip(samples * gain, -32768, 32767).astype(np.int16)
    return PcmAudio(pcm=boosted.tobytes(), rate=audio.rate, width=audio.width, channels=audio.channels)


def resample_pcm(audio: PcmAudio, target_rate: int) -> PcmAudio:
    """Linear-interpolation resample of 16-bit mono PCM. Meant for upsampling
    speech (e.g. ElevenLabs' 24 kHz -> the BOX-3's 48 kHz); downsampling works
    but has no anti-aliasing filter."""
    import numpy as np

    if audio.rate == target_rate or not audio.pcm:
        return audio
    if audio.width != 2 or audio.channels != 1:
        raise ValueError("resample_pcm requires 16-bit mono PCM")
    samples = np.frombuffer(audio.pcm, dtype=np.int16).astype(np.float64)
    positions = np.arange(0, len(samples) - 1 + 1e-9, audio.rate / target_rate)
    resampled = np.interp(positions, np.arange(len(samples)), samples)
    return PcmAudio(
        pcm=np.round(resampled).astype(np.int16).tobytes(),
        rate=target_rate,
        width=audio.width,
        channels=audio.channels,
    )


class StreamResampler:
    """Chunk-safe linear resampler for streamed 16-bit mono PCM.

    Keeps an odd-byte carry (s16le frame alignment) plus the last source
    sample and the fractional read position, so interpolation is continuous
    across chunk boundaries no matter how the network splits the stream.
    """

    def __init__(self, source_rate: int, target_rate: int):
        self._step = source_rate / target_rate
        self._carry = b""  # odd trailing byte from the previous chunk
        self._tail = None  # trailing source samples not yet consumed (np.ndarray)
        self._pos = 0.0  # fractional read position within (tail + new samples)

    def feed(self, chunk: bytes) -> bytes:
        import numpy as np

        data = self._carry + chunk
        aligned = len(data) - (len(data) % 2)
        self._carry = data[aligned:]
        if not aligned:
            return b""
        new = np.frombuffer(data[:aligned], dtype=np.int16).astype(np.float64)
        src = new if self._tail is None else np.concatenate([self._tail, new])
        if len(src) < 2:
            self._tail = src
            return b""
        positions = np.arange(self._pos, len(src) - 1 + 1e-9, self._step)
        out = np.interp(positions, np.arange(len(src)), src)
        # Keep the last source sample so the next chunk can interpolate from it
        consumed = len(src) - 1
        self._pos = (positions[-1] + self._step) - consumed if len(positions) else self._pos - consumed
        self._tail = src[consumed:]
        return np.round(out).astype(np.int16).tobytes()

    def flush(self) -> bytes:
        """Emit the final source sample if the read position still owes it."""
        import numpy as np

        if self._tail is None or len(self._tail) == 0 or self._pos > 0:
            return b""
        return np.round(self._tail[-1:]).astype(np.int16).tobytes()


def encode_flac(audio: PcmAudio, compression_level: int = 5) -> bytes:
    """Encode 16-bit PCM to FLAC (the only format the ESP32-S3-BOX-3
    media player accepts — probed live: flac/48000/mono only)."""
    import numpy as np
    import pyflac

    if audio.width != 2:
        raise ValueError("encode_flac requires 16-bit PCM")
    out = bytearray()

    def _write(buffer: bytes, num_bytes: int, num_samples: int, current_frame: int) -> None:
        out.extend(buffer)

    encoder = pyflac.StreamEncoder(
        write_callback=_write,
        sample_rate=audio.rate,
        compression_level=compression_level,
    )
    samples = np.frombuffer(audio.pcm, dtype=np.int16).reshape(-1, audio.channels)
    encoder.process(samples)
    encoder.finish()
    return bytes(out)


def chunk_pcm(audio: PcmAudio, samples_per_chunk: int = 1024) -> Iterator[bytes]:
    """Yield PCM in frame-aligned chunks of samples_per_chunk frames."""
    step = samples_per_chunk * audio.width * audio.channels
    for i in range(0, len(audio.pcm), step):
        yield audio.pcm[i : i + step]
