"""config.py 单元测试。

验证配置加载和校验逻辑。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from work_order_process import config
from work_order_process.config import ConfigError, _split_csv, load_settings


def test_split_csv_handles_normal_comma_separated() -> None:
    assert _split_csv("a,b,c") == ["a", "b", "c"]


def test_split_csv_handles_empty_string() -> None:
    assert _split_csv("") == []


def test_split_csv_handles_none() -> None:
    assert _split_csv(None) == []


def test_split_csv_strips_whitespace() -> None:
    assert _split_csv(" a , b , c ") == ["a", "b", "c"]


def test_split_csv_removes_empty_items() -> None:
    assert _split_csv("a,,b,") == ["a", "b"]


def test_load_settings_raises_on_missing_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证缺少凭据时抛出 ConfigError。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("work_order_process.config.PROJECT_ROOT", tmp_path)
    # 清除可能从系统环境变量继承的凭据
    monkeypatch.delenv("WORKORDER_USERNAME", raising=False)
    monkeypatch.delenv("WORKORDER_PASSWORD", raising=False)
    # 确保 load_dotenv 不读到真实 .env（override=False 不会覆盖已有 env）
    monkeypatch.setattr("work_order_process.config.load_dotenv", lambda *a, **kw: None)

    with pytest.raises(ConfigError, match="缺少接口认证凭据"):
        load_settings()


def test_load_settings_ignores_agents_file_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        config,
        "_read_agents_defaults",
        lambda: {"username": "file_user", "password": "file_password"},
        raising=False,
    )
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("WORKORDER_USERNAME", raising=False)
    monkeypatch.delenv("WORKORDER_PASSWORD", raising=False)

    with pytest.raises(ConfigError, match="缺少接口认证凭据"):
        load_settings()


def test_load_settings_accepts_env_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证通过环境变量设置凭据可以正常加载。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("work_order_process.config.PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("WORKORDER_USERNAME", "test_user")
    monkeypatch.setenv("WORKORDER_PASSWORD", "test_pass")
    monkeypatch.delenv("WORKORDER_MYSQL_USER", raising=False)

    settings = load_settings()
    assert settings.username == "test_user"
    assert settings.password == "test_pass"
    assert settings.mysql.user == "workorder"
