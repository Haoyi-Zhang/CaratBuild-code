#!/usr/bin/env python3
from pathlib import Path
import hashlib, json, locale, os, platform, sys, time
root=Path(__file__).resolve().parents[1]
def sha(p):
    h=hashlib.sha256();
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()
files={}
for p in sorted(root.rglob('*')):
    if p.is_file() and not any(x in p.parts for x in ('results','__pycache__','.git')):
        files[str(p.relative_to(root))]=sha(p)
out={'schema_version':1,'captured_unix':time.time(),'python_version':sys.version,'python_executable':sys.executable,'implementation':platform.python_implementation(),'platform':platform.platform(),'machine':platform.machine(),'locale':locale.getpreferredencoding(False),'hash_seed':os.environ.get('PYTHONHASHSEED'),'source_file_sha256':files}
(root/'results').mkdir(exist_ok=True)
(root/'results'/'environment.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
print(json.dumps({'source_files_hashed':len(files),'python':platform.python_version()}))
