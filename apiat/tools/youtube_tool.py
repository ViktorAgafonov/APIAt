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
        self._dir.mkdir(parents=True, exist_ok=True)

        try:
            path, title = self._download(url, fmt, max_quality)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"Ошибка yt-dlp: {exc}")

        size = Path(path).stat().st_size if Path(path).exists() else 0
        return ToolResult(
            success=True,
            summary=f"Скачано: {title} ({fmt})",
            data={"path": path, "title": title, "format": fmt},
            attachments=[Attachment(filename=Path(path).name, path=path, size=size)],
        )

    def _download(self, url: str, fmt: str, max_quality: int | None) -> tuple[str, str]:
        import yt_dlp  # ленивый импорт

        outtmpl = str(self._dir / "%(title)s.%(ext)s")
        opts: dict = {"outtmpl": outtmpl, "quiet": True, "noprogress": True}

        if fmt == "mp3":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                }
            ]
        else:
            height = f"[height<={max_quality}]" if max_quality else ""
            opts["format"] = f"bestvideo{height}+bestaudio/best{height}"

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if fmt == "mp3":
                path = str(Path(path).with_suffix(".mp3"))
            return path, info.get("title", "video")
