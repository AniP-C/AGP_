"""Independent numeric/equation validation (deterministic, sympy).

Verifies the ARITHMETIC that the source *prints* — e.g. `344 × 0.1 = 344.4`
computes to 34.4, so the printed value is flagged as inconsistent. This validates
the extraction/source; it NEVER generates a replacement answer. The source block
is preserved and the inconsistency is recorded as a finding.
"""
from __future__ import annotations

import re

from ..schemas import BlockType, CanonicalDocument, ValidationFinding

_NUM_TOKEN = re.compile(r"\d")
_SAFE = re.compile(r"^[-\d.+*/()eE ]+$")


def _num_eval(expr: str):
    e = expr
    e = re.sub(r"\\begin\{[^}]*\}|\\end\{[^}]*\}", "", e)
    e = re.sub(r"\\text\{[^}]*\}", "", e)
    e = e.replace("×", "*").replace("·", "*").replace("÷", "/")
    e = e.replace("\\times", "*").replace("\\div", "/").replace("\\cdot", "*")
    e = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"((\1)/(\2))", e)
    e = re.sub(r"10\s*\^\s*\{?\s*(-?\d+)\s*\}?", r"(10**(\1))", e)
    e = re.sub(r"\\[a-zA-Z]+", "", e)                      # drop remaining \commands
    e = e.replace("\\", " ").replace("&", "").replace("{", "").replace("}", "")
    e = re.sub(r"(\d)\s*[xX]\s*(?=[\d(])", r"\1*", e)      # "3 x 10" → 3*10
    e = re.sub(r"[a-df-zA-DF-ZλνμΩ°%,]", "", e)            # drop unit letters/vars (keep e/E)
    e = e.replace(" ", "")
    if not e or not _NUM_TOKEN.search(e) or not _SAFE.match(e):
        return None
    try:
        import sympy
        return float(sympy.sympify(e))
    except Exception:
        return None


_HAS_OP = re.compile(r"[*/+]|\*\*|\d\s*[-–]\s*\d|\\frac|\\times|\\div|[×÷·]")


def _check_equalities(text: str) -> list[dict]:
    """Verify a chain of `= step` values (multi-line aligned solutions included).

    Consecutive numeric steps should be equal; a mismatch is flagged ONLY when a
    step contains an arithmetic operator (a real computation), so a legitimate
    unit conversion like `4350 m = 4.35 km` (bare value = value) is not a false
    positive, while `344 × 0.1 = 344.4` (operator present) is caught."""
    checks = []
    norm = re.sub(r"\\\\|&|\n", " ", text or "")
    parts = norm.split("=")
    steps = []  # (value, has_op, raw)
    for p in parts:
        v = _num_eval(p)
        if v is not None:
            steps.append((v, bool(_HAS_OP.search(p)), p.strip()))
    for i in range(len(steps) - 1):
        (va, oa, ra), (vb, ob, rb) = steps[i], steps[i + 1]
        if not (oa or ob):                        # bare value=value → skip (conversion)
            continue
        ok = abs(va - vb) <= max(0.06, 0.02 * abs(va))
        checks.append({"expr": f"{ra} = {rb}"[:80], "computed": round(va, 4),
                       "printed": round(vb, 4), "ok": ok})
    return checks


def validate_numeric(doc: CanonicalDocument):
    """Return (findings, block_checks: {block_id: [checks]})."""
    findings: list[ValidationFinding] = []
    block_checks: dict[str, list[dict]] = {}
    targets = {BlockType.EQUATION, BlockType.SOLUTION, BlockType.ANSWER,
               BlockType.EXPLANATION}
    for b in doc.blocks:
        if b.type not in targets:
            continue
        src = " ".join(x for x in (b.latex, b.text) if x)
        checks = _check_equalities(src)
        if not checks:
            continue
        block_checks[b.id] = checks
        for c in checks:
            if not c["ok"]:
                findings.append(ValidationFinding(
                    check="numeric_arithmetic", severity="error",
                    target_type="block", target_id=b.id,
                    message=(f"printed arithmetic inconsistent: '{c['expr']}' "
                             f"computes to {c['computed']} but source prints {c['printed']} "
                             f"(source preserved; flagged, not replaced)")))
    return findings, block_checks
