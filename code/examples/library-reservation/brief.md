# Library reservation demo — participant brief / 참여자 브리프

A student member should be able to reserve an available library copy and cancel their
own reservation. A librarian should be able to inspect active reservations. Two active
reservations for the same copy must never coexist, even when two members try at the
same time.

Keep the first implementation local and small enough to understand in class: one
process, a file-backed database, a tiny HTTP API and automated tests.

Open decisions (do not invent answers): authentication, reservation expiry, and
performance thresholds.

Research, mock interviews and generated survey responses must be labeled as such.
Tests must execute against a recorded candidate before anyone claims the system passes.
