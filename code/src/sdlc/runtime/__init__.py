"""Deterministic coordinator: task graphs, leases, fenced attempts, recovery, quotas.

The runtime is not a seventh agent. It validates what leaders propose, dispatches
registered operations to adapters, records every event, and is the only code that
changes a task's state (handoff docs/06).
"""
