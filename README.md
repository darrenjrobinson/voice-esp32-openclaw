# voice-esp32-openclaw

[![ClawHub](https://img.shields.io/badge/%F0%9F%A6%9E_ClawHub-esp32--voice--assistant-E5533D)](https://clawhub.ai/darrenjrobinson/skills/esp32-voice-assistant)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A hardware voice assistant with **no Home Assistant in the voice path**: an ESP32-S3-BOX-3 running stock ESPHome firmware talks directly to a Python bridge that uses **xAI Grok Voice** or **ElevenLabs** for speech-to-text and text-to-speech (independently switchable — mix and match), and **OpenClaw** as the agent brain.

```
"Yo Marvin"  (wake word, on-device)
     │
ESP32-S3-BOX-3 ──── ESPHome native API (TCP 6053) ────┐
     ▲                                                 │ mic audio (16 kHz PCM)
     │ fetches reply audio (HTTP :10400, FLAC)         ▼
     └───────────────────────────────────── esphome-bridge (Docker)
                                                 │  1. VAD end-of-speech
                                                 │  2. STT   (xAI or ElevenLabs)
                                                 │  3. spoken ack ("give me a moment")
                                                 │  4. OpenClaw  (/v1/chat/completions)
                                                 │  5. TTS   (xAI or ElevenLabs, streamed)
                                                 └─ sentence-chunked playback
```

**Install via ClawHub:** this integration is published as [an OpenClaw skill](https://clawhub.ai/darrenjrobinson/skills/esp32-voice-assistant) — `clawhub install esp32-voice-assistant` gives your agent the setup and operating instructions; the skill source lives in [`skill/esp32-voice-assistant/`](skill/esp32-voice-assistant/). The deployment itself is still this repo (clone + `docker compose`), as described below.

**The key insight:** the ESP32 is the TCP *server* in the ESPHome native API — Home Assistant is just a client that subscribes as the voice-assistant peer. This bridge impersonates that client with [aioesphomeapi](https://pypi.org/project/aioesphomeapi/), so **the stock firmware needs zero changes** — no reflash, no YAML edits. Point it at the device and HA is out of the loop.

## Features

- **Switchable STT/TTS providers** — `STT_PROVIDER` and `TTS_PROVIDER` select xAI or ElevenLabs independently, so you can run everything on one provider or split them (e.g. cheap xAI STT + an ElevenLabs custom voice for TTS).
- **Direct ESP32 ↔ agent pipeline** — no Home Assistant dependency; HA can coexist for the device's non-voice entities.
- **Sentence-chunked playback** — the first sentence speaks while the rest is still synthesizing; chunk N+1 synthesizes during chunk N's playback.
- **Spoken acknowledgment** — a cached phrase ("Fine, give me a moment.") plays right after transcription, so long agent turns (tool calls can take 60 s+) never feel dead.
- **Voice-style brevity prompt** — replies are steered to 1–3 spoken sentences via a system message (fully configurable).
- **Loudness handling** — peak normalization of quiet TTS PCM plus per-reply device volume.
- **Self-healing playback** — wedged announcements are detected and cleared remotely; duration-sized timeouts mean a bad turn can't hang the assistant.
- **Auto-configuration** — the bridge reads the device's supported audio format (e.g. FLAC/48 kHz/mono on the BOX-3) from the media player entity at connect.
- **Custom voices** — 26 xAI built-in voices plus xAI voice cloning (`scripts/custom_voice.py`) where enabled, or any voice in your ElevenLabs account including cloned ones (`scripts/elevenlabs_voices.py` lists the IDs).

## What you need

| Component | Notes |
|---|---|
| **ESP32-S3-BOX-3** | Running an ESPHome voice-assistant firmware with on-device wake word (e.g. the stock [wake-word-voice-assistants](https://github.com/esphome/wake-word-voice-assistants) config or a derivative). Other ESPHome voice devices should work — the bridge adapts to the advertised media format. |
| **xAI API key** *and/or* **ElevenLabs API key** | One key per provider you select. xAI ([console.x.ai](https://console.x.ai), 2026-07: TTS $15/1M chars, STT $0.10/hr — ~$0.003–0.006/query). ElevenLabs ([elevenlabs.io](https://elevenlabs.io)) — the free tier works (~10 min of Flash TTS/month, attribution required); custom/cloned voices use your account's voice IDs. |
| **OpenClaw** | With the OpenAI-compatible `/v1/chat/completions` endpoint enabled, reachable from the bridge. Any OpenAI-compatible chat endpoint should also work. |
| **Docker host on the LAN** | The device must be able to reach the bridge's HTTP port (default 10400) to fetch reply audio. |

## Deploy

### 1. Prepare the device

- Note the device's IP and, if its `api:` block has an `encryption: key:`, that key (base64). A bare `api:` block means plaintext — leave the key empty.
- **Disconnect Home Assistant's voice subscription** for this device: disable the ESPHome integration entry (or just its *assist satellite* entity). The protocol allows **one** voice-assistant subscriber — two subscribers means the wake word silently does nothing.

### 2. Configure and start the bridge

```bash
git clone https://github.com/darrenjrobinson/voice-esp32-openclaw.git
cd voice-esp32-openclaw
cp .env.example .env    # then edit:
```

Required `.env` values:

| Variable | Example | Purpose |
|---|---|---|
| `STT_PROVIDER` / `TTS_PROVIDER` | `xai` | `xai` or `elevenlabs`, independently — any mix works |
| `XAI_API_KEY` | `xai-…` | Required when either provider is `xai` |
| `ELEVENLABS_API_KEY` | `sk_…` | Required when either provider is `elevenlabs` |
| `ELEVENLABS_VOICE_ID` | `21m00T…` | Required when `TTS_PROVIDER=elevenlabs` — list yours with `python scripts/elevenlabs_voices.py` |
| `OPENCLAW_URL` | `http://host.docker.internal:18789` | Chat completions endpoint (compose default reaches an OpenClaw on the Docker host) |
| `OPENCLAW_API_KEY` | `…` | Bearer token if the endpoint requires auth |
| `ESP32_HOST` | `192.168.4.123` | Device IP or `.local` name |
| `ESP32_NOISE_PSK` | *(empty)* | Only if the device's API is encrypted |
| `BRIDGE_ADVERTISE_HOST` | `192.168.6.40` | **LAN IP of the Docker host** — the device fetches reply audio from here; container autodetection returns the wrong (container) IP |

```bash
docker compose --profile bridge up -d --build
docker compose logs -f voice-bridge
# expect: "Connected to <device> (…, ESPHome …)" and "device announcement format: flac @ 48000 Hz"
```

Say the wake word. Done.

### 3. Tune (all optional, in `.env`)

| Variable | Default | Purpose |
|---|---|---|
| `XAI_VOICE` | `eve` | xAI TTS voice: any of the 26 built-in voices (`altair`, `ara`, `atlas`, …) or a custom voice ID |
| `ELEVENLABS_TTS_MODEL` | `eleven_flash_v2_5` | Lowest-latency, half-price model — right for a voice assistant. `eleven_turbo_v2_5` or `eleven_multilingual_v2` trade latency/cost for quality |
| `ELEVENLABS_STT_MODEL` | `scribe_v1` | ElevenLabs transcription model |
| `ELEVENLABS_OUTPUT_RATE` | `24000` | PCM rate requested from the API (free tier caps at 24000; 44100 needs Pro). Resampled locally to the device's rate — no quality knob to worry about |
| `BRIDGE_VOLUME` | `1.0` | Device volume set before each reply (`0` = leave alone) |
| `BRIDGE_ACK_PHRASE` | `On it.` | Spoken while the agent thinks; empty disables. Synthesized once, cached |
| `BRIDGE_CHUNKED` | `true` | Sentence-chunked playback |
| `OPENCLAW_SYSTEM_PROMPT` | *(spoken-brevity prompt)* | System message steering reply length/register |
| `OPENCLAW_SESSION_KEY` | `agent:main:voice` | Session identity sent to OpenClaw (via the `user` field; `OPENCLAW_SESSION_MODE=header` sends `X-Session-Key` instead) |
| `VAD_THRESHOLD` | `500` | Mean-\|amplitude\| speech gate. The BOX-3 mic runs quiet — `60` worked here (speech peaks 130–350, ambient 10–30). Per-turn level stats are logged for tuning |
| `VAD_SILENCE_SECONDS` / `VAD_MAX_SECONDS` | `0.8` / `10` | End-of-utterance silence, and the capture cap |
| `TTS_STREAMING` | `true` | Stream synthesis from the TTS provider (batch fallback is automatic) |

### Provider notes

- **Any combination works.** `STT_PROVIDER=xai` + `TTS_PROVIDER=elevenlabs` is the cost-conscious way to get an ElevenLabs custom voice: xAI transcription is billed per hour of audio while every ElevenLabs character costs credits.
- **ElevenLabs free tier**: roughly 10 minutes of Flash TTS per month, max 2 concurrent requests (the bridge never exceeds this), and [attribution is required](https://elevenlabs.io/pricing) when you publish anything made with it.
- **ElevenLabs audio path**: the API serves PCM at `ELEVENLABS_OUTPUT_RATE` (24 kHz on free tier); the bridge resamples to the device's advertised rate (48 kHz FLAC on the BOX-3) transparently, so no firmware or format concerns apply.
- The spoken language for both providers comes from `XAI_TTS_LANGUAGE` (default `en`).

### Applying `.env` changes to a running deployment

Compose reads the `.env` sitting **next to `docker-compose.yml`**, so always run it from the repo directory:

```bash
cd <repo dir>
docker compose --profile bridge up -d        # recreates only containers whose config changed
```

No `--build` is needed for env-only changes. Notes:

- If it reports nothing to recreate but you know `.env` changed, you probably edited a different copy or ran from the wrong directory; `--force-recreate` settles it either way.
- Verify what a container is actually running with:
  ```bash
  docker inspect voice-bridge --format '{{range .Config.Env}}{{println .}}{{end}}' | grep PROVIDER
  ```
- Containers only read `.env` at creation — editing the file does nothing until they are recreated (`restart` is not enough).

## Device quirks this bridge already handles

Everything below was discovered by live debugging against a real BOX-3 — each one silently broke playback until handled:

| Device behavior | What the bridge does |
|---|---|
| The HTTP audio client rejects `Transfer-Encoding: chunked` (closes + refetches in a tight loop) | Serves complete audio with an exact `Content-Length` |
| The media player decodes **only its advertised format** (BOX-3: FLAC/48 kHz/mono); WAV wedges the announcement pipeline | Probes the media player entity at connect; encodes FLAC with [pyflac](https://pypi.org/project/pyFLAC/) |
| The firmware's own `tts_end` URL playback stalls silently | Plays replies via direct `media_player_command(announcement=True)`; `tts_end` still fires (without URL) for the display |
| The voice-assistant run **owns the speaker until `RUN_END`** — mid-run announcements stall in `PLAYING` | Sends `RUN_END` before any playback |
| Wedged announcements survive soft resets | A remote `STOP (announcement)` clears them — applied automatically before each chunk and on overrun |
| Announce-await API only works from the voice-subscribed client | Playback completion is tracked via `subscribe_states` (PLAYING → IDLE) with duration-sized timeouts |
| xAI TTS PCM is low-level; `volume` combined with `media_url` in one command is ignored | Peak-normalizes to ~90 % full scale (boost-only, so healthy ElevenLabs audio passes through untouched); sends volume as its own command |

Also: xAI's `/v1/tts` returns **raw audio** by default (base64-JSON only with `with_timestamps=true`), and its streamed WAVs carry placeholder RIFF sizes — durations must come from actual PCM length.

## Cost analysis

Per-turn metrics (with providers, USD, and ElevenLabs credits) are appended to `out/metrics.jsonl` on every turn. Rates the cost model uses (confirmed 2026-07):

| Stage | xAI | ElevenLabs |
|---|---|---|
| STT | $0.10 / hour of audio | $0.22 / hour (Scribe) |
| TTS | $15 / 1M chars ($0.015 / 1k) | 0.5 credits/char on Flash & Turbo, 1 credit/char on other models. API billing ≈ $0.10 / 1k credits (Flash ≈ $0.05 / 1k chars); subscription tiers draw from the plan's monthly credit allowance instead |

A typical turn (a ~4 s question, ~300-char spoken reply):

| Combo | STT | TTS | Total |
|---|---|---|---|
| xAI + xAI | ~$0.0001 | ~$0.0045 | **~$0.005** |
| xAI STT + ElevenLabs Flash TTS | ~$0.0001 | 150 credits (≈$0.015) | **~$0.015** |
| ElevenLabs both | ~$0.0002 | 150 credits (≈$0.015) | **~$0.015** |

Practical notes:

- **The ElevenLabs free tier's 10k credits/month ≈ 65–70 such turns** on Flash (about 10 minutes of speech). The `tts_credits` field in `metrics.jsonl` lets you track burn.
- STT is cheap on either provider — the split combo (xAI STT + ElevenLabs TTS) saves ~$0.0001/turn over all-ElevenLabs, so choose STT by accuracy/latency preference rather than cost; TTS is where the money goes.
- The ack phrase is synthesized once per container start (~23 chars), then cached.
- Agent (OpenClaw) costs are whatever your agent burns — not counted here.

## Latency expectations

For a typical turn: ~1 s STT + **agent time** + ~2–4 s to first audio. Agent time dominates — 3–7 s for simple questions, 60 s+ when the agent does real tool work; the ack phrase covers that gap. The voice pipeline itself contributes well under 10 s end-to-end, and Wyoming/HTTP transport overhead is negligible (measured ~7 ms).

## Also in this repo

- **`server/tts_server.py`** — a Wyoming-protocol TTS server (port 10200, started by the default compose profile) that registers in Home Assistant exactly like Piper, with the same streaming/normalization stack and the same provider selection. Useful as an HA-integrated fallback or if you only want this TTS inside HA: add the *Wyoming Protocol* integration pointing at the host, then select `grok-voice` (or `elevenlabs-tts`) as the pipeline TTS.
- **`server/pipeline_server.py` + `satellite/simulate_satellite.py`** — a Wyoming pipeline server and satellite emulator used to validate the STT→agent→TTS core without hardware.
- **`scripts/phase0a_roundtrip.py`** — bare-API round trip (WAV → STT → chat → TTS) with per-stage latency and cost reporting; every turn also appends metrics to `out/metrics.jsonl`.
- **`scripts/test_tts_server.py`** — HA-mimicking Wyoming TTS client that measures time-to-first-audio.
- **`scripts/custom_voice.py`** — list/preview/create/delete xAI voices, including custom voice cloning from a ≤120 s reference recording (`--create` requires the feature enabled on your xAI team; the console offers up to 30 free where available).
- **`scripts/elevenlabs_voices.py`** — list the ElevenLabs voices on your account (including cloned ones) to find the `ELEVENLABS_VOICE_ID` for `.env`.
- **`scripts/record_test_wav.py`** — 16 kHz mono test recordings on Windows.

## Development (without Docker)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env    # fill in
python -m server.esphome_bridge      # never alongside the container — one subscriber only
```

## Rollback

Stop the bridge and re-enable the ESPHome integration in Home Assistant — the device reverts to the HA Assist pipeline immediately. Nothing on the device was ever modified.
