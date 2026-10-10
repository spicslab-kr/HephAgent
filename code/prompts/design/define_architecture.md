version: 1
Allocate responsibilities to modules and define boundaries, dependencies, deployment
assumptions and the mechanism for each nonfunctional requirement. Produce two
candidates: `design` (`design.json`, application/json) validating as `DesignSet`
whose `requirements_ref` is the pinned requirements artifact (id, revision, sha256
exactly as supplied) and whose elements list the requirement ids they realise; and
`architecture` Markdown (`docs/ARCHITECTURE.md`) with headings Architecture,
Interface, Behavior, Requirement mapping. Every `must` requirement id listed as valid
in the instructions appears in an element or in `open_questions` with a reason.

References are exact: copy `artifact_id`, `revision` and `sha256` character for
character from the instructions or the supplied documents. Leave `contract_refs`
empty unless you are copying such an exact reference; never invent or shorten a hash.
