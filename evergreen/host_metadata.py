"""Fail-closed, bounded extended-metadata integrity for host files."""

import ctypes
import errno
import hashlib
import os
from pathlib import Path
import stat
import sys
import time

try:
    import posix as _posix
except ImportError:  # pragma: no cover
    _posix = None


MAX_XATTRS = 100
MAX_XATTR_BYTES = 1024 * 1024
METADATA_ELAPSED_LIMIT_SECONDS = 3


def native_copy_available() -> bool:
    return _posix is not None and hasattr(_posix, "_fcopyfile") and all(
        hasattr(_posix, name)
        for name in ("_COPYFILE_STAT", "_COPYFILE_ACL", "_COPYFILE_XATTR")
    )


def clone(source_fd: int, destination_fd: int, source, atime_ns: int, mtime_ns: int):
    native = native_copy_available()
    if native:
        _posix._fcopyfile(
            source_fd, destination_fd,
            _posix._COPYFILE_STAT | _posix._COPYFILE_ACL | _posix._COPYFILE_XATTR,
        )
    destination = os.fstat(destination_fd)
    if (destination.st_uid, destination.st_gid) != (source.st_uid, source.st_gid):
        os.fchown(destination_fd, source.st_uid, source.st_gid)
    if not native:
        _clone_xattrs(source_fd, destination_fd, time.monotonic() + METADATA_ELAPSED_LIMIT_SECONDS)
    os.fchmod(destination_fd, stat.S_IMODE(source.st_mode))
    os.utime(destination_fd, ns=(atime_ns, mtime_ns))
    os.fsync(destination_fd)


def _clone_xattrs(source_fd: int, destination_fd: int, deadline: float):
    if not all(hasattr(os, name) for name in ("listxattr", "getxattr", "setxattr")):
        raise OSError("extended-attribute APIs are unavailable")
    unsupported = {
        errno.ENODATA, getattr(errno, "ENOATTR", errno.ENODATA),
        errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
    }
    try:
        items = _items_fd(source_fd, deadline=deadline)
    except OSError as error:
        if error.errno not in unsupported:
            raise
        items = []
    for name, value in items:
        if name != "@acl":
            os.setxattr(destination_fd, name, value)


def digest_path(path: Path, expected, *, deadline: float | None = None) -> str:
    deadline = (
        time.monotonic() + METADATA_ELAPSED_LIMIT_SECONDS
        if deadline is None else deadline
    )
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        actual = os.fstat(descriptor)
        def identity(item):
            return (
                stat.S_IFMT(item.st_mode), item.st_dev, item.st_ino,
                stat.S_IMODE(item.st_mode), item.st_nlink, item.st_uid, item.st_gid,
            )
        if identity(actual) != identity(expected):
            raise OSError(f"metadata changed while snapshotting: {path}")
        return digest_fd(descriptor, deadline=deadline)
    finally:
        os.close(descriptor)


def digest_fd(descriptor: int, *, deadline: float | None = None) -> str:
    deadline = (
        time.monotonic() + METADATA_ELAPSED_LIMIT_SECONDS
        if deadline is None else deadline
    )
    items = _items_fd(descriptor, deadline=deadline)
    digest = hashlib.sha256()
    used = 0
    for name, value in items:
        encoded = name.encode("utf-8")
        used += len(encoded) + len(value)
        if used > MAX_XATTR_BYTES:
            raise OSError(f"extended attributes exceed {MAX_XATTR_BYTES} bytes")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def _check_deadline(deadline: float):
    if time.monotonic() > deadline:
        raise TimeoutError("extended-metadata operation exceeded elapsed-time limit")


def _configure_acl_api(library):
    library.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    library.acl_get_fd_np.restype = ctypes.c_void_p
    library.acl_to_text.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t),
    ]
    library.acl_to_text.restype = ctypes.c_void_p
    # macOS declares int acl_free(void *).  Leaving ctypes' default integer
    # argument conversion here truncates 64-bit pointers and can segfault.
    library.acl_free.argtypes = [ctypes.c_void_p]
    library.acl_free.restype = ctypes.c_int


def _bounded_names(raw: bytes, deadline: float) -> list[bytes]:
    names = []
    start = 0
    while start < len(raw):
        _check_deadline(deadline)
        end = raw.find(b"\0", start)
        if end < 0:
            raise OSError("malformed extended-attribute name list")
        if end > start:
            names.append(raw[start:end])
            if len(names) > MAX_XATTRS:
                raise OSError(f"file has more than {MAX_XATTRS} extended attributes")
        start = end + 1
    return names


def _items_fd(descriptor: int, *, deadline: float) -> list[tuple[str, bytes]]:
    _check_deadline(deadline)
    if sys.platform not in ("darwin", "linux"):
        raise OSError("extended-attribute integrity APIs are unavailable")
    library = ctypes.CDLL(None, use_errno=True)
    list_args = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
    if sys.platform == "darwin":
        list_args.append(ctypes.c_int)
    library.flistxattr.argtypes = list_args
    library.flistxattr.restype = ctypes.c_ssize_t
    def list_call(buffer, size):
        if sys.platform == "darwin":
            return library.flistxattr(descriptor, buffer, size, 0)
        return library.flistxattr(descriptor, buffer, size)
    size = list_call(None, 0)
    _check_deadline(deadline)
    if size < 0 or size > MAX_XATTR_BYTES:
        raise OSError(ctypes.get_errno(), "cannot enumerate extended attributes")
    buffer = ctypes.create_string_buffer(size) if size else None
    if size:
        read_size = list_call(buffer, size)
        _check_deadline(deadline)
        if read_size != size:
            raise OSError(ctypes.get_errno(), "cannot read extended-attribute names")
    names = _bounded_names(buffer.raw if size else b"", deadline)
    get_args = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]
    if sys.platform == "darwin":
        get_args.extend((ctypes.c_uint32, ctypes.c_int))
    library.fgetxattr.argtypes = get_args
    library.fgetxattr.restype = ctypes.c_ssize_t
    def get_call(name, value, size):
        if sys.platform == "darwin":
            return library.fgetxattr(descriptor, name, value, size, 0, 0)
        return library.fgetxattr(descriptor, name, value, size)
    items = []
    used = sum(len(name) for name in names)
    for name in names:
        _check_deadline(deadline)
        size = get_call(name, None, 0)
        _check_deadline(deadline)
        if size < 0 or used + size > MAX_XATTR_BYTES:
            raise OSError(ctypes.get_errno(), "cannot size extended attribute")
        value = ctypes.create_string_buffer(size) if size else None
        if size:
            read_size = get_call(name, value, size)
            _check_deadline(deadline)
            if read_size != size:
                raise OSError(ctypes.get_errno(), "cannot read extended attribute")
        items.append((name.decode("utf-8"), value.raw if size else b""))
        used += size
    acl = _acl_bytes(library, descriptor, deadline) if sys.platform == "darwin" else None
    if acl is not None:
        if used + len(acl) > MAX_XATTR_BYTES:
            raise OSError(f"extended attributes exceed {MAX_XATTR_BYTES} bytes")
        items.append(("@acl", acl))
    return sorted(items)


def _acl_bytes(library, descriptor: int, deadline: float) -> bytes | None:
    _check_deadline(deadline)
    _configure_acl_api(library)
    acl = library.acl_get_fd_np(descriptor, 0x100)
    _check_deadline(deadline)
    if not acl:
        if ctypes.get_errno() == errno.ENOENT:
            return None
        raise OSError(ctypes.get_errno(), "cannot read file ACL")
    try:
        length = ctypes.c_ssize_t()
        text = library.acl_to_text(acl, ctypes.byref(length))
        _check_deadline(deadline)
        if not text or length.value < 0 or length.value > MAX_XATTR_BYTES:
            raise OSError(ctypes.get_errno(), "cannot serialize file ACL")
        try:
            value = ctypes.string_at(text, length.value)
            _check_deadline(deadline)
            return value
        finally:
            library.acl_free(text)
            _check_deadline(deadline)
    finally:
        library.acl_free(acl)
        _check_deadline(deadline)
