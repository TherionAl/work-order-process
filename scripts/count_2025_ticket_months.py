"""按创建时间统计 2025 年每月工单数量。

通用列表接口 `/tickets` 不接受创建时间过滤；搜索接口 `/tickets/search.json`
支持 `query=createDT:YYYY-MM`，并返回匹配数量，因此用它快速评估月度导出规模。
"""

from __future__ import annotations

import httpx

from work_order_process.config import load_settings


def main() -> None:
    settings = load_settings()
    auth = httpx.BasicAuth(settings.username, settings.password)
    total = 0

    with httpx.Client(base_url=settings.base_url, auth=auth, timeout=90, follow_redirects=True) as client:
        print("month,count")
        for month in range(1, 13):
            label = f"2025-{month:02d}"
            count = count_month(client, label)
            total += count
            print(f"{label},{count}")
        print(f"2025-total,{total}")


def count_month(client: httpx.Client, month_label: str) -> int:
    """查询单月工单数量。"""

    response = client.get(
        "/tickets/search.json",
        params={
            "query": f"createDT:{month_label}",
            "sort_by": "createDT",
            "sort_order": "asc",
            "per_page": 1,
        },
    )
    response.raise_for_status()
    body = response.json()
    tickets = body.get("tickets") if isinstance(body, dict) else {}
    return int(tickets.get("count") or 0)


if __name__ == "__main__":
    main()
