from pathlib import Path
from datetime import datetime
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QComboBox, QTabWidget, QFormLayout, QLineEdit, QFileDialog, QMessageBox,
    QGroupBox, QAbstractItemView, QStatusBar
)
from PySide6.QtGui import QTextCursor, QDesktopServices
from PySide6.QtCore import QUrl

from core.settings import load_settings, save_settings
from core.history import DownloadHistory
from core.downloader import DownloadWorker, PlaylistResolver, detect_platform, is_youtube_playlist_url, parse_urls

APP_STYLE = r"""
QWidget { background: #17191f; color: #edf0f7; font-size: 13px; }
QMainWindow, QTabWidget::pane { background: #17191f; border: 1px solid #303440; }
QLabel#subtitle { color: #9ba3b5; }
QTextEdit, QLineEdit, QComboBox, QTableWidget {
    background: #22252e; color: #edf0f7; border: 1px solid #3a3f4d;
    border-radius: 6px; padding: 7px; selection-background-color: #6478ee;
}
QComboBox QAbstractItemView { background: #22252e; color: #edf0f7; }
QPushButton {
    background: #2a2e39; border: 1px solid #414655; border-radius: 7px;
    padding: 9px 13px; font-weight: 600;
}
QPushButton:hover { background: #363c4b; }
QPushButton#primary { background: #6478ee; border-color: #6478ee; color: white; }
QPushButton#primary:hover { background: #7889f5; }
QPushButton:disabled { color: #737b8c; background: #22252e; }
QHeaderView::section { background: #292d38; color: #cbd1df; padding: 8px; border: none; }
QTableWidget { gridline-color: #343947; }
QProgressBar { background: #343947; border: none; border-radius: 4px; text-align: center; min-height: 16px; }
QProgressBar::chunk { background: #8293ff; border-radius: 4px; }
QGroupBox { border: 1px solid #343947; border-radius: 8px; margin-top: 12px; padding: 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
QTabBar::tab { background: #22252e; color: #aeb5c5; padding: 10px 18px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
QTabBar::tab:selected { background: #343a4c; color: white; }
"""

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.workers = {}
        self.resolvers = {}
        self.task_titles = {}
        self.task_playlist_folders = {}
        self.task_rows = {}
        self.task_speeds = {}
        self.task_durations = {}
        self.log_entries = []
        self.history = DownloadHistory()
        self.queue_paused = False
        self.setWindowTitle("kachay-vidos")
        self.resize(1050, 720)
        self.setMinimumSize(820, 600)
        self.setStyleSheet(APP_STYLE)
        self._build_ui()
        self._load_settings_into_form()
        self.statusBar().showMessage("Готово · Вставьте ссылки для добавления в очередь")

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        # One-line header: large app name on the left, compact live status on the right.
        header = QHBoxLayout()
        header.setSpacing(18)
        title = QLabel("kachay-vidos")
        title.setStyleSheet("font-size: 28px; font-weight: 750; letter-spacing: 0.2px;")
        header.addWidget(title, 0, Qt.AlignVCenter)
        header.addStretch(1)

        self.activity_dot = QLabel("●")
        self.activity_dot.setStyleSheet("color: #8b93a5; font-size: 15px;")
        self.activity_label = QLabel("Готово")
        self.activity_label.setStyleSheet("font-weight: 600; color: #b8c0d0;")
        self.overall_progress_label = QLabel("0/0 файлов")
        self.overall_progress_label.setStyleSheet("color: #9ba3b5;")
        self.speed_label = QLabel("0 Б/с")
        self.speed_label.setStyleSheet("color: #9ba3b5;")
        self.active_count_label = QLabel("Очередь: 0")
        self.active_count_label.setStyleSheet("color: #9ba3b5;")
        status_panel = QHBoxLayout()
        status_panel.setContentsMargins(10, 6, 10, 6)
        status_panel.setSpacing(10)
        status_panel.addWidget(self.activity_dot)
        status_panel.addWidget(self.activity_label)
        status_panel.addWidget(self._make_status_separator())
        status_panel.addWidget(self.overall_progress_label)
        status_panel.addWidget(self._make_status_separator())
        status_panel.addWidget(self.speed_label)
        status_panel.addWidget(self._make_status_separator())
        status_panel.addWidget(self.active_count_label)
        self.top_status_panel = QWidget()
        self.top_status_panel.setLayout(status_panel)
        self.top_status_panel.setStyleSheet("QWidget { background: #22252e; border: 1px solid #343947; border-radius: 7px; }")
        header.addWidget(self.top_status_panel, 0, Qt.AlignVCenter)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.download_tab = QWidget()
        self.settings_tab = QWidget()
        self.logs_tab = QWidget()
        self.history_tab = QWidget()
        self.changelog_tab = QWidget()
        self.tabs.addTab(self.download_tab, "Загрузки")
        self.tabs.addTab(self.history_tab, "История")
        self.tabs.addTab(self.settings_tab, "Настройки")
        self.tabs.addTab(self.logs_tab, "Логи")
        self.tabs.addTab(self.changelog_tab, "Change log")
        self._build_download_tab()
        self._build_settings_tab()
        self._build_logs_tab()
        self._build_history_tab()
        self._build_changelog_tab()
        self.setStatusBar(QStatusBar())

    @staticmethod
    def _make_status_separator():
        separator = QLabel("·")
        separator.setStyleSheet("color: #555d6d;")
        return separator

    def _build_download_tab(self):
        layout = QVBoxLayout(self.download_tab)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(12)

        layout.addWidget(QLabel("Ссылки — по одной на строку"))
        self.url_input = QTextEdit()
        self.url_input.setPlaceholderText(
            "https://www.youtube.com/watch?v=...\nhttps://www.instagram.com/reel/..."
        )
        self.url_input.setMaximumHeight(112)
        layout.addWidget(self.url_input)

        controls = QHBoxLayout()
        self.add_button = QPushButton("＋ Добавить в очередь")
        self.add_button.clicked.connect(self.add_urls)
        self.start_button = QPushButton("▶  Начать загрузку")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(lambda checked=False: self.start_queue(retry_errors=True))
        self.cancel_button = QPushButton("■ Отменить выбранное")
        self.cancel_button.clicked.connect(self.cancel_selected)
        self.stop_button = QPushButton("⏹ Остановить все")
        self.stop_button.clicked.connect(self.stop_all_downloads)
        controls.addWidget(self.add_button)
        controls.addWidget(self.start_button)
        controls.addWidget(self.cancel_button)
        controls.addWidget(self.stop_button)
        layout.addLayout(controls)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Платформа", "Ссылка", "Состояние", "Прогресс", "Результат"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        self.queue_summary = QLabel("Задач: 0")
        self.queue_summary.setStyleSheet("color: #9ba3b5;")
        clear_button = QPushButton("Очистить завершённые")
        clear_button.clicked.connect(self.clear_finished)
        footer.addWidget(self.queue_summary)
        footer.addStretch()
        footer.addWidget(clear_button)
        layout.addLayout(footer)


    def _build_history_tab(self):
        layout = QVBoxLayout(self.history_tab)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(10)
        layout.addWidget(QLabel("История успешно скачанных файлов · хранится локально в SQLite"))
        controls = QHBoxLayout()
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Поиск по названию, платформе, пути или ссылке…")
        self.history_search.textChanged.connect(self._refresh_history)
        refresh_button = QPushButton("Обновить")
        refresh_button.clicked.connect(self._refresh_history)
        controls.addWidget(self.history_search, 1)
        controls.addWidget(refresh_button)
        layout.addLayout(controls)

        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(["Дата загрузки", "Платформа", "Название", "Файл", "Длительность", "Размер"])
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setAlternatingRowColors(False)
        self.history_table.doubleClicked.connect(self._open_history_file)
        layout.addWidget(self.history_table, 1)

        buttons = QHBoxLayout()
        open_button = QPushButton("Открыть файл")
        open_button.clicked.connect(self._open_history_file)
        remove_button = QPushButton("Убрать запись")
        remove_button.clicked.connect(self._delete_history_record)
        self.history_summary = QLabel("Файлов: 0")
        self.history_summary.setStyleSheet("color: #9ba3b5;")
        buttons.addWidget(self.history_summary)
        buttons.addStretch()
        buttons.addWidget(open_button)
        buttons.addWidget(remove_button)
        layout.addLayout(buttons)
        self._refresh_history()

    def _build_changelog_tab(self):
        layout = QVBoxLayout(self.changelog_tab)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(10)
        layout.addWidget(QLabel("История изменений приложения из файла CHANGELOG.md"))
        self.changelog_view = QTextEdit()
        self.changelog_view.setReadOnly(True)
        self.changelog_view.setLineWrapMode(QTextEdit.WidgetWidth)
        self.changelog_view.setStyleSheet("font-size: 13px; padding: 10px;")
        changelog_path = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
        try:
            content = changelog_path.read_text(encoding="utf-8")
        except OSError:
            content = "Файл CHANGELOG.md не найден."
        self.changelog_view.setPlainText(content)
        layout.addWidget(self.changelog_view, 1)
        refresh = QPushButton("Обновить историю изменений")
        refresh.clicked.connect(lambda: self._reload_changelog(changelog_path))
        layout.addWidget(refresh)

    def _reload_changelog(self, path):
        try:
            self.changelog_view.setPlainText(path.read_text(encoding="utf-8"))
        except OSError:
            self.changelog_view.setPlainText("Файл CHANGELOG.md не найден.")

    @staticmethod
    def _format_file_size(size):
        value = float(size or 0)
        for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
            if value < 1024 or unit == "ТБ":
                return f"{value:.1f} {unit}" if unit != "Б" else f"{int(value)} Б"
            value /= 1024

    @staticmethod
    def _format_duration(seconds):
        if seconds is None or seconds == "":
            return "—"
        try:
            total = max(0, int(float(seconds)))
        except (TypeError, ValueError):
            return "—"
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"

    def _refresh_history(self, *_):
        if not hasattr(self, "history_table"):
            return
        rows = self.history.list_files(self.history_search.text() if hasattr(self, "history_search") else "")
        self.history_table.setRowCount(0)
        for record in rows:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            try:
                date_text = datetime.fromisoformat(record["downloaded_at"]).strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                date_text = str(record["downloaded_at"]).replace("T", " ")[:16]
            values = [date_text, record["platform"], record["title"], record["file_path"], self._format_duration(record["duration_seconds"]), self._format_file_size(record["file_size"])]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(record["file_path"]))
                self.history_table.setItem(row, col, item)
            self.history_table.item(row, 0).setData(Qt.UserRole, record["id"])
            self.history_table.item(row, 3).setData(Qt.UserRole, record["file_path"])
        self.history_summary.setText(f"Файлов: {len(rows)}")

    def _selected_history_record(self):
        row = self.history_table.currentRow()
        if row < 0:
            return None
        return self.history_table.item(row, 0).data(Qt.UserRole), self.history_table.item(row, 3).data(Qt.UserRole)

    def _open_history_file(self, *_):
        selected = self._selected_history_record()
        if not selected:
            QMessageBox.information(self, "История", "Сначала выберите запись.")
            return
        path = Path(selected[1])
        if not path.exists():
            QMessageBox.warning(self, "Файл не найден", f"Файл больше не существует по указанному пути:\n{path}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _delete_history_record(self):
        selected = self._selected_history_record()
        if not selected:
            QMessageBox.information(self, "История", "Сначала выберите запись.")
            return
        self.history.delete_record(selected[0])
        self._refresh_history()

    def on_downloaded_files(self, task_id, file_paths):
        row = self.task_rows.get(task_id)
        if row is None or row >= self.table.rowCount():
            return
        url = self.table.item(row, 1).text()
        platform = detect_platform(url) or "unknown"
        platform_name = {"youtube": "YouTube", "instagram": "Instagram"}.get(platform, platform)
        title = self.task_titles.get(task_id, "")
        duration = getattr(self, "task_durations", {}).get(task_id)
        self.history.add_files(task_id, platform_name, title, url, file_paths, duration_seconds=duration)
        self._refresh_history()
        self._append_log("SUCCESS", f"В историю SQLite добавлено файлов: {len(file_paths)}", task_id=task_id)

    def _build_logs_tab(self):
        layout = QVBoxLayout(self.logs_tab)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Диагностика загрузок · команды, этапы, предупреждения и ошибки"))
        filters = QHBoxLayout()
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("Поиск по сообщению или техническим деталям…")
        self.log_search.textChanged.connect(self._refresh_logs)
        self.log_level = QComboBox()
        self.log_level.addItem("Все уровни", "ALL")
        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "SUCCESS"):
            self.log_level.addItem(level, level)
        self.log_level.currentIndexChanged.connect(self._refresh_logs)
        filters.addWidget(self.log_search, 1)
        filters.addWidget(self.log_level)
        layout.addLayout(filters)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.NoWrap)
        self.log_view.setPlaceholderText("Здесь появятся события загрузки и подробные сообщения yt-dlp.")
        self.log_view.setStyleSheet("font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px;")
        layout.addWidget(self.log_view, 1)

        buttons = QHBoxLayout()
        copy_button = QPushButton("Копировать видимые логи")
        copy_button.clicked.connect(lambda: self._copy_logs())
        save_button = QPushButton("Сохранить логи…")
        save_button.clicked.connect(self._save_logs)
        clear_button = QPushButton("Очистить отображение")
        clear_button.clicked.connect(self._clear_logs)
        buttons.addWidget(copy_button)
        buttons.addWidget(save_button)
        buttons.addStretch()
        buttons.addWidget(clear_button)
        layout.addLayout(buttons)
        self._append_log("INFO", "Приложение запущено. Логи загрузок будут отображаться здесь.")

    def _append_log(self, level, message, details="", task_id=""):
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        entry = {"time": timestamp, "level": level, "message": str(message),
                 "details": str(details or ""), "task_id": str(task_id or "")}
        self.log_entries.append(entry)
        # Keep the in-memory panel bounded for long-running sessions.
        if len(self.log_entries) > 5000:
            del self.log_entries[:500]
        self._refresh_logs()

    def _refresh_logs(self, *_):
        if not hasattr(self, "log_view"):
            return
        query = self.log_search.text().strip().lower()
        level = self.log_level.currentData()
        visible = []
        for entry in self.log_entries:
            if level != "ALL" and entry["level"] != level:
                continue
            line = f"[{entry['time']}] [{entry['level']}]"
            if entry["task_id"]:
                line += f" [task:{entry['task_id'][:8]}]"
            line += f" {entry['message']}"
            if entry["details"]:
                line += f"\n    Технические детали: {entry['details']}"
            if query and query not in (line.lower()):
                continue
            visible.append(line)
        self.log_view.setPlainText("\n".join(visible))
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_view.setTextCursor(cursor)

    def _copy_logs(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.log_view.toPlainText())
        self.statusBar().showMessage("Видимые логи скопированы", 3000)

    def _save_logs(self):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить логи", "video_downloader.log", "Log files (*.log);;Text files (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(self.log_view.toPlainText(), encoding="utf-8")
            self.statusBar().showMessage(f"Логи сохранены: {path}", 4000)
        except OSError as error:
            QMessageBox.warning(self, "Не удалось сохранить логи", str(error))

    def _clear_logs(self):
        self.log_entries.clear()
        self.log_view.clear()
        self.statusBar().showMessage("Отображение логов очищено", 3000)

    def _build_settings_tab(self):
        layout = QVBoxLayout(self.settings_tab)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(12)

        appearance = QGroupBox("Внешний вид")
        appearance_form = QFormLayout(appearance)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Тёмная", "dark")
        self.theme_combo.addItem("Светлая", "light")
        self.theme_combo.addItem("Как в системе", "system")
        appearance_form.addRow("Тема приложения", self.theme_combo)
        layout.addWidget(appearance)

        folders = QGroupBox("Папки сохранения")
        folder_form = QFormLayout(folders)
        self.youtube_path = QLineEdit()
        self.instagram_path = QLineEdit()
        folder_form.addRow("YouTube", self._folder_row(self.youtube_path, "youtube"))
        folder_form.addRow("Instagram", self._folder_row(self.instagram_path, "instagram"))
        layout.addWidget(folders)

        quality = QGroupBox("Качество видео")
        quality_form = QFormLayout(quality)
        self.youtube_quality = QComboBox()
        self._fill_quality(self.youtube_quality, include_4k=True)
        self.instagram_quality = QComboBox()
        self._fill_quality(self.instagram_quality, include_4k=False)
        quality_form.addRow("YouTube", self.youtube_quality)
        quality_form.addRow("Instagram", self.instagram_quality)
        layout.addWidget(quality)

        output = QGroupBox("Формат")
        output_form = QFormLayout(output)
        self.container_combo = QComboBox()
        self.container_combo.addItem("MP4 (совместимый формат)", "mp4")
        self.container_combo.addItem("Исходный формат", "original")
        output_form.addRow("Контейнер", self.container_combo)
        layout.addWidget(output)

        buttons = QHBoxLayout()
        save_button = QPushButton("Сохранить настройки")
        save_button.setObjectName("primary")
        save_button.clicked.connect(self.save_settings_from_form)
        reset_button = QPushButton("Сбросить настройки")
        reset_button.clicked.connect(self.reset_settings)
        buttons.addStretch()
        buttons.addWidget(reset_button)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)
        layout.addStretch()

    def _folder_row(self, line_edit, platform):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        browse = QPushButton("Обзор…")
        browse.clicked.connect(lambda: self.choose_folder(line_edit, platform))
        row_layout.addWidget(line_edit, 1)
        row_layout.addWidget(browse)
        return row

    @staticmethod
    def _fill_quality(combo, include_4k):
        combo.addItem("Лучшее доступное", "best")
        if include_4k:
            combo.addItem("До 4K (2160p)", "2160")
        combo.addItem("До 1080p", "1080")
        combo.addItem("До 720p", "720")
        combo.addItem("До 480p", "480")

    def _load_settings_into_form(self):
        self._set_combo_data(self.theme_combo, self.settings.get("theme", "dark"))
        self.youtube_path.setText(self.settings["download_paths"]["youtube"])
        self.instagram_path.setText(self.settings["download_paths"]["instagram"])
        self._set_combo_data(self.youtube_quality, self.settings["quality"]["youtube"])
        self._set_combo_data(self.instagram_quality, self.settings["quality"]["instagram"])
        self._set_combo_data(self.container_combo, self.settings.get("container", "mp4"))
        self._update_theme_label()

    @staticmethod
    def _set_combo_data(combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def choose_folder(self, line_edit, platform):
        start = line_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Выберите папку сохранения", start)
        if path:
            line_edit.setText(path)

    def save_settings_from_form(self):
        youtube_path = self.youtube_path.text().strip()
        instagram_path = self.instagram_path.text().strip()
        if not youtube_path or not instagram_path:
            QMessageBox.warning(self, "Папка не указана", "Укажите папки для обеих платформ.")
            return
        self.settings = {
            "theme": self.theme_combo.currentData(),
            "download_paths": {"youtube": youtube_path, "instagram": instagram_path},
            "quality": {
                "youtube": self.youtube_quality.currentData(),
                "instagram": self.instagram_quality.currentData(),
            },
            "container": self.container_combo.currentData(),
        }
        save_settings(self.settings)
        self._apply_theme()
        self._update_theme_label()
        self.statusBar().showMessage("Настройки сохранены", 4000)

    def reset_settings(self):
        from core.settings import DEFAULT_SETTINGS
        import copy
        self.settings = copy.deepcopy(DEFAULT_SETTINGS)
        self._load_settings_into_form()
        self.statusBar().showMessage("Значения сброшены. Нажмите «Сохранить настройки», чтобы применить их.", 5000)

    def _apply_theme(self):
        if self.settings.get("theme") == "light":
            self.setStyleSheet("""
                QWidget { background: #f4f6fa; color: #202532; font-size: 13px; }
                QMainWindow, QTabWidget::pane { background: #f4f6fa; border: 1px solid #d7dce6; }
                QLabel#subtitle { color: #657086; }
                QTextEdit, QLineEdit, QComboBox, QTableWidget { background: white; color: #202532; border: 1px solid #cbd2df; border-radius: 6px; padding: 7px; }
                QPushButton { background: #e8ebf2; border: 1px solid #cbd2df; border-radius: 7px; padding: 9px 13px; font-weight: 600; }
                QPushButton#primary { background: #5269dd; color: white; }
                QHeaderView::section { background: #e5e9f1; padding: 8px; border: none; }
                QProgressBar { background: #dce1eb; border: none; border-radius: 4px; text-align: center; }
                QProgressBar::chunk { background: #6478ee; border-radius: 4px; }
                QGroupBox { border: 1px solid #cbd2df; border-radius: 8px; margin-top: 12px; padding: 12px; font-weight: 600; }
                QTabBar::tab { background: #e8ebf2; padding: 10px 18px; }
                QTabBar::tab:selected { background: #d7def8; }
            """)
        else:
            self.setStyleSheet(APP_STYLE)

    def _update_theme_label(self):
        theme = self.settings.get("theme", "dark")
        # Theme is applied silently; theme label was removed from the interface.

    def add_urls(self):
        urls = parse_urls(self.url_input.toPlainText())
        if not urls:
            QMessageBox.information(self, "Нет ссылок", "Вставьте одну или несколько HTTP(S)-ссылок.")
            return
        added = 0
        existing = {self.table.item(row, 1).text() for row in range(self.table.rowCount())}
        for url in urls:
            if url in existing:
                continue
            self._insert_task(url)
            added += 1
            existing.add(url)
        self.url_input.clear()
        self._update_summary()
        self.statusBar().showMessage(f"Добавлено новых ссылок: {added}", 4000)

    def _insert_task(self, url, title="", playlist_folder=""):
        platform = detect_platform(url)
        row = self.table.rowCount()
        self.table.insertRow(row)
        task_id = str(uuid4())
        platform_name = {"youtube": "YouTube", "instagram": "Instagram"}.get(platform, "Неизвестно")
        result = title or ("Плейлист будет разделён на отдельные задачи" if is_youtube_playlist_url(url) else "")
        values = [platform_name, url, "В очереди", "0%", result]
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            if col == 1:
                item.setToolTip(url)
            self.table.setItem(row, col, item)
        self.table.setRowHeight(row, 42)
        self.task_rows[task_id] = row
        self.table.item(row, 0).setData(Qt.UserRole, task_id)
        if title:
            self.task_titles[task_id] = title
        if playlist_folder and platform == "youtube":
            self.task_playlist_folders[task_id] = playlist_folder
        if not platform:
            self.table.item(row, 2).setText("Ошибка")
            self.table.item(row, 4).setText("Неподдерживаемая ссылка")
        return task_id

    def _rebuild_task_rows(self):
        self.task_rows.clear()
        for row in range(self.table.rowCount()):
            task_id = self.table.item(row, 0).data(Qt.UserRole)
            if task_id:
                self.task_rows[task_id] = row

    def on_playlist_resolved(self, task_id, entries, error):
        row = self.task_rows.get(task_id)
        if row is None or row >= self.table.rowCount():
            return
        original_url = self.table.item(row, 1).text()
        if error or not entries:
            cancelled = bool(error and "отмен" in error.lower())
            self.table.item(row, 2).setText("Отменено" if cancelled else "Ошибка")
            self.table.item(row, 4).setText(error or "Не удалось получить видео из плейлиста")
            self._append_log("WARNING" if cancelled else "ERROR", error or "Плейлист пуст или недоступен.", task_id=task_id)
            self._update_summary()
            return

        self._append_log("INFO", f"Плейлист раскрыт в отдельные задачи: {len(entries)} видео.", task_id=task_id)
        self.table.removeRow(row)
        self.task_rows.pop(task_id, None)
        self.task_titles.pop(task_id, None)
        self._rebuild_task_rows()
        existing = {self.table.item(i, 1).text() for i in range(self.table.rowCount())}
        inserted = 0
        for entry in entries:
            video_url = entry.get("url", "").strip()
            if not video_url or video_url in existing:
                continue
            self._insert_task(
                video_url,
                entry.get("title", ""),
                entry.get("playlist_folder", entry.get("playlist_title", "")),
            )
            existing.add(video_url)
            inserted += 1
        self.statusBar().showMessage(f"Плейлист разделён на отдельные задачи: {inserted} видео", 5000)
        self._update_summary()

    def start_queue(self, retry_errors=True):
        # Manual click resumes the queue; internal calls respect a stop request.
        if retry_errors:
            self.queue_paused = False
        elif self.queue_paused:
            return
        if not self.settings:
            return
        active_platforms = {detect_platform(w.url) for w in self.workers.values() if w.isRunning()}
        active_platforms.update(detect_platform(self.table.item(self.task_rows[tid], 1).text())
                                for tid, resolver in self.resolvers.items()
                                if resolver.isRunning() and tid in self.task_rows)
        for row in range(self.table.rowCount()):
            status = self.table.item(row, 2).text()
            # A manual click retries failed tasks and resumes canceled tasks.
            # Internal queue progression only starts tasks that have never been canceled/failed.
            eligible_statuses = ("В очереди", "Ошибка", "Отменено") if retry_errors else ("В очереди",)
            if status not in eligible_statuses:
                continue
            url = self.table.item(row, 1).text()
            platform = detect_platform(url)
            if not platform or platform in active_platforms:
                continue
            task_id = self.table.item(row, 0).data(Qt.UserRole)
            if task_id in self.resolvers:
                continue
            if platform == "youtube" and is_youtube_playlist_url(url):
                resolver = PlaylistResolver(task_id, url)
                resolver.resolved.connect(self.on_playlist_resolved)
                resolver.log_event.connect(self.on_log_event)
                resolver.finished.connect(lambda tid=task_id: self._cleanup_resolver(tid))
                self.resolvers[task_id] = resolver
                self.table.item(row, 2).setText("Разбор плейлиста")
                self.table.item(row, 4).setText("Получение списка видео…")
                resolver.start()
                active_platforms.add(platform)
                continue
            worker = DownloadWorker(
                task_id,
                url,
                self.settings.copy(),
                playlist_folder=self.task_playlist_folders.get(task_id, ""),
            )
            worker.progress.connect(self.on_progress)
            worker.title_changed.connect(self.on_title_changed)
            worker.duration_changed.connect(self.on_duration_changed)
            worker.finished_task.connect(self.on_finished)
            worker.downloaded_files.connect(self.on_downloaded_files)
            worker.log_event.connect(self.on_log_event)
            worker.finished.connect(lambda tid=task_id: self._cleanup_worker(tid))
            self.workers[task_id] = worker
            self.table.item(row, 2).setText("Загрузка")
            self.table.item(row, 4).setText(self.task_titles.get(task_id, "Получение названия видео…"))
            self.task_speeds[task_id] = "0 Б/с"
            worker.start()
            active_platforms.add(platform)
        self._update_summary()

    def _cleanup_resolver(self, task_id):
        resolver = self.resolvers.pop(task_id, None)
        if resolver:
            resolver.deleteLater()
        self._update_summary()
        QTimer.singleShot(0, lambda: self.start_queue(retry_errors=False))

    def on_log_event(self, task_id, level, message, details):
        self._append_log(level, message, details, task_id)

    def on_title_changed(self, task_id, title):
        self.task_titles[task_id] = title
        row = self.task_rows.get(task_id)
        if row is not None and row < self.table.rowCount():
            self.table.item(row, 4).setText(title)
            self.table.item(row, 4).setToolTip(title)

    def on_duration_changed(self, task_id, duration_seconds):
        self.task_durations[task_id] = duration_seconds

    def on_progress(self, task_id, percent, speed):
        row = self.task_rows.get(task_id)
        if row is None or row >= self.table.rowCount():
            return
        self.table.item(row, 3).setText(f"{percent}%")
        self.task_speeds[task_id] = speed or "0 Б/с"
        self._update_activity_status()
        title = self.task_titles.get(task_id, "")
        # Keep the video title on the first line; progress details go below it.
        details = speed or "Загрузка…"
        result = f"{title}\n{details}" if title else details
        self.table.item(row, 4).setText(result)
        self.table.item(row, 4).setToolTip(title if title else details)

    def on_finished(self, task_id, success, message):
        row = self.task_rows.get(task_id)
        if row is None or row >= self.table.rowCount():
            return
        cancelled = (not success) and message.lower().startswith("отменено")
        self.task_speeds.pop(task_id, None)
        self.table.item(row, 2).setText("Готово" if success else ("Отменено" if cancelled else "Ошибка"))
        if success:
            self.table.item(row, 3).setText("100%")
        title = self.task_titles.get(task_id, "")
        result = f"{title}\n{message}" if title else message
        self.table.item(row, 4).setText(result)
        if title:
            self.table.item(row, 4).setToolTip(title)
        log_level = "SUCCESS" if success else ("WARNING" if cancelled else "ERROR")
        self._append_log(log_level, message, task_id=task_id)
        self._update_summary()
        self._update_activity_status()

    def _cleanup_worker(self, task_id):
        worker = self.workers.pop(task_id, None)
        if worker:
            worker.deleteLater()
        self.task_speeds.pop(task_id, None)
        self._update_summary()
        self._update_activity_status()
        # Start the next waiting task automatically after the thread has stopped.
        # Failed tasks are not retried automatically, avoiding an endless retry loop.
        QTimer.singleShot(0, lambda: self.start_queue(retry_errors=False))
        # If no worker could be started, queued tasks may still be blocked by a
        # platform worker that is finishing; its finished signal will continue the queue.

    def stop_all_downloads(self):
        """Pause automatic queue progression and request cancellation of active tasks."""
        self.queue_paused = True
        active_count = 0
        for task_id, resolver in list(self.resolvers.items()):
            if resolver.isRunning():
                resolver.requestInterruption()
                active_count += 1
                row = self.task_rows.get(task_id)
                if row is not None and row < self.table.rowCount():
                    self.table.item(row, 2).setText("Отмена…")
                self._append_log("WARNING", "Запрошена остановка разбора плейлиста.", task_id=task_id)
        for task_id, worker in list(self.workers.items()):
            if worker.isRunning():
                worker.cancel()
                active_count += 1
                row = self.task_rows.get(task_id)
                if row is not None and row < self.table.rowCount():
                    self.table.item(row, 2).setText("Отмена…")
                self._append_log("WARNING", "Запрошена остановка активной загрузки.", task_id=task_id)
        self._update_summary()
        if active_count:
            self.statusBar().showMessage(
                f"Остановка запрошена для активных задач: {active_count}. Очередь приостановлена.", 5000
            )
        else:
            self.statusBar().showMessage(
                "Очередь приостановлена. Нажмите «Начать загрузку», чтобы продолжить.", 5000
            )

    def cancel_selected(self):
        selected_rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if not selected_rows:
            self.statusBar().showMessage("Сначала выберите задачу в очереди.", 3500)
            return
        for row in selected_rows:
            task_id = self.table.item(row, 0).data(Qt.UserRole)
            resolver = self.resolvers.get(task_id)
            if resolver and resolver.isRunning():
                resolver.requestInterruption()
                self.table.item(row, 2).setText("Отмена…")
                self._append_log("WARNING", "Запрошена отмена разбора плейлиста.", task_id=task_id)
                continue
            worker = self.workers.get(task_id)
            if worker and worker.isRunning():
                worker.cancel()
                self.table.item(row, 2).setText("Отмена…")
                self._append_log("WARNING", "Запрошена отмена задачи.", task_id=task_id)
            elif self.table.item(row, 2).text() == "В очереди":
                self.table.item(row, 2).setText("Отменено")
                self.table.item(row, 4).setText("Задача отменена пользователем")
                self._append_log("WARNING", "Задача в очереди отменена пользователем.", task_id=task_id)
        self._update_summary()

    def clear_finished(self):
        removable = {"Готово", "Ошибка", "Отменено"}
        for row in reversed(range(self.table.rowCount())):
            if self.table.item(row, 2).text() in removable:
                task_id = self.table.item(row, 0).data(Qt.UserRole)
                self.task_rows.pop(task_id, None)
                self.task_titles.pop(task_id, None)
                self.task_playlist_folders.pop(task_id, None)
                self.table.removeRow(row)
        self.task_rows.clear()
        for row in range(self.table.rowCount()):
            task_id = self.table.item(row, 0).data(Qt.UserRole)
            self.task_rows[task_id] = row
        self._update_summary()

    def _update_activity_status(self):
        active_ids = [tid for tid, worker in self.workers.items() if worker.isRunning()]
        active_ids += [tid for tid, resolver in self.resolvers.items() if resolver.isRunning()]
        active_ids = list(set(active_ids))
        active_count = len(active_ids)
        total_tasks = self.table.rowCount() if hasattr(self, "table") else 0
        completed = 0
        queued = 0
        progress_values = []
        for row in range(total_tasks):
            status_item = self.table.item(row, 2)
            progress_item = self.table.item(row, 3)
            if status_item and status_item.text() == "Готово":
                completed += 1
            elif status_item and status_item.text() == "В очереди":
                queued += 1
            if progress_item:
                try:
                    progress_values.append(max(0, min(100, int(progress_item.text().replace("%", "")))))
                except ValueError:
                    progress_values.append(0)
        overall = int(sum(progress_values) / len(progress_values)) if progress_values else 0
        if active_count:
            self.activity_dot.setStyleSheet("color: #36c979; font-size: 15px;")
            self.activity_label.setText("Загрузка")
            self.activity_label.setStyleSheet("font-weight: 600; color: #36c979;")
            speeds = [value for tid, value in self.task_speeds.items() if tid in active_ids and value]
            speed_text = " + ".join(speeds) if speeds else "ожидание скорости"
            self.speed_label.setText(speed_text)
        else:
            self.activity_dot.setStyleSheet("color: #8b93a5; font-size: 15px;")
            self.activity_label.setText("Готово")
            self.activity_label.setStyleSheet("font-weight: 600; color: #b8c0d0;")
            self.speed_label.setText("0 Б/с")
        self.overall_progress_label.setText(f"{completed}/{total_tasks} готово · {overall}%")
        self.active_count_label.setText(f"Очередь: {queued} · Активно: {active_count}")

    def _update_summary(self):
        counts = {}
        for row in range(self.table.rowCount()):
            status = self.table.item(row, 2).text()
            counts[status] = counts.get(status, 0) + 1
        self.queue_summary.setText(
            f"Задач: {self.table.rowCount()} · В очереди: {counts.get('В очереди', 0)} · "
            f"Загружается: {counts.get('Загрузка', 0) + counts.get('Разбор плейлиста', 0) + counts.get('Отмена…', 0)} · "
            f"Ошибок: {counts.get('Ошибка', 0)} · Отменено: {counts.get('Отменено', 0)}"
        )
        if hasattr(self, "activity_dot"):
            self._update_activity_status()

    def closeEvent(self, event):
        running = [worker for worker in self.workers.values() if worker.isRunning()]
        running_resolvers = [resolver for resolver in self.resolvers.values() if resolver.isRunning()]
        if running or running_resolvers:
            answer = QMessageBox.question(
                self, "Загрузки выполняются",
                "Есть активные загрузки. Закрыть приложение и прервать их?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            for resolver in running_resolvers:
                resolver.requestInterruption()
                resolver.wait(2500)
            for worker in running:
                worker.cancel()
                worker.wait(2500)
        save_settings(self.settings)
        event.accept()
