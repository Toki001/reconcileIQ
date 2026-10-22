"""Shared resource and Linux network restrictions for untrusted document children."""

import ctypes
import errno
import os
import platform
import resource
import sys


class SandboxUnavailable(Exception):
    pass


def restrict() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (20, 21))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (24 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.umask(0o077)
    if sys.platform == "linux":
        # Deny network syscalls in this child and all OCR descendants. This is not a
        # complete filesystem sandbox: the container's non-root filesystem is still readable.
        calls = {
            "x86_64": [41, 42, 43, 49, 50, 53, 288],
            "aarch64": [198, 199, 200, 201, 202, 203, 242],
        }.get(platform.machine())
        if calls is None:
            raise SandboxUnavailable("PARSER_SANDBOX_UNAVAILABLE")

        class Filter(ctypes.Structure):
            _fields_ = [
                ("code", ctypes.c_ushort),
                ("jt", ctypes.c_ubyte),
                ("jf", ctypes.c_ubyte),
                ("k", ctypes.c_uint),
            ]

        class Program(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

        instructions = [Filter(0x20, 0, 0, 0)]
        for call in calls:
            instructions += [Filter(0x15, 0, 1, call), Filter(0x06, 0, 0, 0x50000 | errno.EPERM)]
        instructions.append(Filter(0x06, 0, 0, 0x7FFF0000))
        filters = (Filter * len(instructions))(*instructions)
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(38, 1, 0, 0, 0) or libc.prctl(
            22, 2, ctypes.byref(Program(len(filters), filters))
        ):
            raise SandboxUnavailable("PARSER_SANDBOX_UNAVAILABLE")
