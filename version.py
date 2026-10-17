import os
import subprocess
from pathlib import Path
from shutil import which


def source_revision() -> str:
    if release := os.getenv('SENTRY_RELEASE'):
        return release
    root = Path(__file__).parent
    git = which('git')
    if git is None:
        return 'unknown'
    try:
        status = subprocess.check_output(  # noqa: S603 - Git is discovered through PATH
            [git, 'status', '--porcelain=v2', '--branch'],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).splitlines()
    except OSError, subprocess.SubprocessError:
        return 'unknown'
    revision = next(
        (line.removeprefix('# branch.oid ') for line in status if line.startswith('# branch.oid ')), 'unknown'
    )
    if revision == '(initial)':
        return 'unknown'
    dirty = any(not line.startswith('# ') for line in status)
    return f'{revision}-dirty' if dirty else revision


VERSION = source_revision()
