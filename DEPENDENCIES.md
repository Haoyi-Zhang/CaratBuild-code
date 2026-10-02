# Dependency statement

The reproduction command performs no package installation and no network access. `scripts/dependency_audit.py` parses every Python file and records top-level import candidates in `results/dependency_audit.json`; this separates standard-library and project-local imports from possible third-party imports visible in the shipped source. The exact interpreter and platform family used for a run are written by `scripts/capture_environment.py` without usernames, hostnames, or absolute paths.

A successful clean reproduction is the authoritative compatibility check. The artifact does not claim byte-identical timing or memory values across platforms.
