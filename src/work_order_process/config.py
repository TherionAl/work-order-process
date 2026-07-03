"""项目配置读取。

配置来源优先级为：环境变量/.env > agents.md > 代码默认值。
这样既能把接口账号、密码等参数记录在 agents.md 中供新会话读取，也允许临时用
.env 或环境变量覆盖接口路径、分页大小、抽样数量等运行参数。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENTS_FILE = PROJECT_ROOT / "agents.md"
DEFAULT_BASE_URL = "https://workorder.bosssoft.com.cn/api/v1"
DEFAULT_DICTIONARY_PATH = PROJECT_ROOT / "数据字典-帮我吧.pdf"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"


def _read_agents_defaults(path: Path = AGENTS_FILE) -> dict[str, str]:
    """从 agents.md 中读取用户名、密码和接口地址前缀。"""

    if not path.exists():
        return {}

    text = path.read_text(encoding="utf-8", errors="ignore")
    patterns = {
        "username": r'USERNAME\s*=\s*"([^"]+)"',
        "password": r'PASSWORD\s*=\s*"([^"]+)"',
        "base_url": r'实际项目地址前缀\s*=\s*"([^"]+)"',
    }
    values: dict[str, str] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            values[key] = match.group(1).strip()
    return values


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


def load_settings() -> Settings:
    """加载项目运行配置。

    默认会读取项目根目录下的 .env 和 agents.md。接口路径保留多个候选值，
    是为了兼容接口文档和实际环境中可能存在的命名差异。
    """

    load_dotenv(PROJECT_ROOT / ".env")
    agents = _read_agents_defaults()

    base_url = os.getenv("WORKORDER_BASE_URL") or agents.get("base_url") or DEFAULT_BASE_URL
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
        request_methods=[method.upper() for method in (_split_csv(os.getenv("WORKORDER_HTTP_METHODS")) or ["GET", "POST"])],
    )

    return Settings(
        username=os.getenv("WORKORDER_USERNAME") or agents.get("username", ""),
        password=os.getenv("WORKORDER_PASSWORD") or agents.get("password", ""),
        base_url=base_url,
        dictionary_path=Path(os.getenv("WORKORDER_DICTIONARY_PATH", str(DEFAULT_DICTIONARY_PATH))),
        output_dir=Path(os.getenv("WORKORDER_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))),
        page_size=int(os.getenv("WORKORDER_PAGE_SIZE", "100")),
        max_pages=int(os.getenv("WORKORDER_MAX_PAGES", "200")),
        ticket_since=os.getenv("WORKORDER_TICKET_SINCE", "2025-01-01"),
        sample_size=int(os.getenv("WORKORDER_SAMPLE_SIZE", "10")),
        endpoint=endpoint,
    )
