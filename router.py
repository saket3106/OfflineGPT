"""Intent routing, constraint extraction, and generation profile selection for Offline GPT."""
from __future__ import annotations

import re
from typing import Any

from grounding import FactualGroundingState, evaluate_request_grounding, normalize_task_id, detect_raw_numbered_sections, is_routing_classification
from logic_engine import analyze_logic
from math_engine import extract_and_evaluate_math

# Configurable profile settings for different user intents
PROFILE_SETTINGS: dict[str, dict[str, Any]] = {
    "normal": {"temperature": 0.3, "top_p": 0.9, "max_tokens": 1200},
    "factual": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 1200},
    "math": {"temperature": 0.0, "top_p": 0.1, "max_tokens": 700},
    "probability": {"temperature": 0.0, "top_p": 0.1, "max_tokens": 600},
    "logic": {"temperature": 0.05, "top_p": 0.5, "max_tokens": 700},
    "exact_format": {"temperature": 0.0, "top_p": 0.2, "max_tokens": 900},
    "coding": {"temperature": 0.15, "top_p": 0.85, "max_tokens": 2000},
    "summarization": {"temperature": 0.2, "top_p": 0.85, "max_tokens": 1000},
    "writing": {"temperature": 0.4, "top_p": 0.92, "max_tokens": 1800},
    "creative": {"temperature": 0.65, "top_p": 0.95, "max_tokens": 2000},
    "tool_required": {"temperature": 0.1, "top_p": 0.7, "max_tokens": 800},
    "multi_task": {"temperature": 0.15, "top_p": 0.85, "max_tokens": 2200},
}

DOCUMENT_MISSING_MESSAGE = (
    "I couldn't find an attached or available document to search. "
    "Attach the PDF or file, then send your question again."
)

_EXACT_DOCUMENT_INTENT = re.compile(
    r"\b(?:exact(?:ly)?|literal(?:ly)?|verbatim|word[- ]for[- ]word|"
    r"contains?\s+(?:the\s+)?(?:phrase|text|string|code|term)|"
    r"contains?\s+['\"“‘]|"
    r"find\s+(?:the\s+)?(?:phrase|text|string|code|term)|"
    r"occurrences?\s+of)\b", re.I,
)


def document_search_mode(text: str) -> str:
    """Choose literal matching only when the user actually requests it."""
    value=str(text or "")
    literal_code_query=re.search(
        r"\b(?:does|do)\s+(?:(?:this|the|my|uploaded|attached)\s+)?"
        r"(?:document|pdf|file|notes?)\s+contain\s+[A-Z0-9_:-]{3,80}\s*\??",
        value,re.I)
    quoted_search=bool(
        re.search(r"\b(?:search|find|look\s+for|contains?)\b",value,re.I)
        and re.search(r'["“”][^"“”]{2,160}["“”]|[‘\'][^‘\']{2,160}[’\']',value)
    )
    return "DOCUMENT_EXACT" if _EXACT_DOCUMENT_INTENT.search(value) or literal_code_query or quoted_search else "DOCUMENT_RAG"


def is_classification_only(text: str) -> bool:
    return is_routing_classification(text)


def classification_target(text: str) -> str | None:
    """Classify a quoted/requested operation without performing that operation."""
    if not is_classification_only(text):
        return None
    value = str(text or "")
    # The colon separates the meta request from the request being classified.
    subject = value.split(":", 1)[-1] if ":" in value else value
    subject = re.sub(r"\b(?:do\s+not|don't)\s+execute\b[^\n.]*", "", subject, flags=re.I).strip()
    subject = re.sub(r"\breturn\s+(?:only|exactly)\b[^\n.]*", "", subject, flags=re.I).strip()
    subject = re.sub(r"\breturn\b[^\n.]*", "", subject, flags=re.I).strip()
    subject = re.sub(r"\b(?:classify\s+only|classification\s+only)\b[:\s]*", "", subject, flags=re.I).strip()
    lines = []
    for line in subject.splitlines():
        line_str = line.strip()
        if re.match(r"(?i)^(?:return\b|format\b|output\b|\d+\.[A-Z]\b|do\s+not|don't)", line_str):
            continue
        if line_str:
            lines.append(line_str)
    cleaned_subject = " ".join(lines).strip()
    if not cleaned_subject or is_classification_only(cleaned_subject):
        return None
    nested = route_request(cleaned_subject)
    return "TOOL" if nested["route_target"] in {
        "CALCULATOR", "PYTHON", "DOCUMENT_EXACT", "DOCUMENT_RAG", "TOOL"
    } else "DIRECT"


def has_document_reference(text: str, has_document: bool = False) -> bool:
    """Return true for a real request to use a local document, not a casual mention."""
    if has_document:
        return True
    value = str(text or "").casefold()
    nouns = r"(?:pdfs?|documents?|files?|notes?|slides?|spreadsheets?|workbooks?|docx|pptx|xlsx|csv|txt|markdown)"
    source = r"(?:(?:a|an|the|my|this|that|attached|uploaded|provided|current)\s+){0,4}"
    # Explicit deictic/upload references such as "this PDF" and "my notes".
    if re.search(r"\b(?:my|this|that|attached|uploaded|provided|current)\s+" + nouns + r"\b", value):
        return True
    if re.search(r"\b(?:no|without(?:\s+any)?|missing)\s+(?:attached|uploaded)\s+" + nouns + r"\b", value):
        return True
    if re.search(r"\b(?:assume\s+)?no\s+" + nouns + r"\s+is\s+uploaded\b", value):
        return True
    if re.search(r"\bmissing-document\b", value):
        return True
    # A document operation tied to a source, including "search an uploaded PDF".
    operation = r"\b(?:according\s+to|based\s+on|from|in|within|search(?:\s+for)?|look\s+(?:in|through)|find\s+in|refer\s+to|read|summari[sz]e|analy[sz]e|edit|explain|what\s+(?:is|does|are)\s+(?:in|from))\s+"
    if re.search(operation + source + nouns + r"\b", value):
        return True
    # A named local file (including a filename without a preceding verb).
    return bool(re.search(r"\b[\w().-]{1,120}\.(?:pdf|docx?|pptx?|xlsx?|csv|txt|md|rtf|html?)\b", value))


def extract_constraints(text: str) -> dict[str, Any]:
    """Extract deterministic format, length, and content constraints from the user's prompt."""
    low = text.casefold()
    constraints: dict[str, Any] = {
        "word_count_exact": None,
        "word_count_max": None,
        "word_count_min": None,
        "sentence_count_exact": None,
        "bullet_count": None,
        "required_words": [],
        "forbidden_words": [],
        "output_only": bool(re.search(r"\b(?:answer\s+only|output\s+only|only\s+(?:the\s+)?answer|no\s+explanation|return\s+only\b)", low)),
        "table_only": bool(re.search(r"\b(?:table\s+only|markdown\s+table\s+only|as\s+a\s+markdown\s+table|format\s+as\s+table)\b", low)),
        "json": bool(re.search(r"\b(?:valid\s+json|json\s+only|return\s+json|output\s+json|as\s+json)\b", low)),
        "json_required_keys": [],
    }

    # Extract exact word count: "in exactly 8 words", "exactly 12 words", "an 8-word sentence", "write 10 words", "10 words exactly"
    exact = (
        re.search(r"\b(?:exactly|in(?:\s+exactly)?|use|write|give(?:\s+me)?|limit\s+to)\s+(\d{1,4})\s+words?\b", low)
        or re.search(r"\b(\d{1,4})\s+words?\s+exactly\b", low)
        or re.search(r"\b(\d{1,4})[-\s]?words?\s+(?:answer|response|summary|sentence|rewrite|statement|about|on)\b", low)
    )
    if exact:
        constraints["word_count_exact"] = int(exact.group(1))

    # Sentence count: "in exactly 3 sentences", "write 2 sentences", "a 3-sentence summary"
    sentences = (
        re.search(r"\b(?:exactly|in|write)\s+(\d{1,3})\s+sentences?\b", low)
        or re.search(r"\b(\d{1,3})[-\s]?sentences?\s+(?:answer|response|summary|statement)\b", low)
    )
    if sentences:
        constraints["sentence_count_exact"] = int(sentences.group(1))

    # Maximum word count: "under 50 words", "at most 30 words", "no more than 20 words", "max 100 words"
    max_words = re.search(r"\b(?:under|at most|no more than|maximum|max|within)\s+(\d{1,4})\s+words?\b", low)
    if max_words:
        constraints["word_count_max"] = int(max_words.group(1))

    # Minimum word count: "at least 50 words", "minimum 30 words", "min 20 words"
    min_words = re.search(r"\b(?:at least|minimum|min)\s+(\d{1,4})\s+words?\b", low)
    if min_words:
        constraints["word_count_min"] = int(min_words.group(1))

    # Bullet point count: "exactly 3 bullet points", "3 bullets", "list exactly 3 benefits"
    bullets = (
        re.search(r"\b(?:exactly\s+)?(\d{1,2})\s+(?:bullet points?|bullets|items?)\b", low)
        or re.search(r"\b(?:list|give|name)\s+(?:exactly\s+)?(\d{1,2})\s+(?:benefits?|reasons?|examples?|tips?|points?|steps?)\b", low)
    )
    if bullets:
        constraints["bullet_count"] = int(bullets.group(1))

    # Required words/phrases: 'include the words "foo", "bar"' or 'must include: photovoltaic, renewable'
    constraints["required_words"] = _extract_quoted_or_listed(
        text,
        r"(?:must\s+include|required\s+words?|include\s+(?:the\s+)?words?|contain\s+(?:the\s+)?words?|must\s+contain|include\s+the\s+phrase)\s*(?:the\s+)?(?:words?|phrases?)?[:\s]*|with\s+(?:the\s+)?(?:words?|phrases?)[:\s]*|include\s*:\s*",
        stop_pattern=r"\b(?:without\s+using|do\s+not\s+use|don't\s+use|avoid|must\s+not\s+include|must\s+not\s+contain|cannot\s+contain|never\s+contain|forbidden)\b",
        negative_preceding_check=True,
    )

    # Forbidden words/phrases: 'without using the words "foo"' or 'must not include: electronic' or 'must not contain: perfect'
    constraints["forbidden_words"] = _extract_quoted_or_listed(
        text,
        r"(?:without using|do not use|don't use|avoid|must not include|must not contain|cannot contain|never contain|should not contain|forbidden(?: words?)?)\s*(?:the\s+)?(?:words?|phrases?)?[:\s]*",
        stop_pattern=r"\b(?:include|must\s+contain|with\s+the\s+words?|must\s+include|required)\b",
    )

    # JSON keys: e.g. "with keys answer and confidence"
    keys_match = re.search(r"\bkeys?\s+([a-zA-Z0-9_, -]+?)(?:\s+for|\.|$)", text, re.I)
    if keys_match and constraints["json"]:
        raw_keys = keys_match.group(1)
        found_keys = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", raw_keys)
        valid_keys = [k for k in found_keys if k.lower() not in {"and", "or", "the", "with"}]
        constraints["json_required_keys"] = valid_keys

    # Required output fields / labels: e.g. "After the sentence write: WORD COUNT: 11" or "WORD COUNT: 11"
    req_fields = []
    for m in re.finditer(r"(?im)(?:after\s+(?:the\s+)?(?:sentence|answer|calculation)\s+write|include\s+(?:the\s+)?(?:line|label)|write\s+exactly)\s*:\s*\n*([A-Za-z0-9 _-]+:\s*[^\n\r]+)", text):
        val = m.group(1).strip()
        if val and val not in req_fields:
            req_fields.append(val)
    m_wc = re.search(r"(?im)^\s*(WORD\s+COUNT\s*:\s*\d+)\s*$", text)
    if m_wc and m_wc.group(1).strip() not in req_fields:
        req_fields.append(m_wc.group(1).strip())
    if constraints.get("word_count_exact") and not any(re.match(r"(?i)^WORD\s+COUNT\s*:", f) for f in req_fields):
        if re.search(r"(?im)\bWORD\s+COUNT\s*:\s*\d+\b", text):
            req_fields.append(f"WORD COUNT: {constraints['word_count_exact']}")
    constraints["required_output_fields"] = req_fields

    # Finish / termination marker constraint
    constraints["finish_marker"] = None
    finish_marker_match = re.search(
        r"(?im)^\s*(?:finish|end)\s+with(?:\s+exactly)?\s*:\s*\n*([^\n\r]+)",
        text,
    )
    if not finish_marker_match:
        finish_marker_match = re.search(
            r"(?im)\b(?:finish|end)\s+with(?:\s+exactly)?\s*:\s*\n*([^\n\r]+)",
            text,
        )
    if finish_marker_match:
        marker = finish_marker_match.group(1).strip()
        marker = re.sub(r"^[`*\"']+|[`*\"']+$", "", marker).strip()
        if marker and not re.search(r"^FINAL\s+CONTROLLER\s+TEST\s+COMPLETE$", marker.strip(), re.I):
            constraints["finish_marker"] = marker

    return constraints


def _extract_quoted_or_listed(
    text: str,
    prefix_pattern: str,
    stop_pattern: str | None = None,
    negative_preceding_check: bool = False,
) -> list[str]:
    """Extract list of words/phrases following a constraint prefix (quoted or unquoted comma/and-separated)."""
    found: list[str] = []
    prefix_pat = re.compile(prefix_pattern, re.I)
    for m in prefix_pat.finditer(text):
        if negative_preceding_check:
            preceding = text[: m.start()].strip()
            if re.search(r"\b(?:not|never|without|don't|do\s+not(?:\s+omit)?|omit|avoid)\s*$", preceding, re.I):
                continue
        clause = text[m.end() : m.end() + 150]
        # Strip leading colon and whitespace if present
        clause = re.sub(r"^[\s:]+", "", clause)
        if stop_pattern:
            clause = re.split(stop_pattern, clause, flags=re.I)[0]
        clause = re.split(r"[.;\n]", clause)[0]

        # Check if clause specifies a count constraint rather than required words:
        # e.g., "The sentence itself must contain exactly 12 words."
        if re.search(r"^\s*(?:exactly|at least|minimum|at most|maximum|more than|less than)?\s*\d+\s*(?:words?|sentences?|bullet points?|bullets|items?|chars?|characters?)\b", clause, re.I):
            continue

        # Skip clauses describing output fields/labels or document formatting
        if re.search(r"^\s*(?:output\s+(?:fields?|labels?|format|mode)|format|fields?|labels?|columns?)\b", clause, re.I):
            continue

        # Extract all quoted substrings in this clause
        quoted = re.findall(r"['\"]([^'\"]+?)['\"]", clause)
        if quoted:
            for q in quoted:
                val = q.strip()
                if val and val not in found:
                    found.append(val)
        else:
            # Split by comma or "and" to get all listed unquoted words
            items = re.split(r"[,/]|(?:\s+and\s+)", clause)
            for item in items:
                # If item describes a count constraint, skip it
                if re.search(r"\b(?:exactly|at least|minimum|at most|maximum|more than|less than)?\s*\d+\s*(?:words?|sentences?|bullet points?|bullets|items?|chars?|characters?)\b", item, re.I):
                    continue
                words = re.findall(r"\b[A-Za-z0-9_-]+\b", item)
                filtered = [w for w in words if w.lower() not in {"the", "a", "an", "words", "word", "phrases", "phrase", "and", "or", "output", "field", "fields", "format", "label", "labels"}]
                if filtered:
                    if all(w.isdigit() or w.lower() in {"exactly", "minimum", "maximum", "least", "most", "about", "approximately"} for w in filtered):
                        continue
                    val = " ".join(filtered)
                    if val and val not in found:
                        found.append(val)

    return found[:12]



def route_request(text: str, has_document: bool = False, rag_context: str = "") -> dict[str, Any]:
    """Classify the user request, select generation profile, detect tools and constraints."""
    original = str(text or "")
    low = original.casefold()
    classification_only = is_classification_only(original)

    constraints = extract_constraints(original if not classification_only else "")
    math_eval = None if classification_only else extract_and_evaluate_math(original)
    logic_eval = None if classification_only else analyze_logic(original)

    # Check intents
    wants_datetime = not classification_only and bool(re.search(r"\b(?:what time is it|what is the time|current time|today's date|what date is it|what day is it)\b", low))
    wants_system_info = not classification_only and bool(re.search(r"\b(?:system info|specs?|ram status|how much ram|hardware specs?|pc specs?)\b", low))
    wants_code = bool(re.search(r"\b(?:python|javascript|typescript|c\+\+|java|rust|go|code|function|class|method|script|debug|traceback|exception|bug|syntaxerror|runtimeerror|compile)\b", low))
    wants_python_execution = bool(
        re.search(r"\b(?:run|execute|test|evaluate)\s+(?:(?:this|the|a|an|the\s+following|following)\s+)*python(?:\s+(?:code|script|program))?\b|\bpython\s+(?:execution|run|eval)\b", low)
    )
    wants_summarize = bool(re.search(r"\b(?:summarize|summary|condense|tldr|tl;dr)\b", low))
    wants_writing = bool(re.search(r"\b(?:email|letter|rewrite|draft|essay|article|blog post|cover letter|announcement)\b", low))
    wants_creative = bool(re.search(r"\b(?:story|poem|creative|imagine|fiction|novel|lyrics|roleplay|dialogue)\b", low))
    wants_document = not classification_only and has_document_reference(original, has_document=has_document)

    has_exact_constraint = (
        constraints["word_count_exact"] is not None
        or constraints["word_count_max"] is not None
        or constraints["word_count_min"] is not None
        or constraints["sentence_count_exact"] is not None
        or constraints["bullet_count"] is not None
        or bool(constraints["required_words"])
        or bool(constraints["forbidden_words"])
        or constraints["output_only"]
        or constraints["table_only"]
        or constraints["json"]
    )

    # Ground each independent task separately. A single unsupported fact must not
    # turn a multi-part request into a single refusal-only response.
    grounding = evaluate_request_grounding(
        original,
        rag_context=rag_context,
        has_local_evidence=has_document,
    )
    factual_state = grounding["state"]
    grounding_reason = grounding["reason"]
    grounding_fallback = grounding["fallback"]
    grounding_tasks = grounding["tasks"]
    is_mixed_grounding = factual_state == FactualGroundingState.MIXED
    raw_detected_sections = detect_raw_numbered_sections(original)
    expected_task_ids = [
        normalize_task_id(task.get("id", index + 1))
        for index, task in enumerate(grounding_tasks)
        if task.get("node_type", "CONTENT_TASK") == "CONTENT_TASK"
    ]
    parsed_sections_set = set()
    for index, task in enumerate(grounding_tasks):
        p_id = task.get("parent_id")
        if p_id is not None:
            try:
                parsed_sections_set.add(int(str(p_id).split(".")[0]))
            except ValueError:
                pass
        t_id = task.get("id", index + 1)
        if t_id is not None:
            try:
                parsed_sections_set.add(int(str(t_id).split(".")[0]))
            except ValueError:
                pass
    parsed_sections = sorted(parsed_sections_set)
    unparsed_sections = [s for s in raw_detected_sections if s not in parsed_sections_set]
    is_multi_task = len(expected_task_ids) > 1 or len(raw_detected_sections) > 1

    capabilities: list[str] = []
    if wants_document:
        capabilities.append("document_search")
    if has_exact_constraint:
        capabilities.append("constraint_validation")
    if math_eval:
        capabilities.append("math")
    if logic_eval:
        capabilities.append("logic")
    if wants_code:
        capabilities.append("code")
    if wants_datetime:
        capabilities.append("datetime")
    if wants_system_info:
        capabilities.append("system_info")

    # Keep deterministic checks attached to their own task. A global word-count
    # or math validator can otherwise turn a valid composite answer into a retry
    # that replaces the complete response with just one constrained item.
    task_analyses: list[dict[str, Any]] = []
    if is_multi_task:
        for task in grounding_tasks:
            task_text = str(task.get("text") or "")
            task_constraints = extract_constraints(task_text)
            task_classification_only=bool(task.get("classification_only")) or is_classification_only(task_text)
            task_math = None if task_classification_only else extract_and_evaluate_math(task_text)
            task_logic = None if task_classification_only else analyze_logic(task_text)
            task_low = task_text.casefold()
            task_datetime = bool(re.search(r"\b(?:what time is it|what is the time|current time|today's date|what date is it|what day is it)\b", task_low))
            task_system_info = bool(re.search(r"\b(?:system info|specs?|ram status|how much ram|hardware specs?|pc specs?)\b", task_low))
            task_code = bool(re.search(r"\b(?:python|javascript|typescript|c\+\+|java|rust|go|code|function|class|method|script|debug|traceback|exception|bug|syntaxerror|runtimeerror|compile)\b", task_low))
            # An attachment is available to all tasks, but only tasks that
            # actually refer to it should be sent through document retrieval.
            task_document = not task_classification_only and (
                has_document_reference(task_text, has_document=False)
                or (wants_document and bool(re.search(
                    r"\b(?:find|search|contains?)\b",
                    task_text, re.I)))
            )
            task_support = task.get("support")
            task_grounding_state = (
                "UNSUPPORTED" if task_support == FactualGroundingState.UNSUPPORTED_SPECIFIC_CLAIM
                else "UNKNOWN" if task_support == FactualGroundingState.UNKNOWN
                else "SUPPORTED"
            )
            if task_classification_only:
                task_route = "CLASSIFICATION_ONLY"
            elif task_document:
                task_route = document_search_mode(task_text)
            elif re.search(r"\b(?:run|execute|test|evaluate)\s+(?:(?:this|the|a|an|the\s+following|following)\s+)*python(?:\s+(?:code|script|program))?\b|\bpython\s+(?:execution|run|eval)\b", task_low):
                task_route = "PYTHON"
            elif task_math:
                task_route = "CALCULATOR"
            elif task_logic:
                task_route = "LOGIC"
            elif task_datetime or task_system_info:
                task_route = "TOOL"
            elif any((task_constraints.get("word_count_exact") is not None,
                      task_constraints.get("word_count_max") is not None,
                      task_constraints.get("word_count_min") is not None,
                      task_constraints.get("json"),task_constraints.get("table_only"))):
                task_route = "CONSTRAINT"
            else:
                task_route = "DIRECT"
            task_tool_context = task_math.get("summary", "") if task_math else (task_logic.get("summary", "") if task_logic else "")
            task_analyses.append({
                "id": normalize_task_id(task.get("id", len(task_analyses) + 1)),
                "text": task_text,
                "raw_text": task.get("raw_text", task_text),
                "source_start": task.get("source_start", -1),
                "source_end": task.get("source_end", -1),
                "parent_id": task.get("parent_id"),
                "node_type": task.get("node_type", "CONTENT_TASK"),
                "classification_subject": task.get("classification_subject") if task_classification_only else None,
                "route_target": task_route,
                "constraints": task_constraints,
                "tool_context": task_tool_context,
                "expected_math_number": task_math.get("expected_number") if task_math else None,
                "expected_math_numbers": task_math.get("expected_numbers", []) if task_math else [],
                "math_type": task_math.get("type") if task_math else None,
                "logic_info": task_logic,
                "factual_grounding_state": task_grounding_state,
                "factual_grounding_classification": task_support,
                "grounding_reason": task.get("reason", ""),
                "has_document": has_document,
                "wants_datetime": task_datetime,
                "wants_system_info": task_system_info,
                "category": (
                    "document" if task_document else "math" if task_math and task_math.get("type") != "diagnostic_probability"
                    else "probability" if task_math else "logic" if task_logic else "tool_required" if task_datetime or task_system_info
                    else "exact_format" if any((task_constraints.get("word_count_exact") is not None,
                        task_constraints.get("word_count_min") is not None, task_constraints.get("word_count_max") is not None,
                        task_constraints.get("sentence_count_exact") is not None, task_constraints.get("bullet_count") is not None,
                        task_constraints.get("required_words"), task_constraints.get("forbidden_words"), task_constraints.get("json"),
                        task_constraints.get("table_only"), task_constraints.get("output_only")))
                    else "coding" if task_code else "normal"
                ),
            })

    # Whole-request arithmetic/logic/format validators cannot safely validate a
    # composite benchmark. They are re-run against each parsed response section.
    if is_multi_task or is_mixed_grounding:
        math_eval = None
        logic_eval = None
        finish_marker = constraints.get("finish_marker")
        constraints = {"finish_marker": finish_marker} if finish_marker else {}

    if is_multi_task or is_mixed_grounding:
        category = "multi_task"
    elif wants_document and len(capabilities) > 1:
        category = "multi_task"
    elif wants_document:
        category = "document"
    elif wants_python_execution:
        category = "coding"
    elif math_eval:
        if math_eval.get("type") == "diagnostic_probability":
            category = "probability"
        else:
            category = "math"
    elif logic_eval:
        category = "logic"
    elif factual_state == FactualGroundingState.UNSUPPORTED:
        category = "factual"
    elif wants_datetime or wants_system_info:
        category = "tool_required"
    elif has_exact_constraint:
        category = "exact_format"
    elif wants_code:
        category = "coding"
    elif wants_summarize:
        category = "summarization"
    elif wants_creative:
        category = "creative"
    elif wants_writing:
        category = "writing"
    elif re.search(r"\b(?:who|what|when|where|why|how|explain|define|meaning of|history of)\b", low):
        category = "factual"
    else:
        category = "normal"

    profile = dict(PROFILE_SETTINGS.get(category, PROFILE_SETTINGS["normal"]))

    # Validation is required for deterministic tasks
    validation_required = bool(
        has_exact_constraint
        or math_eval
        or logic_eval
        or factual_state == FactualGroundingState.UNSUPPORTED
        or factual_state == FactualGroundingState.MIXED
        or is_multi_task
        or category in {"exact_format", "math", "probability", "logic"}
    )
    silent_first_pass = validation_required

    tool_context = ""
    expected_number = None
    expected_numbers: list[Any] = []
    if math_eval:
        tool_context = math_eval["summary"]
        expected_number = math_eval.get("expected_number")
        expected_numbers = math_eval.get("expected_numbers", [])
    elif logic_eval:
        tool_context = logic_eval["summary"]

    if is_multi_task:
        task_context_parts = []
        for task_analysis in task_analyses:
            if task_analysis["tool_context"]:
                task_context_parts.append(f"Task {task_analysis['id']} ({task_analysis['route_target']}): {task_analysis['tool_context']}")
        tool_context = "\n".join(task_context_parts)

    return {
        "category": category,
        "capabilities": capabilities,
        "route_target": ("MULTI_TASK" if is_multi_task else "CLASSIFICATION_ONLY" if classification_only
                         else document_search_mode(original) if wants_document else
                         ("PYTHON" if wants_python_execution else "CALCULATOR" if math_eval else "LOGIC" if logic_eval
                          else "TOOL" if wants_datetime or wants_system_info else "CONSTRAINT" if has_exact_constraint else "DIRECT")),
        "document_required": wants_document,
        "document_available": bool(has_document),
        "missing_document": bool(wants_document and not has_document),
        "document_status": ("NO_DOCUMENT_AVAILABLE" if wants_document and not has_document
                            else "DOCUMENT_AVAILABLE" if wants_document else "NOT_REQUESTED"),
        "document_search_performed": False,
        "profile": profile,
        "constraints": constraints,
        "tool_context": tool_context,
        "expected_math_number": expected_number,
        "expected_math_numbers": expected_numbers,
        "math_type": math_eval.get("type") if math_eval else None,
        "logic_info": logic_eval,
        "factual_grounding_state": factual_state,
        "factual_grounding_reason": grounding_reason,
        "factual_grounding_fallback": grounding_fallback,
        "factual_grounding_classification": grounding["classification"],
        "factual_grounding_scope": grounding["scope"],
        "grounding_tasks": grounding_tasks,
        "task_analyses": task_analyses,
        "expected_task_ids": expected_task_ids if is_multi_task else [],
        "multi_task": is_multi_task,
        "unsupported_task_ids": grounding["unsupported_task_ids"],
        "prompt": original,
        "has_document": has_document,
        "validation_required": validation_required,
        "silent_first_pass": silent_first_pass,
        "max_retries": 2,
        "wants_datetime": wants_datetime,
        "wants_system_info": wants_system_info,
        "wants_python_execution": wants_python_execution,
        "raw_detected_sections": raw_detected_sections,
        "parsed_sections": parsed_sections,
        "unparsed_sections": unparsed_sections,
        "parser_coverage_valid": len(unparsed_sections) == 0,
    }


def get_relevant_tools(analysis: dict[str, Any] | None = None, prompt_text: str | None = None) -> list[str] | None:
    """Determine the subset of tool names required for the given analysis.
    Returns [] for simple conversational requests (DIRECT, CONSTRAINT, CLASSIFICATION_ONLY).
    Returns specific tool names when relevant.
    Returns None if analysis is unknown (allowing all tools as fallback).
    """
    if analysis is None:
        return None

    # Check if multi_task
    if analysis.get("multi_task"):
        tasks = analysis.get("task_analyses") or []
        needed = set()
        for task in tasks:
            # Classification-only never needs execution tools
            if task.get("classification_only") or task.get("route_target") == "CLASSIFICATION_ONLY":
                continue
            rt = task.get("route_target", "")
            cat = task.get("category", "")
            if rt == "CALCULATOR" or cat in {"math", "probability"}:
                needed.add("calculator")
            elif rt == "PYTHON" or cat == "coding" or task.get("wants_python_execution"):
                needed.add("python_runner")
            elif task.get("wants_datetime"):
                needed.add("datetime")
            elif task.get("wants_system_info"):
                needed.add("system_info")
            elif rt == "TOOL":
                tt = (str(task.get("text") or "")).lower()
                if "time" in tt or "date" in tt:
                    needed.add("datetime")
                if "system" in tt or "ram" in tt or "hardware" in tt or "specs" in tt:
                    needed.add("system_info")
                if "calc" in tt or "math" in tt:
                    needed.add("calculator")
                if "python" in tt or "code" in tt:
                    needed.add("python_runner")
                if "file" in tt or "search" in tt:
                    needed.add("file_search")
        return sorted(needed)

    # Single task
    route_target = analysis.get("route_target", "DIRECT")
    category = analysis.get("category", "normal")
    text = prompt_text or analysis.get("prompt", "")

    # Classification-only never needs tool schemas injected
    if route_target == "CLASSIFICATION_ONLY" or is_classification_only(text):
        return []

    tools = []
    if re.search(r'\b(?:calculate|compute|work out)\b.{0,100}\b(?:percent(?:age)?|improvement|difference|ratio|total|average|change)\b',text,re.I) and not re.search(r'\b(?:not|never|without|don.t)\b.{0,30}\bcalculator\b',text,re.I):tools.append('calculator')
    if re.search(r'\b(?:use|using|with|call)\s+(?:the\s+)?(?:local\s+)?calculator(?:\s+tool)?\b',text,re.I) and not re.search(r'\b(?:not|never|without|don.t)\b.{0,30}\bcalculator\b',text,re.I):tools.append('calculator')
    has_python = (
        route_target == "PYTHON"
        or analysis.get("wants_python_execution")
        or bool(re.search(r"\b(?:run|execute|test|evaluate)\s+(?:(?:this|the|a|an|following)\s+)?python(?:\s+(?:code|script|program))?\b|\bpython\s+(?:execution|run|eval)\b", text.lower()))
    )
    if has_python:
        tools.append("python_runner")

    if (route_target == "CALCULATOR" or category in {"math", "probability"}) and not has_python:
        tools.append("calculator")

    if analysis.get("wants_datetime"):
        tools.append("datetime")
    if analysis.get("wants_system_info"):
        tools.append("system_info")

    if route_target == "TOOL" or category == "tool_required":
        p_low = str(text).lower()
        if "file" in p_low or "search" in p_low:
            tools.append("file_search")
        if "calc" in p_low or "math" in p_low:
            tools.append("calculator")
        if "python" in p_low:
            tools.append("python_runner")

    if tools:
        return sorted(set(tools))

    # Simple conversational, formatting, writing, summarization, factual, logic, constraint without tools
    if route_target in {"DIRECT", "CONSTRAINT", "LOGIC", "DOCUMENT_EXACT", "DOCUMENT_RAG"}:
        return []

    if category in {"normal", "factual", "exact_format", "summarization", "writing", "creative"}:
        return []

    return None
