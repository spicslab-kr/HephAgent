"""SDLC Agent Teams: six isolated SDLC teams driven by external LLMs and coding tools.

The package is organised by dependency direction (see handoff docs/03):

    cli -> application -> domain <- runtime
    teams/<team> -> ports, domain
    adapters -> ports
    artifacts, storage, verification -> domain

Nothing in ``domain`` imports a provider SDK, reads a credential or launches a process.
"""

__version__ = "0.1.0.dev0"
SCHEMA_VERSION = "1.0"
