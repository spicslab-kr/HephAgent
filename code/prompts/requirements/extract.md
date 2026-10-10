version: 1
Produce the canonical `requirements` JSON candidate (`requirements.json`) validating as
`RequirementsSet`: stable ids (REQ-xxx), revision 1, kind, statement, rationale,
priority, `source_claim_ids` pointing at claims you list in `claims`, at least one
acceptance condition each, a verification method and open questions. Every claim
cites the import locator (page/paragraph) it came from. Do not silently resolve
contradictions: keep them as open questions.
