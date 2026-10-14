"""Shared rule for recognizing model/assistant identities in reviewer or confirmer fields.

This is an extra guard layer only. A name that passes is not proof of a human review;
human quantity review is proven by qto-human-review-evidence records (see
qto_human_review_evidence.py). Short generic words match whole words so that real names
such as "Wang Kai" or "Bai" are not rejected.
"""
from __future__ import annotations

import re

SHORT_WORDS = frozenset({'ai', 'llm', 'model', 'assistant', 'bot', 'chatbot', 'copilot'})
FAMILY_PREFIXES = ('claude', 'codex', 'gpt-', 'chatgpt')
EXACT_NAMES = frozenset({'anthropic', 'openai', 'gpt'})
CJK_MARKERS = ('模型', '助手', '人工智能', '智能体', '机器人')


def is_model_identity(value) -> bool:
    """True when the text names a model, assistant or vendor instead of a person."""
    if not isinstance(value, str):
        return False
    text = value.strip().casefold()
    if not text:
        return False
    if text in EXACT_NAMES or text.startswith(FAMILY_PREFIXES):
        return True
    if any(word in SHORT_WORDS for word in re.findall(r'[a-z0-9]+', text)):
        return True
    return any(marker in text for marker in CJK_MARKERS)
