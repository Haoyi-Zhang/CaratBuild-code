#!/usr/bin/env python3
from pathlib import Path
import re
root = Path(__file__).resolve().parents[1]
patterns = [
    (re.compile(r"/home/[A-Za-z0-9_.-]+"), "<HOME>"),
    (re.compile(r"/Users/[A-Za-z0-9_.-]+"), "<HOME>"),
    (re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+"), "<HOME>"),
]
changed = 0
text_ext={".json", ".csv", ".txt", ".md", ".log", ".py", ".sh", ".tex", ".bib", ".rst", ".yml", ".yaml", ".toml"}
for f in root.rglob("*"):
    if not f.is_file() or f.stat().st_size > 20_000_000 or f.suffix.lower() not in text_ext:
        continue
    if any(x in f.parts for x in (".git", "__pycache__")):
        continue
    try:
        text = f.read_text(encoding="utf-8")
    except Exception:
        continue
    new = text
    for pattern, replacement in patterns:
        new = pattern.sub(replacement, new)
    if new != text:
        f.write_text(new, encoding="utf-8")
        changed += 1
print(f"sanitized {changed} text files")
