"""Ticket import orchestration isolated behind mysql_storage compatibility facades."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from .api import WorkOrderClient
from .config import MySQLConfig
from .dictionary import DataDictionary
from .import_failures import FailureCollector, ImportFailure
from .mysql_storage import (
    _pymysql,
    _to_datetime,
    _upsert_ticket_detail,
    build_ticket_detail_custom_field_rows,
    build_ticket_detail_main_row,
    ensure_mysql_schema,
)
from .resolver import TicketFieldResolver, _split_id_list, resolve_ticket_detail_values
from .sync_log import write_sync_log

API_FAILURE_STAGE = "api"
DATABASE_FAILURE_STAGE = "database"


def _fetch_month_ticket_rows(
    client: WorkOrderClient,
    year: int,
    month: int,
    per_page: int,
    limit_per_month: int | None = None,
) -> tuple[str, list[dict[str, Any]], str]:
    """Fetch monthly ticket list directly from the API."""

    from .monthly_export import build_month_label, fetch_month_ticket_rows

    month_label = build_month_label(year, month)
    ticket_report = fetch_month_ticket_rows(
        client,
        year,
        month,
        per_page=per_page,
        limit_per_month=limit_per_month,
    )
    return month_label, ticket_report.get("tickets", []), "api"


def import_month_tickets_serial(
    config: MySQLConfig,
    dictionary: DataDictionary,
    client: WorkOrderClient,
    year: int,
    month: int,
    per_page: int = 5000,
    limit_per_month: int | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """拉取某个月的全部工单详情，逐条串行导入（调试用，速度较慢）。"""

    month_label, ticket_rows, ticket_source = _fetch_month_ticket_rows(
        client,
        year,
        month,
        per_page=per_page,
        limit_per_month=limit_per_month,
    )
    if not ticket_rows:
        return {
            "month": month_label,
            "total_in_month": 0,
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "failed_ids": [],
            "failures": [],
            "failures_truncated": False,
        }

    ensure_mysql_schema(config)
    ticket_ids, already_current = _filter_ticket_rows_for_import(config, ticket_rows, month_label)
    if not ticket_ids:
        write_sync_log(
            config,
            task_type="ticket_detail",
            target_year=year,
            target_month=month,
            month_label=month_label,
            status="success",
            total_count=len(ticket_rows),
            success_count=0,
            failed_count=0,
            skipped_count=already_current,
            duration_seconds=0,
            error_message=None,
            extra_json={"ticket_source": ticket_source, "prefiltered": True},
        )
        return {
            "month": month_label,
            "ticket_source": ticket_source,
            "total_in_month": len(ticket_rows),
            "imported": 0,
            "updated": 0,
            "skipped": already_current,
            "failed": 0,
            "failed_ids": [],
            "failures": [],
            "failures_truncated": False,
            "custom_field_rows": 0,
            "duration_seconds": 0,
        }

    field_resolver = TicketFieldResolver(
        client.fetch_ticket_fields(), client.fetch_company_fields()
    )

    imported = 0
    updated = 0
    skipped = already_current
    failed_ids: list[str] = []
    failures = FailureCollector()
    total_custom = 0
    started_at = datetime.now()

    for ticket_id in ticket_ids:
        try:
            raw_detail = client.fetch_ticket_detail(ticket_id)
            if not raw_detail:
                failed_ids.append(ticket_id)
                failures.capture(
                    stage=API_FAILURE_STAGE,
                    exc=RuntimeError("ticket detail API returned no record"),
                    record_id=ticket_id,
                )
                continue
            value_detail = resolve_ticket_detail_values(raw_detail, client, field_resolver)
        except Exception as exc:
            failed_ids.append(ticket_id)
            failures.capture(
                stage=API_FAILURE_STAGE,
                exc=exc,
                record_id=ticket_id,
            )
            continue

        detail_map = {ticket_id: (raw_detail, value_detail)}
        try:
            batch_result = _commit_batch_atomic(config, detail_map)
        except Exception as exc:
            failed_ids.append(ticket_id)
            failures.capture(
                stage=DATABASE_FAILURE_STAGE,
                exc=exc,
                record_id=ticket_id,
            )
            continue
        _merge_failure_payload(failures, batch_result)
        imported += batch_result["imported"]
        updated += batch_result["updated"]
        skipped += batch_result["skipped"]
        failed_ids.extend(batch_result["failed_ids"])
        total_custom += batch_result["custom_rows"]

    duration = int((datetime.now() - started_at).total_seconds())
    overall_status = (
        "success" if not failed_ids else ("partial" if (imported + updated) > 0 else "failed")
    )
    failure_payload = failures.as_payload()
    write_sync_log(
        config,
        task_type="ticket_detail",
        target_year=year,
        target_month=month,
        month_label=month_label,
        status=overall_status,
        total_count=len(ticket_ids),
        success_count=imported + updated,
        failed_count=len(failed_ids),
        skipped_count=skipped,
        duration_seconds=duration,
        error_message=None if overall_status == "success" else f"{len(failed_ids)} 条工单失败",
        extra_json={
            "failed_ids": failed_ids,
            "failures": failure_payload["failures"],
            "failures_truncated": failure_payload["failures_truncated"],
        }
        if failed_ids
        else None,
    )

    return {
        "month": month_label,
        "ticket_source": ticket_source,
        "total_in_month": len(ticket_ids),
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "failed": len(failed_ids),
        "failed_ids": failed_ids,
        "failures": failure_payload["failures"],
        "failures_truncated": failure_payload["failures_truncated"],
        "custom_field_rows": total_custom,
        "duration_seconds": duration,
    }


def import_month_tickets_to_mysql(
    config: MySQLConfig,
    dictionary: DataDictionary,
    client: WorkOrderClient,
    year: int,
    month: int,
    per_page: int = 5000,
    limit_per_month: int | None = None,
    max_workers: int = 8,
    batch_size: int = 100,
    api_rate_limit: int = 10,
) -> dict[str, Any]:
    """Import one month of tickets directly from the API.

    优化策略：
    - 全月工单列表中的实体 ID 去重后预取，后续逐条解析直接命中缓存；
    - 信号量跨批次共享，统一控制 API QPS；
    - 每批用独立连接 + 独立事务，单批失败不影响其他批次。
    """

    month_label, ticket_rows, ticket_source = _fetch_month_ticket_rows(
        client,
        year,
        month,
        per_page=per_page,
        limit_per_month=limit_per_month,
    )
    if not ticket_rows:
        return {
            "month": month_label,
            "ticket_source": ticket_source,
            "total_in_month": 0,
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "failed_ids": [],
            "failures": [],
            "failures_truncated": False,
        }

    ensure_mysql_schema(config)
    ticket_ids, already_current = _filter_ticket_rows_for_import(config, ticket_rows, month_label)
    if not ticket_ids:
        write_sync_log(
            config,
            task_type="ticket_detail",
            target_year=year,
            target_month=month,
            month_label=month_label,
            status="success",
            total_count=len(ticket_rows),
            success_count=0,
            failed_count=0,
            skipped_count=already_current,
            duration_seconds=0,
            error_message=None,
            extra_json={
                "ticket_source": ticket_source,
                "limit_per_month": limit_per_month,
                "prefiltered": True,
            },
        )
        return {
            "month": month_label,
            "ticket_source": ticket_source,
            "total_in_month": len(ticket_rows),
            "imported": 0,
            "updated": 0,
            "skipped": already_current,
            "failed": 0,
            "failed_ids": [],
            "failures": [],
            "failures_truncated": False,
            "custom_field_rows": 0,
            "duration_seconds": 0,
        }

    field_resolver = TicketFieldResolver(
        client.fetch_ticket_fields(), client.fetch_company_fields()
    )
    pending_ids = set(ticket_ids)
    pending_rows = [
        row for row in ticket_rows if str(row.get("ticketId") or "").strip() in pending_ids
    ]

    # ── 1. 预取实体详情（去重后并发请求）────────────────────────
    _prefetch_ticket_entities(client, pending_rows, field_resolver, max_workers, api_rate_limit)

    # ── 2. 分批次获取详情 + 入库 ─────────────────────────────────
    imported = 0
    updated = 0
    skipped = already_current
    failed_ids: list[str] = []
    failures = FailureCollector()
    total_custom = 0
    started_at = datetime.now()
    api_semaphore = threading.Semaphore(max(1, api_rate_limit))

    for batch_start in range(0, len(ticket_ids), batch_size):
        batch = ticket_ids[batch_start : batch_start + batch_size]
        detail_map, api_failures = _fetch_batch_details(
            client,
            batch,
            field_resolver,
            api_semaphore,
            max_workers=max_workers,
        )
        _merge_failure_collectors(failures, api_failures)
        missing_detail_ids = [tid for tid in batch if tid not in detail_map]
        batch_result = _commit_batch_atomic(config, detail_map)
        _merge_failure_payload(failures, batch_result)
        imported += batch_result["imported"]
        updated += batch_result["updated"]
        skipped += batch_result["skipped"]
        failed_ids.extend(missing_detail_ids)
        failed_ids.extend(batch_result["failed_ids"])
        total_custom += batch_result["custom_rows"]

    duration = int((datetime.now() - started_at).total_seconds())
    overall_status = (
        "success" if not failed_ids else ("partial" if (imported + updated) > 0 else "failed")
    )
    failure_payload = failures.as_payload()
    write_sync_log(
        config,
        task_type="ticket_detail",
        target_year=year,
        target_month=month,
        month_label=month_label,
        status=overall_status,
        total_count=len(ticket_rows),
        success_count=imported + updated,
        failed_count=len(failed_ids),
        skipped_count=skipped,
        duration_seconds=duration,
        error_message=None if overall_status == "success" else f"{len(failed_ids)} tickets failed",
        extra_json={
            "failed_ids": failed_ids,
            "failures": failure_payload["failures"],
            "failures_truncated": failure_payload["failures_truncated"],
            "ticket_source": ticket_source,
            "limit_per_month": limit_per_month,
        }
        if failed_ids
        else {
            "ticket_source": ticket_source,
            "limit_per_month": limit_per_month,
        },
    )

    return {
        "month": month_label,
        "ticket_source": ticket_source,
        "total_in_month": len(ticket_rows),
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "failed": len(failed_ids),
        "failed_ids": failed_ids,
        "failures": failure_payload["failures"],
        "failures_truncated": failure_payload["failures_truncated"],
        "custom_field_rows": total_custom,
        "duration_seconds": duration,
    }


def _prefetch_ticket_entities(
    client: WorkOrderClient,
    ticket_rows: list[dict[str, Any]],
    field_resolver: TicketFieldResolver,
    max_workers: int,
    api_rate_limit: int,
) -> None:
    """从工单列表中提取所有引用的实体 ID，去重后批量预取详情。

    预取后，后续逐条调用 resolve_ticket_detail_values 时，
    fetch_contact_detail / fetch_company_detail 等几乎全部命中 LRU 缓存。
    """

    contact_ids: set[str] = set()
    support_ids: set[str] = set()
    group_ids: set[str] = set()
    template_ids: set[str] = set()

    for row in ticket_rows:
        if cust := _str_or_none(row.get("custUserId")):
            contact_ids.add(cust)
        for key in ("servicerUserId", "createrId", "deleterId"):
            if sid := _str_or_none(row.get(key)):
                support_ids.add(sid)
        if gid := _str_or_none(row.get("servicerGroupId")):
            group_ids.add(gid)
        if tid := _str_or_none(row.get("ticketTemplateId")):
            template_ids.add(tid)
        # ccUserIdList 里的客服 ID
        for cid_list_field in ("ccUserIdList",):
            for item in _split_id_list(row.get(cid_list_field)):
                support_ids.add(item)
        for gid_list_field in ("ccGroupIdList",):
            for item in _split_id_list(row.get(gid_list_field)):
                group_ids.add(item)

    semaphore = threading.Semaphore(max(1, api_rate_limit))

    client.prefetch_entities(
        contacts=contact_ids,
        companies=set(),  # 公司 ID 在解析联系人后才能确定，无法提前预取
        supports=support_ids,
        groups=group_ids,
        templates=template_ids,
        max_workers=max_workers,
        semaphore=semaphore,
    )


def _str_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text and text != "0" else None


def _filter_ticket_rows_for_import(
    config: MySQLConfig,
    ticket_rows: list[dict[str, Any]],
    month_label: str,
) -> tuple[list[str], int]:
    """Return ticket IDs that need detail fetching, plus count already current.

    The monthly list already contains ticketId/createDT/updateDT. Use that to
    avoid refetching details for rows whose source_updated_at is unchanged.
    """

    candidates: list[tuple[str, datetime | None, datetime | None]] = []
    for row in ticket_rows:
        ticket_id = str(row.get("ticketId") or "").strip()
        if not ticket_id:
            continue
        candidates.append(
            (ticket_id, _to_datetime(row.get("createDT")), _to_datetime(row.get("updateDT")))
        )

    if not candidates:
        return [], 0

    existing: dict[str, datetime | None] = {}
    pymysql = _pymysql()
    with pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            ids = [ticket_id for ticket_id, _create_dt, _update_dt in candidates]
            for start in range(0, len(ids), 1000):
                chunk = ids[start : start + 1000]
                placeholders = ", ".join(["%s"] * len(chunk))
                cursor.execute(
                    "SELECT ticket_id, source_updated_at "
                    "FROM ticket_detail_main "
                    f"WHERE create_month_label = %s AND ticket_id IN ({placeholders})",
                    [month_label, *chunk],
                )
                for ticket_id, source_updated_at in cursor.fetchall():
                    existing[str(ticket_id)] = source_updated_at

    pending: list[str] = []
    skipped = 0
    for ticket_id, _create_dt, update_dt in candidates:
        if update_dt is None:
            pending.append(ticket_id)
            continue
        source_updated = existing.get(ticket_id)
        if source_updated is not None and _same_datetime(source_updated, update_dt):
            skipped += 1
        else:
            pending.append(ticket_id)
    return pending, skipped


def _same_datetime(left: datetime, right: datetime) -> bool:
    """Compare datetimes at second precision; MySQL may drop microseconds."""

    return left.replace(microsecond=0) == right.replace(microsecond=0)


def _commit_batch_atomic(
    config: MySQLConfig,
    detail_map: dict[str, tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    """Commit one batch in a single connection + single transaction.

    The batch already has row-by-row fallback inside _commit_batch,
    so no outer retry is needed.
    """

    if not detail_map:
        return {
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "failed_ids": [],
            "failures": [],
            "failures_truncated": False,
            "custom_rows": 0,
        }

    pymysql = _pymysql()
    try:
        with pymysql.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            database=config.database,
            charset="utf8mb4",
            autocommit=False,
        ) as connection:
            return _commit_batch(connection, detail_map)
    except Exception as exc:
        failures = FailureCollector()
        for ticket_id in detail_map:
            failures.capture(
                stage=DATABASE_FAILURE_STAGE,
                exc=exc,
                record_id=ticket_id,
            )
        failure_payload = failures.as_payload()
        return {
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "failed_ids": [str(tid) for tid in detail_map.keys()],
            "failures": failure_payload["failures"],
            "failures_truncated": failure_payload["failures_truncated"],
            "custom_rows": 0,
        }


def _fetch_batch_details(
    client: WorkOrderClient,
    ticket_ids: list[str],
    field_resolver: TicketFieldResolver,
    semaphore: threading.Semaphore,
    max_workers: int = 8,
) -> tuple[
    dict[str, tuple[dict[str, Any], dict[str, Any]]],
    FailureCollector,
]:
    """Fetch raw details and resolved details together so raw field keys are preserved."""

    def _fetch_one(
        ticket_id: str,
    ) -> tuple[
        str,
        tuple[dict[str, Any], dict[str, Any]] | None,
        BaseException | None,
    ]:
        with semaphore:
            try:
                raw = client.fetch_ticket_detail(ticket_id)
                if not raw:
                    return (
                        ticket_id,
                        None,
                        RuntimeError("ticket detail API returned no record"),
                    )
                value = resolve_ticket_detail_values(raw, client, field_resolver)
                return ticket_id, (raw, value), None
            except Exception as exc:
                return ticket_id, None, exc

    results: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    failures = FailureCollector()
    worker_count = max(1, min(max_workers, len(ticket_ids))) if ticket_ids else 1
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_fetch_one, ticket_id): ticket_id for ticket_id in ticket_ids}
        for future in as_completed(futures):
            ticket_id, detail_pair, error = future.result()
            if detail_pair is not None:
                results[ticket_id] = detail_pair
            elif error is not None:
                failures.capture(
                    stage=API_FAILURE_STAGE,
                    exc=error,
                    record_id=ticket_id,
                )
    return results, failures


def _commit_batch(
    connection: Any,
    detail_map: dict[str, tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    """Write parsed ticket details in one transaction, retrying row-by-row on batch failure."""

    imported = 0
    updated = 0
    skipped = 0
    failed_ids: list[str] = []
    failures = FailureCollector()
    custom_rows_total = 0

    batch_rows: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for _ticket_id, (raw_detail, value_detail) in detail_map.items():
        main_row = build_ticket_detail_main_row(value_detail)
        custom_rows = build_ticket_detail_custom_field_rows(raw_detail, value_detail)
        batch_rows.append((main_row, custom_rows))

    try:
        batch_imported = 0
        batch_updated = 0
        batch_skipped = 0
        batch_custom_rows = 0
        with connection.cursor() as cursor:
            for main_row, custom_rows in batch_rows:
                action = _upsert_ticket_detail(cursor, main_row, custom_rows)
                if action == "updated":
                    batch_updated += 1
                    batch_custom_rows += len(custom_rows)
                elif action == "skipped":
                    batch_skipped += 1
                else:
                    batch_imported += 1
                    batch_custom_rows += len(custom_rows)
        connection.commit()
        imported += batch_imported
        updated += batch_updated
        skipped += batch_skipped
        custom_rows_total += batch_custom_rows
    except Exception:
        _safe_rollback(connection)
        for main_row, custom_rows in batch_rows:
            try:
                with connection.cursor() as cursor:
                    action = _upsert_ticket_detail(cursor, main_row, custom_rows)
                connection.commit()
                if action == "updated":
                    updated += 1
                    custom_rows_total += len(custom_rows)
                elif action == "skipped":
                    skipped += 1
                else:
                    imported += 1
                    custom_rows_total += len(custom_rows)
            except Exception as exc:
                ticket_id = str(main_row.get("ticket_id", ""))
                failed_ids.append(ticket_id)
                failures.capture(
                    stage=DATABASE_FAILURE_STAGE,
                    exc=exc,
                    record_id=ticket_id,
                )
                _safe_rollback(connection)

    failure_payload = failures.as_payload()
    return {
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "failed_ids": failed_ids,
        "failures": failure_payload["failures"],
        "failures_truncated": failure_payload["failures_truncated"],
        "custom_rows": custom_rows_total,
    }


def _merge_failure_collectors(
    target: FailureCollector,
    source: FailureCollector,
) -> None:
    """Merge bounded structured failures without reconstructing exceptions."""

    target.total += source.total
    remaining = max(0, target.limit - len(target.failures))
    target.failures.extend(source.failures[:remaining])


def _merge_failure_payload(
    target: FailureCollector,
    payload: dict[str, Any],
) -> None:
    """Merge a helper report's already-sanitized structured failures."""

    source_failures = payload.get("failures")
    if not isinstance(source_failures, list):
        return
    target.total += len(payload.get("failed_ids") or source_failures)
    remaining = max(0, target.limit - len(target.failures))
    for failure_payload in source_failures[:remaining]:
        if not isinstance(failure_payload, dict):
            continue
        target.failures.append(
            ImportFailure(
                stage=str(failure_payload.get("stage") or ""),
                record_id=(
                    None
                    if failure_payload.get("record_id") is None
                    else str(failure_payload["record_id"])
                ),
                source_row=(
                    int(failure_payload["source_row"])
                    if failure_payload.get("source_row") is not None
                    else None
                ),
                error_type=str(failure_payload.get("error_type") or ""),
                safe_message=str(failure_payload.get("safe_message") or ""),
            )
        )


def _safe_rollback(connection: Any) -> None:
    """Rollback if the connection is still usable."""

    try:
        connection.rollback()
    except Exception:
        pass


def import_year_tickets_to_mysql(
    config: MySQLConfig,
    dictionary: DataDictionary,
    client: WorkOrderClient,
    year: int,
    months: Iterable[int] | None = None,
    per_page: int = 5000,
    limit_per_month: int | None = None,
    max_workers: int = 8,
    batch_size: int = 100,
    api_rate_limit: int = 10,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """拉取某年指定月份的全部工单详情并写入 MySQL。

    默认导入全年 12 个月。支持断点续跑：已导入的月份可跳过。
    """

    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    month_numbers = list(months) if months is not None else list(range(1, 13))
    month_reports: list[dict[str, Any]] = []
    total_imported = 0
    total_updated = 0
    total_skipped = 0
    total_failed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task(f"MySQL 导入 {year}", total=len(month_numbers))
        for month in month_numbers:
            from .monthly_export import build_month_label

            month_label = build_month_label(year, month)
            progress.update(task, description=f"导入 {month_label}")
            report = import_month_tickets_to_mysql(
                config,
                dictionary,
                client,
                year,
                month,
                per_page=per_page,
                limit_per_month=limit_per_month,
                max_workers=max_workers,
                batch_size=batch_size,
                api_rate_limit=api_rate_limit,
            )
            month_reports.append(report)
            total_imported += report["imported"]
            total_updated += report["updated"]
            total_skipped += report["skipped"]
            total_failed += report["failed"]
            progress.advance(task)

    return {
        "year": year,
        "total_imported": total_imported,
        "total_updated": total_updated,
        "total_skipped": total_skipped,
        "total_failed": total_failed,
        "months": month_reports,
    }
