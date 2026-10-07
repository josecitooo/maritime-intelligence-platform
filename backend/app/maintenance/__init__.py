"""Scheduled maintenance: the 7-day window, the archive, and the upload.

`export` reads `vessel_positions` past the retention cutoff, writes it out
and prunes it; `onedrive` hands the archive to rclone when `RCLONE_REMOTE`
is set. Both are driven by `IngestionWorker`'s maintenance loop, which is
what turns "keep 7 days" from a setting into a schedule.
"""
