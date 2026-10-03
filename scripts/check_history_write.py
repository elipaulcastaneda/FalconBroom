from pathlib import Path
p=Path('data/history/test_write_from_agent.json')
p.write_text('{"ok":true}', encoding='utf-8')
print('wrote', p.resolve())
print('exists?', p.exists())
print('files in data/history:', len(list(Path('data/history').glob('*.json'))))
for f in sorted(Path('data/history').glob('*.json')):
    print(' -', f.name)
