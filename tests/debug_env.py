import os, sys
print('CWD:', os.getcwd())
print('FILES:', os.listdir('.'))
print('sys.path:')
for p in sys.path:
    print(' -', p)
print('\nPackage dirs matching fbroom:')
for root, dirs, files in os.walk('.'):
    if 'fbroom' in dirs:
        print('found at', os.path.join(root,'fbroom'))
        break
