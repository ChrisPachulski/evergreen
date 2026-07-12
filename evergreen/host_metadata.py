"""Fail-closed, bounded extended-metadata integrity for host files."""

import ctypes
import errno
import hashlib
import os
from pathlib import Path
import stat
import sys

try:
    import posix as _posix
except ImportError:  # pragma: no cover
    _posix = None


MAX_XATTRS = 100
MAX_XATTR_BYTES = 1024 * 1024


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
        _clone_xattrs(source_fd, destination_fd)
    os.fchmod(destination_fd, stat.S_IMODE(source.st_mode))
    os.utime(destination_fd, ns=(atime_ns, mtime_ns))
    os.fsync(destination_fd)


def _clone_xattrs(source_fd: int, destination_fd: int):
    if not all(hasattr(os, name) for name in ("listxattr", "getxattr", "setxattr")):
        raise OSError("extended-attribute APIs are unavailable")
    unsupported = {
        errno.ENODATA, getattr(errno, "ENOATTR", errno.ENODATA),
        errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
    }
    try:
        names = set(os.listxattr(source_fd))
    except OSError as error:
        if error.errno not in unsupported:
            raise
        names = set()
    if len(names) > MAX_XATTRS:
        raise OSError(f"file has more than {MAX_XATTRS} extended attributes")
    used = 0
    for name in sorted(names):
        value = os.getxattr(source_fd, name)
        used += len(name.encode("utf-8")) + len(value)
        if used > MAX_XATTR_BYTES:
            raise OSError(f"extended attributes exceed {MAX_XATTR_BYTES} bytes")
        os.setxattr(destination_fd, name, value)


def digest_path(path: Path, expected) -> str:
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        actual = os.fstat(descriptor)
        identity = lambda item: (
            stat.S_IFMT(item.st_mode), item.st_dev, item.st_ino,
            stat.S_IMODE(item.st_mode), item.st_nlink, item.st_uid, item.st_gid,
        )
        if identity(actual) != identity(expected):
            raise OSError(f"metadata changed while snapshotting: {path}")
        return digest_fd(descriptor)
    finally:
        os.close(descriptor)


def digest_fd(descriptor: int) -> str:
    items = _items_fd(descriptor)
    if len(items) > MAX_XATTRS:
        raise OSError(f"file has more than {MAX_XATTRS} extended attributes")
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


def _items_fd(descriptor: int) -> list[tuple[str, bytes]]:
    if all(hasattr(os, name) for name in ("listxattr", "getxattr")):
        return sorted(
            (name, os.getxattr(descriptor, name))
            for name in os.listxattr(descriptor)
        )
    if sys.platform != "darwin":
        raise OSError("extended-attribute integrity APIs are unavailable")
    library = ctypes.CDLL(None, use_errno=True)
    library.flistxattr.argtypes = [
        ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
    ]
    library.flistxattr.restype = ctypes.c_ssize_t
    size = library.flistxattr(descriptor, None, 0, 0)
    if size < 0 or size > MAX_XATTR_BYTES:
        raise OSError(ctypes.get_errno(), "cannot enumerate extended attributes")
    buffer = ctypes.create_string_buffer(size) if size else None
    if size and library.flistxattr(descriptor, buffer, size, 0) != size:
        raise OSError(ctypes.get_errno(), "cannot read extended-attribute names")
    names = buffer.raw.rstrip(b"\0").split(b"\0") if size else []
    library.fgetxattr.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_uint32, ctypes.c_int,
    ]
    library.fgetxattr.restype = ctypes.c_ssize_t
    items = []
    for name in names:
        size = library.fgetxattr(descriptor, name, None, 0, 0, 0)
        if size < 0 or size > MAX_XATTR_BYTES:
            raise OSError(ctypes.get_errno(), "cannot size extended attribute")
        value = ctypes.create_string_buffer(size) if size else None
        if size and library.fgetxattr(descriptor, name, value, size, 0, 0) != size:
            raise OSError(ctypes.get_errno(), "cannot read extended attribute")
        items.append((name.decode("utf-8"), value.raw if size else b""))
    acl = _acl_bytes(library, descriptor)
    if acl is not None:
        items.append(("@acl", acl))
    return sorted(items)


def _acl_bytes(library, descriptor: int) -> bytes | None:
    library.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    library.acl_get_fd_np.restype = ctypes.c_void_p
    acl = library.acl_get_fd_np(descriptor, 0x100)
    if not acl:
        if ctypes.get_errno() == errno.ENOENT:
            return None
        raise OSError(ctypes.get_errno(), "cannot read file ACL")
    try:
        length = ctypes.c_ssize_t()
        library.acl_to_text.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t),
        ]
        library.acl_to_text.restype = ctypes.c_void_p
        text = library.acl_to_text(acl, ctypes.byref(length))
        if not text or length.value > MAX_XATTR_BYTES:
            raise OSError(ctypes.get_errno(), "cannot serialize file ACL")
        try:
            return ctypes.string_at(text, length.value)
        finally:
            library.acl_free(text)
    finally:
        library.acl_free(acl)
