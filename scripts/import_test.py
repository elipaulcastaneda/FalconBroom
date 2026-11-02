import importlib, sys
try:
    import fbroom.main as m
    print('imported')
except Exception as e:
    import traceback
    traceback.print_exc()
    print('ERROR', e)
