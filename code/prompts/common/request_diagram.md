version: 1
Produce the requested diagram as one `diagram_source` candidate. Use Mermaid
(`text/vnd.mermaid`) for flow, state, sequence, class, ER and component views, or
PlantUML (`text/x-plantuml`) when UML notation such as use cases fits better. The
source must reference the ids in `scope_ids` where applicable, explain in `summary`
which engineering question the diagram answers, and be labeled unrendered. If the
requested notation is unsupported, return a `capability_missing` finding and a useful
alternative instead of an unrelated drawing.
