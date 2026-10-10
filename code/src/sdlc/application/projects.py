"""Project directory layout, creation and opening.

``<home>/<name>/``
    ``project.yaml``   validated configuration (credential references only)
    ``state.sqlite3``  runtime control store (privileged; never mounted into an agent)
    ``artifacts/``     content-addressed immutable payloads
    ``workspaces/``    per-team, per-task working directories (M2+)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from sdlc.artifacts.store import ContentStore
from sdlc.common import Clock, IdFactory, new_id, now_utc
from sdlc.config import ProjectConfig, dump_config, load_config
from sdlc.domain.errors import Conflict, MissingInput
from sdlc.storage import repositories as repo
from sdlc.storage.database import Database

CONFIG_FILENAME = "project.yaml"
DB_FILENAME = "state.sqlite3"


@dataclass
class ProjectContext:
    """Everything a service needs: config, control store, payload store, clock and ids."""

    root: Path
    config: ProjectConfig
    db: Database
    store: ContentStore
    clock: Clock = field(default=now_utc)
    ids: IdFactory = field(default=new_id)

    @property
    def project_id(self) -> str:
        return self.config.project.id

    @property
    def workspaces(self) -> Path:
        return self.root / "workspaces"

    def close(self) -> None:
        self.db.close()


def create_project(
    home: Path,
    name: str,
    template: Path,
    *,
    policy: str = "collaborative",
    force: bool = False,
    clock: Clock = now_utc,
    ids: IdFactory = new_id,
) -> ProjectContext:
    """Write the configuration from ``template`` in fixture mode and initialise the stores."""
    root = Path(home) / name
    config_path = root / CONFIG_FILENAME
    if config_path.exists() and not force:
        raise Conflict("project already exists", path=str(config_path))
    raw = yaml.safe_load(template.read_text(encoding="utf-8"))
    raw["project"]["id"] = name
    raw["project"]["mode"] = policy
    raw["project"]["fixture_mode"] = True
    config = ProjectConfig.model_validate(raw)
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspaces").mkdir(exist_ok=True)
    config_path.write_text(dump_config(config), encoding="utf-8")
    context = _open(root, config, clock, ids)
    if repo.get_project(context.db, name) is None:
        with context.db.transaction():
            repo.insert_project(context.db, name, name, config.model_dump(mode="json"), clock())
    return context


def open_project(home: Path, name: str, *, clock: Clock = now_utc, ids: IdFactory = new_id) -> ProjectContext:
    root = Path(home) / name
    config_path = root / CONFIG_FILENAME
    if not config_path.exists():
        raise MissingInput("project configuration not found", path=str(config_path))
    config = load_config(config_path)
    if config.project.id != name:
        raise Conflict("configuration project id differs from directory name", expected=name, found=config.project.id)
    context = _open(root, config, clock, ids)
    if repo.get_project(context.db, name) is None:
        with context.db.transaction():
            repo.insert_project(context.db, name, name, config.model_dump(mode="json"), clock())
    return context


def open_in_memory(config: ProjectConfig, root: Path, *, clock: Clock = now_utc, ids: IdFactory = new_id) -> ProjectContext:
    """For tests: real file store under ``root``, SQLite in memory."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspaces").mkdir(exist_ok=True)
    context = ProjectContext(root=root, config=config, db=Database.open(":memory:"), store=ContentStore(root / "artifacts"), clock=clock, ids=ids)
    with context.db.transaction():
        repo.insert_project(context.db, config.project.id, config.project.id, config.model_dump(mode="json"), clock())
    return context


def _open(root: Path, config: ProjectConfig, clock: Clock, ids: IdFactory) -> ProjectContext:
    return ProjectContext(
        root=root,
        config=config,
        db=Database.open(root / DB_FILENAME),
        store=ContentStore(root / "artifacts"),
        clock=clock,
        ids=ids,
    )
