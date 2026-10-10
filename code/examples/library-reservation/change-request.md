# Change request: reservations with an empty member id

Author: the course instructor, acting as the library's operations lead (simulated
operator feedback; no real user was involved).

## What we observed

Release 1 of the reservation service accepts a reservation whose member id is an empty
string. A librarian typed nothing into the member field and the copy became reserved
by "" (nobody). Nobody can cancel it, because cancelling requires the same member id.

## What should change

- A reservation must name a member: an empty or blank member id is rejected with a
  clear error, and nothing is stored.
- This is a corrective change against the current release; the rule was implied by
  REQ-001 ("a member reserves a copy") but never stated as a validation.

## Evidence

Simulated: the observation above was produced by the instructor while preparing the
course, not collected from a running library system.

## Urgency

Medium: it blocks a copy until a database fix, but happens rarely.
