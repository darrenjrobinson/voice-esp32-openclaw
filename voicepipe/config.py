"""Environment-driven configuration. All settings come from .env / process env."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

VALID_SAMPLE_RATES = {8000, 16000, 22050, 24000, 44100, 48000}


@dataclass
class Config:
    xai_api_key: str
    openclaw_url: str = "http://192.168.6.40:18789"
    openclaw_api_key: str = ""
    openclaw_session_key: str = "agent:main:voice"
    openclaw_session_mode: str = "user"  # "user" | "header"
    openclaw_model: str = "openclaw"
    openclaw_system_prompt: str = (
        "You are answering by voice on a smart speaker. Reply in one to three short, "
        "conversational spoken sentences. No markdown, no lists, no URLs. "
        "Only give longer answers when explicitly asked for detail."
    )
    xai_voice: str = "eve"
    xai_tts_language: str = "en"
    # STT/TTS provider selection — "xai" | "elevenlabs", independently switchable
    stt_provider: str = "xai"
    tts_provider: str = "xai"
    # ElevenLabs (used when a provider above is "elevenlabs")
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_tts_model: str = "eleven_flash_v2_5"
    elevenlabs_stt_model: str = "scribe_v1"
    # PCM rate requested from the ElevenLabs API (free tier caps at 24000;
    # 44100 needs Pro). Audio is resampled to the requested rate automatically.
    elevenlabs_output_rate: int = 24000
    tts_sample_rate: int = 24000
    wyoming_host: str = "127.0.0.1"
    wyoming_port: int = 10300
    tts_port: int = 10200
    tts_streaming: bool = True
    # Phase 2 — ESPHome bridge (direct ESP32, no HA)
    esp32_host: str = ""
    esp32_port: int = 6053
    esp32_noise_psk: str = ""
    bridge_http_port: int = 10400
    bridge_advertise_host: str = ""  # LAN IP the ESP32 fetches TTS from; autodetected if empty
    bridge_volume: float = 0.0  # 0 = leave device volume alone; 0.1-1.0 = set before each reply
    bridge_chunked: bool = True  # sentence-chunked playback: speak sentence 1 while the rest synthesizes
    # Spoken immediately after transcription while the agent works (empty = disabled).
    # Trade-off: requires ending the VA run early, so the device screen may
    # return to idle during long waits instead of showing the answer text.
    bridge_ack_phrase: str = "On it."
    vad_threshold: int = 500  # mean |amplitude| (s16le) above which a chunk counts as speech
    vad_silence_seconds: float = 0.8
    vad_max_seconds: float = 10.0
    log_level: str = "INFO"
    xai_base_url: str = "https://api.x.ai"
    elevenlabs_base_url: str = "https://api.elevenlabs.io"
    extra: dict = field(default_factory=dict)


def load_config(require_xai_key: bool = True) -> Config:
    load_dotenv()

    stt_provider = os.environ.get("STT_PROVIDER", "xai").strip().lower()
    tts_provider = os.environ.get("TTS_PROVIDER", "xai").strip().lower()
    for var, value in (("STT_PROVIDER", stt_provider), ("TTS_PROVIDER", tts_provider)):
        if value not in ("xai", "elevenlabs"):
            raise SystemExit(f"{var}={value!r} invalid; use 'xai' or 'elevenlabs'")

    xai_api_key = os.environ.get("XAI_API_KEY", "").strip()
    if require_xai_key and "xai" in (stt_provider, tts_provider) and not xai_api_key:
        raise SystemExit(
            "XAI_API_KEY is not set. Copy .env.example to .env and fill it in."
        )

    elevenlabs_api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    elevenlabs_voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
    if require_xai_key and "elevenlabs" in (stt_provider, tts_provider) and not elevenlabs_api_key:
        raise SystemExit(
            "ELEVENLABS_API_KEY is not set but an 'elevenlabs' provider is selected."
        )
    if require_xai_key and tts_provider == "elevenlabs" and not elevenlabs_voice_id:
        raise SystemExit(
            "ELEVENLABS_VOICE_ID is not set (TTS_PROVIDER=elevenlabs). "
            "Run `python scripts/elevenlabs_voices.py` to list your voices."
        )

    elevenlabs_output_rate = int(os.environ.get("ELEVENLABS_OUTPUT_RATE", "24000"))
    if elevenlabs_output_rate not in VALID_SAMPLE_RATES:
        raise SystemExit(
            f"ELEVENLABS_OUTPUT_RATE={elevenlabs_output_rate} invalid; "
            f"must be one of {sorted(VALID_SAMPLE_RATES)}"
        )

    tts_sample_rate = int(os.environ.get("TTS_SAMPLE_RATE", "24000"))
    if tts_sample_rate not in VALID_SAMPLE_RATES:
        raise SystemExit(
            f"TTS_SAMPLE_RATE={tts_sample_rate} invalid; must be one of {sorted(VALID_SAMPLE_RATES)}"
        )

    session_mode = os.environ.get("OPENCLAW_SESSION_MODE", "user").strip().lower()
    if session_mode not in ("user", "header"):
        raise SystemExit(f"OPENCLAW_SESSION_MODE={session_mode!r} invalid; use 'user' or 'header'")

    cfg = Config(
        xai_api_key=xai_api_key,
        openclaw_url=os.environ.get("OPENCLAW_URL", Config.openclaw_url).rstrip("/"),
        openclaw_api_key=os.environ.get("OPENCLAW_API_KEY", "").strip(),
        openclaw_session_key=os.environ.get("OPENCLAW_SESSION_KEY", Config.openclaw_session_key),
        openclaw_session_mode=session_mode,
        openclaw_model=os.environ.get("OPENCLAW_MODEL", Config.openclaw_model),
        openclaw_system_prompt=os.environ.get("OPENCLAW_SYSTEM_PROMPT", Config.openclaw_system_prompt),
        xai_voice=os.environ.get("XAI_VOICE", Config.xai_voice),
        xai_tts_language=os.environ.get("XAI_TTS_LANGUAGE", Config.xai_tts_language),
        stt_provider=stt_provider,
        tts_provider=tts_provider,
        elevenlabs_api_key=elevenlabs_api_key,
        elevenlabs_voice_id=elevenlabs_voice_id,
        elevenlabs_tts_model=os.environ.get("ELEVENLABS_TTS_MODEL", Config.elevenlabs_tts_model),
        elevenlabs_stt_model=os.environ.get("ELEVENLABS_STT_MODEL", Config.elevenlabs_stt_model),
        elevenlabs_output_rate=elevenlabs_output_rate,
        tts_sample_rate=tts_sample_rate,
        wyoming_host=os.environ.get("WYOMING_HOST", Config.wyoming_host),
        wyoming_port=int(os.environ.get("WYOMING_PORT", str(Config.wyoming_port))),
        tts_port=int(os.environ.get("TTS_PORT", str(Config.tts_port))),
        tts_streaming=os.environ.get("TTS_STREAMING", "true").strip().lower() != "false",
        esp32_host=os.environ.get("ESP32_HOST", "").strip(),
        esp32_port=int(os.environ.get("ESP32_PORT", str(Config.esp32_port))),
        esp32_noise_psk=os.environ.get("ESP32_NOISE_PSK", "").strip(),
        bridge_http_port=int(os.environ.get("BRIDGE_HTTP_PORT", str(Config.bridge_http_port))),
        bridge_advertise_host=os.environ.get("BRIDGE_ADVERTISE_HOST", "").strip(),
        bridge_volume=float(os.environ.get("BRIDGE_VOLUME", "0")),
        bridge_chunked=os.environ.get("BRIDGE_CHUNKED", "true").strip().lower() != "false",
        bridge_ack_phrase=os.environ.get("BRIDGE_ACK_PHRASE", Config.bridge_ack_phrase),
        vad_threshold=int(os.environ.get("VAD_THRESHOLD", str(Config.vad_threshold))),
        vad_silence_seconds=float(os.environ.get("VAD_SILENCE_SECONDS", str(Config.vad_silence_seconds))),
        vad_max_seconds=float(os.environ.get("VAD_MAX_SECONDS", str(Config.vad_max_seconds))),
        log_level=os.environ.get("LOG_LEVEL", Config.log_level).upper(),
    )

    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return cfg
