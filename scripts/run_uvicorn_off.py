import os
import sys
import uvicorn

# Ensure project root on sys.path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fbroom import main as appmod

if __name__ == '__main__':
    print('run_uvicorn_off: uvicorn version', uvicorn.__version__)
    # Force lifespan off to avoid blocking startup during debugging
    uvicorn.run(appmod.app, host='127.0.0.1', port=3009, log_level='debug', lifespan='off')
