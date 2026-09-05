from __future__ import annotations

import streamlit as st

from ..components import json_viewer, metric_row, status_badge
from ..data import RunBundle


def render(bundle: RunBundle):
    st.header("Validation")
    st.caption(
        "The reliability layer: confidence calibration, the accept/escalate/abstain "
        "gate, and integrity checks — what makes the answers trustworthy, not just present."
    )

    conf = bundle.confidence or {}
    report = bundle.report or {}
    validation_summary = report.get("validation", {})
    integrity = report.get("integrity", {})

    st.subheader("Confidence gate")
    tiers = conf.get("gate_actions") or validation_summary.get("gate_actions", {})
    if tiers:
        metric_row([
            ("Accepted", str(tiers.get("accept", 0)), "High confidence — no intervention needed."),
            ("Escalated", str(tiers.get("escalate", 0)), "Borderline confidence — re-checked with a stronger model."),
            ("Abstained", str(tiers.get("abstain", 0)), "Evidence stayed too weak — left unresolved rather than guessed."),
        ])

    esc = conf.get("escalation") or {}
    if esc:
        st.write(
            f"Of **{esc.get('escalated_questions', 0)}** escalated questions: "
            f"**{esc.get('recovered', 0)}** recovered (status changed after re-check), "
            f"**{esc.get('confirmed', 0)}** confirmed correct, "
            f"**{esc.get('unchanged', 0)}** unchanged."
        )

    if conf.get("calibration_note"):
        st.caption(conf["calibration_note"])

    st.divider()
    st.subheader("Numeric inconsistencies")
    numeric_flags = _collect_numeric_flags(bundle)
    if not numeric_flags:
        st.success("No numeric inconsistencies found — all arithmetic in the source verified.")
    for q_id, expr, computed, printed in numeric_flags:
        with st.container(border=True):
            st.markdown(f"**{q_id}** — numeric inconsistency")
            st.write(f"Source: `{expr}` → printed `{printed}`")
            st.write(f"Validator computed: `{computed}`")
            st.info("Action: source preserved and flagged — the mismatch was **not** silently corrected.")

    st.divider()
    st.subheader("Escalated & abstained questions")
    escalated, abstained = _collect_gate_items(bundle)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Escalated ({len(escalated)})**")
        for q in escalated:
            with st.expander(f"{q['id']} — {(q.get('question_text') or '')[:70]}"):
                v = q["validation"]
                for esc_rec in v.get("escalations", []):
                    st.write(
                        f"Attempt {esc_rec.get('attempt')}: `{esc_rec.get('kind')}` — "
                        f"{esc_rec.get('reason')} → **{esc_rec.get('decision')}** "
                        f"({esc_rec.get('previous_status')} → {esc_rec.get('new_status')})"
                    )
                st.caption("Validation detail")
                st.json(v, expanded=False)
    with c2:
        st.markdown(f"**Abstained ({len(abstained)})**")
        for q in abstained:
            st.write(f"`{q['id']}` — {(q.get('question_text') or '')[:70]}")

    st.divider()
    st.subheader("Integrity & structural findings")
    findings = _collect_findings(bundle)
    if not findings:
        st.success("No integrity findings (unresolved references, zero-yield pages, etc.) reported.")
    else:
        for q_id, f in findings:
            sev = f.get("severity", "info")
            icon = {"error": "🔴", "warn": "🟡", "info": "🔵"}.get(sev, "⚪")
            st.write(f"{icon} `{q_id}` — **{f.get('check')}**: {f.get('message')}")

    if integrity:
        json_viewer(integrity, "Integrity report (raw)")


def _collect_numeric_flags(bundle: RunBundle):
    out = []
    for q in bundle.all_leaf_questions():
        v = q.get("validation") or {}
        numeric = v.get("numeric") or {}
        if numeric.get("status") == "failed_source":
            for chk in numeric.get("checks", []):
                if not chk.get("ok"):
                    out.append((q["id"], chk.get("expr"), chk.get("computed"), chk.get("printed")))
    return out


def _collect_gate_items(bundle: RunBundle):
    escalated, abstained = [], []
    for q in bundle.all_leaf_questions():
        v = q.get("validation") or {}
        action = v.get("gate_action")
        if action == "escalate":
            escalated.append(q)
        elif action == "abstain":
            abstained.append(q)
    return escalated, abstained


def _collect_findings(bundle: RunBundle):
    out = []
    for q in bundle.all_leaf_questions():
        for f in (q.get("validation") or {}).get("findings", []):
            out.append((q["id"], f))
    return out
