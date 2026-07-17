"""Per-stage latency timing, cost model, and JSONL metrics log.

Cost model (docs.x.ai pricing, confirmed 2026-07-17):
  STT: $0.10 per hour of audio
  TTS: $15.00 per 1M characters
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STT_USD_PER_HOUR = 0.10
TTS_USD_PER_MILLION_CHARS = 15.00


@dataclass
class TurnMetrics:
    phase: str  # "0a" | "0b"
    stages: dict[str, float] = field(default_factory=dict)  # stage -> seconds
    transcript: str = ""
    reply_chars: int = 0
    stt_audio_seconds: float = 0.0
    tts_audio_seconds: float = 0.0
    extra: dict = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = time.perf_counter() - start

    @property
    def total_seconds(self) -> float:
        return sum(self.stages.values())

    @property
    def stt_cost_usd(self) -> float:
        return (self.stt_audio_seconds / 3600.0) * STT_USD_PER_HOUR

    @property
    def tts_cost_usd(self) -> float:
        return (self.reply_chars / 1_000_000.0) * TTS_USD_PER_MILLION_CHARS

    @property
    def total_cost_usd(self) -> float:
        return self.stt_cost_usd + self.tts_cost_usd

    def table(self) -> str:
        lines = [f"{'Stage':<22}{'Seconds':>9}", "-" * 31]
        for name, secs in self.stages.items():
            lines.append(f"{name:<22}{secs:>9.3f}")
        lines.append("-" * 31)
        lines.append(f"{'TOTAL':<22}{self.total_seconds:>9.3f}")
        lines.append("")
        lines.append(
            f"Cost: STT ${self.stt_cost_usd:.6f} ({self.stt_audio_seconds:.1f}s audio)"
            f" + TTS ${self.tts_cost_usd:.6f} ({self.reply_chars} chars)"
            f" = ${self.total_cost_usd:.6f}/query"
        )
        return "\n".join(lines)

    def to_record(self) -> dict:
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "phase": self.phase,
            "stages": {k: round(v, 4) for k, v in self.stages.items()},
            "total_seconds": round(self.total_seconds, 4),
            "transcript": self.transcript,
            "reply_chars": self.reply_chars,
            "stt_audio_seconds": round(self.stt_audio_seconds, 2),
            "tts_audio_seconds": round(self.tts_audio_seconds, 2),
            "stt_cost_usd": round(self.stt_cost_usd, 6),
            "tts_cost_usd": round(self.tts_cost_usd, 6),
            "total_cost_usd": round(self.total_cost_usd, 6),
            **self.extra,
        }


def append_metrics(metrics: TurnMetrics, out_dir: str = "out") -> Path:
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)
    file = path / "metrics.jsonl"
    with file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(metrics.to_record(), ensure_ascii=False) + "\n")
    return file
