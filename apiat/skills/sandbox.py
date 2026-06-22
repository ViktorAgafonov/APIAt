"""Docker sandbox: безопасный запуск кода с настраиваемыми лимитами.

Профили изоляции (задаются в метаданных навыка):
  isolated — без сети, без диска хоста (отчёты, вычисления)       [по умолчанию]
  network  — с сетью, без диска хоста (HTTP-запросы, парсинг)
  storage  — без сети, с монтированием data_dir/rw/ (архивы, split-файлы)
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_DOCKER_IMAGE = "python:3.12-slim"

# Дефолтные лимиты
_DEFAULTS: dict[str, str | int] = {
    "memory": "128m",
    "cpu_quota": "50000",   # 50% одного ядра (100000 = 100%)
    "timeout": 30,
    "tmpfs_size": "32m",
    "profile": "isolated",
}

IsolationProfile = Literal["isolated", "network", "storage"]


@dataclass
class SkillConfig:
    """Лимиты и профиль изоляции для конкретного навыка.

    Читаются из метаданных в заголовке файла навыка:
        # skill:memory=256m
        # skill:timeout=60
        # skill:profile=network
        # skill:tmpfs=64m
        # skill:storage_mount=/opt/apiat/data/downloads
    """
    profile: IsolationProfile = "isolated"
    memory: str = "128m"
    cpu_quota: str = "50000"
    timeout: int = 30
    tmpfs_size: str = "32m"
    storage_mount: str = ""   # путь хоста для монтирования (только profile=storage)

    @classmethod
    def from_code(cls, code: str) -> "SkillConfig":
        """Парсит метаданные из комментариев вида '# skill:key=value'."""
        cfg = cls()
        for m in re.finditer(r"#\s*skill:(\w+)=(\S+)", code):
            key, val = m.group(1).lower(), m.group(2)
            if key == "profile" and val in ("isolated", "network", "storage"):
                cfg.profile = val  # type: ignore[assignment]
            elif key == "memory":
                cfg.memory = val
            elif key == "cpu_quota":
                cfg.cpu_quota = val
            elif key == "timeout":
                cfg.timeout = int(val)
            elif key in ("tmpfs", "tmpfs_size"):
                cfg.tmpfs_size = val
            elif key == "storage_mount":
                cfg.storage_mount = val
        return cfg

    def describe(self) -> str:
        """Человекочитаемое описание конфига для промта LLM."""
        lines = [
            f"- Профиль изоляции: {self.profile}",
            f"- RAM лимит: {self.memory}",
            f"- CPU: {int(self.cpu_quota) // 1000}%",
            f"- Timeout: {self.timeout} сек",
            f"- /tmp размер: {self.tmpfs_size}",
        ]
        if self.profile == "network":
            lines.append("- Сеть: ДОСТУПНА (HTTP/HTTPS запросы разрешены)")
        else:
            lines.append("- Сеть: НЕДОСТУПНА (--network none)")
        if self.profile == "storage" and self.storage_mount:
            lines.append(f"- Диск: смонтирован {self.storage_mount} -> /data (read-write)")
        else:
            lines.append("- Диск хоста: НЕДОСТУПЕН (только /tmp)")
        return "\n".join(lines)


@dataclass
class SandboxResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int

    @property
    def output(self) -> str:
        return self.stdout if self.stdout else self.stderr


def run_in_sandbox(
    code: str,
    cfg: SkillConfig | None = None,
    data_dir: Path | None = None,
) -> SandboxResult:
    """Запускает Python-код в Docker-контейнере с параметрами из SkillConfig."""
    if cfg is None:
        cfg = SkillConfig.from_code(code)

    with tempfile.TemporaryDirectory() as tmpdir:
        script = Path(tmpdir) / "skill.py"
        script.write_text(textwrap.dedent(code), encoding="utf-8")

        cmd = [
            "docker", "run", "--rm",
            "--memory", cfg.memory,
            "--cpu-quota", cfg.cpu_quota,
            "--read-only",
            "--tmpfs", f"/tmp:size={cfg.tmpfs_size}",
            "--volume", f"{tmpdir}:/sandbox:ro",
            "--workdir", "/sandbox",
        ]

        # Сеть
        if cfg.profile == "network":
            cmd += ["--network", "bridge"]
        else:
            cmd += ["--network", "none"]

        # Монтирование диска для storage-навыков
        if cfg.profile == "storage":
            mount_src = cfg.storage_mount
            if not mount_src and data_dir:
                mount_src = str(data_dir / "downloads" / "done")
            if mount_src:
                Path(mount_src).mkdir(parents=True, exist_ok=True)
                cmd += ["--volume", f"{mount_src}:/data:rw"]
                # /data доступен на запись — убираем --read-only для корневой fs
                cmd.remove("--read-only")
                cmd += ["--tmpfs", f"/tmp:size={cfg.tmpfs_size}"]

        cmd += [_DOCKER_IMAGE, "python", "skill.py"]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=cfg.timeout + 5,
            )
            return SandboxResult(
                success=result.returncode == 0,
                stdout=result.stdout[:8192],
                stderr=result.stderr[:2048],
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                success=False,
                stdout="",
                stderr=f"Timeout: код не завершился за {cfg.timeout} сек",
                exit_code=-1,
            )
        except FileNotFoundError:
            return SandboxResult(
                success=False,
                stdout="",
                stderr="Docker не найден на сервере",
                exit_code=-2,
            )


class DockerSandbox:
    """Обёртка sandbox для использования из SkillBuilder."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir

    def run(self, code: str, cfg: SkillConfig | None = None) -> SandboxResult:
        if cfg is None:
            cfg = SkillConfig.from_code(code)
        return run_in_sandbox(code, cfg, self._data_dir)
