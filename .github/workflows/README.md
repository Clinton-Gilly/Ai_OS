# CI

`ci.yml` runs on every push and pull request: ruff, the pytest suite, and a CLI
smoke test, across Python 3.10 and 3.12 on both Ubuntu and Windows.

Windows is in the matrix on purpose — it is the target platform, and the
Windows-only code paths (Recycle Bin, `tasklist`, the HKCU startup entry) are
guarded so the suite stays green on Linux too.
