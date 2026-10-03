import subprocess,sys,os

def run(cmd):
    try:
        out = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT)
        print(f"$ {cmd}\n{out}")
    except subprocess.CalledProcessError as e:
        print(f"$ {cmd}\nRETURNED {e.returncode}\n{e.output}")
    except Exception as e:
        print(f"$ {cmd}\nERROR: {e}")

pfile = 'run_uvicorn_single.pid'
if os.path.exists(pfile):
    try:
        pid = open(pfile,'r').read().strip()
        print('PID file:', pid)
    except Exception as e:
        print('Could not read pid file', e)
        pid = None
else:
    print('PID file not found')
    pid = None

if pid:
    run(f'tasklist /FI "PID eq {pid}" /FO LIST')
    # try wmic for commandline
    run(f'wmic process where processid={pid} get CommandLine /FORMAT:LIST')

# netstat for port
run('netstat -ano | findstr ":3009"')

# tail the uvicorn log via existing script
if os.path.exists('scripts\\tail_uv_log.py'):
    run('py -3 scripts\\tail_uv_log.py')
else:
    print('tail script not found')
