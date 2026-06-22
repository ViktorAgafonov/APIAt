"""YouTube Tool: загрузка через yt-dlp (ленивый импорт)."""

from __future__ import annotations

from pathlib import Path

from ..models.base import BaseTask
from ..models.email import Attachment
from .base import Tool, ToolResult


class YoutubeTool(Tool):
    """Скачивает видео/аудио. yt-dlp импортируется только при вызове."""

    name = "youtube"

    def __init__(self, data_dir: str | Path = "data") -> None:
        self._dir = Path(data_dir) / "youtube"

    async def execute(self, task: BaseTask) -> ToolResult:
        url = getattr(task, "url", None) or ""
        channel_search = getattr(task, "channel_search", None)
        fmt = getattr(getattr(task, "format", None), "value", "mp4")
        max_quality = getattr(task, "max_quality", None)
        want_subs = getattr(task, "subtitles", False)
        meta_only = getattr(task, "metadata_only", False)
        want_thumb = getattr(task, "thumbnail", False)
        self._dir.mkdir(parents=True, exist_ok=True)

        # Если задан поиск канала — найти последнее видео
        if channel_search and not url:
            try:
                url = self._find_latest_from_channel(channel_search)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(success=False, error=f"Канал не найден: {exc}")

        if not url:
            return ToolResult(success=False, error="URL не задан и канал не найден")

        try:
            if meta_only:
                return self._get_metadata(url, want_thumb)
            path, title, sub_paths, thumb_path = self._download(url, fmt, max_quality, want_subs, want_thumb)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            # Упрощаем сообщение об ошибке yt-dlp
            if "404" in err or "Unable to download" in err:
                hint = f"\nПопробуйте указать точный URL канала или видео."
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

        summary = f"Скачано: {title} ({fmt})"
        if sub_paths:
            summary += f", субтитры: {len(sub_paths)} файл(ов)"
        if thumb_path:
            summary += ", обложка приложена"
        return ToolResult(
            success=True,
            summary=summary,
            data={"path": path, "title": title, "format": fmt},
            attachments=attachments,
        )

    def _find_latest_from_channel(self, channel_name: str) -> str:
        """Ищет последнее видео канала по имени через ytsearch."""
        import yt_dlp  # ленивый импорт

        # Пробуем прямой handle @channel_name, потом поиск
        for probe_url in (
            f"https://www.youtube.com/@{channel_name}/videos",
            f"https://www.youtube.com/c/{channel_name}/videos",
            f"ytsearch1:{channel_name}",
        ):
            try:
                opts = {"quiet": True, "extract_flat": True, "playlist_items": "1"}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(probe_url, download=False)
                entries = info.get("entries") or []
                if entries:
                    vid_id = entries[0].get("id") or entries[0].get("url", "")
                    if vid_id:
                        return f"https://www.youtube.com/watch?v={vid_id}"
            except Exception:  # noqa: BLE001
                continue
        raise ValueError(f"Канал '{channel_name}' не найден. Укажите точный URL.")

    def _get_metadata(self, url: str, want_thumb: bool = False) -> ToolResult:
        import yt_dlp  # ленивый импорт

        opts: dict = {"quiet": True}
        if want_thumb:
            opts["writethumbnail"] = True
            opts["outtmpl"] = str(self._dir / "%(title)s.%(ext)s")
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
                tp = Path(self._dir / f"{info.get('title', 'thumb')}{ext}")
                if tp.exists():
                    ct = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
                    attachments.append(Attachment(filename=tp.name, path=str(tp), size=tp.stat().st_size, content_type=ct))
                    break
        return ToolResult(success=True, summary="\n".join(lines), data=info, attachments=attachments)

    def _download(
        self, url: str, fmt: str, max_quality: int | None, want_subs: bool, want_thumb: bool
    ) -> tuple[str, str, list[str], str | None]:
        import yt_dlp  # ленивый импорт

        outtmpl = str(self._dir / "%(title)s.%(ext)s")
        opts: dict = {"outtmpl": outtmpl, "quiet": True, "noprogress": True}

        if fmt == "mp3":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]
        else:
            height = f"[height<={max_quality}]" if max_quality else ""
            opts["format"] = f"bestvideo{height}+bestaudio/best{height}"

        if want_subs:
            opts["writesubtitles"] = True
            opts["writeautomaticsub"] = True
            opts["subtitleslangs"] = ["ru", "en"]
            opts["subtitlesformat"] = "srt"

        if want_thumb:
            opts["writethumbnail"] = True

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if fmt == "mp3":
                path = str(Path(path).with_suffix(".mp3"))
            title = info.get("title", "video")

        sub_paths: list[str] = []
        if want_subs:
            base = Path(path).with_suffix("")
            for candidate in base.parent.glob(f"{base.name}*.srt"):
                sub_paths.append(str(candidate))
            for candidate in base.parent.glob(f"{base.name}*.vtt"):
                sub_paths.append(str(candidate))

        thumb_path: str | None = None
        if want_thumb:
            base = Path(path).with_suffix("")
            for ext in (".jpg", ".jpeg", ".webp", ".png"):
                tp = base.parent / (base.name + ext)
                if tp.exists():
                    thumb_path = str(tp)
                    break

        return path, title, sub_paths, thumb_path
