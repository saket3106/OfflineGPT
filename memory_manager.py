"""Advanced conversation memory, token budgeting, and context management for Offline GPT."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ContextBudget:
    total_tokens: int = 4096
    system_tokens: int = 600
    memory_tokens: int = 350
    summary_tokens: int = 450
    document_tokens: int = 1400
    # Recent turns get the remainder: total - (system + memory + summary + document)

    @property
    def recent_tokens(self) -> int:
        used = self.system_tokens + self.memory_tokens + self.summary_tokens + self.document_tokens
        return max(500, self.total_tokens - used)


def estimate_tokens(text: str) -> int:
    """Fast, conservative offline token estimate (approx 4 chars or 0.75 words per token)."""
    if not text:
        return 0
    words = len(text.split())
    chars = len(text)
    return max(1, int(max(words * 1.3, chars / 3.8)))


def fit_text_to_token_budget(text: str, token_budget: int) -> str:
    """Bound an oversized pasted message while keeping its request and ending."""
    text = str(text or "")
    if estimate_tokens(text) <= token_budget:
        return text
    budget = max(64, int(token_budget))
    char_budget = max(256, int(budget * 3.4))
    marker = "\n\n[Middle of long input omitted to fit local model context]\n\n"
    available = max(128, char_budget - len(marker))
    while True:
        head = int(available * 0.6)
        compact = text[:head].rstrip() + marker + text[-(available - head):].lstrip()
        if estimate_tokens(compact) <= budget or available <= 128:
            return compact
        available = max(128, int(available * 0.85))


SENSITIVE_PATTERNS = [
    re.compile(r"\b(?:api[_-]?key|secret|password|passwd|bearer\s+[A-Za-z0-9_\-\.]+)\b", re.I),
    re.compile(r"\b[A-Za-z0-9]{32,64}\b"),  # High entropy hex/hash strings
    re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),  # Credit card numbers
]


def is_sensitive(text: str) -> bool:
    """Detect passwords, tokens, API keys, and sensitive financial strings."""
    return any(p.search(text) for p in SENSITIVE_PATTERNS)


class ContextManager:
    """Manages multi-tier conversation context:

    1. Recent conversation context
    2. Rolling conversation summary
    3. Long-term user preferences
    4. Retrieved document context
    """

    def __init__(self, budget: ContextBudget | None = None):
        self.budget = budget or ContextBudget()

    def fit_recent_messages(
        self,
        messages: list[dict[str, str]],
        reserved_budget: int | None = None,
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Split message history into: (older_messages_to_summarize, recent_messages_to_keep)."""
        limit = reserved_budget if reserved_budget is not None else self.budget.recent_tokens
        if not messages:
            return [], []

        recent: list[dict[str, str]] = []
        older: list[dict[str, str]] = []
        accumulated_tokens = 0

        # Walk backwards from most recent message
        for msg in reversed(messages):
            tokens = estimate_tokens(msg.get("content", ""))
            if accumulated_tokens + tokens <= limit or len(recent) < 2:
                recent.append(msg)
                accumulated_tokens += tokens
            else:
                older.append(msg)

        recent.reverse()
        older.reverse()
        return older, recent

    @staticmethod
    def extract_preference_candidate(text: str) -> str | None:
        """Extract durable user preference or instruction from user message while filtering noise/secrets."""
        if is_sensitive(text):
            return None

        # Look for explicit preference phrasing
        match = re.search(
            r"\b(?:remember that|please remember|my name is|always (?:use|reply|format|write)|"
            r"i prefer|i like to|never (?:use|include|reply)|i am a|my tech stack is)\b\s*(.*)",
            text,
            re.I,
        )
        if match:
            candidate = match.group(0).strip().rstrip(".!")
            if 10 <= len(candidate) <= 180:
                return candidate
        return None

    @staticmethod
    def generate_rolling_summary(older_messages: list[dict[str, str]], existing_summary: str = "") -> str:
        """Create or update a concise structured summary of older conversation turns."""
        if not older_messages:
            return existing_summary

        topics = []
        for msg in older_messages:
            role = msg.get("role", "")
            content = msg.get("content", "").strip()
            if not content or role == "system":
                continue
            first_sentence = re.split(r"[.!?\n]", content)[0].strip()
            if first_sentence and len(first_sentence) > 8:
                summary_line = first_sentence[:90]
                topics.append(f"{role}: {summary_line}")

        if not topics:
            return existing_summary

        new_notes = "; ".join(topics[-6:])
        if existing_summary:
            combined = f"{existing_summary} | {new_notes}"
            # Keep summary bounded
            if len(combined) > 600:
                combined = combined[-600:]
            return combined
        return new_notes


def calculate_token_budget(
    category: str = "normal",
    total_context: int = 4096,
    prompt_tokens: int | None = None,
) -> tuple[int, int]:
    """Dynamically compute (max_prompt_tokens, output_budget) based on intent category and context.

    For exact/math/factual tasks: answers are concise (200-500 tokens).
    For normal/summarization tasks: answers are standard (600-900 tokens).
    For coding/writing/document tasks: answers need larger room (1200-1800 tokens).
    Always ensures headroom so prompt + output <= total_context - 16.
    """
    cat = (category or "normal").lower()

    if cat in {"math", "exact_format", "logic", "probability"}:
        target_output = 450
    elif cat in {"factual", "tool_required"}:
        target_output = 600
    elif cat in {"summarization", "normal"}:
        target_output = 850
    elif cat in {"coding", "writing", "document", "creative"}:
        target_output = 1600
    else:
        target_output = 850

    min_output = 128
    safety_margin = 16

    if prompt_tokens is not None:
        # Prompt size is known; ensure it fits and allocate remaining tokens to output
        available_for_output = max(min_output, total_context - prompt_tokens - safety_margin)
        output_budget = min(target_output, available_for_output)
        max_prompt = max(256, total_context - output_budget - safety_margin)
    else:
        # Estimate budgets before prompt generation
        output_budget = min(target_output, max(min_output, total_context - 512))
        max_prompt = total_context - output_budget - safety_margin

    return max_prompt, output_budget


def calculate_dynamic_context_budget(
    category: str = "normal",
    prompt_text: str = "",
    system_text: str = "",
    history_messages: list[dict[str, str]] | None = None,
    rag_text: str = "",
    total_context: int = 4096,
    safety_margin: int = 128,
    task_count: int = 0,
) -> tuple[int, list[dict[str, str]]]:
    """Dynamically allocate output token budget and trim older history turns as needed.

    Target outputs:
    - SHORT: 450 tokens (300-500) for math, probability, exact_format, logic
    - NORMAL: 800 tokens (600-1000) for factual, tool_required, normal, summarization
    - DETAILED: 1200 tokens (1000-1500) for writing, creative, document
    - CODE: 1600 tokens (dynamic up to 1600) for coding

    Trimming applies to older history turns only; newest user turn and system prompt are never trimmed.
    Returns (allocated_output_tokens, kept_history_messages).
    """
    cat = (category or "normal").lower()
    if cat in {"math", "probability", "exact_format", "logic"}:
        target_output = 450
    elif cat in {"factual", "tool_required", "normal", "summarization"}:
        target_output = 800
    elif cat in {"writing", "creative", "document"}:
        target_output = 1200
    elif cat in {"coding"}:
        target_output = 1600
    elif cat == "multi_task":
        # Reserve usable room for every independent item. A fixed 800-token
        # budget silently truncated larger benchmarks before GPT-OSS could
        # finish them. Context fitting below will discard old history first.
        target_output = min(2200, max(900, 300 + 160 * max(1, int(task_count or 1))))
    else:
        target_output = 800

    system_tokens = estimate_tokens(system_text)
    user_tokens = estimate_tokens(prompt_text)
    rag_tokens = estimate_tokens(rag_text)
    fixed_tokens = system_tokens + user_tokens + rag_tokens + safety_margin + 16

    max_history_budget = max(0, total_context - fixed_tokens - target_output)

    kept_history: list[dict[str, str]] = []
    current_history_tokens = 0

    if history_messages:
        # Walk backwards from most recent turn to preserve newest context
        for msg in reversed(history_messages):
            msg_toks = estimate_tokens(msg.get("content", "")) + 8
            if current_history_tokens + msg_toks <= max_history_budget:
                kept_history.append(msg)
                current_history_tokens += msg_toks
            else:
                break
        kept_history.reverse()

    # Keep the requested output reserve intact. The inference runtime tokenizes
    # the actual chat template and shortens removable history/system context to
    # fit this reserve. Reducing it here from a rough estimate (often to 128)
    # caused incomplete multipart answers even when the real prompt could fit.
    allocated_output = target_output

    return allocated_output, kept_history

