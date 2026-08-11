"""将合规抽检报告结论回写到工单 field_1447，并逐条回读验证。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from work_order_process.api import ApiError, WorkOrderClient
from work_order_process.config import PROJECT_ROOT, load_settings
from work_order_process.ticket_writeback import (
    FAILURE_REASON_FIELD_KEY,
    SAMPLING_STATUS_FIELD_KEY,
    assert_hubei_writeback_scope,
    assert_monthly_overwrite_scope,
    build_failure_reason_plan,
    build_sampling_status_plan,
    current_custom_field_value,
    load_report_scope,
    load_sampling_status_sources,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="合规检查报告 xlsx 文件")
    parser.add_argument(
        "--apply", action="store_true", help="实际调用接口写入；未指定时仅生成写入计划"
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="覆盖已有抽检结论和原因；仅用于完整自然月复检",
    )
    parser.add_argument(
        "--audit-dir",
        type=Path,
        default=PROJECT_ROOT / "output" / "compliance_writeback",
        help="写入前核验和写入结果的审计文件目录",
    )
    return parser.parse_args(argv)


def _audit_path(directory: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return directory / f"填写规范抽检结果回写_{stamp}.jsonl"


def _append_audit(path: Path, event: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.input.is_file():
        raise FileNotFoundError(f"找不到合规检查报告: {args.input}")

    scope = load_report_scope(args.input)
    assert_hubei_writeback_scope(scope)
    if args.overwrite_existing:
        assert_monthly_overwrite_scope(scope)
    sources = load_sampling_status_sources(args.input)
    if not sources:
        raise ValueError("合规检查报告中没有可回写的工单")

    args.audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = _audit_path(args.audit_dir)
    _append_audit(
        audit_path,
        {
            "event": "run_started",
            "at": datetime.now().isoformat(timespec="seconds"),
            "input": str(args.input.resolve()),
            "apply": args.apply,
            "overwrite_existing": args.overwrite_existing,
            "status_field_key": SAMPLING_STATUS_FIELD_KEY,
            "reason_field_key": FAILURE_REASON_FIELD_KEY,
            "province": scope.province,
            "quality_period": scope.quality_period,
            "source_count": len(sources),
        },
    )

    settings = load_settings()
    with WorkOrderClient(settings) as client:
        current_status_values: dict[str, str | None] = {}
        current_reason_values: dict[str, str | None] = {}
        for source in sources:
            detail = client.fetch_ticket_detail(source.ticket_id)
            current_status = current_custom_field_value(detail, SAMPLING_STATUS_FIELD_KEY)
            current_reason = current_custom_field_value(detail, FAILURE_REASON_FIELD_KEY)
            current_status_values[source.ticket_id] = current_status
            current_reason_values[source.ticket_id] = current_reason
            _append_audit(
                audit_path,
                {
                    "event": "preflight",
                    "ticket_id": source.ticket_id,
                    "current_status": current_status,
                    "current_reason": current_reason,
                },
            )

        status_plan = build_sampling_status_plan(
            sources,
            current_status_values,
            overwrite_existing=args.overwrite_existing,
        )
        reason_plan = build_failure_reason_plan(
            sources,
            current_reason_values,
            overwrite_existing=args.overwrite_existing,
        )
        status_updates = sum(item.action == "update" for item in status_plan)
        reason_updates = sum(item.action == "update" for item in reason_plan)
        print(
            f"报告工单 {len(status_plan)} 条；状态待写入 {status_updates} 条；"
            f"不通过原因待写入 {reason_updates} 条。"
        )
        print(f"审计文件：{audit_path}")
        if not args.apply:
            print("当前为预演模式，未调用写入接口。使用 --apply 才会实际回写。")
            return 0

        _apply_plan(client, status_plan, SAMPLING_STATUS_FIELD_KEY, audit_path, "status")
        _apply_plan(client, reason_plan, FAILURE_REASON_FIELD_KEY, audit_path, "failure_reason")

    print("所有待写入字段均已回写并通过回读验证。")
    return 0


def _apply_plan(
    client: WorkOrderClient,
    plan: list[Any],
    field_key: str,
    audit_path: Path,
    field_label: str,
) -> None:
    for item in plan:
        if item.action != "update":
            _append_audit(
                audit_path,
                {
                    "field_key": field_key,
                    "ticket_id": item.ticket_id,
                    "event": item.action,
                    "current_value": item.current_value,
                },
            )
            continue

        try:
            client.update_ticket_custom_field(item.ticket_id, field_key, item.target_value)
            verified_detail = client.fetch_ticket_detail(item.ticket_id)
            verified_value = current_custom_field_value(verified_detail, field_key)
        except ApiError as exc:
            _append_audit(
                audit_path,
                {
                    "event": "write_error",
                    "field_key": field_key,
                    "ticket_id": item.ticket_id,
                    "target_value": item.target_value,
                    "error": str(exc),
                },
            )
            raise RuntimeError(
                f"工单 {item.ticket_id} 的 {field_label} 回写失败，已停止后续写入"
            ) from exc

        _append_audit(
            audit_path,
            {
                "event": "write_verified"
                if verified_value == item.target_value
                else "verify_failed",
                "field_key": field_key,
                "ticket_id": item.ticket_id,
                "target_value": item.target_value,
                "verified_value": verified_value,
            },
        )
        if verified_value != item.target_value:
            raise RuntimeError(
                f"工单 {item.ticket_id} 的 {field_label} 回读值不一致，已停止后续写入"
            )
        print(f"工单 {item.ticket_id} 的 {field_label} 已回写并验证。")


if __name__ == "__main__":
    raise SystemExit(main())
