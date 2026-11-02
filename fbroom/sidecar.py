"""Entry point for the packaged desktop backend (PyInstaller sidecar).

Usage: falconbroom-backend --port 51234 --data-dir "<app data dir>" --parent-pid 1234
"""
import argparse
import os
import sys
import threading
import time


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            # WAIT_TIMEOUT (0x102) means the process is still running.
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x102
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _exit_when_parent_dies(pid: int):
    # A one-file PyInstaller build runs behind a bootloader process, so killing
    # that process would orphan us. Watch the desktop app's PID instead.
    while _pid_alive(pid):
        time.sleep(1.5)
    os._exit(0)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="falconbroom-backend")
    parser.add_argument("--port", type=int, default=3009)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--parent-pid", type=int, default=None)
    args = parser.parse_args(argv)

    if args.data_dir:
        # The backend stores everything under ./data, so run from the app data dir.
        os.makedirs(args.data_dir, exist_ok=True)
        os.chdir(args.data_dir)

    if args.parent_pid:
        threading.Thread(target=_exit_when_parent_dies, args=(args.parent_pid,), daemon=True).start()

    import uvicorn
    from fbroom.main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", lifespan="off")


if __name__ == "__main__":
    main()
