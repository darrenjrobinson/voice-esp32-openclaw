"""Manage xAI custom voices (voice cloning).

Create a voice from a reference recording (max 120 seconds of clean speech),
then set XAI_VOICE=<voice_id> in .env to use it everywhere `eve` is used today.

Usage:
  python scripts/custom_voice.py --list
  python scripts/custom_voice.py --create --name "Marvin" --file audio/marvin_reference.wav
  python scripts/custom_voice.py --delete <voice_id>
  python scripts/custom_voice.py --preview <voice_id> --text "Hello, I am Marvin."
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicepipe.config import load_config


async def main() -> None:
    parser = argparse.ArgumentParser(description="xAI custom voice management")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="list built-in and custom voices")
    group.add_argument("--create", action="store_true", help="clone a voice from a reference recording")
    group.add_argument("--delete", metavar="VOICE_ID", help="delete a custom voice")
    group.add_argument("--preview", metavar="VOICE_ID", help="synthesize a preview with a voice")
    parser.add_argument("--name", help="name for the new voice (--create)")
    parser.add_argument("--file", help="reference audio, max 120s (--create)")
    parser.add_argument("--language", default="en")
    parser.add_argument("--text", default="Hello, I am Marvin, and this is my new voice.")
    parser.add_argument("--description", default=None, help="voice description (--create)")
    parser.add_argument("--gender", default=None, choices=["male", "female", "neutral"])
    parser.add_argument("--accent", default=None, help="e.g. British, American")
    parser.add_argument("--age", default=None, choices=["young", "middle-aged", "old"])
    parser.add_argument("--use-case", default=None, dest="use_case",
                        choices=["conversational", "narration", "characters", "educational",
                                 "advertisement", "social_media", "entertainment"])
    parser.add_argument("--tone", default=None,
                        choices=["warm", "casual", "professional", "friendly",
                                 "authoritative", "expressive", "calm"])
    args = parser.parse_args()

    cfg = load_config()
    headers = {"Authorization": f"Bearer {cfg.xai_api_key}"}

    async with aiohttp.ClientSession(headers=headers) as session:
        if args.list:
            for label, url in [
                ("Built-in", f"{cfg.xai_base_url}/v1/tts/voices"),
                ("Custom", f"{cfg.xai_base_url}/v1/custom-voices"),
            ]:
                async with session.get(url) as resp:
                    body = await resp.json(content_type=None)
                print(f"{label} ({resp.status}): {body}")

        elif args.create:
            if not args.name or not args.file:
                raise SystemExit("--create requires --name and --file")
            path = Path(args.file)
            content_type = {
                ".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac",
                ".ogg": "audio/ogg", ".m4a": "audio/mp4",
            }.get(path.suffix.lower(), "application/octet-stream")
            form = aiohttp.FormData()
            form.add_field("name", args.name)
            form.add_field("language", args.language)
            for field in ("description", "gender", "accent", "age", "use_case", "tone"):
                value = getattr(args, field)
                if value:
                    form.add_field(field, value)
            form.add_field("file", path.read_bytes(), filename=path.name, content_type=content_type)
            async with session.post(f"{cfg.xai_base_url}/v1/custom-voices", data=form) as resp:
                body = await resp.json(content_type=None)
            if resp.status != 200:
                raise SystemExit(f"Create failed HTTP {resp.status}: {body}")
            print(f"Created: {body}")
            voice_id = body.get("voice_id") or body.get("id")
            if voice_id:
                print(f"\nSet in .env to use it:\n  XAI_VOICE={voice_id}")

        elif args.delete:
            async with session.delete(f"{cfg.xai_base_url}/v1/custom-voices/{args.delete}") as resp:
                print(f"Delete HTTP {resp.status}: {await resp.text()}")

        elif args.preview:
            payload = {
                "text": args.text,
                "language": args.language,
                "voice_id": args.preview,
                "output_format": {"codec": "wav", "sample_rate": 24000},
            }
            async with session.post(f"{cfg.xai_base_url}/v1/tts", json=payload) as resp:
                if resp.status != 200:
                    raise SystemExit(f"Preview failed HTTP {resp.status}: {await resp.text()}")
                audio = await resp.read()
            out = Path("out")
            out.mkdir(exist_ok=True)
            path = out / f"voice_preview_{args.preview[:12]}.wav"
            path.write_bytes(audio)
            print(f"Saved {path}")
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME)


if __name__ == "__main__":
    asyncio.run(main())
