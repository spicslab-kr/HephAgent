"""Map collected runner node ids to the Testing team's case ids (handoff docs/04 s6).

A bench author names each test after the case it realises (``test_tc_003_cancel_frees_copy``
for case ``TC-003``). The mapping is purely textual and deterministic: a node belongs to a
case when the *normalised* case id (lower case, every non-alphanumeric run collapsed to
``_``) occurs inside the normalised node id. The runtime, not the model, decides which
cases were observed; a case without an observation is ``not_executed`` and never counts
as a pass.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sdlc.domain.models import CaseStatus, ExecutionRecord

NOT_EXECUTED = "not_executed"


def normalize_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def case_ids_from_documents(texts: list[str]) -> list[str]:
    """Case ids declared in ``test_cases`` / ``test_plan`` JSON documents, in order, unique."""
    seen: list[str] = []
    for text in texts:
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        cases = payload.get("cases") if isinstance(payload, dict) else payload
        for case in cases or []:
            case_id = case.get("case_id") if isinstance(case, dict) else None
            if case_id and case_id not in seen:
                seen.append(case_id)
    return seen


def short_key(case_id: str) -> str | None:
    """``TC-M0M1-007`` -> ``tc_007``: bench authors often keep only the prefix and the number."""
    match = re.match(r"^([A-Za-z]+).*?(\d+)$", case_id.strip())
    if not match:
        return None
    return f"{match.group(1).lower()}_{match.group(2)}"


def cases_for_node(node: str, case_ids: list[str]) -> list[str]:
    """Cases a test node realises: exact normalised id first; if none, the numbered short key
    (``tc_007``) as a whole token, so ``test_tc_007_x`` maps to ``TC-M0M1-007`` but not to ``TC-0071``."""
    normalized = normalize_id(node)
    exact = [c for c in case_ids if normalize_id(c) and normalize_id(c) in normalized]
    if exact:
        return exact
    tokens = normalized.split("_")
    out = []
    for case_id in case_ids:
        key = short_key(case_id)
        if key and any(tokens[i] == key.split("_")[0] and tokens[i + 1] == key.split("_")[1] for i in range(len(tokens) - 1)):
            out.append(case_id)
    return out


def map_nodes_to_cases(nodes: list[str], case_ids: list[str]) -> dict[str, list[str]]:
    """``case_id -> nodes`` for every declared case (empty list when nothing matched)."""
    mapping: dict[str, list[str]] = {case_id: [] for case_id in case_ids}
    for node in nodes:
        for case_id in cases_for_node(node, case_ids):
            mapping[case_id].append(node)
    return mapping


@dataclass
class CaseOutcomes:
    """Per-case verdicts derived from execution records; the truth table of a run."""

    statuses: dict[str, str] = field(default_factory=dict)  # case_id -> passed|failed|error|skipped|not_executed
    nodes: dict[str, list[str]] = field(default_factory=dict)
    unmapped_nodes: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for status in self.statuses.values():
            out[status] = out.get(status, 0) + 1
        return out

    @property
    def all_passed(self) -> bool:
        return bool(self.statuses) and all(s == "passed" for s in self.statuses.values())

    @property
    def failed_cases(self) -> list[str]:
        return [c for c, s in self.statuses.items() if s in ("failed", "error")]


_SEVERITY = {"error": 4, "failed": 3, "skipped": 2, "passed": 1}


def case_outcomes(case_ids: list[str], records: list[ExecutionRecord]) -> CaseOutcomes:
    """Combine observations across records: the worst observation of a case wins; a case
    nobody observed is ``not_executed``; observations that match no case are listed."""
    outcomes = CaseOutcomes(statuses={c: NOT_EXECUTED for c in case_ids}, nodes={c: [] for c in case_ids})
    for record in records:
        for observation in record.case_observations:
            node = observation.message or observation.case_id
            status = observation.status.value if isinstance(observation.status, CaseStatus) else str(observation.status)
            matched = cases_for_node(node, case_ids)
            if not matched:
                outcomes.unmapped_nodes.append(node)
                continue
            for case_id in matched:
                outcomes.nodes[case_id].append(node)
                current = outcomes.statuses[case_id]
                if current == NOT_EXECUTED or _SEVERITY.get(status, 0) > _SEVERITY.get(current, 0):
                    outcomes.statuses[case_id] = status
    return outcomes
