"""Logic verification and constraint analysis engine for Offline GPT.

Detects logic puzzles, constraint satisfaction problems, and deduction tasks.
For finite permutation/assignment puzzles, evaluates whether:
  - 0 valid solutions exist (contradiction)
  - 1 unique solution exists
  - >1 valid solutions exist (underdetermined)
Ensures the assistant never asserts an arbitrary unique answer when a puzzle
is underdetermined.
"""
from __future__ import annotations

import itertools
import re
from typing import Any


LOGIC_TRIGGERS = [
    re.compile(r"\b(?:logic\s+puzzle|riddle|clues?:\s*|clue\s+\d+|who\s+(?:is|lives|owns|sits|wears)|which\s+person)\b", re.I),
    re.compile(r"\b(?:underdetermined|unique\s+solution|multiple\s+solutions|is\s+it\s+possible\s+to\s+determine)\b", re.I),
    re.compile(r"\b(?:knights?\s+and\s+knaves?|truth-?teller|liar\s+puzzle)\b", re.I),
    re.compile(r"\b(?:seating\s+arrangement|order\s+of\s+finish|race\s+results?)\b", re.I),
]


def is_logic_puzzle(text: str) -> bool:
    """Detect if the user prompt is a logic puzzle or constraint satisfaction problem."""
    low = text.casefold()
    return any(p.search(low) for p in LOGIC_TRIGGERS)


def check_finite_assignment_puzzle(text: str) -> dict[str, Any] | None:
    """Attempt finite permutation solving for small 3-4 entity assignment puzzles.

    Recognizes patterns like:
      Entities: [Alice, Bob, Charlie]
      Attributes: [red, green, blue] or [cat, dog, bird]
      Negative clues: "Alice is not wearing red", "Bob does not own a dog"
      Positive clues: "Charlie wears blue"
    """
    low = text.casefold()

    # Look for 3-4 names
    common_names = ["alice", "bob", "charlie", "david", "emma", "frank", "grace"]
    found_names = [name.capitalize() for name in common_names if re.search(rf"\b{name}\b", low)]
    if len(found_names) not in (3, 4):
        return None

    # Look for matching attribute category (e.g. colors, pets, houses)
    color_set = ["red", "green", "blue", "yellow"]
    pet_set = ["cat", "dog", "bird", "fish"]
    fruit_set = ["apple", "banana", "orange", "grape"]

    found_colors = [c for c in color_set if re.search(rf"\b{c}\b", low)]
    found_pets = [p for p in pet_set if re.search(rf"\b{p}\b", low)]
    found_fruits = [f for f in fruit_set if re.search(rf"\b{f}\b", low)]

    attributes = None
    if len(found_colors) == len(found_names):
        attributes = found_colors
    elif len(found_pets) == len(found_names):
        attributes = found_pets
    elif len(found_fruits) == len(found_names):
        attributes = found_fruits

    if not attributes:
        return None

    # Parse negative clues: e.g. "Alice is not wearing red", "Bob does not like cat"
    neg_clues = []
    for name in found_names:
        for attr in attributes:
            pattern = rf"\b{name.lower()}\b[^.]*?\b(?:not|never|doesn't|does\s+not)\b[^.]*?\b{attr}\b"
            if re.search(pattern, low):
                neg_clues.append((name, attr, False))

    # Parse positive clues: e.g. "Alice wears red", "Charlie has the dog"
    pos_clues = []
    for name in found_names:
        for attr in attributes:
            pattern = rf"\b{name.lower()}\b[^.]*?\b(?:is|wears|has|owns|likes)\b(?:\s+the|\s+a)?\s+{attr}\b"
            # Make sure it's not a negated sentence
            if re.search(pattern, low) and not any(n == name and a == attr for n, a, _ in neg_clues):
                pos_clues.append((name, attr, True))

    # Test all permutations of attributes to names
    valid_solutions = []
    for perm in itertools.permutations(attributes):
        assignment = dict(zip(found_names, perm))
        consistent = True
        # Check negative clues
        for name, attr, _ in neg_clues:
            if assignment.get(name) == attr:
                consistent = False
                break
        if not consistent:
            continue
        # Check positive clues
        for name, attr, _ in pos_clues:
            if assignment.get(name) != attr:
                consistent = False
                break
        if consistent:
            valid_solutions.append(assignment)

    count = len(valid_solutions)
    if count == 0:
        return {
            "status": "contradiction",
            "solution_count": 0,
            "solutions": [],
            "guidance": "Contradiction detected: 0 valid assignments satisfy all clues.",
        }
    elif count == 1:
        return {
            "status": "unique",
            "solution_count": 1,
            "solutions": valid_solutions,
            "guidance": f"Unique solution verified: {valid_solutions[0]}.",
        }
    else:
        return {
            "status": "underdetermined",
            "solution_count": count,
            "solutions": valid_solutions,
            "guidance": (
                f"Underdetermined puzzle: {count} valid solutions exist satisfying all clues. "
                "Do NOT choose a single arbitrary answer. State clearly that multiple solutions exist "
                "and list the valid possibilities."
            ),
        }


def analyze_logic(text: str) -> dict[str, Any] | None:
    """Analyze a logic request and produce verification directives."""
    if not is_logic_puzzle(text):
        return None

    finite_eval = check_finite_assignment_puzzle(text)
    if finite_eval:
        return {
            "type": "finite_logic",
            "status": finite_eval["status"],
            "solution_count": finite_eval["solution_count"],
            "solutions": finite_eval["solutions"],
            "guidance": finite_eval["guidance"],
            "summary": finite_eval["guidance"],
        }

    # General logic puzzle fallback
    guidance = (
        "Logic Verification Rule: Check whether (1) zero solutions exist (contradiction), "
        "(2) exactly one unique solution exists, or (3) multiple solutions exist (underdetermined). "
        "If multiple valid solutions exist, state that the problem is underdetermined and present the valid possibilities."
    )
    return {
        "type": "general_logic",
        "status": "ambiguous_check_required",
        "solution_count": None,
        "solutions": [],
        "guidance": guidance,
        "summary": guidance,
    }


def solve_structured_planning(text: str) -> dict[str, Any] | None:
    """Generic structured planning solver for dependency scheduling.

    Represents tasks as Task { id, duration, dependencies }.
    Calculates:
      start(task) = max finish time of dependencies (or 0 for independent tasks)
      finish(task) = start(task) + duration
      project_finish = max(finish times)
    Validates:
      finish - start == duration
      dependency finish <= dependent start
      detects cycles and invalid dependencies
    Returns structured schedule and formatted response, or None if ambiguous.
    """
    if not text:
        return None

    # Match task patterns with duration
    # e.g.: Task A takes 3 hours, A = 2 hours, A: 2h, A 3h, Task 1 takes 2 days
    pat = re.compile(
        r"(?i)(?:(?:task|step|activity)\s+)?([A-Za-z0-9]+)\s*(?:takes|=|is|:|\s*-\s*|\s)\s*(\d+(?:\.\d+)?)\s*(hours?|hrs?|h\b|days?|d\b|minutes?|mins?|m\b)",
    )
    raw_matches = list(pat.finditer(text))
    if len(raw_matches) < 2:
        return None

    stop_words = {
        "IN", "FOR", "AT", "BY", "WITH", "ABOUT", "EVERY", "EACH",
        "THIS", "THAT", "AFTER", "BEFORE", "AND", "OR", "TOTAL", "TIME", "MINIMUM",
        "MAXIMUM", "HOURS", "HOUR", "MINUTES", "DAYS", "DAY", "TASK", "TASKS"
    }
    valid_matches = []
    for m in raw_matches:
        tid = m.group(1).strip()
        # Single letters A-Z are always valid task identifiers
        if len(tid) == 1 and tid.isalpha():
            valid_matches.append(m)
        elif tid.upper() in stop_words or tid.isdigit():
            continue
        else:
            valid_matches.append(m)

    if len(valid_matches) < 2:
        return None

    all_tids = {m.group(1).strip().upper() for m in valid_matches}
    tasks: dict[str, dict[str, Any]] = {}
    unit = "hours"

    for i, m in enumerate(valid_matches):
        tid = m.group(1).strip().upper()
        duration = float(m.group(2))
        raw_unit = m.group(3).lower()
        if "day" in raw_unit:
            unit = "days"
        elif "min" in raw_unit:
            unit = "minutes"
        else:
            unit = "hours"

        start_pos = m.start()
        end_pos = valid_matches[i + 1].start() if i + 1 < len(valid_matches) else len(text)
        clause = text[start_pos:end_pos]

        deps: list[str] = []
        m_dep = re.search(
            r"(?i)(?:starts?\s+after|depends?\s+on|after\s+both|after\s+all|after)\s+([A-Za-z0-9\s,and]+)",
            clause,
        )
        if m_dep:
            raw_tokens = re.findall(r"\b[A-Za-z0-9]+\b", m_dep.group(1))
            deps = [tok.upper() for tok in raw_tokens if tok.upper() in all_tids and tok.upper() != tid]

        starts_immediately = bool(
            re.search(r"\b(?:immediately|starts?\s+at\s+0|from\s+the\s+start)\b", clause, re.I)
        )
        if starts_immediately:
            deps = []

        tasks[tid] = {
            "id": tid,
            "duration": duration,
            "deps": deps,
            "starts_immediately": starts_immediately,
        }

    # Deterministic topological solver with cycle detection
    schedule: dict[str, dict[str, Any]] = {}
    remaining = dict(tasks)
    max_steps = len(tasks) + 1

    for _ in range(max_steps):
        progress = False
        for tid, info in list(remaining.items()):
            deps = info["deps"]
            if all(d in schedule for d in deps):
                start = max([schedule[d]["finish"] for d in deps], default=0.0)
                finish = start + info["duration"]
                schedule[tid] = {
                    "id": tid,
                    "start": start,
                    "finish": finish,
                    "duration": info["duration"],
                    "deps": deps,
                }
                del remaining[tid]
                progress = True
        if not progress or not remaining:
            break

    # If any task couldn't be scheduled, there is a cycle or unresolved dependency
    if remaining:
        return None

    # Deterministic validation
    project_finish = 0.0
    for tid, info in schedule.items():
        # Validate duration: finish - start == duration
        if round(info["finish"] - info["start"], 4) != round(info["duration"], 4):
            return None
        # Validate dependency satisfaction: dependency finish <= dependent start
        for d in info["deps"]:
            if schedule[d]["finish"] > info["start"]:
                return None
        if info["finish"] > project_finish:
            project_finish = info["finish"]

    unit_abbr = "d" if unit == "days" else "m" if unit == "minutes" else "h"
    total_str = f"{int(project_finish)}" if project_finish.is_integer() else f"{project_finish:g}"

    # Build clean Markdown schedule table and summary
    # Sort tasks by start time, then finish time, then ID
    sorted_tasks = sorted(schedule.values(), key=lambda x: (x["start"], x["finish"], x["id"]))

    lines = [
        "| Task | Start Time | Finish Time |",
        "|------|------------|-------------|",
    ]
    for item in sorted_tasks:
        s_val = f"{int(item['start'])}" if item["start"].is_integer() else f"{item['start']:g}"
        f_val = f"{int(item['finish'])}" if item["finish"].is_integer() else f"{item['finish']:g}"
        lines.append(f"| {item['id']}    | {s_val} {unit_abbr}        | {f_val} {unit_abbr}         |")

    lines.append("")
    lines.append(f"**Minimum total completion time:** {total_str} {unit}")

    formatted_answer = "\n".join(lines)
    return {
        "schedule": schedule,
        "total_finish": project_finish,
        "unit": unit,
        "answer": formatted_answer,
        "tasks": tasks,
    }

