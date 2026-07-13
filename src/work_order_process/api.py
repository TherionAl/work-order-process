"""工单系统 API 访问层。

本文件只负责和线上接口通信：统一处理 Basic Auth、请求重试、分页读取、
详情接口读取，以及从接口返回体中提取列表数据。业务上的字段中文化、
外键替换等逻辑放在 resolver/transform 中，避免网络请求层和数据处理层混在一起。

实体详情接口（客服、联系人、客服组、模板）使用内部 LRU 缓存，
避免批量处理多条工单时对同一实体重复请求。
"""

from __future__ import annotations

import json
import os
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
import math
import random
import re
import threading
import time
from typing import Any, Callable, Iterator, TypedDict

import httpx

from .config import Settings


class ApiError(RuntimeError):
    """接口请求、配置或返回格式不符合预期时抛出的项目级异常。"""

    pass


# 工单搜索接口返回结构
class TicketSearchResponse(TypedDict, total=False):
    count: int
    results: list[dict[str, Any]]


class TicketDetail(TypedDict, total=False):
    ticketId: str
    subject: str
    descript: str
    custUserId: str
    servicerUserId: str
    ccUserIdList: str
    ticketType: str
    priorityLevel: str
    tagList: str
    ticketStatus: str
    createDT: str
    updateDT: str
    solveDT: str
    waitDT: str
    openDT: str
    closeDT: str
    servicerGroupId: str
    createrId: str
    agentId: str
    ticketSource: str
    ticketTemplateId: str
    ccGroupIdList: str
    customTemplateId: str
    createrType: str
    currentNodeField: str
    currentNodeFieldValue: str
    nodeFieldIntoTime: str
    queryIDs: str
    workflow_node_id: str
    workflow_id: str
    isDeleted: str
    deleterId: str
    deleteDT: str
    descriptattachments: Any
    custom_fields: list[dict[str, Any]]


@dataclass(frozen=True)
class EndpointResult:
    """探测接口可用性时记录单个路径的结果。"""

    path: str
    status_code: int
    ok: bool
    detail: str


class WorkOrderClient:
    """封装帮我吧工单系统的 HTTP 客户端。

    初始化时会从 Settings 中读取接口地址、用户名和密码，并使用 httpx.BasicAuth
    实现接口文档要求的 HTTP Basic 认证方式。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._ticket_fields_cache: list[dict[str, Any]] | None = None
        self._company_fields_cache: list[dict[str, Any]] | None = None
        auth = None
        if settings.username and settings.password:
            auth = httpx.BasicAuth(settings.username, settings.password)
        self.client = httpx.Client(
            base_url=settings.base_url,
            timeout=httpx.Timeout(float(os.getenv("WORKORDER_API_TIMEOUT", "300"))),
            follow_redirects=True,
            headers={"Accept": "application/json"},
            auth=auth,
        )
        # 实体详情缓存：避免批量处理时对同一实体重复请求
        self._cache: dict[str, dict[str, Any] | None] = {}

    def close(self) -> None:
        """关闭底层 httpx 连接池并清空实体缓存。"""

        self._cache.clear()
        self.client.close()

    def clear_cache(self) -> None:
        """手动清空实体详情缓存。"""

        self._cache.clear()

    def __enter__(self) -> "WorkOrderClient":
        """支持 with WorkOrderClient(...) as client 的用法。"""

        return self

    def __exit__(self, *_args: object) -> None:
        """退出上下文时释放网络连接并清空缓存。"""

        self.close()

    def authenticate(self) -> None:
        """检查认证参数是否已配置。

        线上接口使用 Basic Auth；这里先做本地参数检查，真正的账号密码有效性
        由后续接口响应判断。
        """

        if not self.settings.username or not self.settings.password:
            raise ApiError("Missing WORKORDER_USERNAME or WORKORDER_PASSWORD.")

    def prefetch_entities(
        self,
        *,
        contacts: Iterable[str] | None = None,
        companies: Iterable[str] | None = None,
        supports: Iterable[str] | None = None,
        groups: Iterable[str] | None = None,
        templates: Iterable[str] | None = None,
        max_workers: int = 8,
        semaphore: "threading.Semaphore | None" = None,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        """批量预取实体详情，填入缓存，后续 fetch_*_detail 直接命中缓存。

        使用线程池并发请求，受 semaphore 控制 QPS。
        progress_callback(entity_type, entity_id) 在每次请求完成后回调。
        """

        tasks: list[tuple[str, str]] = []
        for source, entity_type in (
            (contacts, "contact"),
            (companies, "company"),
            (supports, "support"),
            (groups, "group"),
            (templates, "template"),
        ):
            if source:
                for eid in source:
                    key = f"{entity_type}:{eid}"
                    if key not in self._cache:
                        tasks.append((entity_type, eid))

        if not tasks:
            return

        def _fetch_one(item: tuple[str, str]) -> None:
            if semaphore:
                semaphore.acquire()
            try:
                entity_type, entity_id = item
                if entity_type == "contact":
                    self.fetch_contact_detail(entity_id)
                elif entity_type == "company":
                    self.fetch_company_detail(entity_id)
                elif entity_type == "support":
                    self.fetch_support_detail(entity_id)
                elif entity_type == "group":
                    self.fetch_support_group_detail(entity_id)
                elif entity_type == "template":
                    self.fetch_ticket_template_detail(entity_id)
            finally:
                if progress_callback:
                    progress_callback(*item)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(_fetch_one, tasks))

    def probe_paths(self, paths: list[str]) -> list[EndpointResult]:
        """按配置的 GET/POST 方法探测候选接口路径是否可访问。"""

        results: list[EndpointResult] = []
        for path in paths:
            for method in self.settings.endpoint.request_methods:
                try:
                    response = self._request(method, path, {"page": 1, "pageSize": 1})
                    body = response.text[:180].replace("\n", " ")
                    ok = _looks_successful(response)
                    results.append(EndpointResult(f"{method} {path}", response.status_code, ok, body))
                except httpx.HTTPError as exc:
                    results.append(EndpointResult(f"{method} {path}", 0, False, str(exc)))
        return results

    def probe_auth_paths(self) -> list[EndpointResult]:
        """输出当前 Basic Auth 参数是否已从 agents.md 或 .env 读取到。"""

        ok = bool(self.settings.username and self.settings.password)
        detail = "HTTP Basic Auth is configured" if ok else "Missing username or password"
        return [EndpointResult("HTTP Basic Auth", 0, ok, detail)]

    def fetch_all(self, paths: list[str], extra_params: dict[str, Any] | None = None) -> list[dict]:
        """从一组候选路径里选择第一个能返回数据的分页接口。"""

        errors: list[str] = []
        for path in paths:
            try:
                data = self._fetch_paginated(path, extra_params or {})
                if data:
                    return data
            except ApiError as exc:
                errors.append(f"{path}: {exc}")
        raise ApiError("No configured endpoint returned data.\n" + "\n".join(errors[-8:]))

    def probe_entity_paths(
        self,
        paths: list[str],
        entity_type: str,
        sample_size: int = 3,
    ) -> list[dict[str, Any]]:
        """Probe candidate entity endpoints without exposing record values."""

        reports: list[dict[str, Any]] = []
        for path in paths:
            try:
                response = self._first_successful_request(
                    path,
                    {"page": 1, "pageNo": 1, "pageNum": 1, "current": 1, "pageSize": sample_size, "limit": sample_size},
                )
                if not _looks_successful(response):
                    raise ApiError(f"HTTP {response.status_code}: {response.text[:300]}")
                body = _json_or_empty(response)
                rows = _extract_items(body)
                count = _declared_item_count(body, len(rows))
            except ApiError as exc:
                reports.append({"path": path, "entity_type": entity_type, "status": "error", "error": str(exc)})
                continue
            reports.append(
                {
                    "path": path,
                    "entity_type": entity_type,
                    "status": "ok" if rows else "empty",
                    "count": count,
                    "sample_keys": sorted({str(key) for row in rows[:sample_size] for key in row}),
                }
            )
        return reports

    def fetch_customers(self) -> list[dict]:
        """获取客户/公司列表，保留接口原始字段。"""

        return self.fetch_all(self.settings.endpoint.customer_paths)

    def fetch_contacts(self) -> list[dict]:
        """获取联系人列表，保留接口原始字段。"""

        return self.fetch_all(self.settings.endpoint.contact_paths)

    def fetch_companies(self) -> list[dict]:
        """获取公司列表；当前接口文档中公司和客户列表路径一致。"""

        return self.fetch_all(self.settings.endpoint.customer_paths)

    def fetch_company_contacts(self) -> list[dict]:
        """获取公司联系人列表；当前接口文档中公司联系人和联系人路径一致。"""

        return self.fetch_all(self.settings.endpoint.contact_paths)

    def iter_companies(self) -> Iterator[list[dict]]:
        """Yield company rows page by page for large imports."""

        return self.iter_entity_pages(self.settings.endpoint.customer_paths, self.settings.page_size)

    def iter_contacts(self) -> Iterator[list[dict]]:
        """Yield contact rows page by page for large imports."""

        return self.iter_entity_pages(self.settings.endpoint.contact_paths, self.settings.page_size)

    def fetch_contact_detail(self, contact_id: str) -> dict | None:
        """按联系人主键读取联系人详情，用于把 custUserId 替换为联系人姓名。"""

        cache_key = f"contact:{contact_id}"
        if cache_key in self._cache:
            return _copy_detail(self._cache[cache_key])
        body = self._json_get(f"/users/{contact_id}")
        user = body.get("user") if isinstance(body, dict) else None
        if isinstance(user, list):
            result = user[0] if user else None
        else:
            result = user if isinstance(user, dict) else None
        self._cache[cache_key] = _copy_detail(result)
        return _copy_detail(result)

    def fetch_company_detail(self, company_id: str) -> dict | None:
        """按公司主键读取公司详情，供后续需要补全公司名称时复用。"""

        cache_key = f"company:{company_id}"
        if cache_key in self._cache:
            return _copy_detail(self._cache[cache_key])
        body = self._json_get(f"/companies/{company_id}")
        company = body.get("company") if isinstance(body, dict) else None
        if isinstance(company, list):
            result = company[0] if company else None
        else:
            result = company if isinstance(company, dict) else None
        self._cache[cache_key] = _copy_detail(result)
        return _copy_detail(result)

    def fetch_ticket_detail(self, ticket_id: str) -> dict | None:
        """按工单号读取工单详情，是三段式工单导出的原始数据来源。"""

        body = self._json_get(f"/tickets/{ticket_id}")
        ticket = body.get("ticket") if isinstance(body, dict) else None
        if isinstance(ticket, list):
            return ticket[0] if ticket else None
        return ticket if isinstance(ticket, dict) else None

    def search_tickets_by_create_month(self, month_label: str, page: int = 1, per_page: int = 1000) -> dict[str, Any]:
        """按创建月份搜索工单列表。

        通用文档中的搜索接口支持 `query=createDT:YYYY-MM`，并返回匹配总数。
        该接口适合后续按月导出：先拿月度工单 ID，再逐条获取详情。
        """

        body = self._json_get(
            "/tickets/search.json",
            {
                "query": f"createDT:{month_label}",
                "sort_by": "createDT",
                "sort_order": "asc",
                "page": page,
                "per_page": per_page,
            },
        )
        tickets = body.get("tickets") if isinstance(body, dict) else None
        if not isinstance(tickets, dict):
            raise ApiError(f"Unexpected ticket search response: {body}")
        return tickets

    def search_tickets_by_create_month_and_template(
        self,
        month_label: str,
        template_id: str,
        page: int = 1,
        per_page: int = 1000,
    ) -> dict[str, Any]:
        """按创建月份和工单模板搜索工单列表。

        搜索接口支持 `ticketTemplateId:<模板ID>` 条件。这个方法用于按模板分别抽样，
        避免为了分组而拉取整月所有工单详情。
        """

        body = self._json_get(
            "/tickets/search.json",
            {
                "query": f"createDT:{month_label} ticketTemplateId:{template_id}",
                "sort_by": "createDT",
                "sort_order": "asc",
                "page": page,
                "per_page": per_page,
            },
        )
        tickets = body.get("tickets") if isinstance(body, dict) else None
        if not isinstance(tickets, dict):
            raise ApiError(f"Unexpected ticket template search response: {body}")
        return tickets

    def fetch_support_detail(self, support_id: str) -> dict | None:
        """按客服 ID 读取客服详情，用于替换 servicerUserId/createrId 等字段。"""

        cache_key = f"support:{support_id}"
        if cache_key in self._cache:
            return _copy_detail(self._cache[cache_key])
        body = self._json_get(f"/supports/{support_id}")
        support = body.get("support") if isinstance(body, dict) else None
        if isinstance(support, list):
            result = support[0] if support else None
        else:
            result = support if isinstance(support, dict) else None
        self._cache[cache_key] = _copy_detail(result)
        return _copy_detail(result)

    def fetch_support_group_detail(self, group_id: str) -> dict | None:
        """按客服组 ID 读取客服组详情，用 sgName 替换客服组相关字段。"""

        cache_key = f"supportgroup:{group_id}"
        if cache_key in self._cache:
            return _copy_detail(self._cache[cache_key])
        body = self._json_get(f"/supportgroups/{group_id}")
        support_group = body.get("supportgroup") if isinstance(body, dict) else None
        if isinstance(support_group, list):
            result = support_group[0] if support_group else None
        else:
            result = support_group if isinstance(support_group, dict) else None
        self._cache[cache_key] = _copy_detail(result)
        return _copy_detail(result)

    def fetch_ticket_template_detail(self, template_id: str) -> dict | None:
        """按工单模板 ID 读取模板详情，用 ticketTemplateName 替换 ticketTemplateId。"""

        cache_key = f"template:{template_id}"
        if cache_key in self._cache:
            return _copy_detail(self._cache[cache_key])
        body = self._json_get(f"/tickettemplates/{template_id}")
        template = body.get("tickettemplate") if isinstance(body, dict) else None
        if isinstance(template, list):
            result = template[0] if template else None
        else:
            result = template if isinstance(template, dict) else None
        self._cache[cache_key] = _copy_detail(result)
        return _copy_detail(result)

    def fetch_ticket_templates(self) -> list[dict[str, Any]]:
        """读取全部工单模板列表，用于按模板分别抽样。"""

        body = self._json_get_or_list("/tickettemplates")
        if isinstance(body, dict):
            templates = body.get("tickettemplates") or body.get("ticket_templates") or body.get("data") or []
            if isinstance(templates, list):
                return [item for item in templates if isinstance(item, dict)]
        if isinstance(body, list):
            return [item for item in body if isinstance(item, dict)]
        return []

    def fetch_ticket_fields(self) -> list[dict[str, Any]]:
        """读取工单所有字段定义。

        通用文档中的“获取工单所有字段”接口是 /api/v1/tickets/ticket_fields2.json。
        由于当前客户端 base_url 已经是 /api/v1，这里请求 /tickets/ticket_fields2.json。
        返回内容用于解释 custom_fields 中的 field_xxx 和下拉选项 ID。
        """

        if self._ticket_fields_cache is not None:
            return self._ticket_fields_cache

        body = self._json_get_or_list("/tickets/ticket_fields2.json")
        if isinstance(body, list):
            self._ticket_fields_cache = [item for item in body if isinstance(item, dict)]
            return self._ticket_fields_cache
        if isinstance(body, dict):
            fields = body.get("ticket_fields") or body.get("fields") or body.get("data") or []
            if isinstance(fields, list):
                self._ticket_fields_cache = [item for item in fields if isinstance(item, dict)]
                return self._ticket_fields_cache
        self._ticket_fields_cache = []
        return self._ticket_fields_cache

    def fetch_company_fields(self) -> list[dict[str, Any]]:
        """读取公司字段定义，用于补充工单中引用公司字段选项的 value。"""

        if self._company_fields_cache is not None:
            return self._company_fields_cache

        body = self._json_get_or_list("/companies/company_fields.json")
        if isinstance(body, dict):
            fields = body.get("company_fields") or body.get("fields") or body.get("data") or []
            if isinstance(fields, list):
                self._company_fields_cache = [item for item in fields if isinstance(item, dict)]
                return self._company_fields_cache
        if isinstance(body, list):
            self._company_fields_cache = [item for item in body if isinstance(item, dict)]
            return self._company_fields_cache
        self._company_fields_cache = []
        return self._company_fields_cache

    def fetch_tickets(self) -> list[dict]:
        """获取 settings.ticket_since 之后的工单列表。"""

        return self.fetch_all(
            self.settings.endpoint.ticket_paths,
            {
                "startTime": self.settings.ticket_since,
                "createStartTime": self.settings.ticket_since,
                "createDTStart": self.settings.ticket_since,
            },
        )

    def fetch_ticket_sample_since(self, sample_size: int, since: str, seed: int | None = None) -> list[dict]:
        """从指定日期之后的工单中抽样。

        工单列表数量较大时不直接全量拉取，而是先估算 2025 年之后大致从哪一页开始，
        再随机抽页取样，最后用顺序扫描兜底补满样本数量。

        如果 since 之后的工单总数不足 sample_size，则直接全量返回，避免无效循环。
        """

        path = self.settings.endpoint.ticket_paths[0]
        first_body = self._ticket_page(path, 1)
        first_items = _extract_items(first_body)
        if not first_items:
            return []

        per_page = len(first_items)
        total = int(first_body.get("count") or per_page)
        total_pages = max(1, math.ceil(total / per_page))
        first_page = self._estimate_first_ticket_page_since(path, since, total_pages)

        # 先扫描前几页估算 since 之后的实际数量，如果不足 sample_size 直接全取
        since_count = self._count_tickets_since(path, since, first_page, total_pages)
        if since_count <= sample_size:
            return self._fetch_all_tickets_since(path, since, first_page, total_pages)

        rng = random.Random(seed)
        sampled: list[dict] = []
        seen_ids: set[str] = set()
        seen_pages: set[int] = set()

        max_attempts = max(sample_size * 4, 20)
        for _ in range(max_attempts):
            page = rng.randint(first_page, total_pages)
            if page in seen_pages:
                continue
            seen_pages.add(page)
            page_items = [item for item in _extract_items(self._ticket_page(path, page)) if _record_is_since(item, since)]
            rng.shuffle(page_items)
            for item in page_items[:1]:
                ticket_id = str(item.get("ticketId") or item)
                if ticket_id in seen_ids:
                    continue
                sampled.append(item)
                seen_ids.add(ticket_id)
                if len(sampled) >= sample_size:
                    return sampled

        for page in range(first_page, total_pages + 1):
            if page in seen_pages:
                continue
            for item in _extract_items(self._ticket_page(path, page)):
                if not _record_is_since(item, since):
                    continue
                ticket_id = str(item.get("ticketId") or item)
                if ticket_id in seen_ids:
                    continue
                sampled.append(item)
                seen_ids.add(ticket_id)
                if len(sampled) >= sample_size:
                    return sampled
        return sampled

    def _count_tickets_since(self, path: str, since: str, first_page: int, total_pages: int) -> int:
        """快速估算 since 之后的工单数量：扫描前 3 页 + 末页做比例估算。"""

        if total_pages <= 5:
            # 页数很少，直接精确计数
            count = 0
            for page in range(first_page, total_pages + 1):
                count += sum(1 for item in _extract_items(self._ticket_page(path, page)) if _record_is_since(item, since))
            return count

        # 采样前 3 页和末页，按比例估算
        sample_pages = list(range(first_page, min(first_page + 3, total_pages + 1)))
        if total_pages not in sample_pages:
            sample_pages.append(total_pages)

        total_checked = 0
        since_matched = 0
        for page in sample_pages:
            items = _extract_items(self._ticket_page(path, page))
            total_checked += len(items)
            since_matched += sum(1 for item in items if _record_is_since(item, since))

        if total_checked == 0:
            return 0
        estimated_ratio = since_matched / total_checked
        return int(estimated_ratio * total_pages * (total_checked / len(sample_pages)))

    def _fetch_all_tickets_since(self, path: str, since: str, first_page: int, total_pages: int) -> list[dict]:
        """顺序拉取 since 之后的所有工单（用于数量不足 sample_size 时全量返回）。"""

        results: list[dict] = []
        seen_ids: set[str] = set()
        for page in range(first_page, total_pages + 1):
            for item in _extract_items(self._ticket_page(path, page)):
                if not _record_is_since(item, since):
                    continue
                ticket_id = str(item.get("ticketId") or item)
                if ticket_id in seen_ids:
                    continue
                results.append(item)
                seen_ids.add(ticket_id)
        return results

    def _estimate_first_ticket_page_since(self, path: str, since: str, total_pages: int) -> int:
        """根据首页/末页时间粗略估算 since 日期对应的起始页。"""

        first_items = _extract_items(self._ticket_page(path, 1))
        last_items = _extract_items(self._ticket_page(path, total_pages))
        first_date = _record_datetime(first_items[0]) if first_items else None
        last_date = _record_datetime(last_items[-1]) if last_items else None
        since_date = datetime.fromisoformat(since)
        if not first_date or not last_date or last_date <= first_date:
            return 1
        if since_date <= first_date:
            return 1
        if since_date >= last_date:
            return total_pages

        ratio = (since_date - first_date).total_seconds() / (last_date - first_date).total_seconds()
        estimated = int(total_pages * ratio)
        return min(max(1, estimated), total_pages)

    def _ticket_page(self, path: str, page: int) -> dict[str, Any]:
        """读取指定页的工单列表，并校验返回体是 JSON 对象。"""

        response = self.client.get(path, params={"page": page})
        if not _looks_successful(response):
            raise ApiError(f"HTTP {response.status_code}: {response.text[:300]}")
        body = _json_or_empty(response)
        if not isinstance(body, dict):
            raise ApiError(f"Unexpected ticket response: {response.text[:300]}")
        return body

    def _json_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """读取单个详情接口，并校验返回体是 JSON 对象。"""

        response = self.client.get(path, params=params)
        if not _looks_successful(response):
            raise ApiError(f"HTTP {response.status_code}: {response.text[:300]}")
        body = _json_or_empty(response)
        if not isinstance(body, dict):
            raise ApiError(f"Unexpected response: {response.text[:300]}")
        return body

    def _json_get_or_list(self, path: str) -> Any:
        """读取允许返回 JSON 对象或数组的接口。"""

        response = self.client.get(path)
        if not _looks_successful(response):
            raise ApiError(f"HTTP {response.status_code}: {response.text[:300]}")
        body = _json_or_empty(response)
        if not isinstance(body, (dict, list)):
            raise ApiError(f"Unexpected response: {response.text[:300]}")
        return body

    def _fetch_paginated(self, path: str, extra_params: dict[str, Any]) -> list[dict]:
        """通用分页读取逻辑，兼容接口可能使用的多种分页参数名。"""

        items: list[dict] = []
        for page in range(1, self.settings.max_pages + 1):
            params = {
                "page": page,
                "pageNo": page,
                "pageNum": page,
                "current": page,
                "pageSize": self.settings.page_size,
                "limit": self.settings.page_size,
                **extra_params,
            }
            response = self._first_successful_request(path, params)
            if not _looks_successful(response):
                raise ApiError(f"HTTP {response.status_code}: {response.text[:300]}")
            body = _json_or_empty(response)
            page_items = _extract_items(body)
            if not page_items:
                break
            items.extend(page_items)
            if len(page_items) < self.settings.page_size or not _has_more(body, page, len(page_items)):
                break
        return items

    def iter_entity_pages(self, paths: list[str], page_size: int) -> Iterator[list[dict]]:
        """Yield the first non-empty configured entity endpoint one page at a time."""

        errors: list[str] = []
        for path in paths:
            yielded = False
            observed_page_size: int | None = None
            try:
                for page in range(1, self.settings.max_pages + 1):
                    params = {
                        "page": page,
                        "pageNo": page,
                        "pageNum": page,
                        "current": page,
                        "pageSize": page_size,
                        "limit": page_size,
                    }
                    response = self._first_successful_request(path, params)
                    if not _looks_successful(response):
                        raise ApiError(f"HTTP {response.status_code}: {response.text[:300]}")
                    body = _json_or_empty(response)
                    page_items = _extract_items(body)
                    if not page_items:
                        break
                    yielded = True
                    observed_page_size = observed_page_size or len(page_items)
                    yield page_items
                    declared_count = _declared_item_count(body, 0)
                    has_more = (
                        page * observed_page_size < declared_count
                        if declared_count and observed_page_size
                        else _has_more(body, page, len(page_items))
                    )
                    if not has_more:
                        return
            except ApiError as exc:
                errors.append(f"{path}: {exc}")
                continue
            if yielded:
                return
        raise ApiError("No configured endpoint returned data.\n" + "\n".join(errors[-8:]))

    def _first_successful_request(self, path: str, data: dict[str, Any]) -> httpx.Response:
        """同一路径按配置的方法依次尝试，返回第一个成功响应。"""

        responses: list[httpx.Response] = []
        for method in self.settings.endpoint.request_methods:
            response = self._request(method, path, data)
            responses.append(response)
            if _looks_successful(response):
                return response
        return responses[-1]

    def _request(self, method: str, path: str, data: dict[str, Any]) -> httpx.Response:
        """发起 GET/POST 请求；网络抖动时最多重试 3 次。"""

        last_error: httpx.HTTPError | None = None
        for attempt in range(3):
            try:
                if method == "GET":
                    return self.client.get(path, params=data)
                if method == "POST":
                    return self.client.post(path, data=data)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        raise ApiError(f"Unsupported HTTP method: {method}")


def _json_or_empty(response: httpx.Response) -> Any:
    """把响应解析为 JSON；非 JSON 响应返回空字典，便于统一判断。"""

    try:
        return response.json()
    except ValueError:
        repaired = _repair_invalid_json_escapes(response.text)
        if repaired != response.text:
            try:
                return json.loads(repaired)
            except ValueError:
                pass
        return {}


def _repair_invalid_json_escapes(text: str) -> str:
    """Escape bare backslashes in API strings without touching valid JSON escapes."""

    valid_escapes = set('"\\/bfnrtu')
    output: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != "\\":
            output.append(text[index])
            index += 1
            continue

        start = index
        while index < len(text) and text[index] == "\\":
            index += 1
        count = index - start
        next_char = text[index] if index < len(text) else ""
        if next_char not in valid_escapes and count % 2 == 1:
            count += 1
        output.append("\\" * count)
    return "".join(output)


def _copy_detail(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a defensive copy so callers cannot mutate cached API details."""

    return copy.deepcopy(value) if value is not None else None


def _looks_successful(response: httpx.Response) -> bool:
    """兼容 HTTP 状态码和业务 errcode 的成功判断。"""

    if response.status_code >= 400:
        return False
    body = _json_or_empty(response)
    if not isinstance(body, dict):
        return True
    errcode = str(body.get("errcode") or body.get("code") or "")
    message = str(body.get("errmsg") or body.get("message") or "")
    if errcode in {"100047", "404", "401", "403"}:
        return False
    if "Invalid resource URI" in message:
        return False
    return errcode in {"", "0", "200"} or any(key in body for key in ("data", "token", "access_token"))


def _extract_items(body: Any) -> list[dict]:
    """从不同接口可能使用的字段名中提取列表数据。"""

    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if not isinstance(body, dict):
        return []

    candidates: list[Any] = [
        body.get("data"),
        body.get("list"),
        body.get("items"),
        body.get("records"),
        body.get("rows"),
        body.get("companies"),
        body.get("users"),
        body.get("contacts"),
        body.get("contacters"),
        body.get("tickets"),
    ]
    data = body.get("data")
    if isinstance(data, dict):
        candidates.extend(
            [
                data.get("list"),
                data.get("items"),
                data.get("records"),
                data.get("rows"),
                data.get("data"),
            ]
        )
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _declared_item_count(body: Any, fallback: int) -> int:
    """Read a response total without fetching remaining pages."""

    if not isinstance(body, dict):
        return fallback
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    for key in ("total", "count", "totalCount"):
        try:
            value = data.get(key)
            if value not in (None, ""):
                return int(value)
        except (TypeError, ValueError):
            continue
    return fallback


def _has_more(body: Any, page: int, item_count: int) -> bool:
    """根据 total/pageSize/hasMore 等字段判断分页是否还有下一页。"""

    if not isinstance(body, dict):
        return item_count > 0
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    total = data.get("total") or data.get("count") or data.get("totalCount")
    page_size = data.get("pageSize") or data.get("limit")
    if isinstance(total, int) and isinstance(page_size, int):
        return page * page_size < total
    has_more = data.get("hasMore") or data.get("has_more")
    if isinstance(has_more, bool):
        return has_more
    return item_count > 0


def _record_is_since(record: dict, since: str) -> bool:
    """判断记录创建时间是否不早于 since；缺少时间时保守保留。"""

    created_at = _record_datetime(record)
    return created_at is None or created_at >= datetime.fromisoformat(since)


def _record_datetime(record: dict) -> datetime | None:
    """从记录中常见的创建时间字段解析 datetime。"""

    return _parse_datetime(record.get("createDT") or record.get("createTime") or record.get("created_at"))


def _parse_datetime(value: Any) -> datetime | None:
    """兼容常见日期字符串格式并解析为 datetime。"""

    if value in (None, ""):
        return None
    text = str(value).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19 if "%S" in fmt else 10], fmt)
        except ValueError:
            continue
    return None
