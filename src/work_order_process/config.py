"""项目配置读取。

接口和数据库凭据只允许来自环境变量或项目根目录的 .env。
非敏感运行参数可以使用代码默认值，并允许通过环境变量覆盖。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Windows 控制台默认 GBK 编码，Python 输出 UTF-8 会乱码。
# 强制 stdout/stderr 使用 UTF-8，一劳永逸解决所有入口的中文乱码。
# ---------------------------------------------------------------------------
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URL = "https://workorder.bosssoft.com.cn/api/v1"
DEFAULT_DICTIONARY_PATH = PROJECT_ROOT / "数据字典-帮我吧.pdf"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"


def _split_csv(value: str | None) -> list[str]:
    """把逗号分隔的环境变量拆成列表，并去掉空值。"""

    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class EndpointConfig:
    """各类实体接口的候选路径与请求方法配置。"""

    customer_paths: list[str]
    contact_paths: list[str]
    ticket_paths: list[str]
    request_methods: list[str]


@dataclass(frozen=True)
class MySQLConfig:
    """本机 MySQL 连接配置。"""

    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass(frozen=True)
class Settings:
    """运行时完整配置对象。"""

    username: str
    password: str
    base_url: str
    dictionary_path: Path
    output_dir: Path
    page_size: int
    max_pages: int
    ticket_since: str
    sample_size: int
    endpoint: EndpointConfig
    mysql: MySQLConfig


class ConfigError(RuntimeError):
    """配置缺失或不符合预期时抛出的异常。"""

    pass


def load_settings() -> Settings:
    """加载项目运行配置。

    默认会读取项目根目录下的 .env。接口路径保留多个候选值，是为了兼容接口
    文档和实际环境中可能存在的命名差异。

    启动时校验必填字段，避免运行到一半才发现凭据缺失。
    """

    load_dotenv(PROJECT_ROOT / ".env")

    username = os.getenv("WORKORDER_USERNAME", "")
    password = os.getenv("WORKORDER_PASSWORD", "")
    if not username or not password:
        raise ConfigError(
            "缺少接口认证凭据。请在 .env 或环境变量中设置 "
            "WORKORDER_USERNAME 和 WORKORDER_PASSWORD。"
        )

    base_url = os.getenv("WORKORDER_BASE_URL") or DEFAULT_BASE_URL
    base_url = base_url.rstrip("/")

    endpoint = EndpointConfig(
        customer_paths=_split_csv(os.getenv("WORKORDER_CUSTOMER_PATHS"))
        or [
            "/companies",
            "/customers",
            "/customer/list",
            "/company/list",
        ],
        contact_paths=_split_csv(os.getenv("WORKORDER_CONTACT_PATHS"))
        or [
            "/users",
            "/contacts",
            "/contact/list",
            "/contacters",
            "/contacter/list",
        ],
        ticket_paths=_split_csv(os.getenv("WORKORDER_TICKET_PATHS"))
        or [
            "/tickets",
            "/ticket/list",
            "/workorders",
            "/workorder/list",
            "/orders",
            "/order/list",
        ],
        request_methods=[
            method.upper()
            for method in (_split_csv(os.getenv("WORKORDER_HTTP_METHODS")) or ["GET", "POST"])
        ],
    )

    return Settings(
        username=username,
        password=password,
        base_url=base_url,
        dictionary_path=Path(os.getenv("WORKORDER_DICTIONARY_PATH", str(DEFAULT_DICTIONARY_PATH))),
        output_dir=Path(os.getenv("WORKORDER_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))),
        page_size=int(os.getenv("WORKORDER_PAGE_SIZE", "100")),
        max_pages=int(os.getenv("WORKORDER_MAX_PAGES", "200")),
        ticket_since=os.getenv("WORKORDER_TICKET_SINCE", "2025-01-01"),
        sample_size=int(os.getenv("WORKORDER_SAMPLE_SIZE", "10")),
        endpoint=endpoint,
        mysql=MySQLConfig(
            host=os.getenv("WORKORDER_MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("WORKORDER_MYSQL_PORT", "3306")),
            user=os.getenv("WORKORDER_MYSQL_USER", "workorder"),
            password=os.getenv("WORKORDER_MYSQL_PASSWORD", ""),
            database=os.getenv("WORKORDER_MYSQL_DATABASE", "work_order_datalake"),
        ),
    )
