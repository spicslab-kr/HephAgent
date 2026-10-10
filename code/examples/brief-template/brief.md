# Study room booking

## Problem
Our department has four study rooms. Students book them by writing on a paper sheet at the
office, so two groups often show up for the same room, and nobody knows which rooms are free
right now.

## Users
- Students: find a free room and book it for a time slot.
- The office assistant: sees today's bookings and cancels a booking when a group does not show up.

## Must have
- A student can see which rooms are free for a given date and hour.
- A student can book a free room for one hour under their student id.
- A room cannot be booked twice for the same hour.
- A student can cancel their own booking, and the hour becomes free again.
- The office assistant can list all bookings of a day.

## Constraints
- Command-line program in Python; data kept in one local file (no database server).
- Any command answers within one second for up to 1,000 bookings.

## Out of scope
- Logging in with passwords, email notifications, recurring bookings, a web interface.
