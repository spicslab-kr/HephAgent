"""Design operations: prerequisites and output obligations (handoff docs/04 s4).

| Operation                   | Must produce                                             |
|-----------------------------|----------------------------------------------------------|
| design.compare_options      | `options` Markdown with decision records                 |
| design.define_architecture  | `design` JSON (DesignSet) + `architecture` Markdown      |
| design.define_contracts     | `contracts` Markdown (APIs/data/invariants/errors)       |
| design.define_behavior      | `behavior` Markdown (states, interactions, failures)     |
| design.plan_implementation  | `implementation_tasks` Markdown                          |

``design.inspect_system`` and ``design.probe_feasibility`` need a scoped repository
(CodingBackend) and stay disabled until the isolation launcher spike (M5).

Quality rules (FR-008), checked against the *pinned* requirements set that the task
received through its accepted delivery, never against ids the model invents:

* ``DesignSet.requirements_ref`` must be one of the task's pinned ``requirements`` artifacts;
* every ``requirement_ids`` entry of an element must exist in that set;
* every ``must`` requirement is covered by an element or named in ``open_questions``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdlc.application.views import TeamView
from sdlc.domain.models import DesignSet, Finding, RequirementsSet, Severity, Task, WorkerProposal
from sdlc.operations import OperationRegistry
from sdlc.teams.policies import BasePolicy, PolicyRegistry, candidates_of_kind, finding
from sdlc.verification.canonical import load_json_strict as _raw_load_json_strict
from sdlc.verification.canonical import strip_code_fence
from sdlc.verification.outputs import check_candidates, require_kinds

REQUIRED_KINDS: dict[str, tuple[str, ...]] = {
    "compare_options": ("options",),
    "define_architecture": ("design", "architecture"),
    "define_contracts": ("contracts",),
    "define_behavior": ("behavior",),
    "plan_implementation": ("implementation_tasks",),
}


def _load_json(text: str):
    try:
        return _raw_load_json_strict(strip_code_fence(text))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class DesignPolicy(BasePolicy):
    short_name: str = ""

    def check(self, task: Task, view: TeamView) -> list[Finding]:
        findings: list[Finding] = []
        if not task.input_deliveries:
            findings.append(finding("missing_input", "design operations need the requirements handoff delivered to this team"))
        elif not self._pinned_requirement_sets(task, view):
            findings.append(finding("missing_input", "design tasks must pin a delivered artifact of kind 'requirements' (the RequirementsSet) in input_refs"))
        return findings

    def required_kinds(self, task: Task) -> tuple[str, ...]:
        return REQUIRED_KINDS[self.short_name]

    def context_notes(self, task: Task, view: TeamView) -> list[str]:
        pinned = self._pinned_requirement_sets(task, view)
        ids = sorted({r.requirement_id for s in pinned.values() for r in s.requirements})
        refs = [f"{k[0]}@{k[1]} sha256 {k[2]}" for k in pinned]
        notes = []
        if ids:
            notes.append(f"Valid requirement ids, cite only these exactly and never renumber them: {ids}")
        if refs:
            notes.append(f"Pinned requirements artifacts for DesignSet.requirements_ref (copy exactly): {refs}")
        return notes

    def check_outputs(self, task: Task, proposal: WorkerProposal, view: TeamView | None = None) -> list[Finding]:
        candidates = list(proposal.artifact_candidates)
        findings = check_candidates(candidates) + require_kinds(candidates, REQUIRED_KINDS[self.short_name])
        if any(f.code == "invalid_record" for f in findings):
            return findings
        pinned = self._pinned_requirement_sets(task, view) if view is not None else {}
        known_ids = {r.requirement_id for req_set in pinned.values() for r in req_set.requirements}
        must_ids = {r.requirement_id for req_set in pinned.values() for r in req_set.requirements if r.priority.value == "must"}
        if known_ids:
            from sdlc.teams.common import check_document_ids

            findings += check_document_ids(proposal, known_ids)
        for candidate in candidates_of_kind(proposal, "design"):
            if candidate.content_text is None:
                continue
            raw = _load_json(candidate.content_text)
            if raw is None:
                continue
            design = DesignSet.model_validate(raw)
            if pinned and design.requirements_ref.key not in pinned:
                options = sorted(f"{k[0]}@{k[1]}" for k in pinned)
                findings.append(finding("unpinned_requirements", f"DesignSet.requirements_ref must be one of the pinned requirements artifacts {options} (copy id, revision and sha256 exactly); got {design.requirements_ref.artifact_id}@{design.requirements_ref.revision}"))
            ids = [e.element_id for e in design.elements]
            if len(ids) != len(set(ids)):
                findings.append(finding("duplicate_element", "DesignSet element ids must be unique"))
            if known_ids:
                cited = {rid for e in design.elements for rid in e.requirement_ids}
                unknown = sorted(cited - known_ids)
                if unknown:
                    findings.append(finding("unknown_requirement_id", f"elements cite requirement ids that do not exist in the pinned requirements set: {unknown[:10]}; valid ids are {sorted(known_ids)}"))
                mentioned = " ".join(design.open_questions)
                undisposed = sorted(m for m in must_ids - cited if m not in mentioned)
                if undisposed:
                    findings.append(finding("uncovered_requirement", f"must requirements without a design element or an open-question disposition: {undisposed}"))
        return findings

    def normalize(self, task: Task, proposal: WorkerProposal, view: TeamView | None = None) -> tuple[WorkerProposal, list[Finding]]:
        """Fix copied hashes: any {artifact_id, revision, sha256} inside a DesignSet whose id and
        revision name a granted or team-owned artifact gets that artifact's exact sha256.
        Models transcribe 64-hex digests unreliably; identity by id+revision is unambiguous."""
        if view is None:
            return proposal, []
        exact: dict[tuple[str, int], str] = {}
        try:
            for key in view.grants(list(task.input_deliveries)):
                exact[(key[0], key[1])] = key[2]
        except Exception:
            pass
        for artifact in view.list_owned():
            exact[(artifact.artifact_id, artifact.revision)] = artifact.sha256
        if not exact:
            return proposal, []
        findings: list[Finding] = []
        fixed: list[str] = []

        def walk(node):
            if isinstance(node, dict):
                if {"artifact_id", "revision", "sha256"} <= set(node) and (node.get("artifact_id"), node.get("revision")) in exact:
                    want = exact[(node["artifact_id"], node["revision"])]
                    if node.get("sha256") != want:
                        node["sha256"] = want
                        fixed.append(f"{node['artifact_id']}@{node['revision']}")
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        candidates = []
        for candidate in proposal.artifact_candidates:
            if candidate.kind == "design" and candidate.content_text is not None:
                raw = _load_json(candidate.content_text)
                if isinstance(raw, dict):
                    before = len(fixed)
                    walk(raw)
                    if len(fixed) > before:
                        import json as _json

                        candidate = candidate.model_copy(update={"content_text": _json.dumps(raw, ensure_ascii=False, indent=2)})
            candidates.append(candidate)
        if fixed:
            findings.append(finding("ref_normalized", f"sha256 replaced with the exact pinned value for {sorted(set(fixed))}", Severity.WARNING))
        return proposal.model_copy(update={"artifact_candidates": candidates}), findings

    @staticmethod
    def _pinned_requirement_sets(task: Task, view: TeamView) -> dict:
        """Pinned input refs whose artifact kind is ``requirements``, parsed as RequirementsSet."""
        found = {}
        for ref in task.input_refs:
            try:
                granted = view.authorize(ref, list(task.input_deliveries))
            except Exception:
                continue
            if granted.artifact.kind != "requirements":
                continue
            raw = _load_json(view.read_text(ref, list(task.input_deliveries)))
            if raw is None:
                continue
            try:
                found[ref.key] = RequirementsSet.model_validate(raw)
            except ValueError:
                continue
        return found


def install(registry: OperationRegistry, policies: PolicyRegistry) -> None:
    for short in REQUIRED_KINDS:
        name = f"design.{short}"
        policies.register(registry, name, 1, DesignPolicy(name=f"{name}.v1", short_name=short))


__all__ = ["install", "DesignPolicy", "Severity"]
