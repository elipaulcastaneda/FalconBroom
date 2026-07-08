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
    lifespan = 'off' if os.environ.get('LIFESPAN_OFF') in ('1', 'true', 'True') else 'on'
    print(f'run_uvicorn_single: starting uvicorn on 127.0.0.1:3009 lifespan={lifespan}', flush=True)
    uvicorn.run(appmod.app, host='127.0.0.1', port=3009, log_level='debug', lifespan=lifespan)
