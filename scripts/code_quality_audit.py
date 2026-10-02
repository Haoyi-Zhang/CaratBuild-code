#!/usr/bin/env python3
from pathlib import Path
import ast, json, os, re, sys
root=Path(__file__).resolve().parents[1]
rows=[]; syntax=[]; risky=[]; todos=[]; test_functions=0; source_loc=test_loc=0
for p in sorted(root.rglob('*.py')):
    if any(x in p.parts for x in ('__pycache__','.git','.venv','venv','results')): continue
    rel=p.relative_to(root); text=p.read_text(encoding='utf-8',errors='replace'); loc=sum(1 for x in text.splitlines() if x.strip() and not x.lstrip().startswith('#'))
    is_test=any(part in {'test','tests','testing'} for part in rel.parts) or p.name.startswith('test_')
    if is_test: test_loc+=loc
    else: source_loc+=loc
    try: tree=ast.parse(text)
    except Exception as e: syntax.append({'file':str(rel),'error':repr(e)}); continue
    funcs=sum(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) for n in ast.walk(tree)); classes=sum(isinstance(n,ast.ClassDef) for n in ast.walk(tree))
    tests=sum(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test') for n in ast.walk(tree)); test_functions+=tests
    for n in ast.walk(tree):
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in {'eval','exec'}: risky.append({'file':str(rel),'line':n.lineno,'construct':n.func.id})
        if isinstance(n,ast.Raise) and isinstance(n.exc,ast.Call) and isinstance(n.exc.func,ast.Name) and n.exc.func.id=='NotImplementedError': todos.append({'file':str(rel),'line':n.lineno,'kind':'NotImplementedError'})
    for i,line in enumerate(text.splitlines(),1):
        if re.search(r'\b(?:TODO|FIXME|XXX)\b',line,re.I): todos.append({'file':str(rel),'line':i,'kind':'marker','text':line.strip()[:200]})
    rows.append({'file':str(rel),'loc':loc,'is_test':is_test,'functions':funcs,'classes':classes,'test_functions':tests})
repro=(root/'scripts'/'reproduce.sh').read_text(encoding='utf-8') if (root/'scripts'/'reproduce.sh').exists() else ''
network_bootstrap=[]
for i,line in enumerate(repro.splitlines(),1):
    if re.search(r'\b(?:curl|wget|pip\s+install|git\s+clone|npm\s+install)\b',line) and not line.lstrip().startswith('#'): network_bootstrap.append({'line':i,'text':line.strip()})
errors=[]
if syntax: errors.append('Python syntax errors')
if test_functions<1: errors.append('no directly discoverable test functions')
if network_bootstrap: errors.append('reproduction script performs network/bootstrap installation')
if not (root/'LICENSE').exists() and not list(root.glob('LICENSE*')): errors.append('artifact license missing')
out={'schema_version':1,'status':'PASS' if not errors else 'FAIL','errors':errors,'python_files':len(rows),'source_loc':source_loc,'test_loc':test_loc,'direct_test_functions':test_functions,'syntax_errors':syntax,'risky_dynamic_execution':risky,'unfinished_markers':todos,'network_or_install_commands_in_reproduce':network_bootstrap,'files':rows}
(root/'results').mkdir(exist_ok=True)
(root/'results'/'code_quality_audit.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
print(json.dumps({k:out[k] for k in ['status','errors','python_files','source_loc','test_loc','direct_test_functions']},indent=2))
if errors: raise SystemExit(1)
