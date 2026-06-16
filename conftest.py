"""Корневой conftest: добавляет корень проекта в sys.path для импорта apiat."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
