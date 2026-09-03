from __future__ import annotations

import ctypes
import os
import signal
import subprocess


def popen_options(*, detached: bool = False) -> dict[str, object]:
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        if detached:
            flags |= subprocess.DETACHED_PROCESS
        return {"creationflags": flags}
    return {"start_new_session": True}


def terminate(process: subprocess.Popen[object], *, force: bool) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        if force:
            process.kill()
        else:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        return
    os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)


def is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
