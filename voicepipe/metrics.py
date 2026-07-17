"""Per-stage latency timing, cost model, and JSONL metrics log.

Cost model (provider pricing confirmed 2026-07-17):
  xAI        STT $0.10/hour of audio; TTS $15.00 per 1M characters
  ElevenLabs STT (Scribe) $0.22/hour; TTS billed in credits —
             0.5 credits/char on flash/turbo models, 1 credit/char otherwise,
             ~$0.10 per 1k credits on API billing (subscription tiers instead
             draw credits from the plan's monthly allowance, e.g. free = 10k)
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

XAI_STT_USD_PER_HOUR = 0.10
XAI_TTS_USD_PER_MILLION_CHARS = 15.00
ELEVENLABS_STT_USD_PER_HOUR = 0.22
ELEVENLABS_USD_PER_1K_CREDITS = 0.10
# Flash/Turbo models are half-price per character
ELEVENLABS_DISCOUNTED_MODEL_KEYWORDS = ("flash", "turbo")


@dataclass
class TurnMetrics:
    phase: str  # "0a" | "0b"
    stages: dict[str, float] = field(default_factory=dict)  # stage -> seconds
    transcript: str = ""
    reply_chars: int = 0
    stt_audio_seconds: float = 0.0
    tts_audio_seconds: float = 0.0
    stt_provider: str = "xai"
    tts_provider: str = "xai"
    tts_model: str = ""  # used for the ElevenLabs flash/turbo credit discount
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
        rate = (
            ELEVENLABS_STT_USD_PER_HOUR
            if self.stt_provider == "elevenlabs"
            else XAI_STT_USD_PER_HOUR
        )
        return (self.stt_audio_seconds / 3600.0) * rate

    @property
    def tts_credits(self) -> float:
        """ElevenLabs credits consumed by the reply (0 for xAI)."""
        if self.tts_provider != "elevenlabs":
            return 0.0
        per_char = 0.5 if any(k in self.tts_model for k in ELEVENLABS_DISCOUNTED_MODEL_KEYWORDS) else 1.0
        return self.reply_chars * per_char

    @property
    def tts_cost_usd(self) -> float:
        if self.tts_provider == "elevenlabs":
            # API billing equivalent; subscription tiers draw from the monthly allowance
            return (self.tts_credits / 1000.0) * ELEVENLABS_USD_PER_1K_CREDITS
        return (self.reply_chars / 1_000_000.0) * XAI_TTS_USD_PER_MILLION_CHARS

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
        credits = f", {self.tts_credits:.0f} credits" if self.tts_provider == "elevenlabs" else ""
        lines.append(
            f"Cost: STT[{self.stt_provider}] ${self.stt_cost_usd:.6f} ({self.stt_audio_seconds:.1f}s audio)"
            f" + TTS[{self.tts_provider}] ${self.tts_cost_usd:.6f} ({self.reply_chars} chars{credits})"
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
            "stt_provider": self.stt_provider,
            "tts_provider": self.tts_provider,
            "stt_cost_usd": round(self.stt_cost_usd, 6),
            "tts_cost_usd": round(self.tts_cost_usd, 6),
            **({"tts_credits": round(self.tts_credits, 1)} if self.tts_provider == "elevenlabs" else {}),
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
