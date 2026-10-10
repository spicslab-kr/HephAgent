"""Port interfaces between the runtime and external backends (handoff docs/05 s1-2).

Four separate ports, because changing a model endpoint never provides repository
tools, web access or execution isolation:

* :class:`~sdlc.ports.brain.Brain` produces a typed proposal from an explicit context.
* :class:`~sdlc.ports.coding.CodingBackend` inspects or edits a task's repository snapshot.
* :class:`~sdlc.ports.research.ResearchBackend` retrieves evidence with provenance.
* :class:`~sdlc.ports.execution.ProcessRunner` runs declared recipes and collects evidence.

Every adapter also implements :class:`~sdlc.ports.common.Adapter` (``describe`` and
``probe``). Request/reply types here are internal helpers, not persisted contracts.
"""

from sdlc.ports.brain import Brain, BrainReply, BrainRequest
from sdlc.ports.coding import CancelReceipt, CodingBackend, CodingJob
from sdlc.ports.common import Adapter, AdapterDescription, CapabilityStatus, ProbeResult, Support
from sdlc.ports.execution import ExecutionRequest, ProcessRunner
from sdlc.ports.research import ResearchBackend, ResearchReply, ResearchRequest

__all__ = [
    "Adapter",
    "AdapterDescription",
    "Brain",
    "BrainReply",
    "BrainRequest",
    "CancelReceipt",
    "CapabilityStatus",
    "CodingBackend",
    "CodingJob",
    "ExecutionRequest",
    "ProbeResult",
    "ProcessRunner",
    "ResearchBackend",
    "ResearchReply",
    "ResearchRequest",
    "Support",
]
