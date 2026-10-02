#!/usr/bin/env python3
from pathlib import Path
import ast, json, sys
root=Path(__file__).resolve().parents[1]
stdlib=set(getattr(sys,'stdlib_module_names',()))
local={p.stem for p in root.rglob('*.py')} | {p.name for p in root.iterdir() if p.is_dir()}
imports={}
errors=[]
for p in sorted(root.rglob('*.py')):
    if any(x in p.parts for x in ('__pycache__','.git','.venv','venv')): continue
    try: tree=ast.parse(p.read_text(encoding='utf-8'))
    except Exception as e:
        errors.append({'file':str(p.relative_to(root)),'error':repr(e)}); continue
    names=set()
    for n in ast.walk(tree):
        if isinstance(n,ast.Import): names.update(a.name.split('.')[0] for a in n.names)
        elif isinstance(n,ast.ImportFrom) and n.module: names.add(n.module.split('.')[0])
    imports[str(p.relative_to(root))]=sorted(names)
all_names=sorted({n for v in imports.values() for n in v})
third=sorted(n for n in all_names if n not in stdlib and n not in local and n!='__future__')
out={'schema_version':1,'python':sys.version,'files_scanned':len(imports),'stdlib_or_local_imports':sorted(set(all_names)-set(third)),'potential_third_party_imports':third,'parse_errors':errors}
(root/'results').mkdir(exist_ok=True)
(root/'results'/'dependency_audit.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
if errors: raise SystemExit('Python parse errors found')
print(json.dumps({'files_scanned':len(imports),'potential_third_party_imports':third}))
