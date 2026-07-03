"""读取帮我吧通用接口文档并查找工单字段相关页面。"""

from __future__ import annotations

import argparse
import html
import json
import re
from urllib.parse import urljoin

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="读取帮我吧通用接口文档。")
    parser.add_argument("urls", nargs="*", default=["https://doc.bangwo8.com/"])
    parser.add_argument("--keyword", action="append", default=[])
    args = parser.parse_args()

    base = "https://doc.bangwo8.com/"
    if args.urls != [base]:
        for url in args.urls:
            inspect_api_page(url)
        return

    text = httpx.get(base, timeout=60, follow_redirects=True).text
    links = []
    pattern = re.compile(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.S)
    for match in pattern.finditer(text):
        href = html.unescape(match.group(1))
        label = re.sub("<.*?>", "", match.group(2))
        label = html.unescape(re.sub(r"\s+", " ", label)).strip()
        keywords = args.keyword or ["工单", "字段"]
        if any(keyword in label for keyword in keywords):
            links.append({"label": label, "url": urljoin(base, href)})
    print(json.dumps(links, ensure_ascii=False, indent=2))


def inspect_api_page(url: str) -> None:
    """抽取单个接口文档页里的接口路径和关键片段。"""

    text = httpx.get(url, timeout=60, follow_redirects=True).text
    paths = sorted(set(re.findall(r"/api/v[12]/[^<\"\\s]+", text)))
    print(json.dumps({"url": url, "length": len(text), "paths": paths}, ensure_ascii=False, indent=2))
    for marker in ("请求地址", "返回参数", "返回示例", "ticket_fields", "custom_field_options"):
        index = text.find(marker)
        if index == -1:
            continue
        excerpt = text[max(0, index - 300) : index + 800]
        excerpt = re.sub(r"<[^>]+>", " ", excerpt)
        excerpt = html.unescape(re.sub(r"\s+", " ", excerpt)).strip()
        print(f"\n--- {marker} ---")
        print(excerpt)


if __name__ == "__main__":
    main()
