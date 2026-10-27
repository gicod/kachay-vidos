import os
import re
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse

from PySide6.QtCore import QThread, Signal

PLATFORM_YOUTUBE = "youtube"
PLATFORM_INSTAGRAM = "instagram"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".bmp", ".tif", ".tiff"}
ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def clean_terminal_text(value):
    """Remove ANSI color/control sequences from yt-dlp diagnostics."""
    text = ANSI_ESCAPE_RE.sub("", str(value or ""))
    return CONTROL_CHARS_RE.sub("", text).strip()

FFMPEG_INSTALL_HELP = (
    "FFmpeg не найден. Обычно он устанавливается автоматически вместе с зависимостями проекта. "
    "В активированном виртуальном окружении выполните: "
    "python -m pip install -U imageio-ffmpeg yt-dlp. "
    "Альтернатива для Windows: winget install --id Gyan.FFmpeg -e, "
    "затем полностью перезапустите терминал и приложение."
)

def find_ffmpeg():
    """Find a system FFmpeg or the executable supplied by imageio-ffmpeg."""
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return str(Path(system_ffmpeg).resolve())

    try:
        import imageio_ffmpeg
        bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled_ffmpeg and Path(bundled_ffmpeg).is_file():
            return str(Path(bundled_ffmpeg).resolve())
    except (ImportError, OSError, RuntimeError):
        pass
    return None

def detect_platform(url: str):
    host = (urlparse(url).hostname or "").lower()
    if host == "youtu.be" or host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        return PLATFORM_YOUTUBE
    if host.endswith("instagram.com"):
        return PLATFORM_INSTAGRAM
    return None

def is_youtube_playlist_url(url: str) -> bool:
    """Return True for YouTube playlist links (including watch URLs with list=)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query)
    return (
        (host.endswith("youtube.com") and ("list" in query or parsed.path.rstrip("/") == "/playlist"))
        or (host == "youtu.be" and "list" in query)
    )


def safe_folder_name(value: str, max_length: int = 100) -> str:
    """Return a Windows-safe folder name for a playlist."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" ._")
    return (name[:max_length].rstrip(" .") or "YouTube playlist")


def parse_urls(text: str):
    result = []
    seen = set()
    for line in re.split(r"[\s,;]+", text.strip()):
        url = line.strip()
        if url.startswith(("https://", "http://")) and url not in seen:
            seen.add(url)
            result.append(url)
    return result

class PlaylistResolver(QThread):
    """Resolve a YouTube playlist into individual video tasks without downloading media."""
    resolved = Signal(str, object, str)  # source_task_id, entries, error
    log_event = Signal(str, str, str, str)

    def __init__(self, task_id, url):
        super().__init__()
        self.task_id = task_id
        self.url = url

    def run(self):
        safe_url = urlunparse(urlparse(self.url)._replace(query="", fragment=""))
        self.log_event.emit(self.task_id, "INFO", "Разбор плейлиста YouTube.", safe_url)
        try:
            import yt_dlp
            options = {
                "extract_flat": "in_playlist",
                "skip_download": True,
                "quiet": True,
                "no_warnings": False,
                "ignoreerrors": True,
                "noplaylist": False,
                "color": {"stdout": "never", "stderr": "never"},
                "logger": WorkerYtDlpLogger(self),
            }
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(self.url, download=False)
            if self.isInterruptionRequested():
                self.resolved.emit(self.task_id, [], "Разбор плейлиста отменён пользователем.")
                return
            if not info:
                raise RuntimeError("yt-dlp не вернул информацию о плейлисте.")
            raw_entries = info.get("entries")
            if raw_entries is None:
                # The URL may have been recognized as a playlist candidate but resolve to one video.
                raw_entries = [info]
            playlist_title = str(info.get("title") or "YouTube playlist")
            parsed_query = parse_qs(urlparse(self.url).query)
            playlist_id = (parsed_query.get("list") or [""])[0]
            folder_name = safe_folder_name(playlist_title)
            if playlist_id:
                folder_name = safe_folder_name(f"{folder_name} [{playlist_id}]")

            entries = []
            for entry in raw_entries:
                if not entry:
                    continue
                video_id = entry.get("id")
                video_url = entry.get("webpage_url") or entry.get("original_url") or entry.get("url")
                if video_url and not str(video_url).startswith(("http://", "https://")):
                    if video_id:
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                if not video_url and video_id:
                    video_url = f"https://www.youtube.com/watch?v={video_id}"
                if not video_url:
                    continue
                entries.append({
                    "url": str(video_url),
                    "title": str(entry.get("title") or "Без названия"),
                    "playlist_title": playlist_title,
                    "playlist_folder": folder_name,
                })
            if not entries:
                raise RuntimeError("В плейлисте не найдено доступных видео.")
            self.log_event.emit(self.task_id, "SUCCESS", f"Найдено видео в плейлисте: {len(entries)}.", "")
            self.resolved.emit(self.task_id, entries, "")
        except Exception as error:
            details = clean_terminal_text(error)
            self.log_event.emit(self.task_id, "ERROR", explain_error(details), details)
            self.resolved.emit(self.task_id, [], explain_error(details) + " " + details[:200])

    def _log(self, level, message, details=""):
        self.log_event.emit(self.task_id, level, clean_terminal_text(message), clean_terminal_text(details))


class DownloadWorker(QThread):
    progress = Signal(str, int, str)
    title_changed = Signal(str, str)
    finished_task = Signal(str, bool, str)
    log_event = Signal(str, str, str, str)  # task_id, level, message, technical details

    def __init__(self, task_id, url, settings, playlist_folder=""):
        super().__init__()
        self.task_id = task_id
        self.url = url
        self.settings = settings
        self.playlist_folder = safe_folder_name(playlist_folder) if playlist_folder else ""
        self._cancel_requested = False
        self._last_title = ""

    def cancel(self):
        self._cancel_requested = True
        self.requestInterruption()

    def _log(self, level, message, details=""):
        self.log_event.emit(
            self.task_id,
            level,
            clean_terminal_text(message),
            clean_terminal_text(details),
        )

    def run(self):
        platform = detect_platform(self.url)
        safe_url = urlunparse(urlparse(self.url)._replace(query="", fragment=""))
        self._log("INFO", f"Задача запущена: {safe_url}", f"task_id={self.task_id}")
        if not platform:
            self._log("ERROR", "Не удалось определить платформу.", "Поддерживаются YouTube и Instagram.")
            self.finished_task.emit(self.task_id, False, "Неизвестная платформа")
            return
        try:
            import yt_dlp
        except ImportError:
            self._log("ERROR", "Не установлен yt-dlp.", "Установите зависимость: python -m pip install -U yt-dlp")
            self.finished_task.emit(self.task_id, False, "Не установлен yt-dlp: pip install yt-dlp")
            return

        ffmpeg_path = find_ffmpeg()
        if not ffmpeg_path:
            self._log("ERROR", FFMPEG_INSTALL_HELP, "Проверены PATH и пакет imageio-ffmpeg; исполняемый файл ffmpeg не найден.")
            self.finished_task.emit(self.task_id, False, "Не найден FFmpeg. Установите зависимости по README.md.")
            return
        self._log("INFO", "FFmpeg найден.", f"Путь: {ffmpeg_path}")

        folder = Path(self.settings["download_paths"][platform]).expanduser()
        if platform == PLATFORM_YOUTUBE and self.playlist_folder:
            # Every expanded playlist is saved into its own uniquely named directory.
            folder = folder / "Playlists" / self.playlist_folder
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._log("ERROR", "Не удалось создать папку для сохранения.", repr(error))
            self.finished_task.emit(self.task_id, False, f"Не удалось создать папку: {error}")
            return

        quality = self.settings["quality"][platform]
        if quality == "best":
            format_selector = "bestvideo*+bestaudio/best"
        else:
            height = int(quality)
            format_selector = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"

        # Download into a per-task staging directory so media from this URL can
        # be routed after extraction without touching files from older tasks.
        staging_dir = folder / ".working" / self.task_id
        try:
            staging_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._log("ERROR", "Не удалось создать временную папку загрузки.", repr(error))
            self.finished_task.emit(self.task_id, False, f"Не удалось создать временную папку: {error}")
            return

        output_template = str(staging_dir / "%(title).180B [%(id)s].%(ext)s")
        options = {
            "format": format_selector,
            "outtmpl": output_template,
            "noplaylist": False,
            "ignoreerrors": False,
            "continuedl": True,
            "overwrites": False,
            "windowsfilenames": True,
            "progress_hooks": [self._progress_hook],
            "quiet": True,
            "no_warnings": False,
            "color": {"stdout": "never", "stderr": "never"},
            "logger": WorkerYtDlpLogger(self),
            # Pass an explicit executable path so yt-dlp also works when FFmpeg
            # is installed inside the Python environment and is not on PATH.
            "ffmpeg_location": ffmpeg_path,
        }
        if self.settings.get("container", "mp4") == "mp4":
            options["merge_output_format"] = "mp4"

        # Log the equivalent command with the URL query removed to avoid exposing tokens.
        command = ["yt-dlp", "--format", format_selector, "--output", output_template]
        command += ["--ffmpeg-location", ffmpeg_path]
        if options.get("merge_output_format"):
            command += ["--merge-output-format", "mp4"]
        command += [safe_url]
        self._log("INFO", "Подготовлены параметры загрузки.", "Эквивалент команды: " + repr(command))
        self._log("DEBUG", f"Папка сохранения: {folder}", f"Формат: {format_selector}; контейнер: {self.settings.get('container', 'mp4')}")

        try:
            self._log("INFO", "Запуск yt-dlp.")
            with yt_dlp.YoutubeDL(options) as downloader:
                downloader.download([self.url])
            if self.isInterruptionRequested():
                self._log("WARNING", "Загрузка отменена пользователем.")
                self.finished_task.emit(self.task_id, False, "Отменено пользователем")
                return

            moved_count, image_count = self._organize_downloads(staging_dir, folder, platform)
            if moved_count:
                self._log(
                    "INFO",
                    f"Файлы распределены по папкам: {moved_count} файлов.",
                    f"Изображений в отдельной папке: {image_count}",
                )
            if self.isInterruptionRequested():
                self._log("WARNING", "Загрузка остановлена пользователем.")
                self.finished_task.emit(self.task_id, False, "Отменено пользователем")
            else:
                self._log("SUCCESS", "Загрузка успешно завершена.")
                self.finished_task.emit(self.task_id, True, "Загрузка завершена")
        except Exception as error:
            if self.isInterruptionRequested() or isinstance(error, InterruptedError):
                self._log("WARNING", "Загрузка остановлена пользователем.", clean_terminal_text(error))
                self.finished_task.emit(self.task_id, False, "Отменено пользователем")
            else:
                raw_message = clean_terminal_text(error).splitlines()
                technical = raw_message[-1][:1000] if raw_message else repr(error)
                explanation = explain_error(technical)
                self._log("ERROR", explanation, technical)
                self.finished_task.emit(self.task_id, False, f"{explanation} · {technical[:140]}")
        finally:
            try:
                if staging_dir.exists() and not any(staging_dir.iterdir()):
                    staging_dir.rmdir()
                working_root = staging_dir.parent
                if working_root.exists() and not any(working_root.iterdir()):
                    working_root.rmdir()
            except OSError:
                pass

    def _organize_downloads(self, staging_dir, destination_dir, platform):
        """Move downloaded media to final folders; Instagram photos go to Images."""
        moved = 0
        image_count = 0
        image_dir = destination_dir / "Images"
        files = [path for path in staging_dir.rglob("*") if path.is_file()]
        for source in files:
            # Keep sidecar metadata out of the media folders unless it is a media file.
            if source.suffix.lower() in IMAGE_EXTENSIONS:
                target_dir = image_dir
                image_count += 1
            else:
                target_dir = destination_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / source.name
            if target.exists():
                stem, suffix = target.stem, target.suffix
                index = 2
                while (target_dir / f"{stem}_{index}{suffix}").exists():
                    index += 1
                target = target_dir / f"{stem}_{index}{suffix}"
            shutil.move(str(source), str(target))
            moved += 1
        # Remove now-empty staging subfolders.
        for path in sorted(staging_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        try:
            staging_dir.rmdir()
        except OSError:
            pass
        return moved, image_count

    def _progress_hook(self, data):
        info = data.get("info_dict") or {}
        title = clean_terminal_text(info.get("title") or "")
        if title and title != self._last_title:
            self._last_title = title
            self.title_changed.emit(self.task_id, title)
            self._log("INFO", f"Начата загрузка видео: {title}")
        if self.isInterruptionRequested():
            raise InterruptedError("Отмена запрошена")
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            percent = int(downloaded * 100 / total) if total else 0
            speed = data.get("_speed_str", "")
            self.progress.emit(self.task_id, max(0, min(percent, 100)), speed)
        elif status == "finished":
            self._log("INFO", "Файл скачан, выполняется финальная обработка/объединение форматов.")
            self.progress.emit(self.task_id, 100, "Обработка файла…")


class WorkerYtDlpLogger:
    """Bridge yt-dlp diagnostics into the GUI log panel."""
    def __init__(self, worker):
        self.worker = worker

    def debug(self, message):
        text = str(message).strip()
        if text:
            self.worker._log("DEBUG", text)

    def info(self, message):
        text = str(message).strip()
        if text:
            self.worker._log("INFO", text)

    def warning(self, message):
        text = str(message).strip()
        if text:
            self.worker._log("WARNING", text, text)

    def error(self, message):
        text = str(message).strip()
        if text:
            self.worker._log("ERROR", explain_error(text), text)


def explain_error(message):
    """Return a short Russian explanation while preserving the original diagnostic."""
    lowered = message.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "Истекло время ожидания ответа сайта. Проверьте интернет и повторите попытку позже."
    if "ffmpeg" in lowered and ("not installed" in lowered or "not found" in lowered or "unable" in lowered):
        return "Не найден FFmpeg. Установите FFmpeg и перезапустите приложение."
    if "private" in lowered or "login" in lowered or "sign in" in lowered or "authentication" in lowered:
        return "Видео может требовать входа в аккаунт или быть недоступно без авторизации."
    if "403" in lowered or "forbidden" in lowered:
        return "Сайт отказал в доступе к видео. Проверьте ссылку, доступность публикации и обновите yt-dlp."
    if "404" in lowered or "not found" in lowered:
        return "Видео не найдено. Проверьте ссылку и доступность публикации."
    if "unsupported url" in lowered:
        return "Формат ссылки не поддерживается. Проверьте, что это ссылка на YouTube или Instagram."
    if "unable to extract" in lowered:
        return "Не удалось разобрать страницу. Попробуйте обновить yt-dlp и повторить загрузку."
    return "Загрузка завершилась с ошибкой. Подробности приведены в техническом сообщении ниже."
