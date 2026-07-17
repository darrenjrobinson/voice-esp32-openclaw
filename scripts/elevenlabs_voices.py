"""List the ElevenLabs voices available to your account.

Voice creation/cloning happens in the ElevenLabs UI (elevenlabs.io/app) —
this script just finds the voice_id to put in .env:

  python scripts/elevenlabs_voices.py
  # then set:  ELEVENLABS_VOICE_ID=<voice_id>
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicepipe.config import load_config


async def main() -> None:
    cfg = load_config(require_xai_key=False)
    api_key = cfg.elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ELEVENLABS_API_KEY is not set in .env")

    async with aiohttp.ClientSession(headers={"xi-api-key": api_key}) as session:
        async with session.get(f"{cfg.elevenlabs_base_url}/v1/voices") as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                raise SystemExit(f"HTTP {resp.status}: {body}")

    voices = body.get("voices", [])
    if not voices:
        print("No voices found on this account.")
        return
    print(f"{'voice_id':<24} {'category':<12} name")
    for voice in voices:
        print(f"{voice.get('voice_id', ''):<24} {voice.get('category', ''):<12} {voice.get('name', '')}")
    print(f"\n{len(voices)} voice(s). Set in .env to use one:\n  ELEVENLABS_VOICE_ID=<voice_id>")


if __name__ == "__main__":
    asyncio.run(main())
