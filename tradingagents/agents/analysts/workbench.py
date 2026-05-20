from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, TypedDict


AnalystDomain = Literal["market", "news", "fundamentals", "sentiment", "catalyst"]
HypothesisOrigin = Literal[
    "default_prior",
    "anomaly_generated",
    "cross_domain_signal",
    "critic_generated",
    "memory_retrieved",
]
ObservationStatus = Literal["explained", "unexplained", "contradictory", "stale", "low_quality"]
CoverageSeverity = Literal["low", "medium", "high"]


class AnalystObservation(TypedDict, total=False):
    id: str
    domain: AnalystDomain
    claim: str
    source_fact_ids: List[str]
    surprise_score: float
    why_it_matters: str
    status: ObservationStatus


class AnalystQuestion(TypedDict, total=False):
    id: str
    question: str
    triggered_by: List[str]
    decision_relevance: float
    expected_information_gain: float
    evidence_surprise: float
    estimated_tool_cost: float
    cheapest_tool: str | None
    stop_condition: str
    promoted_to_hypothesis: bool


class AnalystHypothesis(TypedDict, total=False):
    id: str
    claim: str
    origin: HypothesisOrigin
    support: List[str]
    against: List[str]
    confidence: float
    falsifier: str
    unresolved_questions: List[str]


class DiscardedHypothesis(TypedDict, total=False):
    id: str
    claim: str
    origin: HypothesisOrigin
    rejected_because: str
    evidence_against: List[str]


class CoverageGap(TypedDict, total=False):
    id: str
    gap: str
    related_observations: List[str]
    why_it_matters: str
    severity: CoverageSeverity
    suggested_next_question: str | None


class CrossDomainHandoff(TypedDict, total=False):
    id: str
    from_domain: AnalystDomain
    to_domain: AnalystDomain
    question: str
    triggered_by: List[str]
    why_needed: str


class AnalystLedger(TypedDict, total=False):
    analyst_domain: AnalystDomain
    observations: List[AnalystObservation]
    anomalies: List[str]
    question_backlog: List[AnalystQuestion]
    hypothesis_candidates: List[AnalystHypothesis]
    active_hypotheses: List[AnalystHypothesis]
    discarded_hypotheses: List[DiscardedHypothesis]
    resolved_questions: List[str]
    open_questions: List[str]
    coverage_gaps: List[CoverageGap]
    cross_domain_handoffs: List[CrossDomainHandoff]
    do_not_fetch_again: List[str]
    unexplained_but_decision_relevant: List[str]
    critic_flags: List[str]


ANALYST_DOMAINS = {"market", "news", "fundamentals", "sentiment", "catalyst"}
HYPOTHESIS_ORIGINS = {
    "default_prior",
    "anomaly_generated",
    "cross_domain_signal",
    "critic_generated",
    "memory_retrieved",
}
OBSERVATION_STATUSES = {"explained", "unexplained", "contradictory", "stale", "low_quality"}
ANALYST_LEDGER_KEYS = [
    "analyst_domain",
    "observations",
    "anomalies",
    "question_backlog",
    "hypothesis_candidates",
    "active_hypotheses",
    "discarded_hypotheses",
    "resolved_questions",
    "open_questions",
    "coverage_gaps",
    "cross_domain_handoffs",
    "do_not_fetch_again",
    "unexplained_but_decision_relevant",
    "critic_flags",
]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _as_str_list(value: Any) -> list[str]:
    out: list[str] = []
    for item in _as_list(value):
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _clamp_float(value: Any, default: float = 0.0, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except Exception:
        number = default
    if number < low:
        return low
    if number > high:
        return high
    return number


def _domain(domain: Any) -> AnalystDomain:
    text = str(domain or "").strip().lower()
    if text == "social":
        text = "sentiment"
    if text not in ANALYST_DOMAINS:
        text = "market"
    return text  # type: ignore[return-value]


def _origin(value: Any) -> HypothesisOrigin:
    text = str(value or "").strip().lower()
    if text not in HYPOTHESIS_ORIGINS:
        text = "default_prior"
    return text  # type: ignore[return-value]


def _status(value: Any) -> ObservationStatus:
    text = str(value or "").strip().lower()
    if text not in OBSERVATION_STATUSES:
        text = "explained"
    return text  # type: ignore[return-value]


def _normalize_observation(domain: AnalystDomain, raw: Any, idx: int) -> AnalystObservation:
    item = raw if isinstance(raw, dict) else {"claim": raw}
    return {
        "id": str(item.get("id") or f"obs_{domain}_{idx:03d}"),
        "domain": _domain(item.get("domain") or domain),
        "claim": str(item.get("claim") or "").strip(),
        "source_fact_ids": _as_str_list(item.get("source_fact_ids")),
        "surprise_score": _clamp_float(item.get("surprise_score"), 0.0),
        "why_it_matters": str(item.get("why_it_matters") or "").strip(),
        "status": _status(item.get("status")),
    }


def _normalize_question(domain: AnalystDomain, raw: Any, idx: int) -> AnalystQuestion:
    item = raw if isinstance(raw, dict) else {"question": raw}
    cost = _clamp_float(item.get("estimated_tool_cost"), 1.0, low=0.25, high=100.0)
    cheapest_tool = item.get("cheapest_tool")
    return {
        "id": str(item.get("id") or f"q_{domain}_{idx:03d}"),
        "question": str(item.get("question") or "").strip(),
        "triggered_by": _as_str_list(item.get("triggered_by")),
        "decision_relevance": _clamp_float(item.get("decision_relevance"), 0.0),
        "expected_information_gain": _clamp_float(item.get("expected_information_gain"), 0.0),
        "evidence_surprise": _clamp_float(item.get("evidence_surprise"), 0.0),
        "estimated_tool_cost": cost,
        "cheapest_tool": str(cheapest_tool).strip() if cheapest_tool else None,
        "stop_condition": str(item.get("stop_condition") or "").strip(),
        "promoted_to_hypothesis": bool(item.get("promoted_to_hypothesis", False)),
    }


def _normalize_hypothesis(domain: AnalystDomain, raw: Any, idx: int) -> AnalystHypothesis:
    item = raw if isinstance(raw, dict) else {"claim": raw}
    return {
        "id": str(item.get("id") or f"h_{domain}_{idx:03d}"),
        "claim": str(item.get("claim") or "").strip(),
        "origin": _origin(item.get("origin")),
        "support": _as_str_list(item.get("support")),
        "against": _as_str_list(item.get("against")),
        "confidence": _clamp_float(item.get("confidence"), 0.0),
        "falsifier": str(item.get("falsifier") or "").strip(),
        "unresolved_questions": _as_str_list(item.get("unresolved_questions")),
    }


def _normalize_discarded(domain: AnalystDomain, raw: Any, idx: int) -> DiscardedHypothesis:
    item = raw if isinstance(raw, dict) else {"claim": raw}
    return {
        "id": str(item.get("id") or f"discarded_{domain}_{idx:03d}"),
        "claim": str(item.get("claim") or "").strip(),
        "origin": _origin(item.get("origin")),
        "rejected_because": str(item.get("rejected_because") or "").strip(),
        "evidence_against": _as_str_list(item.get("evidence_against")),
    }


def _normalize_gap(raw: Any, idx: int) -> CoverageGap:
    item = raw if isinstance(raw, dict) else {"gap": raw}
    severity = str(item.get("severity") or "medium").strip().lower()
    if severity not in {"low", "medium", "high"}:
        severity = "medium"
    return {
        "id": str(item.get("id") or f"gap_{idx:03d}"),
        "gap": str(item.get("gap") or "").strip(),
        "related_observations": _as_str_list(item.get("related_observations")),
        "why_it_matters": str(item.get("why_it_matters") or "").strip(),
        "severity": severity,  # type: ignore[typeddict-item]
        "suggested_next_question": (
            str(item.get("suggested_next_question")).strip()
            if item.get("suggested_next_question")
            else None
        ),
    }


def _normalize_handoff(domain: AnalystDomain, raw: Any, idx: int) -> CrossDomainHandoff:
    item = raw if isinstance(raw, dict) else {"question": raw}
    return {
        "id": str(item.get("id") or f"handoff_{domain}_{idx:03d}"),
        "from_domain": _domain(item.get("from_domain") or domain),
        "to_domain": _domain(item.get("to_domain") or domain),
        "question": str(item.get("question") or "").strip(),
        "triggered_by": _as_str_list(item.get("triggered_by")),
        "why_needed": str(item.get("why_needed") or "").strip(),
    }


def normalize_ledger(domain: str, raw: Any | None = None) -> AnalystLedger:
    raw_ledger = raw if isinstance(raw, dict) else {}
    analyst_domain = _domain(raw_ledger.get("analyst_domain") or domain)
    observations = [
        _normalize_observation(analyst_domain, item, idx)
        for idx, item in enumerate(_as_list(raw_ledger.get("observations")), 1)
    ]
    question_backlog = [
        _normalize_question(analyst_domain, item, idx)
        for idx, item in enumerate(_as_list(raw_ledger.get("question_backlog")), 1)
    ]
    hypothesis_candidates = [
        _normalize_hypothesis(analyst_domain, item, idx)
        for idx, item in enumerate(_as_list(raw_ledger.get("hypothesis_candidates")), 1)
    ]
    active_hypotheses = [
        _normalize_hypothesis(analyst_domain, item, idx)
        for idx, item in enumerate(_as_list(raw_ledger.get("active_hypotheses")), 1)
    ]

    anomalies = _as_str_list(raw_ledger.get("anomalies"))
    if not anomalies:
        anomalies = [
            obs["id"]
            for obs in observations
            if obs["surprise_score"] >= 0.65 and obs["status"] != "explained"
        ]

    open_questions = _as_str_list(raw_ledger.get("open_questions"))
    if not open_questions:
        resolved = set(_as_str_list(raw_ledger.get("resolved_questions")))
        open_questions = [
            q["id"]
            for q in question_backlog
            if q.get("id") and q["id"] not in resolved
        ]

    return {
        "analyst_domain": analyst_domain,
        "observations": observations,
        "anomalies": anomalies,
        "question_backlog": question_backlog,
        "hypothesis_candidates": hypothesis_candidates,
        "active_hypotheses": active_hypotheses,
        "discarded_hypotheses": [
            _normalize_discarded(analyst_domain, item, idx)
            for idx, item in enumerate(_as_list(raw_ledger.get("discarded_hypotheses")), 1)
        ],
        "resolved_questions": _as_str_list(raw_ledger.get("resolved_questions")),
        "open_questions": open_questions,
        "coverage_gaps": [
            _normalize_gap(item, idx)
            for idx, item in enumerate(_as_list(raw_ledger.get("coverage_gaps")), 1)
        ],
        "cross_domain_handoffs": [
            _normalize_handoff(analyst_domain, item, idx)
            for idx, item in enumerate(_as_list(raw_ledger.get("cross_domain_handoffs")), 1)
        ],
        "do_not_fetch_again": _as_str_list(raw_ledger.get("do_not_fetch_again")),
        "unexplained_but_decision_relevant": _as_str_list(
            raw_ledger.get("unexplained_but_decision_relevant")
        ),
        "critic_flags": _as_str_list(raw_ledger.get("critic_flags")),
    }


def strip_executable_proposals(text: Any) -> str:
    out = str(text or "")
    out = re.sub(
        r"(?is)FINAL TRANSACTION PROPOSAL:?\s*(BUY|SELL|HOLD)?",
        "",
        out,
    )
    out = re.sub(r"(?is)\n?---\s*\**FINAL TRANSACTION PROPOSAL:.*", "", out)
    out = re.sub(r"(?is)\n#+\s*Final Transaction Proposal\b.*", "", out)
    return out.strip()


def extract_ledger_and_memo(domain: str, content: Any) -> tuple[AnalystLedger, str]:
    text = str(content or "")
    pattern = r"BEGIN_ANALYST_LEDGER_JSON\s*(\{.*?\})\s*END_ANALYST_LEDGER_JSON"
    matches = list(re.finditer(pattern, text, flags=re.DOTALL | re.IGNORECASE))
    raw_ledger: dict[str, Any] = {}
    if matches:
        try:
            raw_ledger = json.loads(matches[-1].group(1))
        except Exception:
            raw_ledger = {}
        text = re.sub(pattern, "", text, flags=re.DOTALL | re.IGNORECASE)
    return normalize_ledger(domain, raw_ledger), strip_executable_proposals(text)


def _join(items: list[str], empty: str = "-") -> str:
    clean = [str(item).strip() for item in items if str(item).strip()]
    return "<br>".join(clean) if clean else empty


def build_ledger_report(domain: str, ledger: AnalystLedger, memo: Any = "") -> str:
    normalized = normalize_ledger(domain, ledger)
    inference = strip_executable_proposals(memo)
    if "## Domain Inference" in inference:
        inference = re.sub(r"(?im)^##\s*Domain Inference\s*", "", inference).strip()
    if not inference:
        inference = f"{str(domain).title()} inference is captured in the structured ledger below."

    lines: list[str] = ["## Domain Inference", inference, "", "## Active Hypotheses"]
    lines.append("| Hypothesis | Origin | Support | Against | Confidence | Falsifier |")
    lines.append("|---|---|---|---:|---:|---|")
    if normalized["active_hypotheses"]:
        for h in normalized["active_hypotheses"]:
            lines.append(
                "| {claim} | {origin} | {support} | {against} | {confidence:.2f} | {falsifier} |".format(
                    claim=h.get("claim", "-") or "-",
                    origin=h.get("origin", "-") or "-",
                    support=_join(h.get("support", [])),
                    against=_join(h.get("against", [])),
                    confidence=float(h.get("confidence", 0.0) or 0.0),
                    falsifier=h.get("falsifier", "-") or "-",
                )
            )
    else:
        lines.append("| - | - | - | - | 0.00 | - |")

    lines.extend(["", "## Key Observations"])
    lines.append("| Observation | Surprise | Status | Why it matters |")
    lines.append("|---|---:|---|---|")
    for obs in normalized["observations"]:
        lines.append(
            "| {claim} | {surprise:.2f} | {status} | {why} |".format(
                claim=obs.get("claim", "-") or "-",
                surprise=float(obs.get("surprise_score", 0.0) or 0.0),
                status=obs.get("status", "-") or "-",
                why=obs.get("why_it_matters", "-") or "-",
            )
        )
    if not normalized["observations"]:
        lines.append("| - | 0.00 | - | - |")

    resolved = set(normalized.get("resolved_questions", []))
    open_questions = set(normalized.get("open_questions", []))
    lines.extend(["", "## Questions Investigated"])
    lines.append("| Question | Trigger | Result | Status |")
    lines.append("|---|---|---|---|")
    for q in normalized["question_backlog"]:
        qid = q.get("id", "")
        status = "resolved" if qid in resolved else "open" if qid in open_questions else "backlog"
        result = q.get("stop_condition") or "-"
        lines.append(
            f"| {q.get('question', '-') or '-'} | {_join(q.get('triggered_by', []))} | {result} | {status} |"
        )
    if not normalized["question_backlog"]:
        lines.append("| - | - | - | - |")

    lines.extend(["", "## Discarded Explanations"])
    lines.append("| Explanation | Why rejected |")
    lines.append("|---|---|")
    for h in normalized["discarded_hypotheses"]:
        lines.append(f"| {h.get('claim', '-') or '-'} | {h.get('rejected_because', '-') or '-'} |")
    if not normalized["discarded_hypotheses"]:
        lines.append("| - | - |")

    unexplained = list(normalized["unexplained_but_decision_relevant"])
    unexplained.extend(gap.get("gap", "") for gap in normalized["coverage_gaps"] if gap.get("gap"))
    lines.extend(["", "## Unexplained But Decision-Relevant"])
    if unexplained:
        lines.extend(f"- {item}" for item in unexplained if item)
    else:
        lines.append("- None identified.")

    lines.extend(["", "## Watch Items / Falsifiers"])
    falsifiers = [h.get("falsifier", "") for h in normalized["active_hypotheses"] if h.get("falsifier")]
    if falsifiers:
        lines.extend(f"- {item}" for item in falsifiers)
    else:
        lines.append("- No falsifier provided.")

    return strip_executable_proposals("\n".join(lines))


def build_ledger_evidence_summary(label: str, ledger: AnalystLedger, max_chars: int | None = None) -> str:
    normalized = normalize_ledger(label, ledger)
    metrics = build_workbench_metrics(normalized)
    lines = [f"## {str(label).title()} Evidence"]
    lines.append(
        "Workbench: "
        f"{metrics['observation_count']} observations, "
        f"{metrics['anomaly_count']} anomalies, "
        f"{metrics['question_count']} questions, "
        f"{metrics['unexplained_high_surprise_count']} unexplained high-surprise observations."
    )
    lines.append(f"Origin mix: {metrics['hypothesis_origin_counts']}")
    if normalized["active_hypotheses"]:
        lines.append("Active hypotheses:")
        for h in normalized["active_hypotheses"][:4]:
            lines.append(
                f"- [{h.get('origin')}] {h.get('claim')} "
                f"(confidence {float(h.get('confidence', 0.0) or 0.0):.2f}; "
                f"falsifier: {h.get('falsifier') or 'missing'})"
            )
    if normalized["observations"]:
        lines.append("Key observations:")
        for obs in normalized["observations"][:8]:
            lines.append(
                f"- {obs.get('claim')} "
                f"(surprise {float(obs.get('surprise_score', 0.0) or 0.0):.2f}, "
                f"status {obs.get('status')})"
            )
    if normalized["coverage_gaps"]:
        lines.append("Coverage gaps:")
        for gap in normalized["coverage_gaps"][:5]:
            lines.append(f"- [{gap.get('severity')}] {gap.get('gap')}")

    text = "\n".join(lines)
    if max_chars and len(text) > max_chars:
        return text[:max_chars]
    return text


def build_workbench_metrics(ledger: AnalystLedger) -> dict[str, Any]:
    normalized = normalize_ledger(str(ledger.get("analyst_domain") or "market"), ledger)
    origins: dict[str, int] = {}
    for h in normalized["active_hypotheses"]:
        origin = str(h.get("origin") or "default_prior")
        origins[origin] = origins.get(origin, 0) + 1
    total_hypotheses = max(1, len(normalized["active_hypotheses"]))
    unexplained_high = [
        obs
        for obs in normalized["observations"]
        if float(obs.get("surprise_score", 0.0) or 0.0) >= 0.65
        and obs.get("status") in {"unexplained", "contradictory", "stale", "low_quality"}
    ]
    return {
        "observation_count": len(normalized["observations"]),
        "anomaly_count": len(normalized["anomalies"]),
        "question_count": len(normalized["question_backlog"]),
        "hypothesis_count": len(normalized["active_hypotheses"]),
        "hypothesis_origin_counts": origins,
        "default_prior_pct": origins.get("default_prior", 0) / total_hypotheses,
        "unexplained_high_surprise_count": len(unexplained_high),
        "discarded_hypothesis_count": len(normalized["discarded_hypotheses"]),
        "cross_domain_handoff_count": len(normalized["cross_domain_handoffs"]),
        "coverage_gap_count": len(normalized["coverage_gaps"]),
    }


def run_claim_critic(ledger: AnalystLedger) -> list[str]:
    normalized = normalize_ledger(str(ledger.get("analyst_domain") or "market"), ledger)
    flags: list[str] = []
    obs_count = len(normalized["observations"])
    if obs_count < 3:
        flags.append(f"observation_count_below_minimum:{obs_count}")
    if obs_count > 8:
        flags.append(f"observation_count_above_maximum:{obs_count}")

    high_surprise = [
        obs
        for obs in normalized["observations"]
        if float(obs.get("surprise_score", 0.0) or 0.0) >= 0.65
        and obs.get("status") in {"unexplained", "contradictory", "stale", "low_quality"}
    ]
    origins = {str(h.get("origin") or "default_prior") for h in normalized["active_hypotheses"]}
    if high_surprise and not (origins - {"default_prior"}):
        flags.append("material_anomalies_without_discovery_generated_hypothesis")

    for hypothesis in normalized["active_hypotheses"]:
        hid = hypothesis.get("id", "unknown")
        if not hypothesis.get("origin"):
            flags.append(f"{hid}:missing_origin")
        if not hypothesis.get("support"):
            flags.append(f"{hid}:missing_support")
        if not hypothesis.get("falsifier"):
            flags.append(f"{hid}:missing_falsifier")
        confidence = float(hypothesis.get("confidence", 0.0) or 0.0)
        if confidence >= 0.75 and len(hypothesis.get("support", [])) < 2:
            flags.append(f"{hid}:high_confidence_with_thin_support")
    return flags


def merge_coverage_gaps(ledger: AnalystLedger, gaps: list[CoverageGap]) -> AnalystLedger:
    normalized = normalize_ledger(str(ledger.get("analyst_domain") or "market"), ledger)
    existing = {gap.get("id") for gap in normalized["coverage_gaps"]}
    for gap in gaps:
        if gap.get("id") not in existing:
            normalized["coverage_gaps"].append(gap)
    return normalized


def _memo_observation_candidates(text: Any) -> list[str]:
    memo = strip_executable_proposals(text)
    candidates: list[str] = []
    for raw_line in memo.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"^\d+[\.)]\s+", "", line)
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("|"):
            continue
        if len(line) < 24:
            continue
        if line.lower() in {
            "domain inference",
            "key observations",
            "active hypotheses",
            "questions investigated",
        }:
            continue
        candidates.append(line[:260])
    if len(candidates) >= 3:
        return candidates[:8]

    sentences = re.split(r"(?<=[.!?])\s+", memo)
    for sentence in sentences:
        compact = re.sub(r"\s+", " ", sentence).strip()
        if len(compact) >= 40 and compact not in candidates:
            candidates.append(compact[:260])
        if len(candidates) >= 8:
            break
    return candidates[:8]


def build_recovery_ledger(domain: str, memo: Any, reason: str) -> AnalystLedger:
    analyst_domain = _domain(domain)
    candidates = _memo_observation_candidates(memo)
    if len(candidates) < 3:
        candidates.extend(
            [
                "Analyst memo did not provide enough structured observations for audit.",
                "The workbench recovered a ledger from non-compliant output.",
                "Decision relevance remains uncertain until structured observations are regenerated.",
            ]
        )
    observations = [
        {
            "id": f"obs_{analyst_domain}_{idx:03d}",
            "domain": analyst_domain,
            "claim": claim,
            "source_fact_ids": [],
            "surprise_score": 0.7 if idx == 1 else 0.5,
            "why_it_matters": "Recovered from analyst memo because the structured ledger contract was not satisfied.",
            "status": "unexplained" if idx == 1 else "low_quality",
        }
        for idx, claim in enumerate(candidates[:8], 1)
    ]
    question = {
        "id": f"q_{analyst_domain}_recovery_001",
        "question": "Which recovered observation would most change the analyst conclusion if verified or falsified?",
        "triggered_by": [obs["id"] for obs in observations[:3]],
        "decision_relevance": 0.7,
        "expected_information_gain": 0.65,
        "evidence_surprise": 0.7,
        "estimated_tool_cost": 1.0,
        "cheapest_tool": None,
        "stop_condition": "A compliant workbench ledger is regenerated or the recovered observations are manually accepted.",
        "promoted_to_hypothesis": True,
    }
    hypothesis = {
        "id": f"h_{analyst_domain}_recovery_001",
        "claim": "Structured ledger recovery is required before relying on this analyst's detailed inference.",
        "origin": "critic_generated",
        "support": [obs["id"] for obs in observations[:3]],
        "against": [],
        "confidence": 0.35,
        "falsifier": "A regenerated analyst response provides 3-8 structured observations, supported hypotheses, and concrete falsifiers.",
        "unresolved_questions": [question["id"]],
    }
    return normalize_ledger(
        analyst_domain,
        {
            "observations": observations,
            "anomalies": [observations[0]["id"]] if observations else [],
            "question_backlog": [question],
            "hypothesis_candidates": [hypothesis],
            "active_hypotheses": [hypothesis],
            "open_questions": [question["id"]],
            "unexplained_but_decision_relevant": [
                "Analyst output did not satisfy the structured workbench ledger contract."
            ],
            "critic_flags": [reason],
        },
    )


def needs_recovery_ledger(ledger: AnalystLedger) -> bool:
    normalized = normalize_ledger(str(ledger.get("analyst_domain") or "market"), ledger)
    if len(normalized["observations"]) < 3:
        return True
    if not normalized["active_hypotheses"]:
        return True
    for hypothesis in normalized["active_hypotheses"]:
        if not hypothesis.get("support") or not hypothesis.get("falsifier"):
            return True
    return False


DISCOVERY_LANE_PROMPT = """Important: The default hypotheses are not a checklist and not a closed ontology.
They are weak priors.

Before selecting active hypotheses, perform an anomaly/question discovery pass:
1. Identify 3-8 observations that are surprising, contradictory, unusually large, stale, or not explained by the default hypotheses.
2. Generate decision-relevant questions raised by those observations.
3. Promote only the most important questions into active hypotheses.
4. At least one hypothesis should be anomaly-generated when the evidence contains a material unexplained observation.
5. If no anomaly-generated hypothesis is needed, explicitly say why the default hypothesis set sufficiently explains the evidence.

Do not enumerate all possible hypotheses. Use evidence surprise and expected information gain to decide what deserves attention."""


DOMAIN_DISCOVERY_PROMPTS = {
    "market": """During discovery, look for price-action facts that are not fully explained by standard trend/range/exhaustion hypotheses: breakout without volume confirmation, volatility expansion without clear catalyst, options flow contradicting price action, dark-pool or short-interest data inconsistent with visible trend, or gap risk that invalidates clean levels. Promote anomaly-generated hypotheses for squeeze, liquidity vacuum, stealth distribution, failed breakout, or event-driven repricing mechanisms.""",
    "news": """During discovery, look for catalysts that may be misclassified, stale, incomplete, or already priced in: company news smaller than the price reaction, sector sympathy mistaken for company-specific strength, macro headlines overwhelming company catalysts, positive news with bearish price reaction, or missing regulatory, litigation, customer, supply-chain, or earnings-quality angles.""",
    "fundamentals": """During discovery, look for disconnects between valuation, growth expectations, margin trajectory, balance-sheet risk, and narrative strength: multiple expansion without estimate revisions, revenue growth with declining earnings quality, unsupported margin optimism, ignored dilution/liquidity risk, or one-customer/product/cycle dependency.""",
    "sentiment": """During discovery, look for whether attention is informative, reflexive, crowded, stale, or noisy: attention spikes after price has already moved, sentiment divergence from price, retail enthusiasm without institutional confirmation, crowded bullishness increasing downside gap risk, or negative sentiment that fails to pressure price.""",
    "catalyst": """During discovery, look for discrete events that can change timing, thesis validity, or risk budget: earnings proximity, guidance changes, filings, dilution, insider activity, lawsuits/regulatory events, product or customer events, macro dates, and price/volume shocks without a clear explanation.""",
}


DEFAULT_HYPOTHESES = {
    "market": "continuation, exhaustion, range, event dislocation",
    "fundamentals": "structural inflection, cyclical peak, valuation stretch, data insufficient",
    "news": "catalyst continuation, sell-the-news, macro drag, no material catalyst",
    "sentiment": "attention acceleration, crowded trade, narrative fatigue, low-signal noise",
    "catalyst": "thesis-supporting catalyst, thesis-breaking catalyst, timing risk, low materiality noise",
}


def build_minimum_evidence_question(domain: str, tool_name: str | None = None) -> AnalystQuestion:
    analyst_domain = _domain(domain)
    return {
        "id": f"q_{analyst_domain}_minimum_evidence",
        "question": "What compact evidence is needed to extract observations and detect anomalies before hypothesis promotion?",
        "triggered_by": [],
        "decision_relevance": 1.0,
        "expected_information_gain": 1.0,
        "evidence_surprise": 1.0,
        "estimated_tool_cost": 1.0,
        "cheapest_tool": tool_name,
        "stop_condition": "Minimum compact evidence bundle has been reviewed.",
        "promoted_to_hypothesis": False,
    }


def build_workbench_prompt_block(domain: str, allowed_question: AnalystQuestion | None = None) -> str:
    analyst_domain = _domain(domain)
    default_priors = DEFAULT_HYPOTHESES.get(analyst_domain, "")
    allowed = ""
    if allowed_question:
        allowed = (
            "\n\nAllowed tool question for this turn:\n"
            f"- question_id: {allowed_question.get('id')}\n"
            f"- question: {allowed_question.get('question')}\n"
            f"- cheapest_tool: {allowed_question.get('cheapest_tool')}\n"
            f"- stop_condition: {allowed_question.get('stop_condition')}\n"
            "If tools are available, call only the tool that answers this named question. "
            "Do not call fallback tools to fill a static report template."
        )
    return f"""{DISCOVERY_LANE_PROMPT}

Default weak-prior hypotheses for this analyst: {default_priors}.

{DOMAIN_DISCOVERY_PROMPTS.get(analyst_domain, "")}

Final output contract:
- Emit one JSON block between BEGIN_ANALYST_LEDGER_JSON and END_ANALYST_LEDGER_JSON.
- The JSON must match the AnalystLedger shape: observations, anomalies, question_backlog, hypothesis_candidates, active_hypotheses, discarded_hypotheses, resolved_questions, open_questions, coverage_gaps, cross_domain_handoffs, do_not_fetch_again, unexplained_but_decision_relevant.
- If the tool output includes compact bundle facts with `id` fields, every observation should cite the relevant vendor fact IDs in `source_fact_ids`.
- Hypothesis support and against entries should cite observation IDs and/or vendor fact IDs when available; avoid raw prose evidence when an ID exists.
- Every analyst should emit 3-8 observations before selecting active hypotheses.
- Every active hypothesis must include id, claim, origin, support, against, confidence, falsifier, and unresolved_questions.
- Active hypotheses should be capped to 2-4 total, with at most 2 default_prior hypotheses and at least one non-default origin when material anomalies exist.
- Every question must include triggered_by, decision_relevance, expected_information_gain, evidence_surprise, estimated_tool_cost, cheapest_tool, and stop_condition.
- After the JSON block, write the human memo using these sections: Domain Inference, Active Hypotheses, Key Observations, Questions Investigated, Discarded Explanations, Unexplained But Decision-Relevant, Watch Items / Falsifiers.
- Do not output executable BUY/HOLD/SELL proposals.{allowed}"""


def finalize_analyst_workbench_output(domain: str, content: Any) -> dict[str, Any]:
    from tradingagents.agents.analysts.discovery_lane import (
        filter_hypotheses_for_caps,
        run_coverage_critic,
    )

    ledger, memo = extract_ledger_and_memo(domain, content)
    if needs_recovery_ledger(ledger):
        ledger = build_recovery_ledger(
            domain,
            memo or content,
            "missing_or_invalid_analyst_ledger_json",
        )
    ledger["active_hypotheses"] = filter_hypotheses_for_caps(
        ledger.get("active_hypotheses", [])
    )
    critic_flags = run_claim_critic(ledger)
    ledger["critic_flags"] = list(dict.fromkeys([*ledger.get("critic_flags", []), *critic_flags]))
    gaps = run_coverage_critic(ledger)
    ledger = merge_coverage_gaps(ledger, gaps)
    report = build_ledger_report(domain, ledger, memo)
    evidence = build_ledger_evidence_summary(domain, ledger)
    metrics = build_workbench_metrics(ledger)
    metrics["resolved_question_count"] = len(ledger.get("resolved_questions", []))
    return {
        "ledger": ledger,
        "report": report,
        "evidence": evidence,
        "metrics": metrics,
    }
