"""rclone, and the promise that a failed upload is never a quiet one.

Uploading is the least dramatic part of retention and the easiest to fake: a
`try/except` around the call would keep the logs calm while the archive never
left the machine. These tests exist so that adding one is a deliberate act
rather than a tidy-up nobody notices.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app.maintenance.onedrive import upload


def test_it_copies_the_directory_to_the_configured_remote(tmp_path: Path) -> None:
    with patch("app.maintenance.onedrive.subprocess.run") as run:
        upload(tmp_path, "institutional:/Maritime")

    run.assert_called_once_with(
        ["rclone", "copy", str(tmp_path), "institutional:/Maritime"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_a_missing_rclone_is_an_error_not_a_silent_skip(tmp_path: Path) -> None:
    with (
        patch("app.maintenance.onedrive.subprocess.run", side_effect=FileNotFoundError),
        pytest.raises(FileNotFoundError),
    ):
        upload(tmp_path, "institutional:/Maritime")


def test_a_failed_copy_is_an_error_not_a_silent_skip(tmp_path: Path) -> None:
    failure = subprocess.CalledProcessError(1, ["rclone"], stderr="bandwidth limit")
    with (
        patch("app.maintenance.onedrive.subprocess.run", side_effect=failure),
        pytest.raises(subprocess.CalledProcessError),
    ):
        upload(tmp_path, "institutional:/Maritime")
