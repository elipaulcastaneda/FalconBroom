import sys
import os
import uvicorn

# Ensure project root is on sys.path so `fbroom` package imports correctly
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fbroom import main as appmod


if __name__ == '__main__':
    # Run uvicorn in-process (no reload worker subprocesses) so logs and socket binding
    # happen in this process for easier debugging.
    import logging
    import subprocess
    import textwrap
    print('run_uvicorn_single: uvicorn version', uvicorn.__version__)
    logging.basicConfig(level=logging.DEBUG)
    # Startup guard: refuse to start if another python/uvicorn process from
    # this repository is already running. This avoids confusing WinError 10048
    # caused by accidental concurrent starts.
    def _port_listener_info(port):
        """Return list of (pid, proto, local_addr, state) listening on port (Windows netstat parsing).
        Empty list if none."""
        listeners = []
        cur_pid = os.getpid()
        try:
            out = subprocess.check_output('netstat -ano', shell=True, text=True, stderr=subprocess.DEVNULL)
            for ln in out.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                # Lines look like: TCP    127.0.0.1:3009     0.0.0.0:0     LISTENING     1234
                parts = ln.split()
                if len(parts) < 5:
                    continue
                proto = parts[0]
                local = parts[1]
                state = parts[3] if len(parts) >= 4 else ''
                pid_part = parts[-1]
                if local.endswith(f':{port}') and state.upper() == 'LISTENING':
                    try:
                        pid = int(pid_part)
                    except Exception:
                        continue
                    if pid != cur_pid:
                        listeners.append((pid, proto, local, state))
        except Exception:
            pass
        return listeners

    listeners = _port_listener_info(3009)
    if listeners:
        msg = [
            'Refusing to start: port 3009 already in use by another process.',
            'Stop the existing process(es) and retry, or use a different port.',
            '',
            'Detected listeners:'
        ]
        for pid, proto, local, state in listeners:
            # Try to get commandline for pid
            try:
                cmdline = subprocess.check_output(f'wmiC process where "ProcessId={pid}" get CommandLine', shell=True, text=True, stderr=subprocess.DEVNULL)
            except Exception:
                cmdline = '<unknown>'
            msg.append(f'  PID {pid}: {local} {state} cmd: {cmdline.strip()}')
        print(textwrap.dedent('\n'.join(msg)), flush=True)
        sys.exit(1)
    # Allow disabling ASGI lifespan for testing via env var LIFESPAN_OFF=1
    # Default the dev runner to disable ASGI lifespan to avoid the observed
    # race where the app logs startup but the OS does not yet report the
    # listening socket. A developer can override by explicitly setting
    # LIFESPAN_OFF=0 or LIFESPAN_ON=1 in their environment.
    if 'LIFESPAN_OFF' not in os.environ and os.environ.get('LIFESPAN_ON') not in ('1', 'true', 'True'):
        os.environ['LIFESPAN_OFF'] = '1'
    lifespan = 'off' if os.environ.get('LIFESPAN_OFF') in ('1', 'true', 'True') else 'on'

    # Helper to check whether the OS reports the port as LISTENING.
    import time
    import threading

    def _is_port_listening(port):
        # Prefer PowerShell Get-NetTCPConnection on Windows for reliable ownership info.
        try:
            # Use PowerShell to list listening ports for the specified port
            ps = subprocess.run([
                "powershell", "-NoProfile", "-Command",
                f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Where-Object {{$_.State -eq 'Listen' -or $_.State -eq 'Listenning'}} | Format-Table -AutoSize -Property LocalAddress,LocalPort,State,OwningProcess"
            ], capture_output=True, text=True, timeout=2)
            if ps.returncode == 0 and ps.stdout and str(port) in ps.stdout:
                return True
        except Exception:
            pass
        # Fallback to netstat regex parse
        try:
            out = subprocess.check_output('netstat -ano', shell=True, text=True, stderr=subprocess.DEVNULL)
            for ln in out.splitlines():
                if f':{port}' in ln and 'LISTENING' in ln.upper():
                    return True
        except Exception:
            pass
        return False

    def _can_connect(host, port, timeout=0.5):
        import socket
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False

    # Write a PID file so other helper scripts can locate and stop this run if needed.
    pid_path = os.path.join(ROOT, 'run_uvicorn_single.pid')
    try:
        with open(pid_path, 'w', encoding='utf-8') as pf:
            pf.write(str(os.getpid()))
    except Exception:
        pass

    # Install atexit and signal handlers to remove PID file on termination.
    import atexit
    def _cleanup():
        try:
            if os.path.exists(pid_path):
                os.remove(pid_path)
        except Exception:
            pass
    atexit.register(_cleanup)

    # Run uvicorn in a background thread so we can poll the OS for the
    # listening socket, then join the thread to block until shutdown. This
    # preserves the ability to observe the listening port while still letting
    # the process exit cleanly and run atexit handlers.
    import re
    import logging

    # Event and holder to communicate observed child pid from uvicorn logs
    server_started_event = threading.Event()
    server_child_pid = {'pid': None}

    class _WatchHandler(logging.Handler):
        def emit(self, record):
            msg = self.format(record)
            # Look for lines like: "Started server process [23220]"
            m = re.search(r"Started server process \[(\d+)\]", msg)
            if m:
                try:
                    server_child_pid['pid'] = int(m.group(1))
                except Exception:
                    server_child_pid['pid'] = None
                server_started_event.set()
                return
            if 'Uvicorn running on' in msg:
                # Parent or single-process run indicates server is up
                server_started_event.set()

    # Attach handler to root logger so uvicorn messages are observed
    root_logger = logging.getLogger()
    watch_handler = _WatchHandler()
    watch_handler.setLevel(logging.DEBUG)
    watch_handler.setFormatter(logging.Formatter('%(message)s'))
    root_logger.addHandler(watch_handler)
    # Also add a file handler to persist verbose runtime logs for debugging
    try:
        import builtins
        fh = logging.FileHandler(os.path.join(ROOT, 'run_uv_lifespan_off.log'), encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        root_logger.addHandler(fh)
        # We rely on the file handler attached to root logger to persist logs.
        # Avoid overriding builtins.print to prevent recursion issues.
    except Exception:
        pass

    def _run_uvicorn():
        uvicorn.run(appmod.app, host='127.0.0.1', port=3009, log_level='debug', lifespan=lifespan)

    t = threading.Thread(target=_run_uvicorn, daemon=False)
    t.start()

    try:
        wait_seconds = float(os.environ.get('WAIT_LISTEN_SEC', '10'))
    except Exception:
        wait_seconds = 10.0
    try:
        retries = int(os.environ.get('WAIT_LISTEN_RETRIES', '5'))
    except Exception:
        retries = 1
    poll_interval = 0.1
    observed = False
    for attempt in range(1, max(1, retries) + 1):
        waited = 0.0
        # Prefer detecting uvicorn log marker first (child pid or "Uvicorn running on").
        # Wait up to wait_seconds for a log hint, but continue polling netstat too.
        while waited < wait_seconds:
            if server_started_event.is_set():
                # if we captured a child pid, check specifically for that PID
                pid = server_child_pid.get('pid')
                if pid:
                    # check netstat for this PID listening on the port
                    try:
                        out = subprocess.check_output('netstat -ano', shell=True, text=True, stderr=subprocess.DEVNULL)
                        for ln in out.splitlines():
                            if f':{3009}' in ln and ln.strip().upper().endswith(str(pid)):
                                observed = True
                                break
                    except Exception:
                        pass
                else:
                    # fallback: check for any LISTENING on the port
                    if _is_port_listening(3009):
                        observed = True
                if observed:
                    break
            else:
                if _is_port_listening(3009):
                    observed = True
                    break
            time.sleep(poll_interval)
            waited += poll_interval
        if observed:
            break
        if attempt < retries:
            print(f'run_uvicorn_single: port 3009 not observed after {wait_seconds}s, retrying ({attempt}/{retries})...', flush=True)

    if observed:
        print(f'run_uvicorn_single: starting uvicorn on 127.0.0.1:3009 lifespan={lifespan}', flush=True)
    else:
        msg = f'run_uvicorn_single: uvicorn started but port 3009 not observed as LISTENING after {wait_seconds}s x {retries} attempts (continuing)'
        print(msg, flush=True)

    # Block until uvicorn exits (thread runs the server)
    try:
        t.join()
    except KeyboardInterrupt:
        pass
