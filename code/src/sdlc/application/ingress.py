"""Participant ingress: import a document for one team (handoff docs/03 s6, FR-004).

The original bytes are kept with their hash. A deterministic extraction (Markdown,
PDF via pypdf, DOCX via python-docx) produces a second artifact with the normalised
text, page/paragraph locators, warnings and a completeness assessment. Both are
published in one ``manual_input`` bundle from the participant to the target team.

Unreadable or partially readable content never becomes an empty success: the import
result carries ``needs_input`` with the reason (scanned pages, encryption, missing
extractor), and the extraction artifact says exactly which units are missing. Text
inside an imported file is evidence, never a runtime instruction.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from sdlc.application.handoffs import HandoffService
from sdlc.application.projects import ProjectContext
from sdlc.artifacts.imports import DOCX_TYPE, PDF_TYPE, Extraction, extract
from sdlc.domain.errors import DeniedScope, InvalidSchema
from sdlc.domain.models import Artifact, BundleKind, Delivery, DeliveryReceipt, Origin, OwnerTeam, TeamId
from sdlc.verification.canonical import canonical_bytes

TEXT_TYPES = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain", ".html": "text/html", ".htm": "text/html", ".json": "application/json", ".csv": "text/csv"}


@dataclass(frozen=True, slots=True)
class ImportResult:
    artifact: Artifact
    extraction_artifact: Artifact | None
    delivery: Delivery
    receipt: DeliveryReceipt
    media_type: str
    #: "complete", "partial", "unreadable", "blocked" (extractor missing) or "unsupported"
    extraction: str
    #: Set when the participant must act: scanned pages, encryption, missing extractor.
    needs_input: str | None
    warnings: tuple[str, ...]


def detect_media_type(data: bytes, suffix: str) -> tuple[str, str]:
    """Detect by content first, extension second. Returns (media_type, extraction hint)."""
    if data.startswith(b"%PDF-"):
        return PDF_TYPE, "pending"
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return "application/zip", "unsupported"
        if "[Content_Types].xml" in names and any(n.startswith("word/") for n in names):
            return DOCX_TYPE, "pending"
        return "application/zip", "unsupported"
    if suffix.lower() in TEXT_TYPES:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidSchema("text document is not valid UTF-8", detail=str(exc)) from exc
        return TEXT_TYPES[suffix.lower()], "text"
    try:
        data.decode("utf-8")
        return "text/plain", "text"
    except UnicodeDecodeError:
        return "application/octet-stream", "unsupported"


def import_material(
    context: ProjectContext,
    *,
    team: TeamId,
    path: Path,
    purpose: str,
    artifact_id: str | None = None,
    cycle_id: str = "cycle-001",
    actor_id: str = "participant",
) -> ImportResult:
    path = Path(path)
    if not path.is_file():
        raise DeniedScope("import path is not a readable file", path=str(path))
    data = path.read_bytes()
    limit = context.config.imports.max_file_bytes
    if len(data) > limit:
        raise InvalidSchema("file exceeds imports.max_file_bytes", size=len(data), limit=limit)
    media_type, hint = detect_media_type(data, path.suffix)
    if hint == "unsupported":
        raise InvalidSchema("unsupported document type; supply Markdown/plain text, PDF or DOCX", media_type=media_type)
    extraction: Extraction = extract(data, media_type, max_pdf_pages=context.config.imports.max_pdf_pages)

    handoffs = HandoffService(context)
    artifact_id = artifact_id or context.ids("import")
    original = handoffs.artifacts.register(
        owner=OwnerTeam.PARTICIPANT,
        artifact_id=artifact_id,
        kind="import_original",
        media_type=media_type,
        relative_path=f"imports/{artifact_id}/{path.name}",
        data=data,
        origin=Origin.PARTICIPANT,
        actor_is_ingress=True,
    )
    refs = [original.ref]
    extraction_artifact: Artifact | None = None
    if extraction.completeness != "blocked":
        extraction_artifact = handoffs.artifacts.register(
            owner=OwnerTeam.PARTICIPANT,
            artifact_id=f"{artifact_id}-extraction",
            kind="import_extraction",
            media_type="application/json",
            relative_path=f"imports/{artifact_id}/extraction.json",
            data=canonical_bytes({**extraction.to_json_dict(), "source": original.ref.to_json_dict()}),
            origin=Origin.TOOL,
            depends_on=[original.ref],
            actor_is_ingress=True,
        )
        refs.append(extraction_artifact.ref)
    needs_input = None
    if extraction.completeness in ("unreadable", "blocked"):
        needs_input = "; ".join(extraction.warnings) or "document could not be read"
    elif extraction.completeness == "partial":
        needs_input = f"{extraction.units_empty} of {extraction.units_total} units yielded no text; supply the missing text or enable OCR"
    bundle, bundle_ref = handoffs.publish(
        source=OwnerTeam.PARTICIPANT,
        target=team,
        kind=BundleKind.MANUAL_INPUT,
        bundle_id=context.ids("bundle"),
        cycle_id=cycle_id,
        baseline_id=f"participant-{artifact_id}",
        artifact_refs=refs,
        summary=f"Participant import for {team.value}: {purpose} (extraction: {extraction.completeness})",
        next_actions=[f"{team.value}.intake"] + ([f"participant: {needs_input}"] if needs_input else []),
        actor_is_ingress=True,
    )
    current_baseline, _ = handoffs.baseline(team)
    delivery = handoffs.deliver(bundle_ref=bundle_ref, expected_target_baseline=current_baseline)
    receipt = handoffs.receive(delivery.delivery_id)
    return ImportResult(
        artifact=original,
        extraction_artifact=extraction_artifact,
        delivery=delivery,
        receipt=receipt,
        media_type=media_type,
        extraction=extraction.completeness,
        needs_input=needs_input,
        warnings=tuple(extraction.warnings),
    )
