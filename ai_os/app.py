"""Wiring: build a ready-to-use AI OS instance from the saved configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent import Supervisor
from .audit import AuditLog
from .config import Config, load_config, save_config
from .llm import LLMRouter
from .memory import MemoryStore
from .planner import Planner
from .safety import SafetyEngine
from .skills.registry import SkillRegistry, default_registry
from .undo import UndoManager


@dataclass
class AiOS:
    """Everything the CLI and the GUI need, assembled in one place."""

    config: Config
    registry: SkillRegistry
    audit: AuditLog
    router: LLMRouter
    safety: SafetyEngine
    supervisor: Supervisor
    undo: UndoManager
    memory: MemoryStore
    config_path: Path | None = None

    @classmethod
    def create(cls, config: Config | None = None, *, config_path: Path | None = None,
               db_path: Path | None = None,
               registry: SkillRegistry | None = None) -> AiOS:
        config = config or load_config(config_path)
        audit = AuditLog(db_path)
        memory = MemoryStore(audit, config.memory_enabled)
        registry = registry or default_registry(memory)
        router = LLMRouter(config)
        safety = SafetyEngine(config)
        supervisor = Supervisor(config, registry, audit, router, safety, memory,
                                Planner(config.planner_max_steps))
        return cls(
            config=config,
            registry=registry,
            audit=audit,
            router=router,
            safety=safety,
            supervisor=supervisor,
            undo=UndoManager(config, registry, audit),
            memory=memory,
            config_path=config_path,
        )

    def save(self) -> Path:
        return save_config(self.config, self.config_path)

    def close(self) -> None:
        self.audit.close()
