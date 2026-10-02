#!/usr/bin/env python3
from pathlib import Path
import json,re
root=Path(__file__).resolve().parents[1]
scan=[]
for pat in ('*claim*','*evidence*','CURRENT-STATE.md','README.md'):
    scan.extend(root.rglob(pat))
scan=sorted({p for p in scan if p.is_file() and p.suffix.lower() in {'.md','.txt','.csv','.json'} and p.resolve() != (root/'results'/'claim_path_audit.json').resolve()})
refs=[]; missing=[]
pattern=re.compile(r'(?<!https://)(?<!http://)(?<![A-Za-z0-9_.-])((?:artifact/)?(?:results|inputs|proofs|scripts|src|tests?)/[A-Za-z0-9_./-]+\.[A-Za-z0-9_-]+)')
for p in scan:
    try: text=p.read_text(encoding='utf-8')
    except Exception: continue
    for m in pattern.finditer(text):
        token=m.group(1).rstrip('.,;:)`]}')
        candidates=[root/token,root/token.removeprefix('artifact/')]
        ok=any(x.exists() for x in candidates)
        row={'source':str(p.relative_to(root)),'line':text.count('\n',0,m.start())+1,'path':token,'exists':ok}
        refs.append(row)
        if not ok: missing.append(row)
out={'schema_version':1,'status':'PASS' if not missing else 'FAIL','files_scanned':[str(p.relative_to(root)) for p in scan],'reference_count':len(refs),'missing':missing,'references':refs}
(root/'results').mkdir(exist_ok=True)
(root/'results'/'claim_path_audit.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
print(json.dumps({'status':out['status'],'reference_count':len(refs),'missing_count':len(missing)},indent=2))
if missing: raise SystemExit(1)
