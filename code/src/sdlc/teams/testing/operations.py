"""Testing operations (handoff docs/04 s6, docs/06 s8).

| Operation               | How it runs                     | Must produce                                        |
|-------------------------|---------------------------------|-----------------------------------------------------|
| testing.plan            | Brain                           | `test_plan` JSON (TestPlan: levels, cases, exit)    |
| testing.derive_cases    | Brain                           | `test_cases` JSON (`{"cases": [TestCase...]}`)      |
| testing.design_bench    | Brain                           | `bench_design` Markdown                             |
| testing.implement_bench | CodingBackend, testing workspace| bench snapshot + patch (runtime-captured)           |
| testing.preflight       | ProcessRunner (collect only)    | `preflight_report` (node -> case mapping)           |
| testing.execute         | ProcessRunner recipes           | `execution_record`s, `execution_report`, `test_bench`|
| testing.analyze         | Brain (reads the records)       | `defect_report` Markdown + one `defect` JSON each   |
| testing.assess_release  | Brain (reads the records)       | `test_report` Markdown with runtime-appended counts |

Result truth (docs/04 s6): the runtime derives every per-case verdict from collector
records; a case nobody executed is ``not_executed`` and never a pass; a defect must cite
execution records that exist; a release recommendation cannot say RELEASE while any
case failed or was not executed. A bench job may add tests, fixtures and doubles but
never modifies the production source under test (enforced at capture).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sdlc.application.artifacts import ArtifactService
from sdlc.application.views import TeamView
from sdlc.domain.models import ArtifactRef, ExecutionRecord, Finding, Severity, Task, WorkerProposal
from sdlc.operations import OperationRegistry
from sdlc.teams.common import known_requirement_ids
from sdlc.teams.policies import BasePolicy, PolicyRegistry, candidates_of_kind, finding
from sdlc.teams.testing.mapping import case_ids_from_documents, case_outcomes
from sdlc.verification.outputs import check_candidates, require_kinds
from sdlc.verification.schemas import validate_record

REQUIRED_KINDS: dict[str, tuple[str, ...]] = {
    "plan": ("test_plan",),
    "derive_cases": ("test_cases",),
    "design_bench": ("bench_design",),
    "implement_bench": (),  # the runtime captures the bench workspace
    "preflight": (),  # runtime: collect-only run + mapping report
    "execute": (),  # runtime: collector records + execution report
    "analyze": ("defect_report",),
    "assess_release": ("test_report",),
}

RECOMMENDATIONS = ("RELEASE", "HOLD", "REJECT")


def _dependency_records(task: Task, view: TeamView) -> tuple[list[ExecutionRecord], list[ArtifactRef]]:
    """Execution records produced by this task's completed dependencies (same team)."""
    from sdlc.runtime.graphs import TaskGraphService

    artifacts = ArtifactService(view.ctx)
    records: list[ExecutionRecord] = []
    refs: list[ArtifactRef] = []
    for ref in TaskGraphService(view.ctx).dependency_outputs(task.task_id):
        artifact = artifacts.get(ref)
        if artifact.kind == "execution_record":
            refs.append(ref)
            records.append(ExecutionRecord.model_validate(json.loads(artifacts.read_bytes(ref).decode("utf-8"))))
    return records, refs


def _dependency_case_ids(task: Task, view: TeamView) -> list[str]:
    from sdlc.runtime.graphs import TaskGraphService

    artifacts = ArtifactService(view.ctx)
    texts = []
    for ref in list(task.input_refs) + TaskGraphService(view.ctx).dependency_outputs(task.task_id):
        artifact = artifacts.get(ref)
        if artifact.kind in ("test_cases", "test_plan"):
            texts.append(artifacts.read_bytes(ref).decode("utf-8"))
    return case_ids_from_documents(texts)


def observed_counts(task: Task, view: TeamView) -> tuple[dict[str, int], list[str], int]:
    """(per-status counts, failed case ids, number of records) from the collector's records."""
    records, _ = _dependency_records(task, view)
    outcomes = case_outcomes(_dependency_case_ids(task, view), records)
    return outcomes.counts, outcomes.failed_cases, len(records)


@dataclass(frozen=True, slots=True)
class TestingPolicy(BasePolicy):
    short_name: str = ""

    # ------------------------------------------------------------------ prerequisites
    def check(self, task: Task, view: TeamView) -> list[Finding]:
        findings: list[Finding] = []
        if not task.input_deliveries:
            findings.append(finding("missing_input", "testing operations need a delivered package (requirements at least) in this team's inbox"))
            return findings
        kinds = {view.authorize(r, list(task.input_deliveries)).artifact.kind for r in task.input_refs}
        if self.short_name in ("plan", "derive_cases") and not kinds & {"requirements", "requirements_document", "acceptance_criteria"}:
            findings.append(finding("missing_input", f"{self.short_name} needs the requirements (RequirementsSet or REQUIREMENTS.md) pinned in input_refs"))
        if self.short_name in ("implement_bench", "preflight", "execute") and "source_snapshot" not in kinds and not any(a.kind == "source_snapshot" for a in view.list_owned()):
            findings.append(finding("missing_input", f"{self.short_name} needs the exact candidate source_snapshot delivered by implementation"))
        return findings

    def context_notes(self, task: Task, view: TeamView) -> list[str]:
        notes: list[str] = [
            "Independent testing: failing developer checks, open reviews, flaky history or isolation limitations in the delivered package are facts to test and report, never reasons to stop. "
            "A finding with severity blocking means you cannot do this operation at all (no requirements, no candidate); everything else is a warning finding or a test case."
        ]
        known = known_requirement_ids(task, None, view)
        if known and self.short_name in ("plan", "derive_cases", "analyze", "assess_release"):
            notes.append(f"Valid requirement ids, cite only these: {sorted(known)}")
        if self.short_name == "implement_bench":
            notes.append("Name every test after the case it realises, e.g. test_tc_003_cancel_frees_copy for case TC-003; the runtime maps collected test ids to case ids by that name.")
            notes.append("Add tests, fixtures and doubles only. Never edit or delete a file of the candidate under test; the capture refuses such a bench.")
        if self.short_name in ("analyze", "assess_release"):
            counts, failed, n = observed_counts(task, view)
            notes.append(f"Observed by the collector ({n} record(s)): {counts or 'no cases'}; failed cases: {failed or 'none'}. Cite the execution_record references exactly; never invent observations.")
        return notes

    def required_kinds(self, task: Task) -> tuple[str, ...]:
        return REQUIRED_KINDS[self.short_name]

    # ------------------------------------------------------------------ output obligations
    def check_outputs(self, task: Task, proposal: WorkerProposal, view: TeamView | None = None) -> list[Finding]:
        candidates = list(proposal.artifact_candidates)
        findings = check_candidates(candidates) + require_kinds(candidates, REQUIRED_KINDS[self.short_name])
        for candidate in candidates:
            if candidate.kind in ("execution_record", "preflight_report", "execution_report"):
                findings.append(finding("fabricated_evidence", "only the execution collector records runs; the model may not author execution evidence"))
        if self.short_name == "derive_cases":
            findings += self._check_cases(candidates)
        if self.short_name == "analyze" and view is not None:
            findings += self._check_defects(task, proposal, view)
        if self.short_name == "assess_release" and view is not None:
            findings += self._check_recommendation(task, proposal, view)
        return findings

    @staticmethod
    def _check_cases(candidates) -> list[Finding]:
        out: list[Finding] = []
        for candidate in candidates_of_kind_list(candidates, "test_cases"):
            try:
                payload = json.loads(candidate.content_text or "")
                cases = payload["cases"]
                assert isinstance(cases, list) and cases
            except (ValueError, KeyError, AssertionError, TypeError):
                out.append(finding("invalid_test_cases", "test_cases must be JSON of the form {\"cases\": [TestCase, ...]} with at least one case"))
                continue
            seen: set[str] = set()
            for case in cases:
                try:
                    validate_record(case, "TestCase")
                except Exception as exc:  # noqa: BLE001 - validation detail goes into the finding
                    out.append(finding("invalid_test_case", f"a test case does not match the TestCase contract: {str(exc)[:200]}"))
                    continue
                if case["case_id"] in seen:
                    out.append(finding("duplicate_case_id", f"case id {case['case_id']} appears twice"))
                seen.add(case["case_id"])
        return out

    def _check_defects(self, task: Task, proposal: WorkerProposal, view: TeamView) -> list[Finding]:
        out: list[Finding] = []
        records, record_refs = _dependency_records(task, view)
        known = {r.key for r in record_refs}
        outcomes = case_outcomes(_dependency_case_ids(task, view), records)
        defects = candidates_of_kind(proposal, "defect")
        for candidate in defects:
            try:
                payload = json.loads(candidate.content_text or "")
            except ValueError:
                out.append(finding("invalid_defect", "defect candidates must be JSON Defect records"))
                continue
            for ref in payload.get("execution_refs", []):
                try:
                    key = ArtifactRef.model_validate(ref).key
                except Exception:  # noqa: BLE001
                    out.append(finding("invalid_defect", "execution_refs must be artifact references"))
                    continue
                if key not in known:
                    out.append(finding("fabricated_evidence", f"defect {payload.get('defect_id')} cites an execution record that this task's dependencies did not produce: {ref}"))
        if outcomes.failed_cases and not defects:
            out.append(finding("missing_defects", f"the collector observed failing cases {outcomes.failed_cases} but no defect record was produced; file one per distinct failure (bench defects count too)"))
        return out

    def _check_recommendation(self, task: Task, proposal: WorkerProposal, view: TeamView) -> list[Finding]:
        out: list[Finding] = []
        counts, failed, n = observed_counts(task, view)
        for candidate in candidates_of_kind(proposal, "test_report"):
            text = candidate.content_text or ""
            words = {w for w in RECOMMENDATIONS if re.search(rf"\b{w}\b", text)}
            if not words:
                out.append(finding("missing_recommendation", "the Recommendation section must state exactly one of RELEASE, HOLD or REJECT (upper case)"))
                continue
            if len(words) > 1:
                out.append(finding("ambiguous_recommendation", f"state one recommendation, not {sorted(words)}"))
            not_passed = sum(v for k, v in counts.items() if k != "passed")
            if "RELEASE" in words and (n == 0 or not_passed or failed):
                out.append(finding("optimistic_recommendation", f"RELEASE is not supported by the evidence: records={n}, counts={counts}; failed or unexecuted cases are not passes"))
        return out

    # ------------------------------------------------------------------ normalisation
    def normalize(self, task: Task, proposal: WorkerProposal, view: TeamView | None = None) -> tuple[WorkerProposal, list[Finding]]:
        """Append the collector's raw counts to the readable test report so the narrative is
        always read next to the numbers it must agree with."""
        if self.short_name == "derive_cases":
            changed, notes = [], []
            for candidate in proposal.artifact_candidates:
                text = candidate.content_text or ""
                if candidate.kind == "test_cases":
                    try:
                        payload = json.loads(text)
                    except ValueError:
                        payload = None
                    if isinstance(payload, list):  # a bare list of cases: wrap it
                        candidate = candidate.model_copy(update={"content_text": json.dumps({"cases": payload}), "media_type": "application/json"})
                        notes.append(Finding(finding_id="finding-cases-wrapped", code="test_cases_wrapped", severity=Severity.INFO, message="bare case list wrapped as {\"cases\": [...]}", related_refs=[]))
                    elif isinstance(payload, dict) and "cases" not in payload and any(isinstance(v, list) for v in payload.values()):
                        key = next(k for k, v in payload.items() if isinstance(v, list))
                        candidate = candidate.model_copy(update={"content_text": json.dumps({"cases": payload[key]}), "media_type": "application/json"})
                        notes.append(Finding(finding_id="finding-cases-rekeyed", code="test_cases_rekeyed", severity=Severity.INFO, message=f"case list under {key!r} normalised to 'cases'", related_refs=[]))
                changed.append(candidate)
            return proposal.model_copy(update={"artifact_candidates": changed}), notes
        if self.short_name != "assess_release" or view is None:
            return proposal, []
        counts, failed, n = observed_counts(task, view)
        appendix = (
            "\n\n## Observed counts (runtime)\n\n"
            f"- execution records: {n}\n- per-case verdicts: {json.dumps(counts, sort_keys=True)}\n- failed cases: {', '.join(failed) if failed else 'none'}\n"
            "- rule: a case without an observation is not_executed and never counts as a pass\n"
        )
        changed = []
        for candidate in proposal.artifact_candidates:
            if candidate.kind == "test_report" and candidate.content_text and "## Observed counts (runtime)" not in candidate.content_text:
                changed.append(candidate.model_copy(update={"content_text": candidate.content_text.rstrip() + appendix}))
            else:
                changed.append(candidate)
        note = Finding(finding_id="finding-counts-appended", code="runtime_counts_appended", severity=Severity.INFO, message="observed counts appended to the test report by the runtime", related_refs=[])
        return proposal.model_copy(update={"artifact_candidates": changed}), [note]


def candidates_of_kind_list(candidates, kind: str):
    return [c for c in candidates if c.kind == kind]


def install(registry: OperationRegistry, policies: PolicyRegistry) -> None:
    for short in REQUIRED_KINDS:
        name = f"testing.{short}"
        policies.register(registry, name, 1, TestingPolicy(name=f"{name}.v1", short_name=short))
