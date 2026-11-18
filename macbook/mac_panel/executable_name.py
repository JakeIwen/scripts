"""The base name of the file a process is running, from the kernel (`proc_pidpath`).

`ps` cannot give it: argv[0] is whatever the process put there (a title can carry arguments)
and the kernel's own name stops at 16 characters. Only the base name leaves this module;
the path itself, which can include a user's folder names, never does. Any failure (another
user's process, a process that exited, no libproc) is None, and the caller falls back to the
kernel's short name.
"""
import ctypes
from functools import cache
import os

LIBPROC = '/usr/lib/libproc.dylib'
# PROC_PIDPATHINFO_MAXSIZE in <libproc.h>.
PATH_BUFFER = 4 * 1024


@cache
def libproc():
    """The loaded library with `proc_pidpath` typed, or None where it cannot be loaded."""
    try:
        library = ctypes.CDLL(LIBPROC, use_errno=True)
        function = library.proc_pidpath
    except (OSError, AttributeError):
        return None
    function.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
    function.restype = ctypes.c_int
    return function


def executable_name(pid: int) -> str | None:
    """The running file's base name for pid, or None when the kernel will not say."""
    function = libproc()
    if function is None or not 0 < pid < 2 ** 31:
        return None
    buffer = ctypes.create_string_buffer(PATH_BUFFER)
    length = function(pid, buffer, PATH_BUFFER)
    if length <= 0:
        return None
    path = buffer.raw[:length].decode('utf-8', errors='replace')
    return os.path.basename(path) or None
