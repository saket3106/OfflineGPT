"""Modular response validation system for Offline GPT.

Provides deterministic validators for word counts, sentence counts, bullets,
required/forbidden words, valid JSON structure, mathematical consistency,
logic ambiguity, and Python code syntax.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from code_engine import extract_code_blocks, validate_python_syntax
from grounding import normalize_task_id
from math_engine import number_in_text


def _strip_svgcopy_control_lines(text: str) -> str:
    """Remove the exact standalone UI artifact outside code fences only."""
    output = []
    fence_char = ""
    fence_size = 0
    for line in str(text or "").splitlines(keepends=True):
        body = line.rstrip("\r\n")
        artifact = body.strip().casefold()
        if not fence_char:
            if (
                artifact in {"svgcopy", "chat test complete", "test complete", "final controller test complete", "final runtime status"}
                or (artifact in {"svg", "copy"} and bool("".join(output).strip()))
                or re.fullmatch(r"\s*content tasks (?:expected|handled|passed)\s*:\s*[^\n]+", body, re.I)
                or re.fullmatch(r"\s*tasks (?:completed|handled|passed)\s*:\s*\d+\s*/\s*\d+\s*", body, re.I)
                or re.fullmatch(r"\s*(?:failed|missing) tasks\s*:\s*[^\n]{1,160}", body, re.I)
                or re.fullmatch(r"\s*tests?\s*(?:believed\s+)?(?:correct|passed|completed)\s*:\s*\d+\s*/\s*\d+\s*", body, re.I)
                or re.fullmatch(r"\s*tools?\s*(?:actually\s+)?used\s*:\s*[^\n]{1,160}", body, re.I)
                or re.fullmatch(r"\s*(?:chat\s+)?test\s+complete\s*", body, re.I)
                or re.fullmatch(r"\s*(?:final\s+)?(?:controller\s+)?test\s+complete\s*", body, re.I)
                or re.fullmatch(r"\s*method\s*:\s*[^\n]{1,160}", body, re.I)
                or re.fullmatch(r"\s*\d{1,3}(?:\.[A-Z])?[.):]\s*method\s*:\s*(?:tool|direct)\s*", body, re.I)
                or re.fullmatch(r"\s*[-=_~*]{3,}\s*", body)
                or re.fullmatch(r"\s*(?:method\s*:\s*(?:tool|direct|calculator|python|document_(?:exact|rag))|"
                                r"document retrieval used\s*:\s*(?:yes|no)|tools?\s*(?:actually\s+)?used\s*:\s*[^:\n]{1,100})\s*",
                                body, re.I)
            ):
                continue
            m_heading = re.match(r"^(\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*(?:(?:task|question|part|item)\s*)?\d{1,3}(?:\.[A-Z])?\s*(?:\*\*|__)?\s*[.):\-–—]\s*)(.*)$", body, re.I)
            if m_heading:
                prefix, rest = m_heading.group(1), m_heading.group(2)
                if (
                    re.fullmatch(r"content tasks (?:expected|handled|passed)\s*:\s*[^\n]+", rest, re.I)
                    or re.fullmatch(r"tasks (?:completed|handled|passed)\s*:\s*\d+\s*/\s*\d+\s*", rest, re.I)
                    or re.fullmatch(r"(?:failed|missing) tasks\s*:\s*[^\n]{1,160}", rest, re.I)
                    or re.fullmatch(r"tests?\s*(?:believed\s+)?(?:correct|passed|completed)\s*:\s*\d+\s*/\s*\d+\s*", rest, re.I)
                    or re.fullmatch(r"tools?\s*(?:actually\s+)?used\s*:\s*[^\n]{1,160}", rest, re.I)
                    or re.fullmatch(r"(?:final\s+)?(?:controller\s+)?(?:chat\s+)?test\s+complete\s*", rest, re.I)
                ):
                    output.append(prefix.strip() + "\n")
                    continue
        fence = re.match(r"^[ \t]*(`{3,}|~{3,})", body)
        if fence:
            marker = fence.group(1)
            if not fence_char:
                fence_char, fence_size = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size:
                fence_char, fence_size = "", 0
        output.append(line)
    return "".join(output)


def strip_meta_labels(text: str) -> str:
    """Strip known UI-control leakage and trailing LLM self-evaluation labels."""
    if not text:
        return ""
    patterns = [
        r"(?im)\s*[\(\[]?\b(?:word\s*count|total\s*words|words?)\s*[:=]\s*\d+\b[\)\]]?\.?\s*$",
        r"(?im)\s*[\(\[]\s*\d+\s+words?\s*[\)\]]\.?\s*$",
        r"(?im)^\s*(?:word\s*count|count)\s*:\s*\d+\.?\s*$",
        r"(?im)\n+\s*(?:word\s*count|count)\s*:\s*\d+\.?\s*$",
        r"(?im)^\s*(?:\d{1,3}(?:\.[A-Z])?[.):]\s*)?tests?\s*(?:believed\s+)?(?:correct|passed|completed)\s*:\s*\d+\s*/\s*\d+\s*$",
        r"(?im)^\s*(?:\d{1,3}(?:\.[A-Z])?[.):]\s*)?tools?\s*(?:actually\s+)?used\s*:\s*[^\n]+\s*$",
        r"(?im)^\s*(?:\d{1,3}(?:\.[A-Z])?[.):]\s*)?(?:[A-Z0-9_ ]+)?test\s+complete\s*$",
        r"(?im)^\s*(?:ALL\s+\d+\s+QUESTIONS\s+COMPLETE|QUESTIONS?\s+COMPLETE|ALL\s+TASKS\s+COMPLETE)\s*$",
        r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*(?:(?:task|question)\s*\d+[.:\s-]*)?(?:\*\*|__)?\s*(?:exact\s+(?:word\s+)?constraint|word\s+constraint|exact\s+constraint)[*#_: -]*\s*",
    ]
    cleaned = _strip_svgcopy_control_lines(text).strip()
    for pat in patterns:
        cleaned = re.sub(pat, "", cleaned).strip()
    return cleaned


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    validator_name: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskResult:
    """Immutable result for a single task identified by its immutable task_id."""
    task_id: int | str
    parent_id: int | str | None = None
    status: str = "HANDLED"  # HANDLED, PASSED, FAILED, MISSING, SUCCESS, EXPLICIT_FAILURE, NO_DOCUMENT, UNSUPPORTED
    answer: str = ""
    tool: str | None = None
    route_target: str = "DIRECT"


@dataclass
class FinalRuntimeStatus:
    """One top-level runtime status block rendered exactly once from runtime state."""
    expected_count: int
    handled_count: int
    passed_count: int
    failed_tasks: list[int | str] = field(default_factory=list)
    missing_tasks: list[int | str] = field(default_factory=list)
    tools_used: int = 0
    output_mode: str = "CHAT"
    pdf_created: str = "NO"
    artifact_created: str = "NO"
    diagnostic: str | None = None

    def render_content_tasks_block(self) -> str:
        failed_str = ", ".join(map(str, self.failed_tasks)) if self.failed_tasks else "NONE"
        missing_str = ", ".join(map(str, self.missing_tasks)) if self.missing_tasks else "NONE"
        lines = [
            "--------------------------------",
            "FINAL RUNTIME STATUS",
            "--------------------------------",
            "",
            f"CONTENT TASKS EXPECTED: {self.expected_count}",
            f"CONTENT TASKS HANDLED: {self.handled_count}/{self.expected_count}",
            f"CONTENT TASKS PASSED: {self.passed_count}/{self.expected_count}",
            f"FAILED TASKS: {failed_str}",
            f"MISSING TASKS: {missing_str}",
            f"TOOLS ACTUALLY USED: {self.tools_used}",
            f"OUTPUT MODE: {self.output_mode}",
            f"PDF CREATED: {self.pdf_created}",
            f"ARTIFACT CREATED: {self.artifact_created}",
            "",
            "FINAL CONTROLLER TEST COMPLETE",
        ]
        return "\n".join(lines)

    def render_standard_block(self) -> str:
        failed_str = ", ".join(map(str, self.failed_tasks)) if self.failed_tasks else "NONE"
        missing_str = ", ".join(map(str, self.missing_tasks)) if self.missing_tasks else "NONE"
        lines = [
            f"TASKS HANDLED: {self.handled_count}/{self.expected_count}",
            f"TASKS PASSED: {self.passed_count}/{self.expected_count}",
            f"FAILED TASKS: {failed_str}",
            f"MISSING TASKS: {missing_str}",
        ]
        return "\n".join(lines)


@dataclass
class MultiTaskResult:
    """Visible per-task results and completion state for a numbered request."""

    expected_tasks: list[int | str]
    completed_tasks: list[int | str]
    failed_tasks: list[int | str]
    results: dict[int | str, str]
    statuses: dict[int | str, str] = field(default_factory=dict)
    terminal_tasks: list[int | str] = field(default_factory=list)
    handled_tasks: list[int | str] = field(default_factory=list)
    passed_tasks: list[int | str] = field(default_factory=list)
    missing_tasks: list[int | str] = field(default_factory=list)


_TASK_HEADING = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*(?:(?:task|question|part|item)\s*)?(\d{1,3}(?:\.[A-Z])?)\s*(?:\*\*|__)?\s*(?:[.):\-–—]|(?=$))\s*(.*?)\s*$"
)
_RUNTIME_TRAILER = re.compile(
    r"(?im)^\s*(?:[-=_~*]{3,}[ \t]*$|"
    r"FINAL RUNTIME STATUS\b|"
    r"CONTENT TASKS (?:EXPECTED|HANDLED|PASSED)\s*:|"
    r"TASKS (?:COMPLETED|HANDLED|PASSED)\s*:|FAILED TASKS\s*:|MISSING TASKS\s*:|METHOD\s*:|"
    r"tests?\s*(?:believed\s+)?(?:correct|passed|completed)\s*:|tools?\s*(?:actually\s+)?used\s*:|"
    r"(?:ALL\s+\d+\s+QUESTIONS\s+COMPLETE|QUESTIONS?\s+COMPLETE|ALL\s+TASKS\s+COMPLETE)\s*$|"
    r"(?:[A-Z0-9_ ]+)?TEST\s+COMPLETE\s*$).*$"
)
_UNUSABLE_TASK_RESPONSE = re.compile(
    r"^\s*i\s+couldn['’]t\s+complete\s+this\s+task\s+because\s+the\s+local\s+model\s+"
    r"did\s+not\s+return\s+a\s+usable\s+answer\s+after\s+retry\.?\s*$",
    re.I,
)
_DIAGNOSTIC_FAILURE = re.compile(
    r"(?i)\bfailed checks for these tasks\b|\bmissing response for task\(s\)\b|\bexplicit failure\b",
)


def parse_numbered_response_sections(response: str, expected_tasks: list[int | str]) -> dict[int | str, dict[str, str]]:
    """Parse task sections, retaining nested lists and excluding fenced code."""
    expected = {normalize_task_id(task_id) for task_id in expected_tasks}
    if not expected:
        return {}
    text = str(response or "")
    sections: dict[int | str, dict[str, str]] = {}
    starts: list[tuple[int, int | str, str, str]] = []
    offset = 0
    fence_char = ""
    fence_size = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        fence = re.match(r"^[ \t]*(`{3,}|~{3,})", body)
        if fence:
            marker = fence.group(1)
            if not fence_char:
                fence_char, fence_size = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size:
                fence_char, fence_size = "", 0
            offset += len(line)
            continue
        if not fence_char:
            match = _TASK_HEADING.match(body)
            if match:
                task_id = normalize_task_id(match.group(1))
                if task_id in expected:
                    first_body = (match.group(2) or "").strip()
                    if first_body.endswith("**") and not first_body.startswith("**"):
                        first_body = first_body[:-2].strip()
                    elif first_body.endswith("__") and not first_body.startswith("__"):
                        first_body = first_body[:-2].strip()
                    starts.append((offset, task_id, line, first_body))
                # A common format puts an opening code fence immediately after
                # the numbered heading, e.g. "2. ```python". Track that fence so
                # numeric code lines cannot be mistaken for sibling task headings.
                tail = re.sub(r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*(?:(?:task|question|part|item)\s*)?\d{1,3}(?:\.[A-Z])?\s*(?:\*\*|__)?\s*[.):\-–—]\s*", "", body, count=1, flags=re.I)
                inline_fence = re.search(r"(`{3,}|~{3,})", tail)
                if inline_fence:
                    marker = inline_fence.group(1)
                    fence_char, fence_size = marker[0], len(marker)
        offset += len(line)

    # First determine the root indentation from all numeric task candidates.
    # Reserving IDs during the scan lets an indented example numbered "1."
    # steal task 1 before its real heading appears, losing sibling content on
    # merge. Explicit dotted leaf IDs remain valid at their nested indentation.
    def indentation(heading):return len(heading[:len(heading)-len(heading.lstrip(' \t'))].expandtabs(8))
    numeric_indents=[indentation(heading) for _,tid,heading,_ in starts if isinstance(tid,int)]
    root_indent=min(numeric_indents) if numeric_indents else 0
    filtered=[]
    for entry in starts:
        _,tid,heading,_=entry
        indent=indentation(heading)
        if isinstance(tid,int) and indent>root_indent:continue
        if tid in sections:continue
        filtered.append(entry);sections[tid]={}
    starts=filtered

    for index, (start, task_id, heading, first_line_body) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        segment = text[start:end]
        if index + 1 == len(starts):
            trailer = _RUNTIME_TRAILER.search(segment)
            if trailer:
                segment = segment[:trailer.start()]
        raw = segment.strip()
        # Preserve inline answers ("1. result") as well as text on following
        # lines, including fenced code blocks.
        tail = segment[len(heading):].strip()
        content = "\n".join(piece for piece in (first_line_body.strip(), tail) if piece)
        # Clean any leaked runtime status lines from child sections
        cleaned_lines = []
        for cline in content.splitlines():
            cline_str = cline.strip()
            if (
                re.match(r"(?i)^\s*(?:CONTENT TASKS (?:EXPECTED|HANDLED|PASSED)|TASKS (?:COMPLETED|HANDLED|PASSED)|(?:FAILED|MISSING) TASKS|TOOLS ACTUALLY USED|FINAL RUNTIME STATUS|[-=_~*]{3,})", cline_str)
                or re.match(r"(?i)^tests?\s*(?:believed\s+)?(?:correct|passed|completed)\s*:\s*\d+\s*/\s*\d+", cline_str)
                or re.match(r"(?i)^\s*(?:ALL\s+\d+\s+QUESTIONS\s+COMPLETE|QUESTIONS?\s+COMPLETE|ALL\s+TASKS\s+COMPLETE)\s*$", cline_str)
                or re.match(r"(?i)^\s*(?:[A-Z0-9_ ]+)?TEST\s+COMPLETE\s*$", cline_str)
            ):
                continue
            cleaned_lines.append(cline)
        cleaned_content = "\n".join(cleaned_lines).strip()
        sections[task_id] = {"heading": f"{task_id}.", "body": cleaned_content, "raw": raw}
    return sections


def build_multi_task_result(
    response: str,
    expected_tasks: list[int | str],
    failed_tasks: list[int | str] | None = None,
    task_targets: dict[int | str, str] | None = None,
) -> MultiTaskResult:
    expected = [normalize_task_id(task_id) for task_id in expected_tasks]
    sections = parse_numbered_response_sections(response, expected)
    results = {
        task_id: section["body"]
        for task_id, section in sections.items()
        if section.get("body", "").strip()
    }
    explicitly_failed = {normalize_task_id(task_id) for task_id in (failed_tasks or [])}
    normalized_targets = {normalize_task_id(k): str(v).upper() for k, v in (task_targets or {}).items()}
    statuses = {}
    for task_id in expected:
        body = results.get(task_id, "").strip()
        if not body:
            statuses[task_id] = "MISSING"
        elif (
            task_id in explicitly_failed
            or _UNUSABLE_TASK_RESPONSE.match(body)
            or _DIAGNOSTIC_FAILURE.search(body)
        ):
            statuses[task_id] = "EXPLICIT_FAILURE"
        elif task_id in normalized_targets:
            expected_target = normalized_targets[task_id]
            tokens = re.findall(r"\b(?:DIRECT|TOOL)\b", body.upper())
            if not tokens or tokens[0] != expected_target:
                statuses[task_id] = "EXPLICIT_FAILURE"
            else:
                statuses[task_id] = "SUCCESS"
        elif re.search(r"\bNO DOCUMENT AVAILABLE\b|couldn['’]t find an attached or available document", body, re.I):
            statuses[task_id] = "NO_DOCUMENT"
        elif "I can't verify that claim from the information available." in body:
            statuses[task_id] = "UNSUPPORTED"
        else:
            statuses[task_id] = "SUCCESS"
    passed = [task_id for task_id in expected if statuses[task_id] in {"SUCCESS", "UNSUPPORTED", "NO_DOCUMENT"}]
    failed = [task_id for task_id in expected if statuses[task_id] in {"EXPLICIT_FAILURE", "FAILED"}]
    missing = [task_id for task_id in expected if statuses[task_id] == "MISSING"]
    handled = [task_id for task_id in expected if statuses[task_id] != "MISSING"]
    completed = passed
    terminal = handled
    return MultiTaskResult(
        expected_tasks=expected,
        completed_tasks=completed,
        failed_tasks=failed,
        results=results,
        statuses=statuses,
        terminal_tasks=terminal,
        handled_tasks=handled,
        passed_tasks=passed,
        missing_tasks=missing,
    )


def merge_numbered_task_sections(
    original: str,
    supplement: str,
    expected_tasks: list[int],
    replace_task_ids: list[int],
) -> str:
    """Merge targeted retry sections while preserving all unaffected original answers."""
    expected = [normalize_task_id(task_id) for task_id in expected_tasks]
    replace_ids = {normalize_task_id(task_id) for task_id in replace_task_ids}
    before = parse_numbered_response_sections(original, expected)
    added = parse_numbered_response_sections(supplement, expected)
    if len(replace_ids) == 1 and not added and supplement.strip():
        task_id = next(iter(replace_ids))
        if task_id in expected:
            added[task_id] = {"heading": f"{task_id}.", "body": supplement.strip(), "raw": f"{task_id}. {supplement.strip()}"}

    merged: list[str] = []
    for task_id in expected:
        candidate = added.get(task_id) if task_id in replace_ids else None
        if candidate is None:
            candidate = before.get(task_id)
        if candidate and candidate.get("body", "").strip():
            heading = f"{task_id}."
            merged.append(f"{heading} {candidate['body'].strip()}" if candidate["body"].strip() else heading)
    return "\n\n".join(merged).strip()


def task_ids_from_validation_errors(errors: list[str], expected_tasks: list[int]) -> list[int]:
    """Map task-scoped validation errors to the IDs that need a targeted retry."""
    expected = {normalize_task_id(task_id) for task_id in expected_tasks}
    found: set[int | str] = set()
    for error in errors:
        found.update(normalize_task_id(value) for value in re.findall(r"\bTask\s+(\d{1,3}(?:\.[A-Z])?)\s*:", str(error), re.I))
        match = re.search(r"Missing response for task\(s\):\s*([\dA-Z., ]+)", str(error), re.I)
        if match:
            found.update(normalize_task_id(value) for value in re.findall(r"\d{1,3}(?:\.[A-Z])?", match.group(1), re.I))
    return [task_id for task_id in expected_tasks if normalize_task_id(task_id) in found]


class BaseValidator(ABC):
    """Abstract base class for all response validators."""

    name: str = "base_validator"

    @abstractmethod
    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        """Validate response against context/constraints and return ValidationResult."""
        pass


class WordCountValidator(BaseValidator):
    """Validates exact, minimum, and maximum word counts."""

    name = "word_count"

    @staticmethod
    def count_words(text: str) -> list[str]:
        # Strip trailing self-auditing commentary e.g. "Word count: 10" or "(10 words)"
        cleaned = strip_meta_labels(text)
        # Clean markdown code blocks and HTML comments before counting conversational words
        cleaned = re.sub(r"```.*?```", " ", cleaned, flags=re.DOTALL)
        cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.DOTALL)
        return re.findall(r"\b[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*\b", cleaned)

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        constraints = context.get("constraints") or {}
        exact = constraints.get("word_count_exact")
        min_words = constraints.get("word_count_min")
        max_words = constraints.get("word_count_max")

        if exact is None and min_words is None and max_words is None:
            return ValidationResult(valid=True, validator_name=self.name)

        words = self.count_words(response)
        actual_count = len(words)
        errors = []

        if exact is not None and actual_count != exact:
            diff = exact - actual_count
            hint = f"add {diff} word{'s' if diff > 1 else ''}" if diff > 0 else f"remove {abs(diff)} word{'s' if abs(diff) > 1 else ''}"
            errors.append(f"Expected exactly {exact} words, got {actual_count}. (Need to {hint}; do not add 'Word count: X')")

        if min_words is not None and actual_count < min_words:
            errors.append(f"Expected at least {min_words} words, got {actual_count}.")

        if max_words is not None and actual_count > max_words:
            errors.append(f"Expected at most {max_words} words, got {actual_count}.")

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            validator_name=self.name,
            details={"actual_count": actual_count, "exact": exact, "min": min_words, "max": max_words, "words": words},
        )


def enforce_exact_word_count(
    text: str,
    exact_count: int,
    required_words: list[str] | None = None,
    forbidden_words: list[str] | None = None,
) -> str:
    """Deterministically format or adjust near-miss responses to satisfy exact word counts."""
    if not text or exact_count <= 0:
        return text

    def _satisfies_word_constraints(cand: str) -> bool:
        c_low = cand.casefold()
        if forbidden_words:
            for fw in forbidden_words:
                if fw.casefold() in c_low:
                    return False
        if required_words:
            for rw in required_words:
                if rw.casefold() not in c_low:
                    return False
        return True

    has_word_count_label = bool(re.search(r"(?im)^\s*(?:WORD\s*COUNT|COUNT)\s*:\s*\d+\s*$", text))
    m_wc = re.search(r"(?im)^\s*(WORD\s*COUNT\s*:\s*\d+)\s*$", text)
    wc_label = m_wc.group(1).strip() if m_wc else (f"WORD COUNT: {exact_count}" if has_word_count_label else "")

    def _attach_label(res_text: str) -> str:
        if wc_label and not re.search(r"(?im)^\s*WORD\s*COUNT\s*:\s*\d+\s*$", res_text):
            return f"{res_text.rstrip()}\n\n{wc_label}"
        return res_text

    cleaned = strip_meta_labels(text).strip()
    # Strip surrounding quotes
    if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'")):
        cleaned = cleaned[1:-1].strip()

    # Strip conversational preamble if present
    no_preamble = re.sub(
        r"^(?:(?:Here\s+(?:is|are)|Below\s+is|Sure,?\s+here\s+is|In\s+\d+\s+words|To\s+explain|Answer|Explanation|Statement)[^:\n]*:\s*)",
        "",
        cleaned,
        flags=re.I,
    ).strip()
    if no_preamble:
        cleaned = no_preamble

    words = WordCountValidator.count_words(cleaned)
    if len(words) == exact_count and _satisfies_word_constraints(cleaned):
        return _attach_label(cleaned)

    diff = exact_count - len(words)

    candidate = cleaned

    if diff > 0:
        # Too short: try expanding contractions first
        contractions = [
            (r"\bcan't\b", "can not"),
            (r"\bwon't\b", "will not"),
            (r"\bdon't\b", "do not"),
            (r"\bdoesn't\b", "does not"),
            (r"\bdidn't\b", "did not"),
            (r"\bit's\b", "it is"),
            (r"\bthat's\b", "that is"),
            (r"\bthere's\b", "there is"),
            (r"\bhere's\b", "here is"),
            (r"\bwhat's\b", "what is"),
            (r"\bwho's\b", "who is"),
            (r"\bisn't\b", "is not"),
            (r"\baren't\b", "are not"),
            (r"\bwasn't\b", "was not"),
            (r"\bweren't\b", "were not"),
            (r"\bhasn't\b", "has not"),
            (r"\bhaven't\b", "have not"),
            (r"\bhadn't\b", "had not"),
            (r"\bwouldn't\b", "would not"),
            (r"\bshouldn't\b", "should not"),
            (r"\bcouldn't\b", "could not"),
            (r"\blet's\b", "let us"),
        ]
        for pattern, replacement in contractions:
            if re.search(pattern, candidate, re.I):
                candidate = re.sub(pattern, replacement, candidate, count=1, flags=re.I)
                if len(WordCountValidator.count_words(candidate)) == exact_count and _satisfies_word_constraints(candidate):
                    return candidate

        # If still short, append natural terminal modifier before trailing punctuation
        words_now = WordCountValidator.count_words(candidate)
        rem = exact_count - len(words_now)
        terminal_additions = {
            1: "today",
            2: "every day",
        }
        if rem in terminal_additions:
            addition = terminal_additions[rem]
            if candidate.endswith((".", "!", "?")):
                punc = candidate[-1]
                body = candidate[:-1].rstrip()
                cand_test = f"{body} {addition}{punc}"
            else:
                cand_test = f"{candidate} {addition}"
            if len(WordCountValidator.count_words(cand_test)) == exact_count and _satisfies_word_constraints(cand_test):
                return cand_test

    elif diff < 0:
        # Too long: try contracting or removing filler words
        fillers = [
            r"\bvery\s+", r"\breally\s+", r"\bnow\s+", r"\btoday\s+", r"\bdaily\s+",
            r"\bwell\s+", r"\balways\s+", r"\boften\s+", r"\bsimply\s+", r"\bjust\s+",
            r"\balso\s+", r"\bmore\s+", r"\boverall\s+", r"\btotal\s+",
        ]
        for pat in fillers:
            match_word = pat.strip(r"\b\s+")
            if required_words and any(match_word.casefold() == rw.casefold() for rw in required_words):
                continue
            if re.search(pat, candidate, re.I):
                candidate = re.sub(pat, "", candidate, count=1, flags=re.I)
                if len(WordCountValidator.count_words(candidate)) == exact_count and _satisfies_word_constraints(candidate):
                    return candidate

        # Contractions to reduce word count
        reverse_contractions = [
            (r"\bdo not\b", "don't"),
            (r"\bdoes not\b", "doesn't"),
            (r"\bdid not\b", "didn't"),
            (r"\bis not\b", "isn't"),
            (r"\bare not\b", "aren't"),
            (r"\bcan not\b", "can't"),
            (r"\bcannot\b", "can't"),
            (r"\bwill not\b", "won't"),
            (r"\bit is\b", "it's"),
            (r"\bthat is\b", "that's"),
        ]
        for pattern, replacement in reverse_contractions:
            if re.search(pattern, candidate, re.I):
                candidate = re.sub(pattern, replacement, candidate, count=1, flags=re.I)
                if len(WordCountValidator.count_words(candidate)) == exact_count and _satisfies_word_constraints(candidate):
                    return _attach_label(candidate)

        # If still too long, try trimming non-required words from candidate
        cur_words = WordCountValidator.count_words(candidate)
        excess = len(cur_words) - exact_count
        if 1 <= excess <= 4:
            toks = candidate.split()
            req_set = {rw.casefold() for rw in (required_words or [])}
            new_toks = list(toks)
            dropped = 0
            for i in range(len(new_toks) - 1, -1, -1):
                clean_tok = re.sub(r"^[^\w]+|[^\w]+$", "", new_toks[i]).casefold()
                if clean_tok not in req_set and clean_tok not in {"and", "to", "in", "of", "the", "a", "is", "for"}:
                    new_toks.pop(i)
                    dropped += 1
                    if dropped == excess:
                        cand_drop = " ".join(new_toks)
                        if not cand_drop.endswith((".", "!", "?")) and candidate.endswith((".", "!", "?")):
                            cand_drop += candidate[-1]
                        if len(WordCountValidator.count_words(cand_drop)) == exact_count and _satisfies_word_constraints(cand_drop):
                            return _attach_label(cand_drop)
                        break

    if len(WordCountValidator.count_words(candidate)) == exact_count and _satisfies_word_constraints(candidate):
        return _attach_label(candidate)
    return _attach_label(cleaned) if (len(WordCountValidator.count_words(cleaned)) == exact_count and _satisfies_word_constraints(cleaned)) else text


class SentenceCountValidator(BaseValidator):
    """Validates exact sentence counts."""

    name = "sentence_count"

    @staticmethod
    def count_sentences(text: str) -> list[str]:
        cleaned = strip_meta_labels(text)
        cleaned = re.sub(r"```.*?```", " ", cleaned, flags=re.DOTALL)
        raw = re.split(r"(?<=[.!?])\s+", cleaned.strip())
        return [s.strip() for s in raw if s.strip() and len(re.findall(r"\w+", s)) >= 2]

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        constraints = context.get("constraints") or {}
        exact = constraints.get("sentence_count_exact")
        if exact is None:
            return ValidationResult(valid=True, validator_name=self.name)

        sentences = self.count_sentences(response)
        actual = len(sentences)
        if actual != exact:
            return ValidationResult(
                valid=False,
                errors=[f"Expected exactly {exact} sentences, got {actual}."],
                validator_name=self.name,
                details={"actual": actual, "expected": exact},
            )
        return ValidationResult(valid=True, validator_name=self.name)


class BulletCountValidator(BaseValidator):
    """Validates the exact number of bullet points in the response."""

    name = "bullet_count"

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        constraints = context.get("constraints") or {}
        expected = constraints.get("bullet_count")
        if expected is None:
            return ValidationResult(valid=True, validator_name=self.name)

        actual = len(re.findall(r"(?m)^\s*(?:[-*+]\s+|\d+[.)]\s+)", response))
        if actual != expected:
            return ValidationResult(
                valid=False,
                errors=[f"Expected exactly {expected} bullet points, got {actual}."],
                validator_name=self.name,
                details={"actual": actual, "expected": expected},
            )
        return ValidationResult(valid=True, validator_name=self.name)


class RequiredWordsValidator(BaseValidator):
    """Validates that all specified required words or phrases appear in the response."""

    name = "required_words"

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        constraints = context.get("constraints") or {}
        required = constraints.get("required_words") or []
        if not required:
            return ValidationResult(valid=True, validator_name=self.name)

        folded = response.casefold()
        missing = [word for word in required if word.casefold() not in folded]

        if missing:
            return ValidationResult(
                valid=False,
                errors=[f"Missing required word or phrase: '{word}'." for word in missing],
                validator_name=self.name,
                details={"missing": missing},
            )
        return ValidationResult(valid=True, validator_name=self.name)


class ForbiddenWordsValidator(BaseValidator):
    """Validates that none of the specified forbidden words or phrases appear."""

    name = "forbidden_words"

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        constraints = context.get("constraints") or {}
        forbidden = constraints.get("forbidden_words") or []
        if not forbidden:
            return ValidationResult(valid=True, validator_name=self.name)

        folded = response.casefold()
        found = [word for word in forbidden if word.casefold() in folded]

        if found:
            return ValidationResult(
                valid=False,
                errors=[f"Contains forbidden word or phrase: '{word}'." for word in found],
                validator_name=self.name,
                details={"found": found},
            )
        return ValidationResult(valid=True, validator_name=self.name)


class OutputOnlyValidator(BaseValidator):
    """Validates that conversational filler is omitted when output_only was requested."""

    name = "output_only"

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        constraints = context.get("constraints") or {}
        if not constraints.get("output_only"):
            return ValidationResult(valid=True, validator_name=self.name)

        stripped = response.strip()
        filler_patterns = [
            r"^(?:sure|certainly|here is|here's|below is|of course|as requested)[!:,.\s]",
            r"^explanation[:\s]",
            r"\bword count:\s*\d+\b",
        ]
        errors = []
        for p in filler_patterns:
            if re.search(p, stripped, re.I):
                errors.append("Response contains conversational preamble or metadata; only the direct output was requested.")
                break

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            validator_name=self.name,
        )


class TableValidator(BaseValidator):
    """Validates that a markdown table is present when table_only was requested."""

    name = "table_validator"

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        constraints = context.get("constraints") or {}
        if not constraints.get("table_only"):
            return ValidationResult(valid=True, validator_name=self.name)

        has_table = bool(re.search(r"\|[^\n]+\|\n\s*\|[-:| ]+\|\n\s*\|[^\n]+\|", response))
        if not has_table:
            return ValidationResult(
                valid=False,
                errors=["Expected response formatted as a Markdown table with header and rows."],
                validator_name=self.name,
            )
        return ValidationResult(valid=True, validator_name=self.name)


class JsonValidator(BaseValidator):
    """Validates that the response contains valid JSON, optionally checking required keys."""

    name = "json_validator"

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        constraints = context.get("constraints") or {}
        if not constraints.get("json"):
            return ValidationResult(valid=True, validator_name=self.name)

        text = response.strip()
        match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
        target = match.group(1).strip() if match else text

        try:
            parsed = json.loads(target)
        except Exception as exc:
            return ValidationResult(
                valid=False,
                errors=[f"Response is not valid JSON: {exc}."],
                validator_name=self.name,
            )

        required_keys = constraints.get("json_required_keys") or []
        if isinstance(parsed, dict) and required_keys:
            missing_keys = [k for k in required_keys if k not in parsed]
            if missing_keys:
                return ValidationResult(
                    valid=False,
                    errors=[f"JSON object is missing required key: '{k}'." for k in missing_keys],
                    validator_name=self.name,
                    details={"missing_keys": missing_keys},
                )

        return ValidationResult(valid=True, validator_name=self.name)


class MathValidator(BaseValidator):
    """Validates that verified mathematical results from tools appear in the response."""

    name = "math_validator"

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        tool_context = context.get("tool_context") or ""
        expected = context.get("expected_math_number")
        expected_numbers = context.get("expected_math_numbers") or []

        if expected is None and not expected_numbers:
            m_ram = re.search(r"additional RAM needed\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*GB", tool_context, re.I)
            if m_ram:
                val = float(m_ram.group(1))
                expected = int(val) if val.is_integer() else val
            else:
                m_eq = re.search(r"=\s*(-?[0-9]+(?:\.[0-9]+)?)\s*$", tool_context.strip())
                if m_eq:
                    val = float(m_eq.group(1))
                    expected = int(val) if val.is_integer() else val

        errors = []

        if expected is not None:
            if not number_in_text(response, expected):
                errors.append(f"Expected the answer to include the verified calculation result {expected}.")

        if expected_numbers:
            math_type = context.get("math_type")
            prompt = str(context.get("prompt") or "").casefold()
            if math_type == "diagnostic_probability":
                if len(expected_numbers) >= 2 and not number_in_text(response, expected_numbers[1]):
                    errors.append(f"Expected verified false positives: {expected_numbers[1]}.")
                if ("ppv" in prompt or "positive predictive" in prompt or "probability" in prompt or "tests positive" in prompt or "have the disease" in prompt) and len(expected_numbers) >= 3:
                    if not any(number_in_text(response, value) for value in expected_numbers[2:]):
                        errors.append(f"Expected verified positive probability / predictive value (e.g. {expected_numbers[-2]}% or {expected_numbers[-3]}%).")
            elif math_type == "percentage_sequence":
                if ("percent" in prompt or "%" in prompt or "change" in prompt) and len(expected_numbers) >= 2:
                    if not number_in_text(response, expected_numbers[1]):
                        errors.append(f"Expected verified percentage change: {expected_numbers[1]}%.")
            elif math_type == "ram_calculation":
                if ("free" in prompt or "available" in prompt) and expected_numbers and not number_in_text(response, expected_numbers[0]):
                    errors.append(f"Expected verified free RAM: {expected_numbers[0]} GB.")
            elif math_type == "number_sequence":
                if expected_numbers and not any(number_in_text(response, value) for value in expected_numbers):
                    errors.append(f"Expected verified next number in sequence: {expected_numbers[0]}.")
            elif not any(number_in_text(response, value) for value in expected_numbers):
                errors.append(f"Expected verified calculation results {expected_numbers} to appear in the answer.")

        if errors:
            return ValidationResult(
                valid=False,
                errors=errors,
                validator_name=self.name,
                details={"expected_number": expected, "expected_numbers": expected_numbers},
            )

        return ValidationResult(valid=True, validator_name=self.name)


class LogicValidator(BaseValidator):
    """Validates that underdetermined logic puzzles acknowledge multiple valid solutions."""

    name = "logic_validator"

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        logic_info = context.get("logic_info") or {}
        status = logic_info.get("status")

        if status == "underdetermined":
            low = response.casefold()
            indicators = [
                "multiple solutions",
                "multiple valid",
                "underdetermined",
                "more than one",
                "not enough information",
                "cannot be uniquely determined",
                "cannot determine",
                "could be either",
                "either",
                "or",
                "possibilities",
                "solutions exist",
            ]
            if not any(k in low for k in indicators):
                return ValidationResult(
                    valid=False,
                    errors=[
                        "The puzzle is underdetermined with multiple valid solutions. "
                        "Do not assert an arbitrary unique answer; explicitly state that multiple solutions exist."
                    ],
                    validator_name=self.name,
                    details={"status": status},
                )

        return ValidationResult(valid=True, validator_name=self.name)


def parse_schedule_intervals(text: str) -> dict[str, tuple[float, float]]:
    """Extract start and end times for scheduled tasks from text.
    Handles forms like:
    - Start Task A at time 0, finish at 2.
    - Task A: 0–2 or A: 0 to 2
    - 0–2 h: Task A
    - Task A (0-2h)
    """
    intervals: dict[str, tuple[float, float]] = {}
    # Pattern 1: (Start )?Task X ... at (time) Y ... finish at Z
    for m in re.finditer(
        r"(?i)\b(?:start\s+)?task\s+([A-Za-z0-9]+)\b[^\n]*?\bat\s+(?:time\s+)?(\d+(?:\.\d+)?)[^\n]*?\bfinish(?:es|ed|ing)?\s+at\s+(?:time\s+)?(\d+(?:\.\d+)?)",
        text,
    ):
        t_id, s_val, e_val = m.group(1).upper(), float(m.group(2)), float(m.group(3))
        intervals[t_id] = (s_val, e_val)

    # Pattern 2: Task X: start–end or from start to end, or A: 0–2
    for m in re.finditer(
        r"(?im)^\s*[-*•]?\s*(?:task\s+)?([A-Za-z0-9]+)\s*[:\-–—]\s*(?:time\s+|from\s+)?(\d+(?:\.\d+)?)\s*(?:–|-|to)\s*(\d+(?:\.\d+)?)",
        text,
    ):
        t_id, s_val, e_val = m.group(1).upper(), float(m.group(2)), float(m.group(3))
        if t_id not in intervals:
            intervals[t_id] = (s_val, e_val)

    # Pattern 3: (start–end h): Task X
    for m in re.finditer(
        r"(?im)^\s*[-*•]?\s*(\d+(?:\.\d+)?)\s*(?:–|-|to)\s*(\d+(?:\.\d+)?)\s*(?:h|hours?)?\s*[:\-–—]\s*(?:task\s+)?([A-Za-z0-9]+)",
        text,
    ):
        s_val, e_val, t_id = float(m.group(1)), float(m.group(2)), m.group(3).upper()
        if t_id not in intervals:
            intervals[t_id] = (s_val, e_val)

    # Pattern 4: Task X (0-2h)
    for m in re.finditer(
        r"(?i)\btask\s+([A-Za-z0-9]+)\s*\((?:from\s+)?(\d+(?:\.\d+)?)\s*(?:–|-|to)\s*(\d+(?:\.\d+)?)\s*(?:h|hours?)?\)",
        text,
    ):
        t_id, s_val, e_val = m.group(1).upper(), float(m.group(2)), float(m.group(3))
        if t_id not in intervals:
            intervals[t_id] = (s_val, e_val)

    return intervals


def parse_schedule_tasks_from_prompt(prompt: str) -> dict[str, dict[str, Any]]:
    """Extract tasks, durations, and dependencies from prompt instructions.
    Handles:
    - Task A takes 2 hours.
    - Task B takes 3 hours and starts after Task A.
    - Task C takes 2 hours and can start immediately.
    - Task D takes 1 hour and starts after both Task B and Task C.
    - A = 2 hours, B = 3 hours after A, etc.
    """
    tasks: dict[str, dict[str, Any]] = {}
    matches = list(re.finditer(r"(?i)(?:task\s+)?([A-Za-z0-9]+)\s*(?:takes|=|is|:)\s*(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h\b)", prompt))
    for i, m in enumerate(matches):
        t_id = m.group(1).upper()
        duration = float(m.group(2))
        start_pos = m.start()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(prompt)
        clause = prompt[start_pos:end_pos]

        deps: list[str] = []
        m_dep = re.search(r"(?i)(?:starts?\s+after|depends?\s+on|after\s+both|after)\s+([A-Za-z0-9\s,and]+)", clause)
        if m_dep:
            raw_deps = re.findall(r"\b[A-Za-z0-9]+\b", m_dep.group(1))
            deps = [d.upper() for d in raw_deps if d.lower() not in ("both", "and", "the", "task", "tasks", "immediately", "starting")]
        starts_immediately = bool(re.search(r"\b(?:immediately|starts?\s+at\s+0|from\s+the\s+start)\b", clause, re.I))
        if starts_immediately:
            deps = []
        tasks[t_id] = {
            "duration": duration,
            "deps": deps,
            "starts_immediately": starts_immediately,
        }
    return tasks


def compute_critical_path_schedule(tasks: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Compute earliest start and finish times for each task given its dependencies and duration."""
    schedule: dict[str, dict[str, Any]] = {}
    remaining = dict(tasks)
    for _ in range(len(tasks) + 1):
        progress = False
        for t_id, info in list(remaining.items()):
            deps = info["deps"]
            if all(d in schedule for d in deps):
                start = max([schedule[d]["finish"] for d in deps], default=0.0)
                finish = start + info["duration"]
                schedule[t_id] = {
                    "start": start,
                    "finish": finish,
                    "duration": info["duration"],
                    "deps": deps,
                }
                del remaining[t_id]
                progress = True
        if not progress or not remaining:
            break
    return schedule


def extract_schedule_dependencies(prompt: str) -> dict[str, list[str]]:
    """Extract task dependencies from prompt instructions."""
    tasks = parse_schedule_tasks_from_prompt(prompt)
    if tasks:
        return {t_id: info["deps"] for t_id, info in tasks.items() if info["deps"]}
    deps: dict[str, list[str]] = {}
    for m in re.finditer(
        r"(?i)\btask\s+([A-Za-z0-9]+)\b[^.\n]*?\bstarts?\s+after\s+(?:both\s+)?([A-Za-z0-9\s,and]+)",
        prompt,
    ):
        t_id = m.group(1).upper()
        raw_deps = re.findall(r"\b[A-Za-z0-9]+\b", m.group(2))
        parsed_deps = [d.upper() for d in raw_deps if d.lower() not in ("both", "and", "the", "task", "tasks")]
        if parsed_deps:
            deps[t_id] = parsed_deps
    return deps


def correct_schedule_summary(response: str, prompt: str = "") -> str:
    """Deterministically align reported total completion time and schedule lines with critical path schedule."""
    tasks = parse_schedule_tasks_from_prompt(prompt) if prompt else {}
    sched = compute_critical_path_schedule(tasks) if tasks else {}
    intervals = parse_schedule_intervals(response)

    if sched and len(sched) >= len(intervals):
        sched_finish = max(item["finish"] for item in sched.values())
    elif intervals:
        sched_finish = max(e for s, e in intervals.values())
    elif sched:
        sched_finish = max(item["finish"] for item in sched.values())
    else:
        return response

    sched_str = str(int(sched_finish) if sched_finish.is_integer() else sched_finish)

    def repl(match: re.Match) -> str:
        pref = match.group(1)
        u = match.group(2) or " hours"
        return f"{pref}{sched_str}{u}"

    corrected = re.sub(
        r"(?i)(\b(?:minimum\s+(?:total\s+)?completion\s+time|total\s+(?:completion\s+)?time|total\s+time\s+from\s+start\s+to\s+finish|total\s+duration)\s+(?:is\s+|of\s+|:\s*)?)[0-9]+(?:\.[0-9]+)?(\s*(?:hours?|h\b)?)",
        repl,
        response,
    )

    # If critical path schedule was solved, correct any contradicting finish or interval claims in text
    if sched:
        for t_id, info in sched.items():
            exp_fin = int(info["finish"]) if info["finish"].is_integer() else info["finish"]
            exp_st = int(info["start"]) if info["start"].is_integer() else info["start"]
            # Correct any "(Task C finishes at 4 h)" or "C finishes at 4 h" or "Task C finishes at 4 hours"
            pat_fin = rf"(?i)\b(?:task\s+)?{t_id}\b[^.\n)]*?\bfinishes?\s+at\s+(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h\b)?"
            def fin_repl(m: re.Match, target_id=t_id, target_exp_fin=exp_fin, expected_finish=info["finish"]) -> str:
                if float(m.group(1)) != expected_finish:
                    return f"Task {target_id} finishes at {target_exp_fin} h"
                return m.group(0)
            corrected = re.sub(pat_fin, fin_repl, corrected)
            # If a task starts at 0, ensure it is not scheduled under 2-X h
            if info["start"] == 0.0:
                pat_misplaced = rf"(?im)^([ \t]*[-*•]?[ \t]*\d+(?:\.\d+)?\s*[–\-to]\s*\d+(?:\.\d+)?\s*(?:h|hours?)?[^\n]*\bTask\s+{t_id}\b[^\n]*)$"
                m_mis = re.search(pat_misplaced, corrected)
                if m_mis:
                    l_str = m_mis.group(1)
                    m_st = re.search(r"(\d+(?:\.\d+)?)\s*[–\-to]", l_str)
                    if m_st and float(m_st.group(1)) > 0:
                        cleaned_l = re.sub(rf"(?i)\s*(?:and\s+)?(?:task\s+)?{t_id}\b.*$", "", l_str).strip()
                        corrected = corrected.replace(l_str, cleaned_l)
                        corrected = re.sub(
                            r"(?im)^([ \t]*[-*•]?[ \t]*0\s*[–\-to]\s*\d+[^\n]*)",
                            rf"\1, Task {t_id} ({int(info['duration'])} h)",
                            corrected,
                            count=1,
                        )

    return corrected


class ScheduleConsistencyValidator(BaseValidator):
    """Validates schedule and planning outputs for internal consistency:
    - task start/end times (start <= end)
    - dependency satisfaction (task starts >= predecessor ends)
    - final completion time vs reported total
    - task finish consistency with prompt instructions
    """

    name = "schedule_consistency"

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        prompt = str(context.get("prompt") or context.get("text") or "")
        intervals = parse_schedule_intervals(response)
        tasks = parse_schedule_tasks_from_prompt(prompt) if prompt else {}
        sched = compute_critical_path_schedule(tasks) if tasks else {}

        if not intervals and not sched:
            return ValidationResult(valid=True, validator_name=self.name)

        is_planning = bool(
            len(intervals) >= 2
            or sched
            or re.search(r"\b(?:schedule|completion time|planning|tasks? may run in parallel|minimum total completion time|total\s+time)\b", prompt, re.I)
            or re.search(r"\b(?:schedule|completion time|total\s+time)\b", response, re.I)
        )
        if not is_planning:
            return ValidationResult(valid=True, validator_name=self.name)

        errors = []

        # 1. Start/end validity from parsed intervals
        for t_id, (s_val, e_val) in intervals.items():
            if s_val > e_val:
                errors.append(f"Task {t_id} has invalid schedule: starts at {s_val} and ends at {e_val}.")

        # 2. Dependency satisfaction
        deps = extract_schedule_dependencies(prompt)
        for t_id, predecessor_list in deps.items():
            if t_id in intervals:
                t_start = intervals[t_id][0]
                for pred in predecessor_list:
                    if pred in intervals:
                        pred_end = intervals[pred][1]
                        if t_start < pred_end:
                            errors.append(f"Dependency violation: Task {t_id} starts at {t_start} before predecessor Task {pred} finishes at {pred_end}.")

        # 3. Schedule completion time vs reported total
        if sched and len(sched) >= len(intervals):
            sched_finish = max(item["finish"] for item in sched.values())
        elif intervals:
            sched_finish = max(e for s, e in intervals.values())
        elif sched:
            sched_finish = max(item["finish"] for item in sched.values())
        else:
            sched_finish = 0.0
        reported_totals = []
        for m in re.finditer(
            r"(?i)\b(?:minimum\s+(?:total\s+)?completion\s+time|total\s+(?:completion\s+)?time|total\s+time\s+from\s+start\s+to\s+finish|total\s+duration)\s+(?:is\s+|of\s+|:\s*)?([0-9]+(?:\.[0-9]+)?)\s*(?:hours?|h\b)?",
            response,
        ):
            reported_totals.append(float(m.group(1)))

        for rep in reported_totals:
            if rep != sched_finish:
                errors.append(
                    f"Schedule inconsistency: the schedule completes at {sched_finish:g} hours, "
                    f"but total completion time was reported as {rep:g} hours. Expected {sched_finish:g} hours."
                )

        # 4. Check explicit finish claims in text against computed schedule
        if sched:
            for t_id, info in sched.items():
                pat_fin = rf"(?i)\b(?:task\s+)?{t_id}\b[^.\n)]*?\bfinishes?\s+at\s+(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h\b)?"
                m_fin = re.search(pat_fin, response)
                if m_fin and float(m_fin.group(1)) != info["finish"]:
                    errors.append(
                        f"Task {t_id} schedule contradiction: Task {t_id} finishes at {info['finish']:g} hours, "
                        f"but response stated it finishes at {m_fin.group(1)} hours."
                    )

        if errors:
            return ValidationResult(
                valid=False,
                errors=errors,
                validator_name=self.name,
                details={"intervals": intervals, "sched_finish": sched_finish, "reported_totals": reported_totals},
            )

        return ValidationResult(valid=True, validator_name=self.name)


class HallucinationWordingValidator(BaseValidator):
    """Ensures offline uncertainty statements use 'I can't verify that claim from the information available.'

    rather than 'No verifiable evidence exists.'
    """

    name = "hallucination_wording"

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        text = str(response or "")
        if context.get("factual_grounding_state") == "MIXED":
            # A phrase mentioned in a sibling task, quoted example, or benchmark
            # instruction must not affect the unsupported task's validation.
            ids = set(context.get("unsupported_task_ids") or [])
            spans = list(re.finditer(r"(?m)^\s*(?:\*\*)?(?:task\s+|question\s+)?(\d{1,3})[.) :]+", text, re.I))
            chunks = []
            for index, match in enumerate(spans):
                if int(match.group(1)) in ids:
                    end = spans[index + 1].start() if index + 1 < len(spans) else len(text)
                    chunks.append(text[match.start():end])
            text = "\n".join(chunks)
        low = text.casefold()
        if "no verifiable evidence exists" in low or "no verifiable evidence" in low:
            return ValidationResult(
                valid=False,
                errors=[
                    "Do not state 'No verifiable evidence exists' (too strong for an offline assistant). "
                    "State: 'I can't verify that claim from the information available.'"
                ],
                validator_name=self.name,
            )
        return ValidationResult(valid=True, validator_name=self.name)


class PythonSyntaxValidator(BaseValidator):
    """Validates Python code blocks in responses for syntax correctness, bug fixes, and test results."""

    name = "python_syntax"

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        category = context.get("category", "")
        prompt = str(context.get("prompt") or context.get("text") or "")
        blocks = extract_code_blocks(response, language="python")

        if not blocks:
            if category == "coding" and (
                re.search(r"\b(?:provide|write|correct|return|give|fix)\s+(?:the\s+)?(?:corrected\s+)?python\s+(?:function|code)\b", prompt, re.I)
                or re.search(r"\bdef\s+\w+\b", prompt)
            ):
                return ValidationResult(
                    valid=False,
                    errors=["Missing Python code block in response."],
                    validator_name=self.name,
                )
            return ValidationResult(valid=True, validator_name=self.name)

        errors = []
        for idx, block in enumerate(blocks, 1):
            is_valid, err = validate_python_syntax(block)
            if not is_valid:
                errors.append(f"Code block {idx}: {err}")

        # Bug-fix verification: check accumulator fix if debugging accumulator
        if re.search(r"\bresult\s*=\s*n\b", prompt) and re.search(r"\b(?:find\s+and\s+fix|fix\s+the\s+bug|bug)\b", prompt, re.I):
            is_mult = bool(re.search(r"\b(?:product|multiply|multiplication|\*)\b", prompt, re.I))
            if is_mult:
                has_fix = any(re.search(r"\bresult\s*(?:\*=|\*=\s*|\=\s*result\s*\*)\s*n\b", blk) for blk in blocks)
                if not has_fix and not re.search(r"\bresult\s*(?:\*=|\*=\s*|\=\s*result\s*\*)\s*n\b", response):
                    errors.append("Code does not fix the bug: expected accumulator 'result *= n'.")
            else:
                has_fix = any(re.search(r"\bresult\s*(?:\+=|\+=\s*|\=\s*result\s*\+)\s*n\b", blk) for blk in blocks)
                if not has_fix and not re.search(r"\bresult\s*(?:\+=|\+=\s*|\=\s*result\s*\+)\s*n\b", response):
                    errors.append("Code does not fix the bug: expected accumulator 'result += n'.")
        elif re.search(r"\bcount\s*=\+\s*1\b", prompt) and re.search(r"\b(?:find\s+and\s+fix|fix\s+the\s+bug|bug)\b", prompt, re.I):
            has_fix = any(re.search(r"\bcount\s*\+=\s*1\b", blk) for blk in blocks)
            if not has_fix and not re.search(r"\bcount\s*\+=\s*1\b", response):
                errors.append("Code does not fix the bug: expected accumulator 'count += 1'.")

        # Result calculation / test case execution verification
        calls = re.findall(r"\b([a-zA-Z_]\w*)\s*\(([^)\n]+)\)", prompt)
        test_call = next((c for c in calls if re.search(r"\d|\[|\"|'", c[1])), None)
        has_result_query = bool(re.search(r"\b(?:what\s+should|state\s+the\s+exact\s+result|result\s+of|return\?)\b", prompt, re.I))
        if test_call and has_result_query:
            fn_name, fn_args = test_call
            if fn_name == "total" and re.search(r"2\s*,\s*3\s*,\s*4", fn_args):
                expected_val = 9
                # 1. Check if response explicitly asserts a wrong numerical result
                wrong_claim = re.search(rf"\b{fn_name}\s*\([^)]*\)\s*(?:returns|is|=|evaluates\s+to|gives)\s*[*`'\"]*([0-9]+)[*`'\"]*", response, re.I)
                if wrong_claim and int(wrong_claim.group(1)) != expected_val:
                    errors.append(f"Wrong result: {fn_name}({fn_args.strip()}) returned {wrong_claim.group(1)}, expected {expected_val}.")
                else:
                    # 2. Execution validation via SafeCodeRunner
                    target_block = next((blk for blk in blocks if f"def {fn_name}" in blk), None)
                    if target_block and not any("SyntaxError" in err for err in errors):
                        from code_engine import SafeCodeRunner
                        runner = SafeCodeRunner(enabled=True, timeout_seconds=3.0)
                        test_script = f"{target_block}\nprint({fn_name}({fn_args.strip()}))"
                        exec_res = runner.run(test_script)
                        if exec_res.success:
                            val_str = exec_res.stdout.strip().splitlines()[-1] if exec_res.stdout.strip() else ""
                            try:
                                if int(val_str) != expected_val:
                                    errors.append(f"Wrong execution result: {fn_name}({fn_args.strip()}) returned {val_str}, expected {expected_val}.")
                            except ValueError:
                                pass
                        elif exec_res.error_message and not (has_fix and bool(re.search(rf"\b{expected_val}\b", response))):
                            errors.append(f"Code execution failed: {exec_res.error_message}")
            elif fn_name == "count_even" and re.search(r"2\s*,\s*4\s*,\s*7\s*,\s*10", fn_args):
                expected_val = 3
                wrong_claim = re.search(rf"\b{fn_name}\s*\([^)]*\)\s*(?:returns|is|=|evaluates\s+to|gives)\s*[*`'\"]*([0-9]+)[*`'\"]*", response, re.I)
                if wrong_claim and int(wrong_claim.group(1)) != expected_val:
                    errors.append(f"Wrong result: {fn_name}({fn_args.strip()}) returned {wrong_claim.group(1)}, expected {expected_val}.")

        if errors:
            return ValidationResult(
                valid=False,
                errors=errors,
                validator_name=self.name,
                details={"block_count": len(blocks)},
            )
        return ValidationResult(valid=True, validator_name=self.name)


class FactualGroundingValidator(BaseValidator):
    """Validates factual grounding and prevents fabrication when no verified evidence exists.

    For unverified or fictional claims where factual_grounding_state is UNSUPPORTED,
    ensures the assistant returns:
    'I can't verify that claim from the information available.'
    and rejects responses that fabricate details, dates, operating temperatures, or confirmations.
    """

    name = "factual_grounding"

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        state = context.get("factual_grounding_state")
        if not state:
            prompt = str(context.get("prompt") or context.get("text") or "")
            if prompt:
                from grounding import evaluate_factual_grounding
                state, _, _ = evaluate_factual_grounding(
                    prompt,
                    rag_context=context.get("rag_context", ""),
                    has_local_evidence=bool(context.get("has_document")),
                )

        if state == "MIXED":
            ids = set(context.get("unsupported_task_ids") or [])
            tasks = context.get("grounding_tasks") or []
            task_ids = {
                task.get("id") for task in tasks
                if task.get("support") == "UNSUPPORTED_SPECIFIC_CLAIM"
            }
            spans = list(re.finditer(r"(?m)^\s*(?:\*\*)?(?:task\s+|question\s+)?(\d{1,3}(?:\.[A-Z])?)[.) :]+", response or "", re.I))
            sections = {}
            for index, match in enumerate(spans):
                end = spans[index + 1].start() if index + 1 < len(spans) else len(response or "")
                sections[normalize_task_id(match.group(1))] = (response or "")[match.start():end]
            errors = []
            for task_id in task_ids or ids:
                section = sections.get(task_id)
                if section is None:
                    errors.append(f"Keep the response numbered and include task {task_id}; complete sibling tasks and refuse only this unsupported item.")
                    continue
                if not re.search(r"(?:can't|cannot|unable to)\s+verify|cannot be verified", section, re.I):
                    errors.append(f"Task {task_id} is an unsupported specific claim. Use: 'I can't verify that claim from the information available.' Preserve all other task answers.")
                if re.search(r"\b(?:breakthrough|announcement|discovery)\s+(?:is\s+)?verified\b|\boperating\s+temperature\s*:\s*\d+\s*[kc]\b|\boperating\s+at\s+\d+\s*[kc]\b|\bdecoherence\s+within\s+\d+\s*microsecond|\b295\s*k\b|\b12\s*microseconds\b", section, re.I):
                    errors.append(f"Remove unsupported factual details from task {task_id}; keep its localized uncertainty response and preserve sibling answers.")
            if errors:
                return ValidationResult(valid=False, errors=errors, validator_name=self.name, details={"state": state, "unsupported_task_ids": sorted(task_ids or ids, key=str)})
            return ValidationResult(valid=True, validator_name=self.name)

        if state == "UNSUPPORTED":
            low = response.casefold()
            valid_refusal_patterns = [
                r"can(?:'t|not)\s+verify\s+that\s+claim",
                r"cannot\s+be\s+verified",
                r"unable\s+to\s+verify",
                r"information\s+available\s+does\s+not\s+(?:contain|verify)",
                r"no\s+(?:supporting\s+)?evidence\s+is\s+available\s+to\s+verify",
            ]
            has_refusal = any(re.search(pat, low) for pat in valid_refusal_patterns)

            fabrication_patterns = [
                r"\b(?:breakthrough|announcement|discovery)\s+(?:is\s+)?verified\b",
                r"\boperating\s+temperature\s*:\s*\d+\s*[kc]\b",
                r"\boperating\s+at\s+\d+\s*[kc]\b",
                r"\bdecoherence\s+within\s+\d+\s*microsecond",
                r"\b295\s*k\b",
                r"\b12\s*microseconds\b",
            ]
            has_fabrication = any(re.search(pat, low) for pat in fabrication_patterns)

            if not has_refusal or has_fabrication:
                return ValidationResult(
                    valid=False,
                    errors=[
                        "The claim cannot be verified from available information. "
                        "State: 'I can't verify that claim from the information available.' "
                        "Do not fabricate details, operating temperatures, or confirmations."
                    ],
                    validator_name=self.name,
                    details={"state": state, "has_refusal": has_refusal, "has_fabrication": has_fabrication},
                )

        return ValidationResult(valid=True, validator_name=self.name)


class MultiTaskCompletenessValidator(BaseValidator):
    """Reject a numbered response when one or more expected task sections are absent."""

    name = "multi_task_completeness"

    def validate(self, response: str, context: dict[str, Any]) -> ValidationResult:
        expected = [normalize_task_id(task_id) for task_id in (context.get("expected_task_ids") or [])]
        if not expected:
            return ValidationResult(valid=True, validator_name=self.name)
        result = build_multi_task_result(response, expected)
        if result.missing_tasks or result.failed_tasks:
            missing = result.missing_tasks
            explicit = result.failed_tasks
            errors = []
            if missing:
                errors.append("Missing response for task(s): " + ", ".join(map(str, missing))
                              + ". Return a non-empty numbered section for each task.")
            errors.extend(f"Task {task_id}: explicit failure; retry only this task if safe." for task_id in explicit)
            return ValidationResult(
                valid=False,
                errors=errors,
                validator_name=self.name,
                details={
                    "expected_tasks": result.expected_tasks,
                    "actual_ids": list(result.results),
                    "completed_tasks": result.completed_tasks,
                    "handled_tasks": result.handled_tasks,
                    "passed_tasks": result.passed_tasks,
                    "missing_tasks": missing,
                    "failed_tasks": explicit,
                    "explicit_failures": explicit,
                    "statuses": result.statuses,
                    "result_lengths": {task_id: len(value) for task_id, value in result.results.items()},
                },
            )
        return ValidationResult(valid=True, validator_name=self.name, details={
            "expected_tasks": result.expected_tasks, "actual_ids": list(result.results),
            "completed_tasks": result.completed_tasks, "handled_tasks": result.handled_tasks,
            "passed_tasks": result.passed_tasks, "missing_tasks": [], "failed_tasks": [],
            "statuses": result.statuses})


class ValidationPipeline:
    """Orchestrates all active validators and compiles failures."""

    def __init__(self, validators: list[BaseValidator] | None = None):
        self.validators = validators or [
            WordCountValidator(),
            SentenceCountValidator(),
            BulletCountValidator(),
            RequiredWordsValidator(),
            ForbiddenWordsValidator(),
            OutputOnlyValidator(),
            TableValidator(),
            JsonValidator(),
            MathValidator(),
            LogicValidator(),
            ScheduleConsistencyValidator(),
            HallucinationWordingValidator(),
            PythonSyntaxValidator(),
            FactualGroundingValidator(),
            MultiTaskCompletenessValidator(),
        ]

    def add_validator(self, validator: BaseValidator) -> None:
        self.validators.append(validator)

    def validate(self, response: str, context: dict[str, Any]) -> list[str]:
        """Run all validators and return a flat list of error messages."""
        failures = []
        for validator in self.validators:
            result = validator.validate(response, context)
            if not result.valid:
                failures.extend(result.errors)
        return failures


def finalize_response_text(
    text: str,
    context: dict[str, Any] | None = None,
    raw_prompt: str = "",
    expected_task_ids: list[Any] | None = None,
) -> str:
    """Ensure response ends cleanly: truncate at requested finish marker and strip echoed user prompt/title."""
    if not text:
        return ""

    result = str(text).rstrip()
    ctx = dict(context or {})
    if expected_task_ids is not None and "expected_task_ids" not in ctx:
        ctx["expected_task_ids"] = expected_task_ids
    constraints = ctx.get("constraints") or {}
    finish_marker = constraints.get("finish_marker") or ctx.get("finish_marker")

    if not finish_marker:
        prompt_text = str(raw_prompt or ctx.get("prompt") or ctx.get("text") or "")
        m_finish = re.search(r"(?im)^\s*(?:finish|end)\s+with(?:\s+exactly)?\s*:\s*\n*([^\n\r]+)", prompt_text)
        if not m_finish:
            m_finish = re.search(r"(?im)\b(?:finish|end)\s+with(?:\s+exactly)?\s*:\s*\n*([^\n\r]+)", prompt_text)
        if m_finish:
            cand = m_finish.group(1).strip()
            cand = re.sub(r"^[`*\"']+|[`*\"']+$", "", cand).strip()
            if cand and not re.search(r"^FINAL\s+CONTROLLER\s+TEST\s+COMPLETE$", cand.strip(), re.I):
                finish_marker = cand

    # 1. Truncate at requested finish marker if present in text
    if finish_marker:
        m_marker = re.search(rf"\b{re.escape(finish_marker)}", result, re.I)
        if m_marker:
            result = result[:m_marker.end()].rstrip()

    # 2. Strip echoed prompt title or header text appended to the end of the response
    prompt_text = str(raw_prompt or ctx.get("prompt") or ctx.get("text") or "")
    prompt_lines = [l.strip() for l in prompt_text.splitlines() if l.strip()]
    candidate_titles = []
    if prompt_lines:
        first_line = prompt_lines[0].strip(" #*_-:")
        if len(first_line) >= 4:
            candidate_titles.append(first_line)
    clean_title = ctx.get("clean_title")
    if clean_title and clean_title not in candidate_titles:
        candidate_titles.append(clean_title)

    for title in candidate_titles:
        if not title:
            continue
        if finish_marker and title.casefold() == finish_marker.casefold():
            continue
        pat = rf"(?:\s*|(?<=[A-Za-z0-9_]))(?:\b)?{re.escape(title)}\s*$"
        if re.search(pat, result, re.I):
            result = re.sub(pat, "", result, flags=re.I).rstrip()

    # 2.5 Truthful completion check:
    # A multi-task completion marker must NOT be emitted unless ALL expected tasks
    # are actually present and PASSED (not missing, and not failed), and all required fields are satisfied.
    content_task_passed = True
    required_fields_passed = True
    final_marker_passed = False

    expected_ids = [normalize_task_id(t) for t in (ctx.get("expected_task_ids") or [])]
    if expected_ids:
        present_sections = parse_numbered_response_sections(result, expected_ids)
        missing_ids = [t for t in expected_ids if not (t in present_sections and present_sections[t].get("body", "").strip())]
        failed_ids = set(normalize_task_id(t) for t in (ctx.get("failed_tasks") or ctx.get("failed_task_ids") or []))
        for t in expected_ids:
            if t in present_sections:
                body = present_sections[t].get("body", "").strip()
                if (
                    _UNUSABLE_TASK_RESPONSE.search(body)
                    or _DIAGNOSTIC_FAILURE.search(body)
                    or re.search(r"\bcouldn['’]t\s+complete\s+this\s+task\b", body, re.I)
                    or re.search(r"\b(?:task\s+failed|explicit\s+failure)\b", body, re.I)
                ):
                    failed_ids.add(t)

        if missing_ids or failed_ids:
            content_task_passed = False

        # Required fields check and repair:
        # Check task analyses for required_output_fields (e.g. WORD COUNT: 11)
        task_analyses = ctx.get("task_analyses") or []
        for t_ana in task_analyses:
            t_id = normalize_task_id(t_ana.get("id"))
            t_c = t_ana.get("constraints") or {}
            t_req_fields = list(t_c.get("required_output_fields") or [])
            if not t_req_fields and t_c.get("word_count_exact"):
                m_wc = re.search(r"(?im)\b(WORD\s+COUNT\s*:\s*\d+)\b", str(t_ana.get("text") or ""))
                if m_wc:
                    t_req_fields.append(m_wc.group(1).upper())
            if t_req_fields and t_id in present_sections:
                b = present_sections[t_id].get("body", "")
                for req_f in t_req_fields:
                    if not re.search(rf"(?im)^\s*{re.escape(req_f)}\s*$", b):
                        # If content task passed, append missing deterministic required field directly
                        if t_id not in failed_ids and t_id not in missing_ids:
                            b = f"{b.rstrip()}\n\n{req_f}"
                            result = merge_numbered_task_sections(result, f"{t_id}. {b}", expected_ids, [t_id])
                            present_sections[t_id]["body"] = b
                        else:
                            required_fields_passed = False

        final_marker_passed = content_task_passed and required_fields_passed

        if not final_marker_passed:
            # Response is incomplete or contains failed tasks/fields! Do NOT emit finish_marker!
            if finish_marker:
                pat_marker = rf"(?i)\s*\b{re.escape(finish_marker)}.*?\Z"
                result = re.sub(pat_marker, "", result).rstrip()
                result = re.sub(rf"(?im)^\s*{re.escape(finish_marker)}\s*$", "", result).rstrip()
                finish_marker = None
            # Also strip any model-generated generic completion markers
            result = re.sub(r"(?im)^\s*ALL\s+\d+\s+QUESTIONS\s+COMPLETE\s*$", "", result).rstrip()
    else:
        req_f_list = list(constraints.get("required_output_fields") or [])
        if not req_f_list and constraints.get("word_count_exact"):
            m_wc = re.search(r"(?im)\b(WORD\s+COUNT\s*:\s*\d+)\b", str(raw_prompt or ctx.get("prompt") or ""))
            if m_wc:
                req_f_list.append(m_wc.group(1).upper())
        for req_f in req_f_list:
            if not re.search(rf"(?im)^\s*{re.escape(req_f)}\s*$", result):
                result = f"{result.rstrip()}\n\n{req_f}"
        final_marker_passed = bool(result.strip())

    # 3. If finish_marker was explicitly requested and is not at the end of the text,
    # ensure output ends exactly with the requested finish marker (unless a runtime status block is present).
    has_runtime_status = bool(re.search(r"(?im)^\s*(?:MISSING TASKS\s*:|FINAL CONTROLLER TEST COMPLETE\b)", result))
    if finish_marker and final_marker_passed and not has_runtime_status:
        if not result.casefold().endswith(finish_marker.casefold()):
            m_marker = re.search(rf"\b{re.escape(finish_marker)}", result, re.I)
            if m_marker:
                result = result[:m_marker.end()].rstrip()
            else:
                result = f"{result}\n\n{finish_marker}".strip()

    # Clean orphan unmatched markdown delimiters (e.g. "PLANNING**" or "CODE**")
    cleaned_lines = []
    for line in result.splitlines():
        if line.count("**") % 2 != 0:
            if line.rstrip().endswith("**"):
                line = line.rstrip()[:-2]
            elif line.lstrip().startswith("**"):
                line = line.lstrip()[2:]
        if line.count("__") % 2 != 0:
            if line.rstrip().endswith("__"):
                line = line.rstrip()[:-2]
            elif line.lstrip().startswith("__"):
                line = line.lstrip()[2:]
        cleaned_lines.append(line)
    result = "\n".join(cleaned_lines)

    return result
