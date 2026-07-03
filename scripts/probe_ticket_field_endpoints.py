"""探测工单字段相关接口。

用途：
    在线文档中提到“工单所有字段”接口，但路径需要结合实际服务确认。
    这个脚本会带上项目 Basic Auth，批量尝试常见的字段/模板/自定义字段接口命名，
    并输出哪些路径返回了可用 JSON，方便判断哪些工单详情字段还能继续替换。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import httpx

from work_order_process.config import load_settings


def main() -> None:
    settings = load_settings()
    auth = httpx.BasicAuth(settings.username, settings.password)

    template_ids = ["4", "56"]
    custom_template_ids = ["2"]
    ticket_ids = ["22059727", "21771178"]

    option_ids = ["4272477", "4273140", "4318062", "4320484"]
    field_keys = ["field_12", "field_31", "field_609"]

    paths = list(_candidate_paths(template_ids, custom_template_ids, ticket_ids, option_ids, field_keys))
    param_sets = [
        {"page": 1, "pageSize": 20},
        {"page": 1, "pageSize": 20, "ticketTemplateId": "4"},
        {"page": 1, "pageSize": 20, "templateId": "4"},
        {"page": 1, "pageSize": 20, "tId": "4"},
        {"page": 1, "pageSize": 20, "customTemplateId": "2"},
        {"page": 1, "pageSize": 20, "ticketId": "22059727"},
        {"page": 1, "pageSize": 20, "fieldKey": "field_31"},
        {"page": 1, "pageSize": 20, "field": "field_31"},
        {"page": 1, "pageSize": 20, "fieldId": "31"},
    ]

    successes: list[dict[str, Any]] = []
    with httpx.Client(
        base_url=settings.base_url,
        auth=auth,
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
        headers={"Accept": "application/json"},
    ) as client:
        for path in paths:
            for method in ("GET", "POST"):
                for params in param_sets:
                    response = _request(client, method, path, params)
                    info = _summarize_response(method, path, response, params)
                    if info["ok"] and _is_new_semantic_response(path, info):
                        successes.append(info)
                        print(json.dumps(info, ensure_ascii=False))

    print(f"SUCCESS_COUNT={len(successes)}")


def _candidate_paths(
    template_ids: Iterable[str],
    custom_template_ids: Iterable[str],
    ticket_ids: Iterable[str],
    option_ids: Iterable[str],
    field_keys: Iterable[str],
) -> Iterable[str]:
    """生成可能的工单字段接口路径。"""

    static_paths = [
        "/fields",
        "/field",
        "/field/list",
        "/fields/list",
        "/customfields",
        "/custom-fields",
        "/customfield",
        "/customfield/list",
        "/ticketfields",
        "/ticketFields",
        "/ticket-fields",
        "/ticket_fields",
        "/ticketfield",
        "/ticketfield/list",
        "/ticketfields/list",
        "/ticketfields/all",
        "/tickets/fields",
        "/ticket/fields",
        "/ticket/allfields",
        "/ticket/all-fields",
        "/ticket/customfields",
        "/ticket/custom-fields",
        "/ticketCustomFields",
        "/ticketcustomfields",
        "/ticketcustomfield/list",
        "/ticketTemplateFields",
        "/ticketTemplateField",
        "/tickettemplatefields",
        "/ticketTemplateField/list",
        "/ticketTemplateFields/list",
        "/ticket-template-fields",
        "/templatefields",
        "/templateFields",
        "/template-fields",
        "/tickettemplates",
        "/ticketTemplates",
        "/tickettemplates/fields",
        "/ticketTemplates/fields",
        "/customtemplates",
        "/customTemplates",
        "/customtemplates/fields",
        "/customTemplates/fields",
        "/customTemplateFields",
        "/customtemplatefields",
        "/customTemplateField/list",
        "/customTemplateFields/list",
        "/formfields",
        "/formFields",
        "/ticketFormFields",
        "/ticketformfields",
        "/ticketFieldOptions",
        "/ticketfieldoptions",
        "/ticket-field-options",
        "/fieldoptions",
        "/fieldOptions",
        "/field-options",
        "/fields/options",
        "/field/options",
        "/customFieldOptions",
        "/customfieldoptions",
        "/custom-field-options",
        "/customfields/options",
        "/customFields/options",
        "/options",
        "/workflows",
        "/workflow/fields",
        "/ticket/workflows",
        "/ticketsources",
        "/ticketSources",
        "/agents",
    ]
    yield from static_paths

    for template_id in template_ids:
        yield f"/tickettemplates/{template_id}"
        yield f"/ticketTemplates/{template_id}"
        yield f"/tickettemplates/{template_id}/fields"
        yield f"/ticketTemplates/{template_id}/fields"
        yield f"/ticketTemplateFields/{template_id}"
        yield f"/ticketTemplateField/{template_id}"
        yield f"/tickettemplatefields/{template_id}"
        yield f"/templates/{template_id}/fields"
        yield f"/templatefields/{template_id}"
        yield f"/templateFields/{template_id}"

    for custom_template_id in custom_template_ids:
        yield f"/customtemplates/{custom_template_id}"
        yield f"/customTemplates/{custom_template_id}"
        yield f"/customtemplates/{custom_template_id}/fields"
        yield f"/customTemplates/{custom_template_id}/fields"
        yield f"/ticketcustomtemplates/{custom_template_id}/fields"
        yield f"/ticketCustomTemplates/{custom_template_id}/fields"
        yield f"/customTemplateFields/{custom_template_id}"
        yield f"/customtemplatefields/{custom_template_id}"

    for ticket_id in ticket_ids:
        yield f"/tickets/{ticket_id}/fields"
        yield f"/ticket/{ticket_id}/fields"

    for option_id in option_ids:
        yield f"/fieldoptions/{option_id}"
        yield f"/fieldOptions/{option_id}"
        yield f"/ticketFieldOptions/{option_id}"
        yield f"/ticketfieldoptions/{option_id}"
        yield f"/customFieldOptions/{option_id}"
        yield f"/customfieldoptions/{option_id}"
        yield f"/options/{option_id}"

    for field_key in field_keys:
        yield f"/fields/{field_key}"
        yield f"/field/{field_key}"
        yield f"/ticketfields/{field_key}"
        yield f"/ticketFields/{field_key}"
        yield f"/customfields/{field_key}"
        yield f"/customFields/{field_key}"
        yield f"/fieldoptions/{field_key}"
        yield f"/fieldOptions/{field_key}"


def _request(client: httpx.Client, method: str, path: str, params: dict[str, Any]) -> httpx.Response:
    """发起一次 GET 或 POST 探测请求。"""

    if method == "GET":
        return client.get(path, params=params)
    return client.post(path, data=params)


def _summarize_response(method: str, path: str, response: httpx.Response, params: dict[str, Any]) -> dict[str, Any]:
    """压缩接口响应，避免把大量数据直接刷到终端。"""

    body = _json_or_text(response)
    text = response.text.replace("\n", " ")[:300]
    ok = _looks_useful(response, body)
    return {
        "method": method,
        "path": path,
        "params": params,
        "status_code": response.status_code,
        "ok": ok,
        "top_keys": list(body.keys())[:12] if isinstance(body, dict) else [],
        "item_keys": _first_item_keys(body),
        "preview": text,
    }


def _json_or_text(response: httpx.Response) -> Any:
    """优先解析 JSON；失败时返回原始文本。"""

    try:
        return response.json()
    except ValueError:
        return response.text


def _looks_useful(response: httpx.Response, body: Any) -> bool:
    """判断探测结果是否像一个真实可用接口。"""

    if response.status_code >= 400:
        return False
    if not isinstance(body, dict):
        return False
    message = str(body.get("errmsg") or body.get("message") or "")
    errcode = str(body.get("errcode") or body.get("code") or "")
    if "Invalid resource URI" in message:
        return False
    if errcode in {"100047", "404", "401", "403"}:
        return False
    if isinstance(body.get("tickettemplate"), list) and not body["tickettemplate"]:
        return False
    return True


def _is_new_semantic_response(path: str, info: dict[str, Any]) -> bool:
    """过滤宽松路由导致的伪成功结果。"""

    top_keys = set(info["top_keys"])
    preview = str(info["preview"])
    if path.startswith("/ticketsources") and "tickets" in top_keys:
        return False
    if "/fields" in path.lower() and top_keys == {"ticket"}:
        return False
    if "/fields" in path.lower() and top_keys == {"tickettemplate"} and "ticketTemplateName" in preview:
        return False
    return True


def _first_item_keys(body: Any) -> list[str]:
    """提取返回列表中第一条记录的字段名。"""

    if not isinstance(body, dict):
        return []
    candidates = [
        body.get("data"),
        body.get("list"),
        body.get("items"),
        body.get("records"),
        body.get("fields"),
        body.get("ticketfields"),
        body.get("customfields"),
    ]
    data = body.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("list"), data.get("items"), data.get("records"), data.get("fields")])
    for candidate in candidates:
        if isinstance(candidate, list) and candidate and isinstance(candidate[0], dict):
            return list(candidate[0].keys())[:20]
    return []


if __name__ == "__main__":
    main()
