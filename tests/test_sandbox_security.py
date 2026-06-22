"""Тесты безопасности sandbox: валидация storage_mount."""

from pathlib import Path
from apiat.skills.sandbox import SkillConfig, run_in_sandbox


def test_storage_mount_outside_data_dir_blocked(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cfg = SkillConfig(profile="storage", storage_mount="/etc")
    result = run_in_sandbox("print('ok')", cfg, data_dir=data_dir)
    assert not result.success
    assert "вне data_dir" in result.stderr or result.exit_code == -3


def test_storage_mount_inside_data_dir_allowed_path(tmp_path):
    """Проверяем что валидный путь внутри data_dir проходит без ошибки пути."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    valid_mount = str(data_dir / "downloads" / "done")
    cfg = SkillConfig(profile="storage", storage_mount=valid_mount)
    # Docker может отсутствовать — проверяем только что ошибка не про mount
    result = run_in_sandbox("print('ok')", cfg, data_dir=data_dir)
    assert result.exit_code != -3  # не заблокировано по безопасности


def test_skill_config_from_code_parses_profile():
    code = "# skill:profile=network\nprint('hi')"
    cfg = SkillConfig.from_code(code)
    assert cfg.profile == "network"


def test_skill_config_rejects_unknown_profile():
    code = "# skill:profile=admin\nprint('hi')"
    cfg = SkillConfig.from_code(code)
    assert cfg.profile == "isolated"  # дефолт, неизвестный профиль игнорируется


def test_skill_config_parses_memory_and_timeout():
    code = "# skill:memory=256m\n# skill:timeout=60\nprint('x')"
    cfg = SkillConfig.from_code(code)
    assert cfg.memory == "256m"
    assert cfg.timeout == 60
