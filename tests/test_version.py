import os
import subprocess
import sys
from pathlib import Path


def test_image_release_identity_does_not_need_git(tmp_path):
    source = Path('version.py').read_text()
    (tmp_path / 'version.py').write_text(source)
    environment = {'PATH': '', 'SENTRY_RELEASE': '1234567890abcdef1234567890abcdef12345678ab'}
    result = subprocess.run(
        [sys.executable, '-c', 'from version import VERSION; print(VERSION)'],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == environment['SENTRY_RELEASE']


def test_checkout_version_reports_git_revision_and_dirty_state():
    environment = {k: v for k, v in os.environ.items() if k != 'SENTRY_RELEASE'}
    result = subprocess.run(
        [sys.executable, '-c', 'from version import VERSION; print(VERSION)'],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()  # noqa: S607
    dirty = subprocess.check_output(['git', 'status', '--porcelain'])  # noqa: S607
    assert result.stdout.strip() == (f'{revision}-dirty' if dirty else revision)
