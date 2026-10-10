"""SQLite persistence: connection pragmas, checksummed migrations, repositories.

Only the runtime broker writes this database. Team agents never receive a connection.
"""

from sdlc.storage.database import Database

__all__ = ["Database"]
