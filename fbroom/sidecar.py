"""Entry point for the packaged desktop backend (PyInstaller sidecar).

Usage: falconbroom-backend --port 51234 --data-dir "<app data dir>" --parent-pid 1234
"""
import argparse
import ipaddress
import os
import sys
import threading
import time
from pathlib import Path


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

    secret = os.environ.get("FALCONBROOM_DESKTOP_SECRET", "")
    if len(secret) != 64 or any(c not in "0123456789abcdef" for c in secret):
        parser.error("A desktop session secret must be supplied by the launcher")
    os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)

    try:
        local_backend = ipaddress.ip_address(args.host.strip("[]")).is_loopback
    except ValueError:
        local_backend = args.host.lower() == "localhost"
    os.environ["FALCONBROOM_LOCAL_MODE"] = "1" if local_backend else "0"
    os.environ["ENV"] = "development" if local_backend else "production"

    if args.data_dir:
        # The backend stores everything under ./data, so run from the app data dir.
        os.makedirs(args.data_dir, exist_ok=True)
        os.chdir(args.data_dir)

    if args.parent_pid:
        threading.Thread(target=_exit_when_parent_dies, args=(args.parent_pid,), daemon=True).start()

    import uvicorn
    from fbroom import desktop_vault
    from fbroom.desktop_request_gate import desktop_startup_gate
    from fbroom import main as fb

    if local_backend and os.environ.get("FALCONBROOM_CONTROL_ONLY") != "1":
        with desktop_startup_gate():
            desktop_vault.initialize(os.path.join(os.getcwd(), "data"))
            if not (Path("data") / ".test-deletion-pending").exists():
                desktop_vault.cleanup_abandoned_scratch()
                desktop_vault.migrate_plaintext_files(
                    (fb.UPLOAD_DIR, fb.OUTPUTS_DIR, fb.RECIPES_DIR, fb.HISTORY_DIR)
                )
                legacy_output = Path.cwd() / "output_from_text.csv"
                if legacy_output.is_file() or legacy_output.is_symlink():
                    desktop_vault.migrate_plaintext_file(
                        legacy_output, fb.OUTPUTS_DIR / "output_from_text_legacy.csv"
                    )

    uvicorn.run(fb.app, host=args.host, port=args.port, log_level="warning", lifespan="off")


if __name__ == "__main__":
    main()
