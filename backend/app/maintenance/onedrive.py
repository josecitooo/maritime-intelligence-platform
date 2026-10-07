"""Push the archive to the institutional remote — or do not, and say so.

There is no OneDrive integration in this project. There is a directory of
Parquet files and, possibly, `rclone`. The distinction matters because the
failure mode of pretending otherwise is an export that quietly never leaves
the machine: rows are deleted, the ledger says "archived", and the only copy
is on a disk somebody is about to rebuild.

So this is called only when `RCLONE_REMOTE` is set, and it raises when rclone
is absent or unhappy. Nothing here converts a failure into a skip.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def upload(export_dir: Path, remote: str) -> None:
    """Copy every file rclone does not already have at `remote`.

    `rclone copy` compares size and modification time, so a file that made it
    across last time is left alone. That is what makes this the retry for an
    earlier failure as well as the upload for a fresh one — it runs whether
    or not this maintenance run exported anything.

    Raises `FileNotFoundError` when rclone is not installed and
    `CalledProcessError` when it fails. Neither is caught here: an archive
    that never left the machine is an error, not a detail, and swallowing it
    is exactly the fiction this module exists to avoid.
    """
    subprocess.run(
        ["rclone", "copy", str(export_dir), remote],
        capture_output=True,
        text=True,
        check=True,
    )
