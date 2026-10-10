# Library reservation brief / 요구사항 명세서 input

This is a human-readable input fixture, not an approved product specification.

A student member should reserve an available library copy and cancel their reservation. A librarian should inspect active reservations. Prevent two active reservations for the same copy. Keep the first implementation local and small enough to understand in class.

Proposed acceptance: two concurrent attempts for the same available copy must not both succeed. Define the expected conflict response during requirements/design. Authentication, reservation expiry and performance thresholds remain open decisions. Do not invent agreed answers.

Suggested diagrams: use cases to discuss actor goals; a state view if expiry/cancellation is adopted; a sequence view for conflicting reservation attempts. A student may request additional diagrams with a stated purpose.

Research, mock interviews and generated survey responses must be explicitly labeled. Tests must execute against a recorded candidate before anyone claims the system passes.
