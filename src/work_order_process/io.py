"""文件输出工具。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, data: Any) -> None:
    """以 UTF-8 和中文不转义的方式写入 JSON 文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
