#!/usr/bin/env python3
"""Cross-input and metamorphic robustness checks for preserved unified diffs.

This is deliberately independent of CARAT's accounting implementation.  It
checks that public-input evidence is not accepted only because of one exact
byte string and that malformed patches fail closed.  It is not a claim of
statistical generalization to production build workloads.
"""
from __future__ import annotations
from pathlib import Path
import hashlib, json, os, re, subprocess, sys

def split_files(text: str) -> list[str]:
    starts=[m.start() for m in re.finditer(r'(?m)^diff --git ',text)]
    if not starts: raise ValueError('no diff --git header')
    starts.append(len(text))
    return [text[starts[i]:starts[i+1]] for i in range(len(starts)-1)]

def parse_patch(text: str) -> dict[str,int]:
    blocks=split_files(text)
    files=hunks=added=deleted=0
    for block in blocks:
        head=block.splitlines()
        if not head or not head[0].startswith('diff --git '): raise ValueError('bad file header')
        if not re.search(r'(?m)^--- ',block) or not re.search(r'(?m)^\+\+\+ ',block): raise ValueError('missing old/new path')
        hs=list(re.finditer(r'(?m)^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@',block))
        if not hs: raise ValueError('file has no textual hunk')
        files+=1; hunks+=len(hs)
        for line in block.splitlines():
            if line.startswith('+') and not line.startswith('+++'): added+=1
            elif line.startswith('-') and not line.startswith('---'): deleted+=1
    return {'files':files,'hunks':hunks,'added_lines':added,'deleted_lines':deleted}

def main() -> int:
    root=Path(__file__).resolve().parents[1]
    inp=root/'inputs'/'public-patches'
    rows=[]; errors=[]
    for p in sorted(inp.glob('*.diff')):
        raw=p.read_bytes(); text=raw.decode('utf-8')
        baseline=parse_patch(text)
        variants={
            'lf':text,
            'crlf':text.replace('\n','\r\n'),
            'file_order_reversed':'\n'.join(reversed(split_files(text))),
            'terminal_newline_removed':text.rstrip('\n'),
        }
        observed={}
        for name,v in variants.items():
            try: observed[name]=parse_patch(v.replace('\r\n','\n'))
            except Exception as e: errors.append(f'{p.name}:{name}:{e}')
        for name,val in observed.items():
            if val!=baseline: errors.append(f'{p.name}:{name}:metamorphic count changed')
        malformed={
            'missing_new_path':re.sub(r'(?m)^\+\+\+ .*\n','',text,count=1),
            'missing_hunks':re.sub(r'(?m)^@@ .*?@@.*\n','',text),
            'not_a_diff':'ordinary text\n',
        }
        rejected=[]
        for name,v in malformed.items():
            try: parse_patch(v)
            except Exception: rejected.append(name)
        if len(rejected)!=len(malformed): errors.append(f'{p.name}:malformed input accepted')
        rows.append({'file':p.name,'sha256':hashlib.sha256(raw).hexdigest(),'baseline':baseline,'metamorphic_variants':sorted(observed),'malformed_variants_rejected':sorted(rejected)})
    # Detect accidental exact-fixture coupling in executable source.
    coupling=[]
    known=[r['sha256'] for r in rows]
    for p in root.rglob('*.py'):
        if p==Path(__file__): continue
        try: t=p.read_text(encoding='utf-8')
        except Exception: continue
        for digest in known:
            if digest in t: coupling.append({'file':str(p.relative_to(root)),'literal':digest})
    if coupling: errors.append('executable source hard-codes a public-input digest')
    out={'schema_version':1,'status':'PASS' if not errors else 'FAIL','scope':'cross-input parser and artifact-pipeline robustness; not production-workload representativeness','public_patch_count':len(rows),'patches':rows,'exact_fixture_coupling':coupling,'errors':errors}
    (root/'results').mkdir(exist_ok=True)
    (root/'results'/'robustness_audit.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
    print(json.dumps(out,indent=2))
    return 0 if not errors and len(rows)>=3 else 1
if __name__=='__main__': raise SystemExit(main())
