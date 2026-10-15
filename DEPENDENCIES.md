# Dependency statement

Complete reproduction targets Linux with Bash and Python 3.12, matching
Ubuntu 24.04/Python 3.12 in `.github/workflows/scientific-checks.yml`.
It needs only the Python standard library, including POSIX `fcntl`, `resource`,
process signals and file/directory synchronization. Native Windows can run
selected portable tests and local storage controls, not the complete service
and crash-recovery workflow. Set `CARAT_PYTHON` if the required interpreter is
not available as `python`.

The reproduction command performs no package installation and no network access. `scripts/dependency_audit.py` parses every Python file and records top-level import candidates in `results/dependency_audit.json`; this separates standard-library and project-local imports from possible third-party imports visible in the shipped source. The exact interpreter and platform family used for a run are written by `scripts/capture_environment.py` without usernames, hostnames, or absolute paths.

A successful clean reproduction is the authoritative compatibility check. The artifact does not claim byte-identical timing or memory values across platforms.
