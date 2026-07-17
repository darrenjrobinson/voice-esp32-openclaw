"""OpenClaw chat completions client (OpenAI-compatible endpoint).

Session key transport is configurable via OPENCLAW_SESSION_MODE:
  "user"   -> sent as the OpenAI `user` field in the request body
  "header" -> sent as an X-Session-Key request header
Confirm which one OpenClaw honors on first live run and set .env accordingly.

Smoke test:
  python -m voicepipe.openclaw_client "What time is it?"
"""
from __future__ import annotations

import logging

import aiohttp

from .config import Config

log = logging.getLogger("voicepipe.openclaw")


class OpenClawClient:
    def __init__(self, config: Config, session: aiohttp.ClientSession):
        self._cfg = config
        self._session = session

    async def chat(self, user_text: str) -> str:
        messages = []
        if self._cfg.openclaw_system_prompt:
            messages.append({"role": "system", "content": self._cfg.openclaw_system_prompt})
        messages.append({"role": "user", "content": user_text})
        payload: dict = {
            "model": self._cfg.openclaw_model,
            "messages": messages,
        }
        headers = {"Content-Type": "application/json"}
        if self._cfg.openclaw_api_key:
            headers["Authorization"] = f"Bearer {self._cfg.openclaw_api_key}"
        if self._cfg.openclaw_session_mode == "user":
            payload["user"] = self._cfg.openclaw_session_key
        else:
            headers["X-Session-Key"] = self._cfg.openclaw_session_key

        async with self._session.post(
            f"{self._cfg.openclaw_url}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=self._cfg.openclaw_timeout_seconds),
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                raise RuntimeError(f"OpenClaw HTTP {resp.status}: {body}")

        try:
            reply = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected OpenClaw response shape: {body}") from exc

        log.info("OpenClaw: %d chars in -> %d chars out", len(user_text), len(reply))
        return reply


async def _smoke() -> None:
    import sys

    from .config import load_config

    if len(sys.argv) < 2:
        raise SystemExit('usage: python -m voicepipe.openclaw_client "your question"')

    cfg = load_config(require_xai_key=False)
    async with aiohttp.ClientSession() as session:
        client = OpenClawClient(cfg, session)
        reply = await client.chat(" ".join(sys.argv[1:]))
        print(reply)


if __name__ == "__main__":
    import asyncio

    asyncio.run(_smoke())
