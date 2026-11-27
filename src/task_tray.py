"""Durable, append-only operator task tray for strategy and deployment gates."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

STATES = {"queued", "blocked", "ready", "running", "review", "complete", "cancelled"}

@dataclass(frozen=True)
class Task:
    task_id: str
    title: str
    area: str
    state: str = "queued"
    dependencies: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    owner: str = "operator"
    requires_approval: bool = False
    live_impact: bool = False
    next_action: str = ""
    evidence_hashes: dict[str, str] | None = None

    def __post_init__(self):
        if self.state not in STATES: raise ValueError("invalid task state")
        if not self.task_id.strip() or not self.title.strip(): raise ValueError("task identity required")
        if self.live_impact and not self.requires_approval: raise ValueError("live-impact tasks require approval")
        if self.evidence_hashes is None: object.__setattr__(self, "evidence_hashes", {})

class TaskTray:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, task: Task, *, event: str = "created") -> None:
        row = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, "task": asdict(task)}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    def transition(self, task_id: str, state: str, *, reason: str = "") -> None:
        if state not in STATES: raise ValueError("invalid task state")
        current = self.latest().get(task_id)
        if current is None: raise KeyError(task_id)
        all_tasks = self.latest()
        if state == "running" and any(all_tasks.get(d) is None or all_tasks[d].state != "complete" for d in current.dependencies): raise ValueError("dependencies incomplete")
        if state == "running" and current.live_impact and not self.approvals(task_id): raise PermissionError("operator approval required")
        self.append(Task(**{**asdict(current), "state": state}), event=f"state:{reason or state}")

    def attach_evidence(self, task_id: str, artifact: str, *, sha256: str = "") -> None:
        current = self.latest().get(task_id)
        if current is None: raise KeyError(task_id)
        if not artifact.strip(): raise ValueError("evidence artifact required")
        evidence = tuple(dict.fromkeys((*current.evidence, artifact)))
        hashes = dict(current.evidence_hashes or {})
        if sha256:
            if len(sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha256): raise ValueError("invalid sha256")
            hashes[artifact] = sha256.lower()
        self.append(Task(**{**asdict(current), "evidence": evidence, "evidence_hashes": hashes}), event="evidence_attached")

    def approve(self, task_id: str, operator: str, *, scope: str) -> None:
        current = self.latest().get(task_id)
        if current is None: raise KeyError(task_id)
        if not operator.strip() or not scope.strip(): raise ValueError("approval identity and scope required")
        if not current.requires_approval: raise ValueError("task does not require approval")
        row = {"ts": datetime.now(timezone.utc).isoformat(), "event": "operator_approved", "task_id": task_id, "operator": operator, "scope": scope}
        with self.path.open("a", encoding="utf-8") as f: f.write(json.dumps(row, sort_keys=True) + "\n")

    def latest(self) -> dict[str, Task]:
        current: dict[str, Task] = {}
        if not self.path.exists(): return current
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            row = json.loads(line)
            if "task" not in row: continue
            raw = row["task"]; raw["dependencies"] = tuple(raw.get("dependencies", ())); raw["evidence"] = tuple(raw.get("evidence", ())); raw.setdefault("evidence_hashes", {})
            current[raw["task_id"]] = Task(**raw)
        return current

    def approvals(self, task_id: str) -> list[dict]:
        if not self.path.exists(): return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try: row = json.loads(line)
            except json.JSONDecodeError: continue
            if row.get("event") == "operator_approved" and row.get("task_id") == task_id: out.append(row)
        return out

    def ready(self) -> list[Task]:
        current = self.latest()
        return [t for t in current.values() if t.state == "ready" and all(current.get(d, Task(d, d, "unknown")).state == "complete" for d in t.dependencies)]
