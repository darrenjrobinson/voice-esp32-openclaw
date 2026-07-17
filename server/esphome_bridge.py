"""Phase 2: ESPHome bridge — drives the ESP32-S3-BOX-3 directly, no Home Assistant.

The ESP32 is the TCP server (native API, port 6053); this service connects to
it with aioesphomeapi and subscribes as the voice-assistant peer — exactly what
HA's esphome integration does, so the stock firmware needs no changes.

Per turn (wake word is on-device):
  device: pipeline start (+ mic audio over the encrypted API connection)
  bridge: RUN_START, STT_START
  bridge: VAD decides end of utterance -> STT -> STT_END {text}  (stops mic)
  bridge: INTENT_START -> OpenClaw chat -> INTENT_END
  bridge: TTS_START {text} -> TTS_END {url}  (device fetches audio over HTTP)
  bridge: RUN_END
STT/TTS providers (xAI or ElevenLabs) are selected via STT_PROVIDER /
TTS_PROVIDER. TTS audio is streamed: synthesis chunks are relayed into the
HTTP response as they arrive, so playback starts before synthesis finishes.

IMPORTANT: only one voice-assistant subscriber per device — disable/disconnect
HA's esphome integration for this device while the bridge is running.

Run:
  python -m server.esphome_bridge   (needs ESP32_HOST + ESP32_NOISE_PSK in .env)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import time

import aiohttp
from aiohttp import web
from aioesphomeapi import (
    APIClient,
    ReconnectLogic,
    VoiceAssistantAudioSettings,
    VoiceAssistantEventType as VAEvent,
)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicepipe.audio import PcmAudio, build_wav_bytes, encode_flac, normalize_pcm, parse_wav_bytes
from voicepipe.config import Config, load_config
from voicepipe.metrics import TurnMetrics, append_metrics
from voicepipe.openclaw_client import OpenClawClient
from voicepipe.providers import create_stt_client, create_tts_client
from voicepipe.text import split_for_speech
from voicepipe.vad import EndOfSpeechDetector

log = logging.getLogger("voicepipe.bridge")

MIC_RATE = 16000  # ESPHome voice_assistant mic format: 16 kHz s16le mono
MIC_WIDTH = 2


class AudioRelay:
    """Holds a finished TTS audio file for the device's HTTP fetch(es)."""

    def __init__(self, body: bytes, content_type: str):
        self.body = body
        self.content_type = content_type
        self.created = time.monotonic()


class VoiceBridge:
    def __init__(self, cfg: Config, session: aiohttp.ClientSession):
        self._cfg = cfg
        self._stt = create_stt_client(cfg, session)
        self._tts = create_tts_client(cfg, session)
        self._openclaw = OpenClawClient(cfg, session)
        self._api = APIClient(cfg.esp32_host, cfg.esp32_port, noise_psk=cfg.esp32_noise_psk or None)
        self._audio_q: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._turn_task: asyncio.Task | None = None
        self._relays: dict[str, AudioRelay] = {}
        self._advertise_host = cfg.bridge_advertise_host or self._detect_advertise_host()
        self._unsubscribe = None
        # Overwritten from the device's media player entity on connect
        self._tts_format = "flac"
        self._tts_rate = 48000
        self._media_player_key: int | None = None
        self._media_player_state: int = 0  # MediaPlayerState (1=IDLE, 2=PLAYING)
        self._ack_audio: tuple[bytes, float] | None = None  # cached ack (body, seconds)

    # -- connection ---------------------------------------------------------

    def _detect_advertise_host(self) -> str:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((self._cfg.esp32_host, self._cfg.esp32_port))
            return probe.getsockname()[0]

    async def start_http(self) -> None:
        app = web.Application()
        app.router.add_get("/tts/{relay_id}.flac", self._serve_tts)
        app.router.add_get("/tts/{relay_id}.wav", self._serve_tts)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self._cfg.bridge_http_port)
        await site.start()
        log.info(
            "TTS fetch endpoint: http://%s:%d/tts/<id>.wav",
            self._advertise_host, self._cfg.bridge_http_port,
        )

    async def run(self) -> None:
        await self.start_http()
        reconnect = ReconnectLogic(
            client=self._api,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
        )
        await reconnect.start()
        log.info("Connecting to ESP32 at %s:%d ...", self._cfg.esp32_host, self._cfg.esp32_port)
        await asyncio.Event().wait()  # run forever; ReconnectLogic maintains the link

    async def _on_connect(self) -> None:
        info = await self._api.device_info()
        log.info("Connected to %s (%s, ESPHome %s)", info.name, info.mac_address, info.esphome_version)
        await self._probe_announcement_format()
        self._api.subscribe_states(self._on_state)
        self._unsubscribe = self._api.subscribe_voice_assistant(
            handle_start=self._handle_pipeline_start,
            handle_stop=self._handle_pipeline_stop,
            handle_audio=self._handle_audio,  # presence of this opts in to API audio
        )

    def _on_state(self, state) -> None:
        if self._media_player_key is not None and getattr(state, "key", None) == self._media_player_key:
            self._media_player_state = int(getattr(state, "state", 0))

    async def _probe_announcement_format(self) -> None:
        """The device's media player dictates the TTS format — the BOX-3
        accepts ONLY flac/48000/mono (probed live; WAV is rejected with an
        endless refetch loop). Read it from the entity list like HA does."""
        from aioesphomeapi import MediaPlayerInfo

        try:
            entities, _ = await self._api.list_entities_services()
            for entity in entities:
                if isinstance(entity, MediaPlayerInfo):
                    self._media_player_key = entity.key
                    for fmt in entity.supported_formats:
                        if fmt.purpose == 1:  # ANNOUNCEMENT
                            self._tts_format = fmt.format
                            self._tts_rate = fmt.sample_rate
                            log.info(
                                "device announcement format: %s @ %d Hz", fmt.format, fmt.sample_rate
                            )
                            return
        except Exception:  # noqa: BLE001
            log.exception("could not read media player formats; using flac/48000")

    async def _on_disconnect(self, expected_disconnect: bool) -> None:
        log.warning("Disconnected from ESP32 (expected=%s)", expected_disconnect)
        self._cancel_turn()

    # -- voice assistant callbacks (called by aioesphomeapi) -----------------

    async def _handle_pipeline_start(
        self,
        conversation_id: str,
        flags: int,
        audio_settings: VoiceAssistantAudioSettings,
        wake_word_phrase: str | None,
    ) -> int | None:
        log.info("pipeline start (wake=%r, conversation=%s)", wake_word_phrase, conversation_id or "-")
        self._cancel_turn()
        while not self._audio_q.empty():  # drop any stale audio
            self._audio_q.get_nowait()
        self._turn_task = asyncio.create_task(self._run_turn())
        return 0  # 0 = send mic audio over the API connection, no UDP

    async def _handle_audio(self, data: bytes, _extra: bytes | None = None) -> None:
        await self._audio_q.put(data)

    async def _handle_pipeline_stop(self, abort: bool) -> None:
        log.info("pipeline stop from device (abort=%s)", abort)
        if abort:
            self._cancel_turn()

    def _cancel_turn(self) -> None:
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        self._turn_task = None

    # -- the actual turn ------------------------------------------------------

    def _event(self, event_type: VAEvent, data: dict[str, str] | None = None) -> None:
        self._api.send_voice_assistant_event(event_type, data)

    async def _run_turn(self) -> None:
        metrics = TurnMetrics(phase="2")
        chat_task: asyncio.Task | None = None
        try:
            self._event(VAEvent.VOICE_ASSISTANT_RUN_START)
            self._event(VAEvent.VOICE_ASSISTANT_STT_START)

            with metrics.stage("listen (VAD)"):
                vad = EndOfSpeechDetector(
                    rate=MIC_RATE,
                    width=MIC_WIDTH,
                    threshold=self._cfg.vad_threshold,
                    silence_seconds=self._cfg.vad_silence_seconds,
                    max_seconds=self._cfg.vad_max_seconds,
                )
                while True:
                    chunk = await asyncio.wait_for(
                        self._audio_q.get(), timeout=self._cfg.vad_max_seconds + 5
                    )
                    if chunk is None:
                        break
                    if vad.feed(chunk):
                        break
            query = PcmAudio(pcm=vad.pcm, rate=MIC_RATE, width=MIC_WIDTH, channels=1)
            log.info("utterance captured: %.1fs (%s)", query.duration_seconds, vad.stats())

            with metrics.stage(f"stt ({self._cfg.stt_provider})"):
                stt = await self._stt.stt(build_wav_bytes(query))
            metrics.transcript = stt.text
            metrics.stt_audio_seconds = stt.duration_seconds or query.duration_seconds
            self._event(VAEvent.VOICE_ASSISTANT_STT_END, {"text": stt.text})  # stops the mic
            if not stt.text.strip():
                raise RuntimeError("STT returned an empty transcript")

            self._event(VAEvent.VOICE_ASSISTANT_INTENT_START)
            chat_task = asyncio.ensure_future(self._openclaw.chat(stt.text))

            # Play via direct media commands instead of putting a url in
            # tts_end: the firmware's own tts_end playback stalls silently on
            # this device (verified live), while announcement commands play
            # reliably. tts_end still fires (no url) for phase transitions.
            if self._media_player_key is not None:
                if 0 < self._cfg.bridge_volume <= 1:
                    # Separate command — volume combined with media_url is ignored
                    self._api.media_player_command(
                        self._media_player_key, volume=self._cfg.bridge_volume
                    )
                # RUN_END must come BEFORE any playback: the voice-assistant
                # run owns the speaker (the firmware's on_end waits for the
                # I2S bus), so announcements issued mid-run stall silently in
                # PLAYING — verified live. Ending it here also lets the ack
                # play immediately while the agent is still thinking.
                self._event(VAEvent.VOICE_ASSISTANT_RUN_END)
                ack = await self._get_ack_url()
                if ack is not None and not chat_task.done():
                    await self._play_and_wait(ack[0], ack[1], label="ack")

                with metrics.stage("chat (OpenClaw)"):
                    reply = await chat_task
                metrics.reply_chars = len(reply)
                self._event(VAEvent.VOICE_ASSISTANT_INTENT_END)
                self._event(VAEvent.VOICE_ASSISTANT_TTS_START, {"text": reply})
                self._event(VAEvent.VOICE_ASSISTANT_TTS_END, {})
                with metrics.stage("tts + playback"):
                    await self._speak_chunked(reply)
            else:  # unknown device shape — fall back to firmware playback
                with metrics.stage("chat (OpenClaw)"):
                    reply = await chat_task
                metrics.reply_chars = len(reply)
                self._event(VAEvent.VOICE_ASSISTANT_INTENT_END)
                self._event(VAEvent.VOICE_ASSISTANT_TTS_START, {"text": reply})
                with metrics.stage(f"tts ({self._cfg.tts_provider} + flac)"):
                    url, _ = await self._start_tts_relay(reply)
                self._event(VAEvent.VOICE_ASSISTANT_TTS_END, {"url": url})
                self._event(VAEvent.VOICE_ASSISTANT_RUN_END)
            log.info("turn complete: %r -> %d chars", stt.text, len(reply))
        except asyncio.CancelledError:
            log.info("turn cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("turn failed")
            self._event(
                VAEvent.VOICE_ASSISTANT_ERROR,
                {"code": "bridge-error", "message": str(exc)},
            )
            self._event(VAEvent.VOICE_ASSISTANT_RUN_END)
        finally:
            if chat_task is not None and not chat_task.done():
                chat_task.cancel()
            append_metrics(metrics)

    async def _get_ack_url(self) -> tuple[str, float] | None:
        """Cached 'working on it' acknowledgment — synthesized once per process,
        replayed instantly on every turn while the agent thinks."""
        if not self._cfg.bridge_ack_phrase or self._media_player_key is None:
            return None
        if self._ack_audio is None:
            pcm = normalize_pcm(
                await self._synthesize_pcm(self._cfg.bridge_ack_phrase, self._tts_rate)
            )
            body = encode_flac(pcm) if self._tts_format == "flac" else build_wav_bytes(pcm)
            self._ack_audio = (body, pcm.duration_seconds)
            log.info("ack cached: %r (%.1fs)", self._cfg.bridge_ack_phrase, pcm.duration_seconds)
        body, duration = self._ack_audio
        ext = "flac" if self._tts_format == "flac" else "wav"
        relay_id = f"ack{int(time.time() * 1000):x}"
        self._relays[relay_id] = AudioRelay(body, f"audio/{'flac' if ext == 'flac' else 'wav'}")
        self._prune_relays()
        url = f"http://{self._advertise_host}:{self._cfg.bridge_http_port}/tts/{relay_id}.{ext}"
        return url, duration

    async def _speak_chunked(self, reply: str) -> None:
        """Speak the reply as sentence chunks: chunk N+1 synthesizes while
        chunk N plays, so audio starts after one short sentence's synthesis
        instead of the whole reply's."""
        chunks = split_for_speech(reply) if self._cfg.bridge_chunked else [reply]
        if not chunks:
            return
        log.info("speaking %d chunk(s)", len(chunks))
        next_item = asyncio.ensure_future(self._start_tts_relay(chunks[0]))
        for i in range(len(chunks)):
            url, duration = await next_item
            if i + 1 < len(chunks):
                next_item = asyncio.ensure_future(self._start_tts_relay(chunks[i + 1]))
            await self._play_and_wait(url, duration, label=f"chunk {i + 1}/{len(chunks)}")

    PLAYING = 2  # MediaPlayerState

    async def _ensure_player_idle(self) -> None:
        """Un-wedge a stuck announcement (they survive soft resets) — the
        remote STOP is proven to clear it."""
        if self._media_player_state != self.PLAYING:
            return
        log.warning("media player already PLAYING before announce — sending STOP to un-wedge")
        from aioesphomeapi import MediaPlayerCommand

        self._api.media_player_command(
            self._media_player_key, command=MediaPlayerCommand.STOP, announcement=True
        )
        for _ in range(30):  # up to 3s
            if self._media_player_state != self.PLAYING:
                return
            await asyncio.sleep(0.1)
        log.warning("player still PLAYING after STOP; continuing anyway")

    async def _play_and_wait(self, url: str, duration_seconds: float, label: str = "") -> None:
        """Start an announcement and wait for the media player to go idle
        (state tracked via subscribe_states). Timeouts are sized from the
        chunk's actual audio duration so a wedge can't hang the turn."""
        await self._ensure_player_idle()
        self._api.media_player_command(self._media_player_key, media_url=url, announcement=True)

        loop = asyncio.get_event_loop()
        start_deadline = loop.time() + 10
        while self._media_player_state != self.PLAYING:
            if loop.time() > start_deadline:
                log.warning("%s: playback never started", label)
                return
            await asyncio.sleep(0.1)

        finish_deadline = loop.time() + duration_seconds + 15
        while self._media_player_state == self.PLAYING:
            if loop.time() > finish_deadline:
                log.warning("%s: still PLAYING %.0fs past expected end — treating as wedged", label, 15.0)
                await self._ensure_player_idle()
                return
            await asyncio.sleep(0.1)
        log.info("%s played (%.1fs)", label, duration_seconds)

    async def _start_tts_relay(self, text: str) -> tuple[str, float]:
        """Synthesize the reply; return (device-fetchable URL, audio seconds).

        Two constraints learned from live testing against the BOX-3:
        - The audio reader needs a complete body with an exact Content-Length
          (chunked responses are closed and re-fetched in a tight loop).
        - The media player decodes ONLY its advertised announcement format —
          flac/48000/mono on this device. WAV downloads fine, then the decoder
          rejects it and the player refetches forever.
        So: synthesize PCM at the device's rate, encode FLAC, serve complete.
        """
        relay_id = f"{int(time.time() * 1000):x}"
        self._prune_relays()

        pcm_audio = normalize_pcm(await self._synthesize_pcm(text, self._tts_rate))
        if self._tts_format == "flac":
            body, content_type, ext = encode_flac(pcm_audio), "audio/flac", "flac"
        else:  # non-BOX-3 device that accepts wav
            body, content_type, ext = build_wav_bytes(pcm_audio), "audio/wav", "wav"

        self._relays[relay_id] = AudioRelay(body, content_type)
        url = f"http://{self._advertise_host}:{self._cfg.bridge_http_port}/tts/{relay_id}.{ext}"
        return url, pcm_audio.duration_seconds

    async def _synthesize_pcm(self, text: str, rate: int) -> PcmAudio:
        if self._cfg.tts_streaming:
            try:
                pcm = bytearray()
                async for chunk in self._tts.tts_stream(text, codec="pcm", sample_rate=rate):
                    pcm.extend(chunk)
                return PcmAudio(pcm=bytes(pcm), rate=rate, width=2, channels=1)
            except Exception:  # noqa: BLE001
                log.exception("streaming TTS failed; retrying as batch")
        result = await self._tts.tts(text, codec="wav", sample_rate=rate)
        return parse_wav_bytes(result.audio)

    def _prune_relays(self, max_age_seconds: float = 300) -> None:
        cutoff = time.monotonic() - max_age_seconds
        for relay_id in [rid for rid, r in self._relays.items() if r.created < cutoff]:
            del self._relays[relay_id]

    # -- HTTP -----------------------------------------------------------------

    async def _serve_tts(self, request: web.Request) -> web.StreamResponse:
        relay = self._relays.get(request.match_info["relay_id"])
        if relay is None:
            raise web.HTTPNotFound()
        # Complete body with exact Content-Length — the ESP32 audio reader
        # does not tolerate chunked responses (see _start_tts_relay).
        return web.Response(body=relay.body, content_type=relay.content_type)


async def main() -> None:
    parser = argparse.ArgumentParser(description="ESPHome voice bridge (direct ESP32, no HA)")
    parser.add_argument("--esp32-host", default=None, help="override ESP32_HOST")
    args = parser.parse_args()

    cfg = load_config()
    if args.esp32_host:
        cfg.esp32_host = args.esp32_host
    if not cfg.esp32_host:
        raise SystemExit("ESP32_HOST is not set (device IP or .local name)")
    if not cfg.esp32_noise_psk:
        log.warning("ESP32_NOISE_PSK not set — connection will fail if the device uses API encryption")

    async with aiohttp.ClientSession() as session:
        await VoiceBridge(cfg, session).run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
