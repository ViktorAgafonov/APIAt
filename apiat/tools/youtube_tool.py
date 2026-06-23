"""YouTube Tool: загрузка через yt-dlp (ленивый импорт)."""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from ..models.base import BaseTask
from ..models.email import Attachment
from .base import Tool, ToolResult


def _fmt_srt_time(seconds: float) -> str:
    """Конвертирует секунды в формат SRT: HH:MM:SS,mmm."""
    ms = int((seconds % 1) * 1000)
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# Допустимые значения качества (ограничиваем снизу/сверху)
_ALLOWED_QUALITIES = (144, 240, 360, 480, 720, 1080)
_DEFAULT_QUALITY = 480
_YOUTUBE_TTL_SEC = 2 * 3600  # 2 часа во временной папке


class YoutubeTool(Tool):
    """Скачивает видео/аудио. yt-dlp импортируется только при вызове."""

    name = "youtube"

    def __init__(self, data_dir: str | Path = "data") -> None:
        self._dir = Path(data_dir) / "youtube"
        self._pending = self._dir / "pending"  # временная папка с TTL

    @staticmethod
    def _clamp_quality(q: int | None) -> int:
        """Округляет качество до ближайшего допустимого значения ≤ запрошенного."""
        if q is None:
            return _DEFAULT_QUALITY
        # Берём наибольшее допустимое значение, которое не превышает запрошенное
        candidates = [v for v in _ALLOWED_QUALITIES if v <= q]
        return max(candidates) if candidates else _ALLOWED_QUALITIES[0]

    def _cleanup_pending(self) -> None:
        """Удаляет файлы из pending старше TTL."""
        if not self._pending.exists():
            return
        now = time.time()
        for f in self._pending.iterdir():
            try:
                if now - f.stat().st_mtime > _YOUTUBE_TTL_SEC:
                    if f.is_dir():
                        shutil.rmtree(f, ignore_errors=True)
                    else:
                        f.unlink(missing_ok=True)
            except OSError:
                pass

    async def execute(self, task: BaseTask) -> ToolResult:
        url = getattr(task, "url", None) or ""
        channel_search = getattr(task, "channel_search", None)
        fmt = getattr(getattr(task, "format", None), "value", "mp4")
        max_quality = self._clamp_quality(getattr(task, "max_quality", None))
        want_subs = getattr(task, "subtitles", False)
        meta_only = getattr(task, "metadata_only", False)
        want_thumb = getattr(task, "thumbnail", False)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._pending.mkdir(parents=True, exist_ok=True)
        self._cleanup_pending()

        # Если задан поиск канала — найти последнее видео
        if channel_search and not url:
            try:
                url = self._find_latest_from_channel(channel_search)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(success=False, error=f"Канал не найден: {exc}")

        if not url:
            return ToolResult(success=False, error="URL не задан и канал не найден")

        # Эвристика: если url похож на название канала а не на ссылку — ищем через channel_search
        if url and not url.startswith("http") and not url.startswith("ytsearch"):
            channel_hint = url.lstrip("@").strip()
            try:
                url = self._find_latest_from_channel(channel_hint)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(success=False, error=f"Канал '{channel_hint}' не найден: {exc}")

        try:
            if meta_only:
                return self._get_metadata(url, want_thumb)
            path, title, sub_paths, thumb_path = self._download(url, fmt, max_quality, want_subs, want_thumb)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            # При 429 на видео — пробуем только метаданные + обложку
            if "429" in err or "Too Many Requests" in err:
                logger.warning("429 при скачивании видео, переключаемся на метаданные")
                try:
                    result = self._get_metadata(url, want_thumb)
                    result = result.model_copy(update={
                        "summary": result.summary + "\n\n⚠️ Видео не скачано (YouTube rate-limit по IP). "
                                   "Субтитры и обложка могут быть приложены из метаданных.",
                    })
                    return result
                except Exception:  # noqa: BLE001
                    pass
            if "404" in err or "Unable to download" in err or "429" in err:
                hint = "\nYouTube временно ограничил запросы с IP сервера. Попробуйте через 10-15 минут."
            else:
                hint = ""
            return ToolResult(success=False, error=f"Ошибка yt-dlp: {err}{hint}")

        size = Path(path).stat().st_size if Path(path).exists() else 0
        attachments = [Attachment(filename=Path(path).name, path=path, size=size)]
        for sp in sub_paths:
            sp = Path(sp)
            if sp.exists():
                attachments.append(Attachment(
                    filename=sp.name, path=str(sp),
                    size=sp.stat().st_size, content_type="text/plain",
                ))
        if thumb_path and Path(thumb_path).exists():
            tp = Path(thumb_path)
            ct = "image/jpeg" if tp.suffix.lower() in (".jpg", ".jpeg") else "image/webp"
            attachments.append(Attachment(filename=tp.name, path=str(tp), size=tp.stat().st_size, content_type=ct))

        summary = f"Скачано: {title} ({fmt}, до {max_quality}p)"
        if want_subs and sub_paths:
            summary += f", субтитры: {len(sub_paths)} файл(ов)"
        elif want_subs and not sub_paths:
            summary += "\nСубтитры недоступны (YouTube ограничил запросы с этого IP). Попробуйте позже или запросите субтитры отдельно."
        if thumb_path:
            summary += ", обложка приложена"
        summary += f"\nФайл хранится {_YOUTUBE_TTL_SEC // 3600} ч, после чего удаляется автоматически."
        return ToolResult(
            success=True,
            summary=summary,
            data={"path": path, "title": title, "format": fmt},
            attachments=attachments,
        )

    def _find_latest_from_channel(self, channel_name: str) -> str:
        """Ищет последнее видео канала по имени."""
        import yt_dlp  # ленивый импорт

        # Нормализуем: убираем пробелы → транслитерация не нужна, YouTube ищет по unicode
        name_clean = channel_name.strip()
        # Сначала пробуем handle (без пробелов), потом полное имя через поиск
        handle = name_clean.replace(" ", "_")
        probe_urls = [
            f"ytsearch1:{name_clean} канал",   # самый надёжный — через YouTube Search
            f"ytsearch1:{name_clean}",
            f"https://www.youtube.com/@{handle}/videos",
            f"https://www.youtube.com/c/{handle}/videos",
        ]
        opts = {"quiet": True, "extract_flat": True, "playlist_items": "1"}
        for probe_url in probe_urls:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(probe_url, download=False)
                entries = info.get("entries") or []
                if not entries and info.get("id"):
                    # Одиночное видео из поиска
                    return f"https://www.youtube.com/watch?v={info['id']}"
                if entries:
                    first = entries[0]
                    vid_id = first.get("id") or ""
                    if vid_id and len(vid_id) == 11:  # YouTube video ID всегда 11 символов
                        return f"https://www.youtube.com/watch?v={vid_id}"
            except Exception:  # noqa: BLE001
                continue
        raise ValueError(f"Канал '{channel_name}' не найден. Укажите точный URL канала.")

    def _get_metadata(self, url: str, want_thumb: bool = False) -> ToolResult:
        import yt_dlp  # ленивый импорт

        outtmpl = str(self._pending / "%(title)s.%(ext)s")
        opts: dict = {
            "quiet": True,
            "http_headers": {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
            "sleep_interval": 2,
        }
        if want_thumb:
            opts["writethumbnail"] = True
            opts["outtmpl"] = outtmpl
            opts["skip_download"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=want_thumb)
        lines = [
            f"Название : {info.get('title', '')}",
            f"Канал    : {info.get('uploader', '')}",
            f"Дата     : {info.get('upload_date', '')}",
            f"Длина    : {info.get('duration_string', '')}",
            f"Просмотры: {info.get('view_count', '')}",
            f"Описание : {(info.get('description') or '')[:300]}",
        ]
        attachments = []
        if want_thumb:
            for ext in (".jpg", ".jpeg", ".webp", ".png"):
                tp = self._pending / f"{info.get('title', 'thumb')}{ext}"
                if tp.exists():
                    ct = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
                    attachments.append(Attachment(filename=tp.name, path=str(tp), size=tp.stat().st_size, content_type=ct))
                    break
        return ToolResult(success=True, summary="\n".join(lines), data=info, attachments=attachments)

    @staticmethod
    def _base_opts(outtmpl: str) -> dict:
        return {
            "outtmpl": outtmpl,
            "quiet": True,
            "noprogress": True,
            "sleep_interval": 3,
            "max_sleep_interval": 8,
            "ignoreerrors": False,
            "extractor_args": {"youtube": {"skip": ["hls", "dash"]}},
            "http_headers": {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        }

    def _download(
        self, url: str, fmt: str, max_quality: int, want_subs: bool, want_thumb: bool
    ) -> tuple[str, str, list[str], str | None]:
        import yt_dlp  # ленивый импорт

        # Скачиваем в pending/ — файлы живут TTL и потом удаляются
        outtmpl = str(self._pending / "%(title)s.%(ext)s")
        opts = self._base_opts(outtmpl)

        if fmt == "mp3":
            # bestaudio без постпроцессора — ffmpeg может отсутствовать
            opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        else:
            # best — уже смерженный стрим, не требует ffmpeg
            opts["format"] = f"best[height<={max_quality}]/best"

        if want_thumb:
            opts["writethumbnail"] = True

        # Шаг 1: скачиваем видео (без субтитров — они могут дать 429)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if fmt == "mp3":
                path = str(Path(path).with_suffix(".mp3"))
            title = info.get("title", "video")

        # Шаг 2: субтитры отдельным проходом, чтобы ошибка не убила весь результат
        sub_paths: list[str] = []
        if want_subs:
            sub_paths = self._download_subs(url, title)

        thumb_path: str | None = None
        if want_thumb:
            base = Path(path).with_suffix("")
            for ext in (".jpg", ".jpeg", ".webp", ".png"):
                tp = base.parent / (base.name + ext)
                if tp.exists():
                    thumb_path = str(tp)
                    break

        return path, title, sub_paths, thumb_path

    def _download_subs(self, url: str, title: str) -> list[str]:
        """Скачивает субтитры через youtube-transcript-api (без ffmpeg/429).

        Fallback: yt-dlp skip_download если transcript-api не установлена.
        """
        # Извлекаем video_id из URL
        video_id = self._extract_video_id(url)
        if video_id:
            path = self._subs_via_transcript_api(video_id, title)
            if path:
                return [path]

        # Fallback: yt-dlp
        return self._subs_via_ytdlp(url, title)

    @staticmethod
    def _extract_video_id(url: str) -> str:
        """Извлекает 11-символьный video ID из YouTube URL."""
        m = re.search(r"(?:v=|youtu\.be/|/v/|/embed/)([\w-]{11})", url)
        return m.group(1) if m else ""

    def _subs_via_transcript_api(self, video_id: str, title: str) -> str | None:
        """Получает субтитры через youtube-transcript-api (быстро, без 429)."""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi  # ленивый импорт
        except ImportError:
            return None

        try:
            transcript = YouTubeTranscriptApi.get_transcript(
                video_id, languages=["ru", "ru-RU", "en"]
            )
        except Exception:  # noqa: BLE001 — субтитры могут отсутствовать
            try:
                # Попробуем любые доступные
                transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
                transcript = next(iter(transcripts)).fetch()
            except Exception:  # noqa: BLE001
                logger.warning("youtube-transcript-api: субтитры не найдены для %s", video_id)
                return None

        safe_title = title[:40].replace("/", "_").replace("\\", "_")
        out_path = self._pending / f"{safe_title}.srt"
        with out_path.open("w", encoding="utf-8") as f:
            for i, entry in enumerate(transcript, 1):
                start = entry["start"]
                duration = entry.get("duration", 2.0)
                end = start + duration
                f.write(f"{i}\n")
                f.write(f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n")
                f.write(f"{entry['text']}\n\n")
        return str(out_path)

    def _subs_via_ytdlp(self, url: str, title: str) -> list[str]:
        """Fallback: субтитры через yt-dlp skip_download; при ошибке — пустой список."""
        import yt_dlp  # ленивый импорт

        outtmpl = str(self._pending / "%(title)s.%(ext)s")
        opts = self._base_opts(outtmpl)
        opts["skip_download"] = True
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = ["ru", "en"]
        opts["subtitlesformat"] = "srt"
        opts["ignoreerrors"] = True

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
        except Exception:  # noqa: BLE001
            logger.warning("yt-dlp субтитры недоступны, пропускаем")
            return []

        prefix = title[:40].replace("/", "_")
        paths = []
        for pat in (f"*{prefix}*.srt", f"*{prefix}*.vtt"):
            paths.extend(str(p) for p in self._pending.glob(pat))
        return paths
