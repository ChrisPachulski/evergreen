"""Typed contracts shared by host planning and filesystem transactions."""

from dataclasses import asdict, dataclass, field, fields
from enum import Enum
import hashlib
import json
from pathlib import Path
import stat


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

    def matches(self, actual: "PathSnapshot", *, nlink: int | None = None) -> bool:
        return (
            actual.kind == self.kind and actual.data == self.data and
            actual.target == self.target and (actual.dev, actual.ino) == (self.dev, self.ino) and
            actual.mode == self.mode and actual.nlink == (self.nlink if nlink is None else nlink) and
            (actual.uid, actual.gid) == (self.uid, self.gid) and
            (actual.atime_ns, actual.mtime_ns) == (self.atime_ns, self.mtime_ns) and
            actual.metadata_digest == self.metadata_digest
        )

    def matches_stat(self, metadata) -> bool:
        return (
            self.kind, self.dev, self.ino, self.mode, self.nlink, self.uid, self.gid,
        ) == (
            _kind_from_mode(metadata.st_mode), metadata.st_dev, metadata.st_ino,
            stat.S_IMODE(metadata.st_mode), metadata.st_nlink,
            metadata.st_uid, metadata.st_gid,
        )


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
    RECOVERING = "recovering"


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

    def names_match(self, target: str, transaction_id: str, artifacts: dict[str, str]) -> bool:
        expected = {"journal": self.journal}
        if self.temporary is not None:
            expected["temporary"] = self.temporary
        if self.backup is not None:
            expected["backup"] = self.backup
        return (
            self.target == target and self.transaction_id == transaction_id and
            self.journal == f".{target}.evergreen-journal-{transaction_id}" and
            (self.temporary is None or self.temporary == f".{target}.evergreen-{transaction_id}") and
            (self.backup is None or self.backup == f".{target}.evergreen-backup-{transaction_id}") and
            set(artifacts).issubset(expected) and
            all(expected[kind] == name for kind, name in artifacts.items())
        )

    @staticmethod
    def artifact_name(target: str, name: str) -> tuple[str, str] | None:
        prefixes = (
            ("backup", f".{target}.evergreen-backup-"),
            ("journal", f".{target}.evergreen-journal-"),
            ("temporary", f".{target}.evergreen-"),
        )
        for kind, prefix in prefixes:
            transaction_id = name.removeprefix(prefix)
            if transaction_id != name and len(transaction_id) == 32 and all(
                char in "0123456789abcdef" for char in transaction_id
            ):
                return kind, transaction_id
        return None


@dataclass(frozen=True)
class RollbackEntry:
    before: PathSnapshot
    after: PathSnapshot
    parent: PathSnapshot
    backup: str | None = None
    journal: str | None = None


def _kind_from_mode(mode: int) -> str:
    for predicate, kind in (
        (stat.S_ISLNK, "symlink"), (stat.S_ISDIR, "directory"),
        (stat.S_ISREG, "regular"),
    ):
        if predicate(mode):
            return kind
    return "other"
