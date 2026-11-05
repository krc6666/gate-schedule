"""Prepare reproducible, self-hosted browser runtime assets without user data."""
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / '.tmp' / 'browser-public'
RUNTIME = PUBLIC / 'runtime'
PYODIDE = RUNTIME / 'pyodide'
CACHE = ROOT / '.tmp' / 'browser-wheel-cache'


def main():
    PYODIDE.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    package = ROOT / 'web' / 'node_modules' / 'pyodide'
    for name in ('pyodide.asm.js', 'pyodide.asm.wasm', 'pyodide.js', 'pyodide.mjs', 'python_stdlib.zip', 'pyodide-lock.json'):
        shutil.copyfile(package / name, PYODIDE / name)
    manifest = json.loads((ROOT / 'scripts' / 'browser-wheels.json').read_text(encoding='utf-8'))
    for item in manifest:
        cached = CACHE / item['file']
        if not cached.exists():
            with urllib.request.urlopen(item['url'], timeout=120) as response:
                content = response.read()
            if hashlib.sha256(content).hexdigest() != item['sha256']:
                raise ValueError(f"运行依赖校验失败：{item['file']}")
            cached.write_bytes(content)
        if hashlib.sha256(cached.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError(f"缓存校验失败：{item['file']}")
        shutil.copyfile(cached, PYODIDE / item['file'])
    shutil.copyfile(ROOT / 'web' / 'node_modules' / 'coi-serviceworker' / 'coi-serviceworker.js', PUBLIC / 'coi-serviceworker.js')
    (PUBLIC / '.nojekyll').write_bytes(b'')
    excluded = {'server.py', 'web.py', 'cli.py', 'portable.py', '__main__.py'}
    with ZipFile(RUNTIME / 'gate_schedule.zip', 'w', ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / 'gate_schedule').glob('*.py')):
            if path.name not in excluded:
                archive.writestr('gate_schedule/' + path.name, path.read_bytes())
    print('Browser runtime prepared; source only, no personnel/configuration/history.')


if __name__ == '__main__':
    main()
