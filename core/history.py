"""SQLite-backed history of files successfully downloaded by the application."""
from pathlib import Path
import sqlite3
from datetime import datetime
import re
import shutil
import subprocess


class DownloadHistory:
    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path else Path(__file__).resolve().parent.parent / "data" / "download_history.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL,
                    file_path TEXT NOT NULL UNIQUE,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    duration_seconds INTEGER,
                    downloaded_at TEXT NOT NULL
                )
            """)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(downloads)")}
            if "duration_seconds" not in columns:
                connection.execute("ALTER TABLE downloads ADD COLUMN duration_seconds INTEGER")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_downloads_date ON downloads(downloaded_at DESC)")
        # Repair older history rows that were saved before duration probing existed.
        self._backfill_missing_durations()

    @staticmethod
    def _probe_duration(file_path):
        """Read duration from the actual downloaded media file when metadata is absent."""
        path = Path(file_path)
        if not path.is_file() or path.suffix.lower() in {
            ".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".tif", ".tiff"
        }:
            return None

        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            try:
                result = subprocess.run(
                    [ffprobe, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                    capture_output=True, text=True, timeout=8, check=False,
                )
                value = float(result.stdout.strip()) if result.returncode == 0 else 0
                if value > 0:
                    return int(round(value))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass

        # imageio-ffmpeg ships an FFmpeg executable even when ffprobe is not installed.
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-i", str(path)],
                capture_output=True, text=True, timeout=8, check=False,
            )
            diagnostic = (result.stderr or "") + "\n" + (result.stdout or "")
            match = re.search(r"Duration:\s*(\d{2,}):(\d{2}):(\d{2}(?:\.\d+)?)", diagnostic)
            if match:
                hours, minutes, seconds = match.groups()
                duration = int(round(int(hours) * 3600 + int(minutes) * 60 + float(seconds)))
                return duration if duration > 0 else None
        except (ImportError, OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            pass
        return None

    def _backfill_missing_durations(self):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, file_path FROM downloads WHERE duration_seconds IS NULL"
            ).fetchall()
        for row in rows:
            duration = self._probe_duration(row["file_path"])
            if duration is not None:
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE downloads SET duration_seconds = ? WHERE id = ? AND duration_seconds IS NULL",
                        (duration, row["id"]),
                    )

    def add_files(self, task_id, platform, title, source_url, file_paths, duration_seconds=None):
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        records = []
        for value in file_paths:
            path = Path(value)
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            is_image = path.suffix.lower() in {
                ".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".tif", ".tiff"
            }
            media_duration = None if is_image else duration_seconds
            if not is_image and media_duration is None:
                media_duration = self._probe_duration(path)
            records.append((str(task_id), str(platform), str(title or path.stem), str(source_url), str(path.resolve()), size, media_duration, timestamp))
        if records:
            with self._connect() as connection:
                connection.executemany("""
                    INSERT INTO downloads (task_id, platform, title, source_url, file_path, file_size, duration_seconds, downloaded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_path) DO UPDATE SET
                        task_id=excluded.task_id, platform=excluded.platform, title=excluded.title,
                        source_url=excluded.source_url, file_size=excluded.file_size,
                        duration_seconds=excluded.duration_seconds, downloaded_at=excluded.downloaded_at
                """, records)

    def list_files(self, search=""):
        query = "%" + search.strip() + "%"
        with self._connect() as connection:
            return connection.execute("""
                SELECT id, task_id, platform, title, source_url, file_path, file_size, duration_seconds, downloaded_at
                FROM downloads
                WHERE title LIKE ? OR file_path LIKE ? OR source_url LIKE ? OR platform LIKE ?
                ORDER BY downloaded_at DESC, id DESC
            """, (query, query, query, query)).fetchall()

    def delete_record(self, record_id):
        with self._connect() as connection:
            connection.execute("DELETE FROM downloads WHERE id = ?", (int(record_id),))
