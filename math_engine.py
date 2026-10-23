"""Safe mathematical evaluation, multi-component RAM/capacity planning, and diagnostic probability calculations for Offline GPT."""
from __future__ import annotations

import ast
import operator
import math
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

# Supported binary and unary operations for safe AST evaluation
_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_SAFE_UNARY = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_SAFE_FUNCS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
}


def safe_eval_expression(expr: str, *, strict_syntax: bool = False) -> int | float:
    """Evaluate an arithmetic expression using AST without using Python's unsafe eval()."""
    # Existing prose calculator accepts thousands separators. Structured tools
    # use strict arithmetic syntax so function-argument commas stay intact.
    cleaned = expr.strip() if strict_syntax else expr.strip().replace(",", "")
    cleaned = cleaned.replace("×", "*").replace("÷", "/").replace("−", "-").replace("^", "**")
    # Replace single 'x' between numbers with '*'
    cleaned = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", " * ", cleaned)

    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid arithmetic expression: {expr}") from exc

    return _evaluate_ast_node(tree.body)


def _evaluate_ast_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value

    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_UNARY:
        operand = _evaluate_ast_node(node.operand)
        return _SAFE_UNARY[type(node.op)](operand)

    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
        left = _evaluate_ast_node(node.left)
        right = _evaluate_ast_node(node.right)

        if isinstance(node.op, ast.Pow):
            if abs(right) > 10:
                raise ValueError("Exponent too large for safe evaluation (maximum exponent is 10)")
            if isinstance(left, (int, float)) and abs(left) > 1000 and right > 4:
                raise ValueError("Calculation result would exceed safe numeric limits")

        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ZeroDivisionError("Division by zero")

        result = _SAFE_OPS[type(node.op)](left, right)
        if isinstance(result, (int, float)) and abs(result) > 10**18:
            raise ValueError("Numeric overflow: result exceeds safe integer/float bounds")
        return result

    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        func_name = node.func.id
        if func_name in _SAFE_FUNCS:
            args = [_evaluate_ast_node(arg) for arg in node.args]
            return _SAFE_FUNCS[func_name](*args)
        raise ValueError(f"Unsupported math function: {func_name}")

    raise ValueError(f"Unsupported AST node in math expression: {type(node).__name__}")


def calculate_ram_shortfall(text: str) -> tuple[float, float, float, float, float] | None:
    """Extract and compute RAM requirements: total, used, required, free, additional_needed.

    Supports both single-used and multi-component used breakdown (e.g. 8 GB OS, 52 GB AI, 6 GB database, 10 GB other).
    """
    # 1. Multi-component check: "Total RAM = 96 GB ... 8 GB OS, 52 GB AI ... new task requires 25 GB"
    total_m = re.search(r"(\d+(?:\.\d+)?)\s*GB\s*(?:total|RAM total|total RAM)\b|\b(?:total(?:\s+RAM)?|has)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*GB\b", text, re.I)
    req_m = re.search(r"\b(?:new\s+(?:process|vm|app|application|task)|requires?|needs?|new requirement)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*GB\b", text, re.I)

    if total_m and req_m:
        total = float(total_m.group(1) or total_m.group(2))
        required = float(req_m.group(1))

        # Check for multiple used allocations: e.g. "8 GB OS, 52 GB AI, 6 GB database, 10 GB other"
        # Find all "X GB" instances between total and req
        used_matches = list(re.finditer(r"(\d+(?:\.\d+)?)\s*GB(?:\s+[a-zA-Z]+)?", text, re.I))
        components = []
        for m in used_matches:
            val = float(m.group(1))
            # Don't count total or required as a used component
            if val == total or val == required:
                continue
            components.append(val)

        if len(components) >= 2:
            used = sum(components)
            free = max(0.0, total - used)
            additional = max(0.0, required - free)
            return total, used, required, free, additional

    # 2. Standard pattern: total ... used ... requires/needs
    m1 = re.search(
        r"(?P<total>\d+(?:\.\d+)?)\s*GB\b.{0,80}?(?P<used>\d+(?:\.\d+)?)\s*GB\b.{0,80}?\bused\b.{0,120}?\b(?:requires?|needs?|new\s+\w+)\s*[:=]?\s*(?P<required>\d+(?:\.\d+)?)\s*GB",
        text,
        re.I | re.S,
    )
    m2 = re.search(
        r"(?P<total>\d+(?:\.\d+)?)\s*GB\b.{0,80}?\bused\b.{0,80}?(?P<used>\d+(?:\.\d+)?)\s*GB\b.{0,120}?\b(?:requires?|needs?|new\s+\w+)\s*[:=]?\s*(?P<required>\d+(?:\.\d+)?)\s*GB",
        text,
        re.I | re.S,
    )
    m3 = re.search(
        r"\b(?:has|total)\s+(?P<total>\d+(?:\.\d+)?)\s*GB\b.{0,80}?(?P<used>\d+(?:\.\d+)?)\s*GB\s+is\s+used\b.{0,120}?\b(?:requires?|needs?|new\s+\w+)\s*[:=]?\s*(?P<required>\d+(?:\.\d+)?)\s*GB",
        text,
        re.I | re.S,
    )

    match = m1 or m2 or m3
    if not match:
        return None

    total = float(match.group("total"))
    used = float(match.group("used"))
    required = float(match.group("required"))
    free = max(0.0, total - used)
    additional = max(0.0, required - free)
    return total, used, required, free, additional


def calculate_diagnostic_probability(text: str) -> dict[str, Any] | None:
    """Calculate epidemiological / diagnostic test probabilities:

    prevalence, sensitivity, specificity -> true positives, false positives, PPV, NPV.
    """
    low = text.casefold()
    if not any(k in low for k in ("sensitivity", "specificity", "prevalence", "disease", "positive predictive value", "ppv", "tests positive", "test positive", "actually have")):
        return None

    # Population: e.g. "Population = 10,000", "affects 10% of 1,000 people", "sample of 5,000 patients"
    pop_m = (
        re.search(r"\bpopulation\s*(?:of|=|is|:|\bhas\b)?\s*(\d[\d,]*)\b", low)
        or re.search(r"\b(?:of|in|sample\s+of|cohort\s+of|group\s+of)\s*(\d[\d,]*)\s*(?:people|individuals|patients|persons|cases|citizens|subjects)\b", low)
        or re.search(r"\b(\d[\d,]*)\s*(?:people|individuals|patients|persons|cases)\b", low)
    )
    population = float(pop_m.group(1).replace(",", "")) if pop_m else 1000.0

    # Prevalence: check ratio "1 in 1000" or percentage "Prevalence = 2%", "affects 10% of 1,000 people", "10% have the disease"
    frac_m = re.search(r"\b(?:prevalence\s*(?:rate\s*)?(?:of|=|is|:)?|affects?|cases?\s+in)?\s*(\d+)\s*(?:in|out\s+of)\s*(\d[\d,]*)\b", low)
    if frac_m:
        prevalence = float(frac_m.group(1)) / float(frac_m.group(2).replace(",", ""))
    else:
        # Standard prevalence patterns
        prev_m = (
            re.search(r"(?:prevalence\s*(?:rate\s*)?(?:of|=|is|:)?\s*(\d+(?:\.\d+)?)\s*(%|percent)?|(\d+(?:\.\d+)?)\s*(%|percent)?\s*(?:disease\s+)?prevalence)", low)
            or re.search(r"\b(?:affects?|infects?|has|have|contract(?:ed)?|carry|diagnosed with|positive for)\s*(?:the\s+)?(?:disease|condition|illness|infection)?\s*(?:in\s+)?(\d+(?:\.\d+)?)\s*(%|percent)", low)
            or re.search(r"(\d+(?:\.\d+)?)\s*(%|percent)\s+(?:of\s+(?:\d[\d,]*\s+)?(?:people|population|patients|individuals)|have\b|has\b|are\s+affected|carry)", low)
            or re.search(r"(?:disease|condition|infection)\s+(?:affects?|infects?|occurs?\s+in)\s+(\d+(?:\.\d+)?)\s*(%|percent)", low)
        )
        if not prev_m:
            return None
        val_str = next(g for g in prev_m.groups() if g and g.replace(".", "", 1).isdigit())
        val = float(val_str)
        prevalence = val / 100.0 if ("%" in prev_m.group(0) or "percent" in prev_m.group(0) or val > 1.0) else val

    # Sensitivity: e.g. "Sensitivity = 95%", "90% sensitivity"
    sens_m = re.search(r"(?:sensitivity\s*(?:of|=|is|:)?\s*(\d+(?:\.\d+)?)\s*(%|percent)?|(\d+(?:\.\d+)?)\s*(%|percent)?\s*(?:test\s+)?sensitivity)", low)
    if not sens_m:
        return None
    s_val = float(sens_m.group(1) or sens_m.group(3))
    sensitivity = s_val / 100.0 if (sens_m.group(2) or sens_m.group(4) or s_val > 1.0) else s_val

    # Specificity: e.g. "Specificity = 90%", "95% specificity"
    spec_m = re.search(r"(?:specificity\s*(?:of|=|is|:)?\s*(\d+(?:\.\d+)?)\s*(%|percent)?|(\d+(?:\.\d+)?)\s*(%|percent)?\s*(?:test\s+)?specificity)", low)
    if not spec_m:
        return None
    sp_val = float(spec_m.group(1) or spec_m.group(3))
    specificity = sp_val / 100.0 if (spec_m.group(2) or spec_m.group(4) or sp_val > 1.0) else sp_val

    # Exact Bayesian computations without premature rounding
    diseased = population * prevalence
    healthy = population - diseased
    true_positive = diseased * sensitivity
    false_negative = diseased - true_positive
    true_negative = healthy * specificity
    false_positive = healthy * (1.0 - specificity)

    total_positive_tests = true_positive + false_positive
    ppv = true_positive / total_positive_tests if total_positive_tests else 0.0
    ppv_pct = ppv * 100.0

    total_negative_tests = true_negative + false_negative
    npv = true_negative / total_negative_tests if total_negative_tests else 0.0
    npv_pct = npv * 100.0

    def _fmt(val: float) -> str:
        if abs(val - round(val)) < 1e-9:
            return f"{int(round(val)):,}"
        return f"{val:,.4f}".rstrip("0").rstrip(".")

    def _clean_num(val: float) -> int | float:
        if abs(val - round(val)) < 1e-9:
            return int(round(val))
        return round(val, 4)

    summary = (
        f"Diagnostic probability calculation:\n"
        f"- Population: {_fmt(population)}\n"
        f"- Diseased: {_fmt(diseased)} ({prevalence * 100:g}% of {_fmt(population)})\n"
        f"- Healthy: {_fmt(healthy)} ({_fmt(population)} - {_fmt(diseased)})\n"
        f"- Test characteristics: {sensitivity * 100:g}% sensitivity, {specificity * 100:g}% specificity\n"
        f"- True Positives (TP): {_fmt(diseased)} * {sensitivity * 100:g}% = {_fmt(true_positive)}\n"
        f"- False Positives (FP): {_fmt(healthy)} * (1 - {specificity * 100:g}%) = {_fmt(false_positive)}\n"
        f"- Total Positive Tests: {_fmt(true_positive)} + {_fmt(false_positive)} = {_fmt(total_positive_tests)}\n"
        f"- Probability of disease given positive test:\n"
        f"  P(Disease | Positive) = TP / (TP + FP) = {_fmt(true_positive)} / ({_fmt(true_positive)} + {_fmt(false_positive)}) = {_fmt(true_positive)} / {_fmt(total_positive_tests)} ~= {ppv:.4f} ({ppv_pct:.2f}%)"
    )

    return {
        "type": "diagnostic_probability",
        "population": _clean_num(population),
        "prevalence": prevalence,
        "sensitivity": sensitivity,
        "specificity": specificity,
        "diseased": _clean_num(diseased),
        "healthy": _clean_num(healthy),
        "true_positives": _clean_num(true_positive),
        "false_positives": _clean_num(false_positive),
        "false_negatives": _clean_num(false_negative),
        "true_negatives": _clean_num(true_negative),
        "total_positives": _clean_num(total_positive_tests),
        "ppv": ppv,
        "ppv_percentage": round(ppv_pct, 2),
        "summary": summary,
        "expected_numbers": [
            _clean_num(true_positive),
            _clean_num(false_positive),
            _clean_num(total_positive_tests),
            round(ppv_pct, 1),
            round(ppv_pct, 2),
            round(ppv, 4),
        ],
    }


def solve_number_sequence(text: str) -> dict[str, Any] | None:
    """Analyze and determine the next number in mathematical sequences (AP, GP, quadratic, cubic, powers, Fibonacci)."""
    low = text.casefold()
    if not any(k in low for k in ("sequence", "series", "pattern", "next number", "next term", "what comes next", "continue the")):
        return None

    # Find candidate sequence of numbers: prefer comma-separated sequences
    comma_matches = list(re.finditer(r"(-?\d+(?:\.\d+)?(?:\s*,\s*-?\d+(?:\.\d+)?){2,})", text))
    cand_str = ""
    if comma_matches:
        cand_str = max(comma_matches, key=lambda m: len(m.group(0))).group(0)
    else:
        kw_m = re.search(r"(?:sequence|series|pattern|terms?)(?:\s+is|\s+of|:)?\s*([0-9\s,\.\-\–\—\?]+)", text, re.I)
        if kw_m:
            cand_str = kw_m.group(1)

    if not cand_str:
        return None

    raw_nums = re.findall(r"-?\d+(?:\.\d+)?", cand_str)
    if len(raw_nums) < 3:
        return None

    nums = [float(x) for x in raw_nums]
    n = len(nums)

    # 1. Powers of integers (e.g., squares: 1, 4, 9, 16 or cubes: 1, 8, 27, 64)
    if all(x > 0 for x in nums):
        sq_roots = [round(x**0.5) for x in nums]
        if all(r * r == x for r, x in zip(sq_roots, nums)):
            d = [sq_roots[i + 1] - sq_roots[i] for i in range(len(sq_roots) - 1)]
            if len(set(d)) == 1:
                next_root = sq_roots[-1] + d[0]
                next_val = float(next_root**2)
                rule = f"Sequence of squares ({next_root}^2)"
                return _format_seq_result(nums, next_val, rule)
        cb_roots = [round(x ** (1.0 / 3.0)) for x in nums]
        if all(r**3 == x for r, x in zip(cb_roots, nums)):
            d = [cb_roots[i + 1] - cb_roots[i] for i in range(len(cb_roots) - 1)]
            if len(set(d)) == 1:
                next_root = cb_roots[-1] + d[0]
                next_val = float(next_root**3)
                rule = f"Sequence of cubes ({next_root}^3)"
                return _format_seq_result(nums, next_val, rule)

    # 2. Method of differences (AP, quadratic, cubic)
    d1 = [nums[i + 1] - nums[i] for i in range(n - 1)]
    if all(abs(d - d1[0]) < 1e-6 for d in d1):
        diff = d1[0]
        next_val = nums[-1] + diff
        rule = f"Arithmetic progression with common difference of {diff:g}"
        return _format_seq_result(nums, next_val, rule)

    if len(d1) >= 2:
        d2 = [d1[i + 1] - d1[i] for i in range(len(d1) - 1)]
        if len(d2) >= 2 and all(abs(d - d2[0]) < 1e-6 for d in d2):
            diff2 = d2[0]
            next_d1 = d1[-1] + diff2
            next_val = nums[-1] + next_d1
            rule = f"Quadratic sequence with constant second difference of {diff2:g}"
            return _format_seq_result(nums, next_val, rule)

        if len(d2) >= 3:
            d3 = [d2[i + 1] - d2[i] for i in range(len(d2) - 1)]
            if len(d3) >= 2 and all(abs(d - d3[0]) < 1e-6 for d in d3):
                diff3 = d3[0]
                next_d2 = d2[-1] + diff3
                next_d1 = d1[-1] + next_d2
                next_val = nums[-1] + next_d1
                rule = f"Cubic sequence with constant third difference of {diff3:g}"
                return _format_seq_result(nums, next_val, rule)

    # 3. Geometric progression
    if all(abs(x) > 1e-9 for x in nums):
        ratios = [nums[i + 1] / nums[i] for i in range(n - 1)]
        if all(abs(r - ratios[0]) < 1e-6 for r in ratios):
            ratio = ratios[0]
            next_val = nums[-1] * ratio
            rule = f"Geometric progression with common ratio of {ratio:g}"
            return _format_seq_result(nums, next_val, rule)

    # 4. Fibonacci-like additive rule
    if n >= 3:
        if all(abs(nums[i] - (nums[i - 1] + nums[i - 2])) < 1e-6 for i in range(2, n)):
            next_val = nums[-1] + nums[-2]
            rule = "Fibonacci additive rule (each term is sum of preceding two)"
            return _format_seq_result(nums, next_val, rule)

    # 5. Affine recurrence x_(n+1) = a * x_n + b
    if n >= 3 and abs(nums[1] - nums[0]) > 1e-9:
        a = (nums[2] - nums[1]) / (nums[1] - nums[0])
        b = nums[1] - a * nums[0]
        if all(abs(nums[i + 1] - (a * nums[i] + b)) < 1e-6 for i in range(n - 1)):
            next_val = a * nums[-1] + b
            rule = f"Linear recurrence: x_(n+1) = {a:g} * x_n + {b:g}"
            return _format_seq_result(nums, next_val, rule)

    return None


def _format_seq_result(nums: list[float], next_val: float, rule: str) -> dict[str, Any]:
    int_next = int(round(next_val)) if abs(next_val - round(next_val)) < 1e-6 else round(next_val, 6)
    formatted_nums = [int(round(x)) if abs(x - round(x)) < 1e-6 else round(x, 6) for x in nums]
    nums_str = ", ".join(map(str, formatted_nums))
    return {
        "type": "number_sequence",
        "sequence": formatted_nums,
        "next_number": int_next,
        "result": int_next,
        "expected_number": int_next,
        "expected_numbers": [int_next],
        "rule": rule,
        "summary": f"Number sequence [{nums_str}]: {rule}. Next number is {int_next}.",
    }


def extract_and_evaluate_math(text: str) -> dict[str, Any] | None:
    """Extract arithmetic, capacity, or probability calculations from text and safely compute results."""
    # 1. Diagnostic test / probability calculations
    prob = calculate_diagnostic_probability(text)
    if prob:
        return prob

    # 2. Check for RAM shortfall / capacity calculations
    ram = calculate_ram_shortfall(text)
    if ram:
        total, used, required, free, additional = ram
        add_str = f"{int(additional)} GB" if additional.is_integer() else f"{additional:g} GB"
        free_str = f"{int(free)} GB" if free.is_integer() else f"{free:g} GB"
        return {
            "type": "ram_calculation",
            "total_gb": total,
            "used_gb": used,
            "free_gb": free,
            "required_gb": required,
            "additional_needed_gb": additional,
            "expected_number": int(additional) if additional.is_integer() else additional,
            "expected_numbers": [int(free) if free.is_integer() else free, int(additional) if additional.is_integer() else additional],
            "summary": (
                f"RAM calculation: total={total:g} GB, used={used:g} GB, free={free_str}, "
                f"new requirement={required:g} GB -> additional RAM needed = {add_str}."
            ),
        }

    # 3. Check for number sequences
    seq = solve_number_sequence(text)
    if seq:
        return seq

    # Apply sequential percentage changes to the running amount, rather than
    # adding percentage points. Decimal keeps money and rounded percentages
    # stable across Windows/Python versions.
    sequence = re.search(
        r"(?<![\w.])(?P<base>\d[\d,]*(?:\.\d+)?)\s*(?P<changes>(?:[+\-]\s*\d[\d,]*(?:\.\d+)?\s*%?\s*){2,})",
        text,
    )
    if sequence:
        try:
            original = Decimal(sequence.group("base").replace(",", ""))
            running = original
            changes = re.findall(r"([+\-])\s*(\d[\d,]*(?:\.\d+)?)\s*(%?)", sequence.group("changes"))
            if len(changes) >= 2:
                for sign, raw_value, percent in changes:
                    value = Decimal(raw_value.replace(",", ""))
                    adjustment = running * value / Decimal(100) if percent else value
                    running = running + adjustment if sign == "+" else running - adjustment
                delta_percent = (running - original) / original * Decimal(100) if original else Decimal(0)
                rounded_percent = delta_percent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                final_number = int(running) if running == running.to_integral_value() else float(running)
                return {
                    "type": "percentage_sequence",
                    "result": final_number,
                    "change_percentage": float(rounded_percent),
                    "expected_number": final_number,
                    "expected_numbers": [final_number, abs(float(rounded_percent))],
                    "summary": f"Sequential calculation: start {original:g}, final {running:g}; change {rounded_percent:+}% from the original. Apply each percentage to the current amount.",
                }
        except (InvalidOperation, OverflowError, ValueError, ZeroDivisionError):
            pass

    # 3. Check for percentage calculation (e.g., "15% of 850", "what is 20 percent of 500")
    # Guard: do NOT match simple percentage inside multi-step probability/diagnostic word problems!
    is_multi_param = bool(
        re.search(r"\b(?:sensitivity|specificity|prevalence|diagnostic|tests?\s+positive|false\s+positive|probability|bayes)\b", text, re.I)
        or len(re.findall(r"\d+(?:\.\d+)?\s*%", text)) >= 2
    )
    if not is_multi_param:
        pct_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s+(?:of|\*)\s+(\d[\d,]*(?:\.\d+)?)", text, re.I)
        if pct_match:
            pct = float(pct_match.group(1))
            base = float(pct_match.group(2).replace(",", ""))
            res = (pct / 100.0) * base
            res_val = int(res) if res.is_integer() else round(res, 6)
            return {
                "type": "percentage",
                "expression": f"{pct}% of {base}",
                "result": res_val,
                "expected_number": res_val,
                "summary": f"Calculation: {pct}% of {base:g} = {res_val}",
            }

    # 4. Extract arithmetic expression
    expr = _extract_arithmetic_expr(text)
    if expr:
        try:
            val = safe_eval_expression(expr)
            val_out = int(val) if isinstance(val, float) and val.is_integer() else (round(val, 6) if isinstance(val, float) else val)
            return {
                "type": "arithmetic",
                "expression": expr,
                "result": val_out,
                "expected_number": val_out,
                "summary": f"Calculation: {expr} = {val_out}",
            }
        except Exception:
            pass

    return None


def _extract_arithmetic_expr(text: str) -> str:
    cleaned = text.strip().replace(",", "")
    cleaned = re.sub(r"\b(?:what(?:'s| is)|calculate|compute|solve|equals?|answer)\b", "", cleaned, flags=re.I)
    cleaned = cleaned.replace("×", "*").replace("÷", "/").replace("−", "-").replace("^", "**")
    cleaned = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", " * ", cleaned)

    # Extract the whole contiguous expression, including grouped operands.
    # Never salvage an inner multiplication from an incomplete parenthesized
    # expression and report that fragment as the complete calculation.
    match = re.search(r"(?<![\w.])([+\-(\d][\d\s.+*/%()\-]*(?:\d|\)))", cleaned)
    if not match:
        return ""
    expr = match.group(1).strip()
    if len(expr)>100 or not re.search(r'[+*/%\-]',expr):return ''
    try:
        tree=ast.parse(expr,mode='eval')
        if not any(isinstance(n,ast.BinOp) for n in ast.walk(tree)):return ''
    except (SyntaxError,ValueError):return ''
    return expr


def number_in_text(text: str, expected_number: int | float | str) -> bool:
    """Check if the expected number is present in the response in any standard numeric format."""
    candidates = set()
    try:
        num = float(expected_number)
        if num.is_integer():
            i = int(num)
            candidates.add(str(i))
            candidates.add(f"{i:,}")
            candidates.add(f"{i}.0")
        else:
            candidates.add(f"{num:g}")
            candidates.add(f"{num:.1f}")
            candidates.add(f"{num:.2f}")
            candidates.add(f"{num:,.1f}")
            candidates.add(f"{num:,.2f}")
            candidates.add(f"{num:,.6g}")
    except (ValueError, TypeError):
        candidates.add(str(expected_number))

    for cand in candidates:
        pattern = rf"(?<![\d.]){re.escape(cand)}(?!\d)(?!\.\d)"
        if re.search(pattern, text):
            return True
    return False
