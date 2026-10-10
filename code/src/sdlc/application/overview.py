"""A static HTML overview of a project (handoff docs/09 M8: "richer UI if wanted").

One self-contained ``index.html``: stages in order with their current documents (status,
origin, evidence labels), task states, releases, open questions and what to do next. It
is generated from the same services the CLI uses and links to the exported files next to
it, so a reader without the tool can still follow the project. No external assets.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from sdlc.application.artifacts import ArtifactService
from sdlc.application.projects import ProjectContext
from sdlc.application.release import ReleaseService
from sdlc.artifacts.store import safe_relative_path
from sdlc.domain.models import OwnerTeam
from sdlc.domain.teams import TEAM_ORDER

NOISE = {"rejected_output", "checkpoint_summary", "task_graph", "execution_log"}
STAGE_LABELS = {"planning": "Planning", "requirements": "Requirements", "design": "Design", "implementation": "Implementation", "testing": "Testing", "maintenance": "Maintenance"}

_CSS = """
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:2rem auto;max-width:1100px;color:#1d1d1f;background:#fafafa;padding:0 1rem}
h1{font-size:1.6rem}h2{font-size:1.2rem;border-bottom:1px solid #ddd;padding-bottom:.3rem;margin-top:2rem}
table{border-collapse:collapse;width:100%;font-size:.92rem}th,td{border:1px solid #e3e3e3;padding:.35rem .5rem;text-align:left;vertical-align:top}
th{background:#f0f0f2}.tag{display:inline-block;border-radius:.4rem;padding:0 .4rem;font-size:.8rem;margin-right:.25rem;background:#eef}
.stale{background:#fde8e8}.superseded{background:#eee;color:#666}.accepted{background:#e6f6e6}.reviewable{background:#fff6dd}.draft{background:#eef}
.nonconforming{background:#ffe9cc}.fixture{background:#f3e5ff}.state-completed{color:#1a7f37}.state-needs_input,.state-paused{color:#b35900;font-weight:600}
.state-cancelled{color:#888}.note{color:#555;font-size:.9rem}code{background:#f2f2f2;padding:0 .2rem}
"""


def _esc(value) -> str:
    return html.escape(str(value))


def build_overview(context: ProjectContext, scheduler, *, exported_root: Path | None = None) -> str:
    """The HTML text. ``scheduler`` supplies task descriptions; ``exported_root`` makes document links relative to an export folder."""
    artifacts = ArtifactService(context)
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{_esc(context.project_id)} overview</title><style>{_CSS}</style></head><body>"]
    parts.append(f"<h1>Project {_esc(context.project_id)}</h1>")
    cfg = context.config
    parts.append(f"<p class='note'>mode {_esc(cfg.project.mode)} · isolation <b>{_esc(cfg.runtime.isolation)}</b> (launcher {_esc(cfg.runtime.launcher_profile)}) · "
                 f"brains: {', '.join(f'{t.value}={b.brain}' for t, b in cfg.teams.items())}</p>")
    releases = ReleaseService(context).releases()
    parts.append("<h2>Releases</h2>")
    if releases:
        parts.append("<table><tr><th>release</th><th>candidate</th><th>conformance</th><th>known issues</th></tr>")
        for artifact in releases:
            manifest = json.loads(artifacts.read_bytes(artifact.ref).decode("utf-8"))
            conformance = next((i for i in manifest.get("known_issues", []) if i.startswith("conformance:")), "conforming")
            parts.append(f"<tr><td>{_esc(manifest['release_id'])}</td><td><code>{_esc(manifest['source_ref']['artifact_id'])}@{manifest['source_ref']['revision']}</code></td>"
                         f"<td>{_esc(conformance)}</td><td>{len(manifest.get('known_issues', []))}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p class='note'>none yet (a release needs a RELEASE recommendation backed by executed cases)</p>")
    questions: list[str] = []
    for team in TEAM_ORDER:
        owner = OwnerTeam(team.value)
        parts.append(f"<h2>{STAGE_LABELS[team.value]}</h2>")
        latest = {}
        for a in artifacts.list(owner):
            if a.kind in NOISE:
                continue
            if a.artifact_id not in latest or a.revision > latest[a.artifact_id].revision:
                latest[a.artifact_id] = a
        if latest:
            parts.append("<table><tr><th>document</th><th>kind</th><th>status</th><th>origin</th><th>labels</th></tr>")
            for a in sorted(latest.values(), key=lambda x: (x.relative_path, x.artifact_id)):
                status = artifacts.status(a.ref)
                labels = []
                if a.origin.value == "fixture":
                    labels.append("<span class='tag fixture'>fixture evidence</span>")
                if cfg.runtime.isolation != "strict" and a.kind in ("execution_record", "source_snapshot", "test_bench", "check_report", "execution_report"):
                    labels.append("<span class='tag nonconforming'>nonconforming</span>")
                rel = safe_relative_path(a.relative_path)
                href = f"{owner.value}/{'/'.join(rel.parts)}" if exported_root is not None and (exported_root / owner.value / Path(*rel.parts)).exists() else None
                name = f"<a href='{_esc(href)}'>{_esc(a.relative_path)}</a>" if href else _esc(a.relative_path)
                parts.append(f"<tr><td>{name}<br><span class='note'><code>{_esc(a.artifact_id)}@{a.revision}</code></span></td><td>{_esc(a.kind)}</td>"
                             f"<td><span class='tag {_esc(status)}'>{_esc(status)}</span></td><td>{_esc(a.origin.value)}</td><td>{''.join(labels)}</td></tr>")
            parts.append("</table>")
        else:
            parts.append("<p class='note'>no documents yet</p>")
        tasks = scheduler.describe(team) if scheduler is not None else []
        if tasks:
            parts.append("<table><tr><th>task</th><th>operation</th><th>state</th><th>cause / next</th></tr>")
            for t in tasks:
                cause = t.get("cause") or ""
                nxt = t.get("resume") or ""
                parts.append(f"<tr><td><code>{_esc(t['task_id'])}</code></td><td>{_esc(t['operation'])}</td><td class='state-{_esc(t['state'])}'>{_esc(t['state'])}</td>"
                             f"<td>{_esc(cause[:300])}{'<br><code>' + _esc(nxt) + '</code>' if nxt else ''}</td></tr>")
                for q in t.get("questions") or []:
                    questions.append(f"{t['task_id']}: {q}")
            parts.append("</table>")
    parts.append("<h2>Open questions</h2>")
    parts.append("<ul>" + "".join(f"<li>{_esc(q)}</li>" for q in questions) + "</ul>" if questions else "<p class='note'>none</p>")
    parts.append("<p class='note'>Generated by sdlc. Statuses are the runtime's lifecycle projections; labels say how the evidence was produced.</p></body></html>")
    return "".join(parts)


def write_overview(context: ProjectContext, scheduler, destination: Path, *, exported_root: Path | None = None) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(build_overview(context, scheduler, exported_root=exported_root), encoding="utf-8")
    return destination
