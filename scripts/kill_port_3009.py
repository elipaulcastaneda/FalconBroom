import subprocess
import sys

def main():
    try:
        out = subprocess.check_output(['netstat', '-ano'], shell=False)
    except Exception:
        out = subprocess.check_output('netstat -ano', shell=True)
    s = out.decode(errors='ignore')
    pids = set()
    for line in s.splitlines():
        if ':3009' in line:
            parts = line.split()
            if parts:
                try:
                    pid = int(parts[-1])
                    pids.add(pid)
                except Exception:
                    continue
    if not pids:
        print('No process found listening on 3009')
        return 0
    for pid in sorted(pids):
        try:
            subprocess.check_call(['taskkill', '/PID', str(pid), '/F'])
            print('Killed', pid)
        except Exception as e:
            print('Failed to kill', pid, e)
    return 0

if __name__ == '__main__':
    sys.exit(main())
