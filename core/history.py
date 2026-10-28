"""SQLite-backed history of files successfully downloaded by the application."""
from pathlib import Path
import sqlite3
from datetime import datetime


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
                    downloaded_at TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_downloads_date ON downloads(downloaded_at DESC)")

    def add_files(self, task_id, platform, title, source_url, file_paths):
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        records = []
        for value in file_paths:
            path = Path(value)
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            records.append((str(task_id), str(platform), str(title or path.stem), str(source_url), str(path.resolve()), size, timestamp))
        if records:
            with self._connect() as connection:
                connection.executemany("""
                    INSERT INTO downloads (task_id, platform, title, source_url, file_path, file_size, downloaded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_path) DO UPDATE SET
                        task_id=excluded.task_id, platform=excluded.platform, title=excluded.title,
                        source_url=excluded.source_url, file_size=excluded.file_size,
                        downloaded_at=excluded.downloaded_at
                """, records)

    def list_files(self, search=""):
        query = "%" + search.strip() + "%"
        with self._connect() as connection:
            return connection.execute("""
                SELECT id, task_id, platform, title, source_url, file_path, file_size, downloaded_at
                FROM downloads
                WHERE title LIKE ? OR file_path LIKE ? OR source_url LIKE ? OR platform LIKE ?
                ORDER BY downloaded_at DESC, id DESC
            """, (query, query, query, query)).fetchall()

    def delete_record(self, record_id):
        with self._connect() as connection:
            connection.execute("DELETE FROM downloads WHERE id = ?", (int(record_id),))
