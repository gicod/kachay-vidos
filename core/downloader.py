import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from PySide6.QtCore import QThread, Signal

PLATFORM_YOUTUBE = "youtube"
PLATFORM_INSTAGRAM = "instagram"

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

def parse_urls(text: str):
    result = []
    seen = set()
    for line in re.split(r"[\s,;]+", text.strip()):
        url = line.strip()
        if url.startswith(("https://", "http://")) and url not in seen:
            seen.add(url)
            result.append(url)
    return result

class DownloadWorker(QThread):
    progress = Signal(str, int, str)
    finished_task = Signal(str, bool, str)
    log_event = Signal(str, str, str, str)  # task_id, level, message, technical details

    def __init__(self, task_id, url, settings):
        super().__init__()
        self.task_id = task_id
        self.url = url
        self.settings = settings
        self._cancel_requested = False

    def cancel(self):
        self._cancel_requested = True
        self.requestInterruption()

    def _log(self, level, message, details=""):
        self.log_event.emit(self.task_id, level, message, details)

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

        options = {
            "format": format_selector,
            "outtmpl": str(folder / "%(title).180B [%(id)s].%(ext)s"),
            "noplaylist": False,
            "ignoreerrors": False,
            "continuedl": True,
            "overwrites": False,
            "windowsfilenames": True,
            "progress_hooks": [self._progress_hook],
            "quiet": True,
            "no_warnings": False,
            "logger": WorkerYtDlpLogger(self),
            # Pass an explicit executable path so yt-dlp also works when FFmpeg
            # is installed inside the Python environment and is not on PATH.
            "ffmpeg_location": ffmpeg_path,
        }
        if self.settings.get("container", "mp4") == "mp4":
            options["merge_output_format"] = "mp4"

        # Log the equivalent command with the URL query removed to avoid exposing tokens.
        command = ["yt-dlp", "--format", format_selector, "--output", str(folder / "%(title).180B [%(id)s].%(ext)s")]
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
                self.finished_task.emit(self.task_id, False, "Отменено (остановка после текущего действия)")
            else:
                self._log("SUCCESS", "Загрузка успешно завершена.")
                self.finished_task.emit(self.task_id, True, "Загрузка завершена")
        except Exception as error:
            raw_message = str(error).strip().splitlines()
            technical = raw_message[-1][:1000] if raw_message else repr(error)
            explanation = explain_error(technical)
            self._log("ERROR", explanation, technical)
            self.finished_task.emit(self.task_id, False, f"{explanation} · {technical[:140]}")

    def _progress_hook(self, data):
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
