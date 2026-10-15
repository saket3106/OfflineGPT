"""Task-scoped factual grounding for Offline GPT.

Grounding is deliberately applied to independent request items, not to every
word in a complete message. Deterministic, creative, coding, and formatting
tasks are outside the factual claim guard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


def is_routing_classification(text: str) -> bool:
    """Recognize request-routing labels, never ordinary domain categories."""
    value=str(text or '')
    if not re.search(r'\bclassif(?:y|ication)\b',value,re.I):return False
    categories=re.search(r'\b(?:as|into|categories|labels)\s*:?\s*([^\n.;:]+)',value,re.I)
    if categories:
        category_text=re.split(r'\b(?:based on|depending on|then|for each|using|and (?:return|explain))\b',categories.group(1),maxsplit=1,flags=re.I)[0]
        labels=set(re.findall(r'\b[A-Za-z]+\b',category_text.upper()))-{'AND','OR','EITHER','ONE','OF','THE','CATEGORIES','LABELS'}
        if labels and not labels<={'DIRECT','TOOL'}:return False
    if re.search(r'\bDIRECT\b',value,re.I) and re.search(r'\bTOOL\b',value,re.I):return True
    if re.search(r'\brouting\s+classification\b',value,re.I):return True
    # Legacy "Classify only: <operation>" is a meta request. Explicit domain
    # label sets (including a category named TOOL) remain model content tasks.
    if re.search(r'\b(?:as|into|categories|labels)\b',value,re.I):return False
    return bool(re.search(r'\bclassify\s+only\s*:\s*(?:explain|calculate|compute|run|execute|search|find|summari[sz]e|write|generate|create)\b',value,re.I))


@dataclass
class TaskNode:
    """Explicit representation of a node in the task tree."""
    id: int | str
    parent_id: int | str | None = None
    node_type: str = "CONTENT_TASK"  # "CONTENT_TASK", "PARENT_CONTAINER", "RUNTIME_METADATA"
    raw_text: str = ""
    text: str = ""
    source_start: int = -1
    source_end: int = -1
    classification_only: bool = False
    classification_subject: str | None = None
    children: list[TaskNode] = field(default_factory=list)


class FactualGroundingState:
    # Legacy values retained for existing callers and persisted diagnostics.
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"
    MIXED = "MIXED"

    # More descriptive per-task classifications.
    GENERAL_KNOWLEDGE = "GENERAL_KNOWLEDGE"
    SUPPORTED_BY_CONTEXT = "SUPPORTED_BY_CONTEXT"
    SUPPORTED_BY_RAG = "SUPPORTED_BY_RAG"
    UNSUPPORTED_SPECIFIC_CLAIM = "UNSUPPORTED_SPECIFIC_CLAIM"
    NOT_APPLICABLE = "NOT_APPLICABLE"


UNCERTAINTY_RESPONSE = "I can't verify that claim from the information available."
SPECIFIC_EVENT_VERBS = re.compile(
    r"\b(?:announced|announce|breakthrough|discovered|discovery|unveiled|released|confirmed|published|invented|patented|awarded|revealed)\b",
    re.I,
)
EXPLICIT_UNSUPPORTED_PHRASES = re.compile(
    r"\b(?:no\s+(?:supporting\s+)?evidence(?:\s+is)?(?:\s+provided|\s+available)?|"
    r"without\s+(?:any\s+)?(?:supporting\s+)?evidence|evidence\s+(?:is\s+)?unavailable|"
    r"unverified\s+(?:claim|announcement|breakthrough)|unsupported\s+claim|fictional)\b",
    re.I,
)
DOCUMENT_QUERY = re.compile(
    r"\b(?:according to|in the uploaded|in the attached)\s+(?:document|pdf|file|[\w.-]+\.(?:pdf|docx|txt))\b",
    re.I,
)
NON_FACTUAL = re.compile(
    r"\b(?:\d+\s*[+*/×-]\s*\d+|calculate|arithmetic|probability|logic|\bcode\b|coding|"
    r"debug(?:ging)?|traceback|syntax|format(?:ting)?|json|yaml|xml|write (?:a |an )?(?:story|poem|email|letter)|"
    r"creative|fictional story|logic puzzle|solve this puzzle|reformat|convert this text|writing|draft|compose)\b",
    re.I,
)
NAMED_ENTITY_PATTERNS = (
    re.compile(r"\b(?:Professor|Prof\.?|Dr\.?|Mr\.?|Ms\.?|President|CEO)\s+(?:[A-Z][a-z]+|[A-Z])(?:\s+[A-Z][a-z]+)*\b"),
    re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Institute|University|Laboratory|Lab|Center|Observatory|Foundation|Society)\b"),
)
DATE_PATTERN = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
    re.I,
)
TASK_HEADER = re.compile(
    r"^(?P<indent>[ \t]{0,8})(?:#{1,6}[ \t]+)?"
    r"(?:(?P<label>task|question|part)[ \t]+)?"
    r"(?P<number>\d{1,3})(?:\.|\)|:|-)(?=[ \t]|$)[ \t]*(?P<text>[^\n]*)$",
    re.I,
)
_TRAILING_FOOTER_RE = re.compile(
    r"(?m)\n+[ \t]*(?:[-=_~*]{3,}[ \t]*\n+)?"
    r"(?:FINAL RUNTIME STATUS\b|"
    r"CONTENT TASKS (?:EXPECTED|HANDLED|PASSED)\s*:|"
    r"TASKS (?:COMPLETED|HANDLED|PASSED)\s*:\s*\d+\s*/\s*\d+|"
    r"tests?\s*(?:believed\s+)?(?:correct|passed|completed)\s*:\s*\d+\s*/\s*\d+|"
    r"(?:FAILED|MISSING) TASKS\s*:|"
    r"(?:(?:CHAT\s+|FINAL\s+(?:CONTROLLER\s+)?)?TEST\s+COMPLETE))\b.*?\Z",
    re.I | re.S,
)


def unwrap_outer_prompt_envelope(source: str) -> str:
    """If the entire user request is wrapped in an outer code fence envelope (e.g. ```markdown ... ```),
    unwrap the envelope while preserving all inner text, indentation, and intentional code blocks."""
    if not source:
        return ""
    text = source.strip()
    match = re.match(r"^(`{3,}|~{3,})([a-zA-Z0-9_-]*)[ \t]*\r?\n([\s\S]*?)\r?\n\1[ \t]*$", text)
    if not match:
        return source
    marker, lang, body = match.group(1), match.group(2).lower(), match.group(3)
    has_inner_fence = bool(re.search(r"(`{3,}|~{3,})", body))
    has_numbered_tasks = bool(re.search(r"(?m)^\s*(?:Task|Question|Part)?\s*1[.):]", body)) and bool(re.search(r"(?m)^\s*(?:Task|Question|Part)?\s*2[.):]", body))
    is_doc_or_text_lang = lang in {"", "markdown", "md", "text", "plaintext"}
    if has_inner_fence or has_numbered_tasks or is_doc_or_text_lang:
        return body
    return source


def detect_raw_numbered_sections(source: str) -> list[int]:
    """Scan raw user request for all top-level numbered sections outside code fences."""
    source = unwrap_outer_prompt_envelope(source)
    sections: list[int] = []
    in_fence = False
    fence_char = ""
    fence_size = 0
    lines = str(source or "").splitlines()
    for line in lines:
        fence = re.match(r"^[ \t]*(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_char, fence_size = True, marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size:
                in_fence, fence_char, fence_size = False, "", 0
            continue
        match = TASK_HEADER.match(line)
        if match and in_fence:
            indent = len(match.group("indent") or "")
            num = int(match.group("number"))
            expected_num = (sections[-1] + 1) if sections else 1
            text = (match.group("text") or "").strip()
            is_valid_next_task = (
                indent == 0
                and len(sections) > 0
                and num == expected_num
                and (bool(match.group("label")) or (bool(text) and text[0].isupper()))
            )
            if is_valid_next_task:
                in_fence, fence_char, fence_size = False, "", 0
        if in_fence or line.lstrip().startswith(">"):
            continue
        if re.search(r"\b(?:content\s+tasks|tasks\s+(?:handled|passed|completed)|tests?\s+believed)\b", line, re.I):
            continue
        if match:
            num = int(match.group("number"))
            if num not in sections:
                sections.append(num)

    # If fewer than 2 numbered sections were found outside fences, scan lines regardless of fences
    # so tasks wrapped in code blocks or prompt envelopes are not dropped
    if len(sections) < 2:
        fallback_sections: list[int] = []
        for line in lines:
            if line.lstrip().startswith(">"):
                continue
            if re.search(r"\b(?:content\s+tasks|tasks\s+(?:handled|passed|completed)|tests?\s+believed)\b", line, re.I):
                continue
            match = TASK_HEADER.match(line)
            if match:
                num = int(match.group("number"))
                if num not in fallback_sections:
                    fallback_sections.append(num)
        if len(fallback_sections) >= 2 and (1 in fallback_sections or fallback_sections == list(range(fallback_sections[0], fallback_sections[0] + len(fallback_sections)))):
            sections = fallback_sections

    headers = [{"source_start": m.start(), "explicit": bool(m.group("label"))} for m in re.finditer(TASK_HEADER.pattern, source, TASK_HEADER.flags | re.M)]
    return [] if _shared_instruction_list(source, headers) else sections
GLOBAL_FOOTER = re.compile(
    r"^[ \t]*(?:[-=_~*]{3,}[ \t]*$|"
    r"#{0,6}[ \t]*(?:FINAL\b|RUNTIME STATUS|CONTENT TASKS EXPECTED)|"
    r"at the end write exactly[ \t]*:|"
    r"tasks?\s+(?:completed|handled|passed)[ \t]*:|"
    r"(?:failed|missing)\s+tasks[ \t]*:|"
    r"tools?\s+(?:actually\s+)?used[ \t]*:|"
    r"method[ \t]*:|output mode[ \t]*:|pdf created[ \t]*:|"
    r"artifact created[ \t]*:)",
    re.I,
)
BULLET_HEADER = re.compile(r"^ {0,3}[-*•]\s+(?P<text>.*\S)\s*$")
TASK_INTENT = re.compile(
    r"\b(?:what|who|when|where|why|how|write|calculate|debug|solve|format|explain|define|"
    r"create|design|review|return|convert|identify|list|summari[sz]e|compare|provide|find|tell|describe|"
    r"name|draft|compose|rewrite|translate|classify|implement|fix|give|show|compute|"
    r"greet|say|answer|respond|output|run|execute|search|check|does|is|are|"
    r"arithmetic|coding|logic|formatting|probability|debugging|networking|json|math|"
    r"planning|schedule|plan|timeline|duration|critical\s+path|dependency|determine|evaluate|can)\b"
    r"|[?=+*/×÷]",
    re.I,
)

NESTED_HEADER = re.compile(
    r"^(?P<indent>[ \t]{0,8})(?:[-*][ \t]+)?(?P<letter>[A-Z])[.):][ \t]+(?P<text>[^\n]*)$"
)


def normalize_task_id(value: Any) -> int | str:
    """Keep legacy numeric IDs numeric and lettered child IDs stable."""
    text = str(value).strip().upper()
    if re.fullmatch(r"\d{1,3}", text):
        return int(text)
    if re.fullmatch(r"\d{1,3}\.[A-Z]", text):
        return text
    raise ValueError(f"Invalid task ID: {value!r}")


def _expand_lettered_subtasks(item: dict[str, Any], source: str) -> list[dict[str, Any]]:
    """Expose sequential A/B/... children as independent outputs with source spans."""
    start, end = int(item["source_start"]), int(item["source_end"])
    raw = source[start:end]
    headings: list[tuple[str, int, int]] = []
    offset = start
    fence_char = ""
    fence_size = 0
    for line_with_end in raw.splitlines(keepends=True):
        line = line_with_end.rstrip("\n")
        fence = re.match(r"^[ \t]*(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)
            if not fence_char:
                fence_char, fence_size = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size:
                fence_char, fence_size = "", 0
        elif not fence_char and not line.lstrip().startswith(">"):
            match = NESTED_HEADER.match(line)
            if match:
                headings.append((match.group("letter"), offset, offset + match.start("text")))
        offset += len(line_with_end)
    if len(headings) < 2 or [letter for letter, *_ in headings] != [chr(65 + n) for n in range(len(headings))]:
        return [item]
    # Guard against splitting project/schedule activity lists (e.g. critical path / PERT) into subtasks
    is_schedule_or_planning = bool(
        re.search(r"\b(?:schedule|project|critical\s+path|gantt|pert|dependencies|activity|activities|duration|slack)\b", raw, re.I)
        and re.search(r"\b(?:depends\s+on|predecessor|duration|days?|hours?|weeks?)\b", raw, re.I)
    )
    if is_schedule_or_planning:
        return [item]
    preamble = source[start:headings[0][1]].strip()
    if not preamble:
        return [item]

    postamble_start = end
    postamble_lines: list[str] = []
    last_marker = headings[-1][1]
    curr = last_marker
    found_postamble = False
    for line_with_end in source[last_marker:end].splitlines(keepends=True):
        line = line_with_end.rstrip("\r\n")
        if curr > last_marker:
            if GLOBAL_FOOTER.match(line):
                if not found_postamble:
                    postamble_start = curr
                break
            if not found_postamble and re.match(r"^[ \t]*(?:do\s+not|don't|return\b|answer\s+only|both\s+must|all\s+must|each\s+must|format\b|output\b)", line, re.I):
                if not re.search(r"\b(?:runtime\s+status|content\s+tasks|tasks\s+(?:handled|passed)|tests?\s+believed)\b", line, re.I):
                    found_postamble = True
                    postamble_start = curr
            if found_postamble:
                if GLOBAL_FOOTER.match(line) or re.search(r"\b(?:runtime\s+status|content\s+tasks|tasks\s+(?:handled|passed)|output\s+mode\s*:)\b", line, re.I):
                    break
                postamble_lines.append(line)
        curr += len(line_with_end)
    postamble_text = "\n".join(postamble_lines).strip()
    if re.search(r"\b(?:runtime\s+status|content\s+tasks|tasks\s+(?:handled|passed)|output\s+mode\s*:)\b", postamble_text, re.I):
        postamble_text = ""

    combined_instruction = (preamble + ("\n" + postamble_text if postamble_text else "")).strip()
    is_classification = is_routing_classification(combined_instruction)

    children = []
    parent_id = item["id"]
    for index, (letter, marker_start, text_start) in enumerate(headings):
        child_end = headings[index + 1][1] if index + 1 < len(headings) else postamble_start
        child_raw = source[text_start:child_end]
        child_text = child_raw.rstrip("\n")
        child_text_full = (combined_instruction + "\n" + child_text).strip()
        children.append({
            "id": f"{parent_id}.{letter}",
            "parent_id": parent_id,
            "node_type": "CONTENT_TASK",
            "parent_instruction": combined_instruction,
            "classification_only": is_classification,
            "classification_subject": child_text.strip() if is_classification else None,
            "text": child_text_full,
            "raw_text": child_raw,
            "source_start": text_start,
            "source_end": child_end,
        })
    return children


def _candidate_is_task_list(items: list[dict[str, Any]]) -> bool:
    """Reject numbered examples and data lists that are not independent requests."""
    if len(items) < 2:
        return bool(len(items) == 1
                    and re.search(r"\b(?:each|following|subtasks|parts)\b", str(items[0].get("text") or ""), re.I)
                    and sum(bool(NESTED_HEADER.match(line)) for line in str(items[0].get("text") or "").splitlines()) >= 2)
    explicit = sum(bool(item.get("explicit")) for item in items)
    intent_count = sum(bool(TASK_INTENT.search(str(item.get("text") or ""))) for item in items)
    return explicit >= 2 or intent_count >= max(2, (len(items) + 1) // 2)


def _shared_instruction_list(source: str, items: list[dict[str, Any]]) -> bool:
    """A requirements list refines one deliverable; explicit independent tasks win."""
    if not items or any(item.get("explicit") for item in items):
        return False
    preamble = source[:items[0].get("marker_start", items[0].get("source_start", 0))]
    if re.search(r"\b(?:independent|separate|unrelated)\s+(?:tasks|questions|requests)\b|\b(?:answer|complete|solve)\s+(?:each|all|these|the following|following)\s+(?:\d+\s+)?(?:question|task|request)", preamble, re.I):
        return False
    # Require a list-role declaration, not just an engineering keyword.
    return bool(re.search(
        r"(?:^|\n)[ \t]*#{0,6}[ \t]*(?:(?:implementation|design|output|acceptance|formatting)\s+)?"
        r"(?:requirements|instructions|constraints|acceptance criteria|expected output|steps to (?:implement|fix))"
        r"[ \t]*:?[ \t]*(?:\n|$)", preamble, re.I))


def _numbered_task_candidates(source: str) -> list[list[dict[str, Any]]]:
    """Collect numbered tasks by source span without reconstructing task text."""
    source = unwrap_outer_prompt_envelope(source)
    candidates: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    expected_number = 1
    in_fence = False
    fence_char = ""
    fence_size = 0
    offset = 0

    def finish(end: int) -> None:
        nonlocal current
        if current:
            last_item = current[-1]
            tail_text = source[last_item["source_start"]:end]
            m = _TRAILING_FOOTER_RE.search(tail_text)
            adjusted_end = (last_item["source_start"] + m.start()) if m else end

            materialized = []
            for index, item in enumerate(current):
                task_end = current[index + 1]["marker_start"] if index + 1 < len(current) else adjusted_end
                raw = source[item["source_start"]:task_end]
                # Strip trailing markdown horizontal rules / separators before the next task
                lines = raw.rstrip("\r\n").splitlines()
                while lines and re.match(r"^[ \t]*[-=_~*]{3,}[ \t]*$", lines[-1]):
                    lines.pop()
                cleaned_text = "\n".join(lines).strip()
                materialized.append({
                    "id": item["id"],
                    "text": cleaned_text,
                    "raw_text": raw,
                    "source_start": item["source_start"],
                    "source_end": task_end,
                    "explicit": item["explicit"],
                })
            if _candidate_is_task_list(materialized) and not _shared_instruction_list(source, current):
                candidates.append(materialized)
        current = []

    def scan_pass(ignore_fences: bool = False) -> None:
        nonlocal current, expected_number, in_fence, fence_char, fence_size, offset
        current = []
        expected_number = 1
        in_fence = False
        fence_char = ""
        fence_size = 0
        offset = 0

        for line_with_end in source.splitlines(keepends=True):
            line = line_with_end.rstrip("\n")
            fence = re.match(r"^[ \t]*(`{3,}|~{3,})", line)
            if fence:
                marker = fence.group(1)
                if not in_fence:
                    in_fence, fence_char, fence_size = True, marker[0], len(marker)
                elif marker[0] == fence_char and len(marker) >= fence_size:
                    in_fence, fence_char, fence_size = False, "", 0
                if not ignore_fences:
                    offset += len(line_with_end)
                    continue
            match = TASK_HEADER.match(line)
            if match and in_fence and not ignore_fences:
                indent = len(match.group("indent") or "")
                number = int(match.group("number"))
                text = (match.group("text") or "").strip()
                is_valid_next_task = (
                    indent == 0
                    and len(current) > 0
                    and number == expected_number
                    and (bool(match.group("label")) or (bool(text) and text[0].isupper()))
                )
                if is_valid_next_task:
                    in_fence, fence_char, fence_size = False, "", 0
            if (not ignore_fences and in_fence) or line.lstrip().startswith(">"):
                offset += len(line_with_end)
                continue
            if not match:
                offset += len(line_with_end)
                continue
            number = int(match.group("number"))
            explicit = bool(match.group("label"))
            indent = len(match.group("indent") or "")
            if current and indent > int(current[0].get("indent", 0)):
                offset += len(line_with_end)
                continue
            item = {"id": number, "source_start": offset + match.start("text"),
                    "marker_start": offset, "explicit": explicit, "indent": indent}
            if number == 1:
                finish(offset)
                current = [item]
                expected_number = 2
            elif current and number == expected_number:
                current.append(item)
                expected_number += 1
            offset += len(line_with_end)
        finish(len(source))

    scan_pass(ignore_fences=False)
    if not candidates:
        scan_pass(ignore_fences=True)

    return candidates


def _bullet_task_candidates(lines: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    base_indent: int | None = None
    in_fence = False
    fence_char = ""
    fence_size = 0
    for line in lines:
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_char, fence_size = True, marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size:
                in_fence, fence_char, fence_size = False, "", 0
            if items:
                items[-1]["text"] = (str(items[-1]["text"]) + "\n" + line).strip()
            continue
        if in_fence:
            if items and line.strip():
                items[-1]["text"] = (str(items[-1]["text"]) + "\n" + line.strip()).strip()
            continue
        match = BULLET_HEADER.match(line)
        if match:
            indent = len(line) - len(line.lstrip(" "))
            if base_indent is None:
                base_indent = indent
            if indent <= base_indent:
                items.append({"id": len(items) + 1, "text": match.group("text").strip(), "explicit": False})
            elif items:
                items[-1]["text"] = (str(items[-1]["text"]) + "\n" + line.strip()).strip()
        elif items and line.strip() and not line.lstrip().startswith(">"):
            items[-1]["text"] = (str(items[-1]["text"]) + "\n" + line.strip()).strip()
    return items if _candidate_is_task_list(items) else []


def decompose_request_tasks(prompt: str) -> list[dict[str, Any]]:
    """Split independent request tasks without mistaking examples for task headings."""
    text = unwrap_outer_prompt_envelope(str(prompt or "").replace("\r\n", "\n").replace("\r", "\n"))
    if not text.strip():
        return []
    lines = text.splitlines()
    raw_detected_sections = detect_raw_numbered_sections(text)
    numbered = _numbered_task_candidates(text)
    if not numbered and _shared_instruction_list(text, [{"source_start": m.start(), "explicit": bool(m.group("label"))} for m in re.finditer(TASK_HEADER.pattern, text, TASK_HEADER.flags | re.M)]):
        return [{"id": 1, "text": text, "raw_text": text, "source_start": 0, "source_end": len(text)}]
    if numbered:
        # Prefer the longest coherent request list. If equal, use the first one;
        # later examples or quoted benchmark notes cannot inflate its task count.
        chosen = max(enumerate(numbered), key=lambda pair: (len(pair[1]), -pair[0]))[1]
        parsed_top_ids = [item["id"] for item in chosen]
        unparsed = [s for s in raw_detected_sections if s not in parsed_top_ids]
        if unparsed and len(numbered) > 1:
            combined = []
            seen = set()
            for cand in numbered:
                for item in cand:
                    if item["id"] not in seen:
                        seen.add(item["id"])
                        combined.append(item)
            combined.sort(key=lambda x: x["source_start"])
            if all(s in seen for s in raw_detected_sections):
                chosen = combined
        expanded = []
        for item in chosen:
            if item["text"].strip():
                expanded.extend(_expand_lettered_subtasks(
                    {key: value for key, value in item.items() if key != "explicit"}, text))
        return expanded

    if re.search(r"(?im)^\s*#{0,6}\s*(?:requirements|instructions|constraints|acceptance criteria|expected output)\s*:?[ \t]*$", text):
        return [{"id": 1, "text": text, "raw_text": text, "source_start": 0, "source_end": len(text)}]

    bullets = _bullet_task_candidates(lines)
    if bullets:
        return [{"id": index, "text": item["text"].strip(), "raw_text": item["text"],
                 "source_start": -1, "source_end": -1}
                for index, item in enumerate(bullets, 1) if item["text"].strip()]

    # Separate clearly independent paragraph questions; ordinary prose remains one task.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) > 1 and all(TASK_INTENT.search(p) and p.endswith("?") for p in paragraphs):
        return [{"id": index, "text": paragraph, "raw_text": paragraph,
                 "source_start": text.find(paragraph), "source_end": text.find(paragraph) + len(paragraph)}
                for index, paragraph in enumerate(paragraphs, 1)]
    return [{"id": 1, "text": text, "raw_text": text, "source_start": 0, "source_end": len(text)}]


def build_task_tree(prompt: str) -> list[TaskNode]:
    """Parse hierarchical task structure distinguishing content leaves, parent containers, and metadata."""
    text = unwrap_outer_prompt_envelope(str(prompt or "").replace("\r\n", "\n").replace("\r", "\n"))
    if not text.strip():
        return []
    raw_detected_sections = detect_raw_numbered_sections(text)
    numbered = _numbered_task_candidates(text)
    tree: list[TaskNode] = []
    if numbered:
        chosen = max(enumerate(numbered), key=lambda pair: (len(pair[1]), -pair[0]))[1]
        parsed_top_ids = [item["id"] for item in chosen]
        unparsed = [s for s in raw_detected_sections if s not in parsed_top_ids]
        if unparsed and len(numbered) > 1:
            combined = []
            seen = set()
            for cand in numbered:
                for item in cand:
                    if item["id"] not in seen:
                        seen.add(item["id"])
                        combined.append(item)
            combined.sort(key=lambda x: x["source_start"])
            if all(s in seen for s in raw_detected_sections):
                chosen = combined
        for item in chosen:
            if not str(item.get("text", "")).strip():
                continue
            subtasks = _expand_lettered_subtasks({k: v for k, v in item.items() if k != "explicit"}, text)
            if len(subtasks) == 1 and subtasks[0].get("id") == item["id"]:
                tree.append(TaskNode(
                    id=normalize_task_id(item["id"]),
                    parent_id=None,
                    node_type="CONTENT_TASK",
                    raw_text=item.get("raw_text", ""),
                    text=item.get("text", ""),
                    source_start=item.get("source_start", -1),
                    source_end=item.get("source_end", -1),
                ))
            else:
                children = [
                    TaskNode(
                        id=normalize_task_id(child["id"]),
                        parent_id=normalize_task_id(child.get("parent_id", item["id"])),
                        node_type="CONTENT_TASK",
                        raw_text=child.get("raw_text", ""),
                        text=child.get("text", ""),
                        source_start=child.get("source_start", -1),
                        source_end=child.get("source_end", -1),
                        classification_only=child.get("classification_only", False),
                        classification_subject=child.get("classification_subject"),
                    )
                    for child in subtasks
                ]
                tree.append(TaskNode(
                    id=normalize_task_id(item["id"]),
                    parent_id=None,
                    node_type="PARENT_CONTAINER",
                    raw_text=item.get("raw_text", ""),
                    text=item.get("text", ""),
                    source_start=item.get("source_start", -1),
                    source_end=item.get("source_end", -1),
                    children=children,
                ))
        last_end = max((node.source_end for node in tree), default=-1)
        if last_end >= 0 and last_end < len(text):
            trailing = text[last_end:].strip()
            if trailing:
                tree.append(TaskNode(
                    id="RUNTIME_METADATA",
                    parent_id=None,
                    node_type="RUNTIME_METADATA",
                    raw_text=trailing,
                    text=trailing,
                    source_start=last_end,
                    source_end=len(text),
                ))
        return tree

    flat = decompose_request_tasks(text)
    return [
        TaskNode(
            id=normalize_task_id(t["id"]),
            parent_id=None,
            node_type="CONTENT_TASK",
            raw_text=t.get("raw_text", ""),
            text=t.get("text", ""),
            source_start=t.get("source_start", -1),
            source_end=t.get("source_end", -1),
        )
        for t in flat
    ]


def get_content_leaf_tasks(nodes: list[TaskNode]) -> list[TaskNode]:
    """Return only executable content leaves; parent containers and metadata are excluded."""
    leaves: list[TaskNode] = []
    for node in nodes:
        if node.node_type == "CONTENT_TASK":
            leaves.append(node)
        elif node.node_type == "PARENT_CONTAINER":
            for child in node.children:
                if child.node_type == "CONTENT_TASK":
                    leaves.append(child)
    return leaves


def _task_kind(text: str) -> str:
    if NON_FACTUAL.search(text):
        return "deterministic_or_nonfactual"
    if re.search(r"\b(?:story|poem|fiction|imagine|roleplay|creative writing)\b", text, re.I):
        return "creative"
    if re.search(r"\b(?:what|who|when|where|why|how|explain|define|meaning|history)\b", text, re.I):
        return "factual_or_explanatory"
    return "general"


def _named_specific_claim(text: str) -> tuple[bool, list[str]]:
    names: list[str] = []
    for pattern in NAMED_ENTITY_PATTERNS:
        names.extend(match.group(0) for match in pattern.finditer(text))
    has_date = bool(DATE_PATTERN.search(text))
    explicit = bool(EXPLICIT_UNSUPPORTED_PHRASES.search(text))
    event_like = bool(SPECIFIC_EVENT_VERBS.search(text)) or (explicit and has_date)
    # “fictional” is meaningful only inside this task and alongside a factual/event query.
    fictional_claim = bool(re.search(r"\bfictional\b", text, re.I) and re.search(r"\b(?:what|who|when|announce|announced|yesterday|recent|claim)\b", text, re.I))
    return bool((event_like and (names or has_date)) or (fictional_claim and names)), names


def _classify_task(text: str, rag_context: str = "", has_local_evidence: bool = False) -> tuple[str, str]:
    kind = _task_kind(text)
    if kind in {"deterministic_or_nonfactual", "creative"}:
        return FactualGroundingState.NOT_APPLICABLE, "task_does_not_require_factual_grounding"

    is_specific, entities = _named_specific_claim(text)
    rag = str(rag_context or "").casefold()
    if is_specific:
        if rag and any(entity.casefold() in rag for entity in entities):
            return FactualGroundingState.SUPPORTED_BY_RAG, "named_claim_supported_by_retrieved_context"
        # An unrelated attachment is not evidence for a named event. The
        # retrieved context must actually mention the entity.
        return FactualGroundingState.UNSUPPORTED_SPECIFIC_CLAIM, "named_specific_claim_without_matching_local_evidence"

    if DOCUMENT_QUERY.search(text) and not rag_context and not has_local_evidence:
        return FactualGroundingState.UNKNOWN, "document_query_without_retrieved_evidence"
    if kind == "factual_or_explanatory":
        return FactualGroundingState.GENERAL_KNOWLEDGE, "general_knowledge_question"
    return FactualGroundingState.NOT_APPLICABLE, "no_specific_factual_claim_to_verify"


def evaluate_request_grounding(
    prompt: str,
    rag_context: str = "",
    has_local_evidence: bool = False,
) -> dict[str, Any]:
    """Return grounding classifications per independent task plus a compatible aggregate state."""
    tasks = decompose_request_tasks(prompt)
    classified = []
    for index, task in enumerate(tasks, 1):
        state, reason = ((FactualGroundingState.NOT_APPLICABLE, "classification_only")
                         if task.get("classification_only") else
                         _classify_task(task["text"], rag_context, has_local_evidence))
        # Every request item gets one stable, unique ordinal even if malformed
        # source text contains repeated or nested numbering.
        classified.append({**task, "id": normalize_task_id(task.get("id", index)),
                           "support": state, "reason": reason, "kind": _task_kind(task["text"])})
    unsupported = [task for task in classified if task["support"] == FactualGroundingState.UNSUPPORTED_SPECIFIC_CLAIM]
    unknown = [task for task in classified if task["support"] == FactualGroundingState.UNKNOWN]

    if unsupported and len(classified) > 1:
        aggregate = FactualGroundingState.MIXED
        reason = "unsupported_specific_claims_are_scoped_to_independent_tasks"
        fallback = UNCERTAINTY_RESPONSE
        scope = "task_level"
    elif unsupported:
        aggregate = FactualGroundingState.UNSUPPORTED
        reason = unsupported[0]["reason"]
        fallback = UNCERTAINTY_RESPONSE
        scope = "single_task"
    elif unknown and len(classified) == 1:
        aggregate = FactualGroundingState.UNKNOWN
        reason = unknown[0]["reason"]
        fallback = "I can't verify that claim from the information available in the attached document."
        scope = "single_task"
    elif any(item["support"] == FactualGroundingState.SUPPORTED_BY_RAG for item in classified):
        aggregate, reason, fallback = FactualGroundingState.SUPPORTED, "retrieved_context_supports_claim", None
        scope = "none"
    else:
        aggregate, reason, fallback = FactualGroundingState.SUPPORTED, "no_unsupported_specific_claim_detected", None
        scope = "none"

    return {
        "state": aggregate,
        "classification": classified[0]["support"] if len(classified) == 1 else aggregate,
        "reason": reason,
        "fallback": fallback,
        "scope": scope,
        "tasks": classified,
        "unsupported_task_ids": [task["id"] for task in unsupported],
    }


def evaluate_factual_grounding(
    prompt: str,
    rag_context: str = "",
    has_local_evidence: bool = False,
) -> tuple[str, str, str | None]:
    """Backward-compatible aggregate tuple; see evaluate_request_grounding for task detail."""
    result = evaluate_request_grounding(prompt, rag_context, has_local_evidence)
    return result["state"], result["reason"], result["fallback"]


def localize_unsupported_task_responses(response: str, grounding_tasks: list[dict[str, Any]]) -> str:
    """Ensure refusal remains attached to unsupported task(s), preserving sibling output."""
    text = str(response or "").strip()
    unsupported = [item for item in grounding_tasks if item.get("support") == FactualGroundingState.UNSUPPORTED_SPECIFIC_CLAIM]
    if not unsupported:
        return text
    sections: dict[int | str, tuple[int, int]] = {}
    matches = list(re.finditer(r"(?m)^\s*(?:\*\*)?(?:task\s+|question\s+)?(\d{1,3}(?:\.[A-Z])?)[.) :]+", text, re.I))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[normalize_task_id(match.group(1))] = (match.start(), end)

    edits: list[tuple[int, int, str]] = []
    missing: list[str] = []
    for task in unsupported:
        task_id = task.get("id")
        refusal = UNCERTAINTY_RESPONSE
        if task_id in sections:
            start, end = sections[task_id]
            section = text[start:end]
            # Replace only this unsupported task section, even if the model
            # mixed a refusal with invented details in the same section.
            heading_match = re.match(r"\s*(?:\*\*)?(?:task\s+|question\s+)?\d{1,3}(?:\.[A-Z])?[.) :]\s*", section, re.I)
            heading = heading_match.group(0).rstrip() if heading_match else f"{task_id}."
            separator = "\n" if end < len(text) else ""
            edits.append((start, end, f"{heading} {refusal}{separator}"))
        else:
            missing.append(f"{task_id}. {refusal}" if task_id is not None else refusal)
    for start, end, replacement in reversed(edits):
        text = text[:start] + replacement + text[end:]
    if missing:
        suffix = "\n\n" if text else ""
        text += suffix + "\n".join(missing)
    return text
