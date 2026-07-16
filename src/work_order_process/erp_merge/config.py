from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "config"
    / "erp_merge_rules.toml"
)
REQUIRED_SECTIONS = ["统计日期区间", "营销平台映射", "体系工程师", "金额换算"]
REQUIRED_DATE_KEYS = ["去年起始", "去年截止", "今年起始", "今年截止"]


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """加载并校验TOML规则配置，缺失必填项时抛出 ValueError"""
    target_path = config_path or DEFAULT_CONFIG_PATH
    if not target_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{target_path}")

    with open(target_path, "rb") as f:
        config = tomllib.load(f)

    for section in REQUIRED_SECTIONS:
        if section not in config:
            raise ValueError(f"配置缺少必填项：{section}")

    date_section = config["统计日期区间"]
    for key in REQUIRED_DATE_KEYS:
        if key not in date_section:
            raise ValueError(f"统计日期区间缺少必填项：{key}")

    return config
