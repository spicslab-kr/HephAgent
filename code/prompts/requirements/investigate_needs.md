version: 1
Identify actor goals, domain questions and objections. Where real customers or
experts are unavailable, you may role-play them from the supplied evidence: output
those statements as a `research_evidence` JSON candidate with `synthetic: true`,
`generation_method` describing the persona and prompt, and claims whose `basis` is
`synthetic`. Retrieved facts keep `basis: retrieved` with source locators. Produce
also a `needs_investigation` Markdown candidate (`docs/NEEDS.md`).
