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
        url = getattr(task, "url", None)
        if not url:
            return ToolResult(success=False, error="URL не задан")

        fmt = getattr(getattr(task, "format", None), "value", "mp4")
        max_quality = getattr(task, "max_quality", None)
        want_subs = getattr(task, "subtitles", False)
        meta_only = getattr(task, "metadata_only", False)
        self._dir.mkdir(parents=True, exist_ok=True)

        try:
            if meta_only:
                return self._get_metadata(url)
            path, title, sub_paths = self._download(url, fmt, max_quality, want_subs)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"Ошибка yt-dlp: {exc}")

        size = Path(path).stat().st_size if Path(path).exists() else 0
        attachments = [Attachment(filename=Path(path).name, path=path, size=size)]
        for sp in sub_paths:
            sp = Path(sp)
            if sp.exists():
                attachments.append(Attachment(filename=sp.name, path=str(sp), size=sp.stat().st_size, content_type="text/plain"))

        summary = f"Скачано: {title} ({fmt})"
        if sub_paths:
            summary += f", субтитры: {len(sub_paths)} файл(ов)"
        return ToolResult(
            success=True,
            summary=summary,
            data={"path": path, "title": title, "format": fmt},
            attachments=attachments,
        )

    def _get_metadata(self, url: str) -> ToolResult:
        import yt_dlp  # ленивый импорт

        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        lines = [
            f"Название : {info.get('title', '')}",
            f"Канал    : {info.get('uploader', '')}",
            f"Дата     : {info.get('upload_date', '')}",
            f"Длина    : {info.get('duration_string', '')}",
            f"Просмотры: {info.get('view_count', '')}",
            f"Описание : {(info.get('description') or '')[:300]}",
        ]
        return ToolResult(success=True, summary="\n".join(lines), data=info)

    def _download(
        self, url: str, fmt: str, max_quality: int | None, want_subs: bool
    ) -> tuple[str, str, list[str]]:
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

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if fmt == "mp3":
                path = str(Path(path).with_suffix(".mp3"))
            title = info.get("title", "video")

        sub_paths: list[str] = []
        if want_subs:
            for lang in ("ru", "en"):
                for ext in (".ru.srt", ".en.srt", f".{lang}.vtt"):
                    sp = Path(path).with_suffix(ext)
                    if sp.exists():
                        sub_paths.append(str(sp))
        return path, title, sub_paths
