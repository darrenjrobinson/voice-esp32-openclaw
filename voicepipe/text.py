"""Sentence splitting for chunked TTS playback."""
from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_for_speech(text: str, merge_min_chars: int = 160) -> list[str]:
    """Split a reply into speakable chunks.

    The first chunk is always a single sentence (fast time-to-first-audio);
    later sentences are merged into chunks of at least merge_min_chars so
    per-announcement overhead stays small.
    """
    sentences = [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
        elif not chunks or len(current) >= merge_min_chars:
            chunks.append(current)
            current = sentence
        else:
            current += " " + sentence
    if current:
        chunks.append(current)
    # Replies without sentence punctuation (e.g. data read-outs) would come
    # through as one giant chunk — hard-split so first audio still starts fast.
    return [piece for chunk in chunks for piece in _hard_split(chunk)]


def _hard_split(chunk: str, max_chars: int = 250) -> list[str]:
    pieces: list[str] = []
    while len(chunk) > max_chars:
        cut = -1
        for sep in (", ", "; ", ": ", " "):
            cut = chunk.rfind(sep, max_chars // 2, max_chars)
            if cut != -1:
                cut += len(sep.rstrip())
                break
        if cut <= 0:
            cut = max_chars
        pieces.append(chunk[:cut].strip())
        chunk = chunk[cut:].strip()
    if chunk:
        pieces.append(chunk)
    return pieces
