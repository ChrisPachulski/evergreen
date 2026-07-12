"""Typed contracts shared by host planning and filesystem transactions."""

from dataclasses import asdict, dataclass, field, fields
from enum import Enum
import hashlib
import json
from pathlib import Path


@dataclass(frozen=True)
class HostStatus:
    name: str
    present: bool
    root: Path
    instructions: Path
    skill: Path
    ownership: Path
    problem: str | None = None


@dataclass(frozen=True)
class OperationResult:
    ok: bool
    messages: tuple[str, ...]


@dataclass(frozen=True)
class Ownership:
    schema_version: int
    host: str
    plugin_root: str
    instruction_existed: bool
    instruction_separator: bool
    skill_target: str


@dataclass(frozen=True)
class PathSnapshot:
    path: Path
    kind: str
    data: bytes | None = None
    target: str | None = None
    mode: int | None = None
    dev: int | None = None
    ino: int | None = None
    nlink: int | None = None
    uid: int | None = None
    gid: int | None = None
    atime_ns: int | None = field(default=None, compare=False)
    mtime_ns: int | None = field(default=None, compare=False)
    metadata_digest: str | None = None

    def journal_identity(self) -> dict[str, object]:
        return {
            "kind": self.kind, "dev": self.dev, "ino": self.ino,
            "mode": self.mode, "nlink": self.nlink, "uid": self.uid,
            "gid": self.gid, "atime_ns": self.atime_ns,
            "mtime_ns": self.mtime_ns,
            "size": len(self.data) if self.data is not None else 0,
            "sha256": hashlib.sha256(self.data or b"").hexdigest(),
            "metadata_digest": self.metadata_digest,
        }


class MutationKind(str, Enum):
    CREATE_REGULAR = "create_regular"
    CREATE_SYMLINK = "create_symlink"
    REPLACE_SYMLINK = "replace_symlink"
    CREATE_DIRECTORY = "create_directory"
    REPLACE_REGULAR = "replace_regular"
    DELETE_REGULAR = "delete_regular"
    DELETE_SYMLINK = "delete_symlink"
    DELETE_DIRECTORY = "delete_directory"


class JournalPhase(str, Enum):
    PREPARED = "prepared"
    PUBLISHED = "published"


@dataclass(frozen=True)
class Mutation:
    kind: MutationKind
    path: Path
    before: PathSnapshot
    parent: PathSnapshot
    data: bytes | None = None
    target: str | None = None
    mode: int | None = None


@dataclass(frozen=True)
class JournalRecord:
    schema_version: int
    transaction_id: str
    phase: JournalPhase
    mutation: MutationKind
    target: str
    temporary: str | None
    backup: str | None
    journal: str
    before: dict[str, object]
    after: dict[str, object]

    @classmethod
    def parse(cls, payload: bytes) -> "JournalRecord":
        value = json.loads(payload)
        names = {item.name for item in fields(cls)}
        if not isinstance(value, dict) or set(value) != names or value["schema_version"] != 1:
            raise ValueError("invalid transaction journal")
        if not isinstance(value["before"], dict) or not isinstance(value["after"], dict):
            raise ValueError("invalid transaction snapshots")
        return cls(
            schema_version=1, transaction_id=value["transaction_id"],
            phase=JournalPhase(value["phase"]),
            mutation=MutationKind(value["mutation"]), target=value["target"],
            temporary=value["temporary"], backup=value["backup"],
            journal=value["journal"], before=value["before"], after=value["after"],
        )

    def encode(self) -> bytes:
        return json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")


@dataclass(frozen=True)
class RollbackEntry:
    before: PathSnapshot
    after: PathSnapshot
    parent: PathSnapshot
    backup: str | None = None
    journal: str | None = None
