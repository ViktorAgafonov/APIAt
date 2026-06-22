"""Docker sandbox: безопасный запуск сгенерированного кода в изолированном контейнере."""

from __future__ import annotations

import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

# Образ с Python и базовыми библиотеками (psutil, requests уже доступны)
_DOCKER_IMAGE = "python:3.12-slim"

# Лимиты контейнера
_MEM_LIMIT = "128m"
_CPU_QUOTA = "50000"   # 50% одного ядра
_TIMEOUT_SEC = 30


@dataclass
class SandboxResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int

    @property
    def output(self) -> str:
        return self.stdout if self.stdout else self.stderr


def run_in_sandbox(code: str, timeout: int = _TIMEOUT_SEC) -> SandboxResult:
    """Запускает Python-код в изолированном Docker-контейнере.

    Контейнер:
    - без сетевого доступа (--network none)
    - без доступа к файловой системе хоста
    - лимит RAM 128 MB, CPU 50%
    - автоудаление после завершения (--rm)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        script = Path(tmpdir) / "skill.py"
        script.write_text(textwrap.dedent(code), encoding="utf-8")

        cmd = [
            "docker", "run", "--rm",
            "--network", "none",
            "--memory", _MEM_LIMIT,
            "--cpu-quota", _CPU_QUOTA,
            "--read-only",
            "--tmpfs", "/tmp:size=32m",
            "--volume", f"{tmpdir}:/sandbox:ro",
            "--workdir", "/sandbox",
            _DOCKER_IMAGE,
            "python", "skill.py",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 5,
            )
            return SandboxResult(
                success=result.returncode == 0,
                stdout=result.stdout[:4096],
                stderr=result.stderr[:2048],
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                success=False,
                stdout="",
                stderr=f"Timeout: код не завершился за {timeout} сек",
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
    """Обёртка для удобного использования sandbox из SkillBuilder."""

    def run(self, code: str, timeout: int = _TIMEOUT_SEC) -> SandboxResult:
        return run_in_sandbox(code, timeout)
