"""Idempotent customer/contact ingestion with raw retention and history versions."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Iterator

from .config import MySQLConfig
from .mysql_storage import _pymysql, ensure_mysql_schema
from .structured_entities import (
    CONTACT_HASH_FIELDS,
    CUSTOMER_HASH_FIELDS,
    build_contact_row,
    build_customer_row,
    entity_row_hash,
)

WRITE_BATCH_SIZE = 500


@dataclass(frozen=True)
class SyncReport:
    batch_id: str
    fetched: int
    raw_saved: int
    inserted: int
    changed: int
    unchanged: int
    failed: int
    status: str


class MySQLCustomerContactStore:
    """Keeps one entity write atomic while preserving completed prior entities."""

    def __init__(self, config: MySQLConfig) -> None:
        ensure_mysql_schema(config)
        pymysql = _pymysql()
        self.connection = pymysql.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            database=config.database,
            charset="utf8mb4",
            autocommit=False,
        )

    def close(self) -> None:
        self.connection.close()

    def start_batch(self, entity_type: str) -> str:
        batch_id = str(uuid.uuid4())
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO api_sync_batch (sync_batch_id, entity_type, status, started_at) VALUES (%s, %s, 'running', %s)",
                (batch_id, entity_type, datetime.now()),
            )
        self.connection.commit()
        return batch_id

    def finish_batch(self, batch_id: str, status: str, **counts: object) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE api_sync_batch
                SET status = %s, fetched_count = %s, raw_saved_count = %s,
                    inserted_count = %s, changed_count = %s, unchanged_count = %s,
                    failed_count = %s, error_message = %s, finished_at = %s
                WHERE sync_batch_id = %s
                """,
                (
                    status,
                    int(counts.get("fetched", 0)),
                    int(counts.get("raw_saved", 0)),
                    int(counts.get("inserted", 0)),
                    int(counts.get("changed", 0)),
                    int(counts.get("unchanged", 0)),
                    int(counts.get("failed", 0)),
                    counts.get("error_message"),
                    datetime.now(),
                    batch_id,
                ),
            )
        self.connection.commit()

    def save_entity(
        self,
        *,
        entity_type: str,
        row: dict[str, Any],
        raw_record: dict[str, Any],
        source_name: str,
        batch_id: str,
    ) -> str:
        entity_id_column = "customer_id" if entity_type == "customer" else "contact_id"
        entity_id = str(row[entity_id_column])
        row_hash = str(row["row_hash"])
        current_table = "customers" if entity_type == "customer" else "contacts"
        history_table = "customer_history" if entity_type == "customer" else "contact_history"
        now = datetime.now()
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO api_raw_record (sync_batch_id, entity_type, source_name, source_record_id, payload_json)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (batch_id, entity_type, source_name, entity_id, json.dumps(raw_record, ensure_ascii=False, default=str)),
                )
                cursor.execute(f"SELECT row_hash FROM {current_table} WHERE {entity_id_column} = %s", (entity_id,))
                current = cursor.fetchone()
                if current and str(current[0] or "") == row_hash:
                    cursor.execute(
                        f"UPDATE {current_table} SET last_sync_at = CURRENT_TIMESTAMP, sync_batch_id = %s WHERE {entity_id_column} = %s",
                        (batch_id, entity_id),
                    )
                    self.connection.commit()
                    return "unchanged"

                cursor.execute(f"SELECT COALESCE(MAX(version_no), 0) FROM {history_table} WHERE {entity_id_column} = %s", (entity_id,))
                version_no = int(cursor.fetchone()[0]) + 1
                if current:
                    cursor.execute(
                        f"UPDATE {history_table} SET effective_to = %s, is_current = 0 "
                        f"WHERE {entity_id_column} = %s AND is_current = 1",
                        (now, entity_id),
                    )
                    if entity_type == "contact":
                        cursor.execute(
                            "UPDATE customer_contact_relation_history SET effective_to = %s, is_current = 0 "
                            "WHERE contact_id = %s AND is_current = 1",
                            (now, entity_id),
                        )

                self._upsert_current(cursor, current_table, entity_id_column, row, batch_id)
                self._insert_history(cursor, history_table, entity_id_column, row, batch_id, version_no, now)
                if entity_type == "contact":
                    cursor.execute(
                        """
                        INSERT INTO customer_contact_relation_history
                        (contact_id, version_no, customer_id, customer_name, sync_batch_id, effective_from, is_current)
                        VALUES (%s, %s, %s, %s, %s, %s, 1)
                        """,
                        (entity_id, version_no, row.get("customer_id"), row.get("customer_name"), batch_id, now),
                    )
            self.connection.commit()
            return "changed" if current else "inserted"
        except Exception:
            self.connection.rollback()
            raise

    def save_entities(self, *, entity_type: str, records: list[dict[str, Any]], batch_id: str) -> dict[str, int]:
        """Write one page-sized batch with a bounded number of database round trips."""

        if not records:
            return {"raw_saved": 0, "inserted": 0, "changed": 0, "unchanged": 0}
        id_column = "customer_id" if entity_type == "customer" else "contact_id"
        current_table = "customers" if entity_type == "customer" else "contacts"
        history_table = "customer_history" if entity_type == "customer" else "contact_history"
        ids = [str(item["row"][id_column]) for item in records]
        placeholders = ", ".join(["%s"] * len(ids))
        now = datetime.now()
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(f"SELECT {id_column}, row_hash FROM {current_table} WHERE {id_column} IN ({placeholders})", ids)
                current_hashes = {str(row[0]): str(row[1] or "") for row in cursor.fetchall()}
                cursor.execute(
                    f"SELECT {id_column}, COALESCE(MAX(version_no), 0) FROM {history_table} "
                    f"WHERE {id_column} IN ({placeholders}) GROUP BY {id_column}",
                    ids,
                )
                versions = {str(row[0]): int(row[1]) for row in cursor.fetchall()}
                cursor.executemany(
                    """
                    INSERT INTO api_raw_record (sync_batch_id, entity_type, source_name, source_record_id, payload_json)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    [
                        (batch_id, entity_type, item["source_name"], str(item["row"][id_column]), json.dumps(item["raw_record"], ensure_ascii=False, default=str))
                        for item in records
                    ],
                )
                unchanged = [item for item in records if current_hashes.get(str(item["row"][id_column])) == item["row"]["row_hash"]]
                active = [item for item in records if item not in unchanged]
                if unchanged:
                    cursor.executemany(
                        f"UPDATE {current_table} SET last_sync_at = CURRENT_TIMESTAMP, sync_batch_id = %s WHERE {id_column} = %s",
                        [(batch_id, str(item["row"][id_column])) for item in unchanged],
                    )
                if active:
                    columns = list(active[0]["row"])
                    column_sql = ", ".join(f"`{column}`" for column in columns)
                    value_sql = ", ".join(["%s"] * (len(columns) + 1))
                    updates = ", ".join(f"`{column}` = VALUES(`{column}`)" for column in columns if column != id_column)
                    cursor.executemany(
                        f"INSERT INTO {current_table} ({column_sql}, `sync_batch_id`) VALUES ({value_sql}) "
                        f"ON DUPLICATE KEY UPDATE {updates}, last_sync_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP",
                        [[item["row"][column] for column in columns] + [batch_id] for item in active],
                    )
                    changed_ids = [str(item["row"][id_column]) for item in active if str(item["row"][id_column]) in current_hashes]
                    if changed_ids:
                        cursor.executemany(
                            f"UPDATE {history_table} SET effective_to = %s, is_current = 0 WHERE {id_column} = %s AND is_current = 1",
                            [(now, entity_id) for entity_id in changed_ids],
                        )
                        if entity_type == "contact":
                            cursor.executemany(
                                "UPDATE customer_contact_relation_history SET effective_to = %s, is_current = 0 WHERE contact_id = %s AND is_current = 1",
                                [(now, entity_id) for entity_id in changed_ids],
                            )
                    history_columns = columns + ["version_no", "sync_batch_id", "effective_from"]
                    history_sql = ", ".join(f"`{column}`" for column in history_columns)
                    history_values = ", ".join(["%s"] * len(history_columns))
                    cursor.executemany(
                        f"INSERT INTO {history_table} ({history_sql}, `is_current`) VALUES ({history_values}, 1)",
                        [
                            [item["row"][column] for column in columns]
                            + [versions.get(str(item["row"][id_column]), 0) + 1, batch_id, now]
                            for item in active
                        ],
                    )
                    if entity_type == "contact":
                        cursor.executemany(
                            """
                            INSERT INTO customer_contact_relation_history
                            (contact_id, version_no, customer_id, customer_name, sync_batch_id, effective_from, is_current)
                            VALUES (%s, %s, %s, %s, %s, %s, 1)
                            """,
                            [
                                (
                                    str(item["row"][id_column]),
                                    versions.get(str(item["row"][id_column]), 0) + 1,
                                    item["row"].get("customer_id"),
                                    item["row"].get("customer_name"),
                                    batch_id,
                                    now,
                                )
                                for item in active
                            ],
                        )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        inserted = sum(1 for item in active if str(item["row"][id_column]) not in current_hashes)
        return {"raw_saved": len(records), "inserted": inserted, "changed": len(active) - inserted, "unchanged": len(unchanged)}

    @staticmethod
    def _upsert_current(
        cursor: Any,
        table_name: str,
        id_column: str,
        row: dict[str, Any],
        batch_id: str,
    ) -> None:
        payload = {**row, "sync_batch_id": batch_id}
        columns = list(payload)
        column_sql = ", ".join(f"`{column}`" for column in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        updates = ", ".join(f"`{column}` = VALUES(`{column}`)" for column in columns if column != id_column)
        cursor.execute(
            f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders}) "
            f"ON DUPLICATE KEY UPDATE {updates}, last_sync_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP",
            [payload[column] for column in columns],
        )

    @staticmethod
    def _insert_history(
        cursor: Any,
        table_name: str,
        id_column: str,
        row: dict[str, Any],
        batch_id: str,
        version_no: int,
        now: datetime,
    ) -> None:
        payload = {**row, "version_no": version_no, "sync_batch_id": batch_id, "effective_from": now}
        columns = list(payload)
        column_sql = ", ".join(f"`{column}`" for column in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        cursor.execute(
            f"INSERT INTO {table_name} ({column_sql}, `is_current`) VALUES ({placeholders}, 1)",
            [payload[column] for column in columns],
        )


def sync_customer_entities(
    config: MySQLConfig | None,
    client: Any,
    *,
    sources: Iterable[str] = ("companies",),
    require_nonempty: bool = True,
    max_records: int | None = None,
    store: Any | None = None,
) -> SyncReport:
    return _sync_entities(
        config,
        client,
        entity_type="customer",
        sources=sources,
        require_nonempty=require_nonempty,
        max_records=max_records,
        store=store,
    )


def sync_contact_entities(
    config: MySQLConfig | None,
    client: Any,
    *,
    sources: Iterable[str] = ("users",),
    require_nonempty: bool = True,
    max_records: int | None = None,
    store: Any | None = None,
) -> SyncReport:
    return _sync_entities(
        config,
        client,
        entity_type="contact",
        sources=sources,
        require_nonempty=require_nonempty,
        max_records=max_records,
        store=store,
    )


def _sync_entities(
    config: MySQLConfig | None,
    client: Any,
    *,
    entity_type: str,
    sources: Iterable[str],
    require_nonempty: bool,
    max_records: int | None,
    store: Any | None,
) -> SyncReport:
    owns_store = store is None
    if store is None:
        if config is None:
            raise ValueError("config is required when no store is supplied")
        store = MySQLCustomerContactStore(config)
    batch_id = store.start_batch(entity_type)
    fetched = 0
    try:
        reports = {"raw_saved": 0, "inserted": 0, "changed": 0, "unchanged": 0, "failed": 0}
        seen_ids: set[str] = set()
        saw_record = False
        for source in sources:
            for page in _iter_source_pages(client, entity_type, source):
                prepared: list[dict[str, Any]] = []
                for record in page:
                    saw_record = True
                    fetched += 1
                    try:
                        row = _build_row(entity_type, record, source)
                        entity_id = str(row["customer_id" if entity_type == "customer" else "contact_id"])
                        if entity_id not in seen_ids:
                            seen_ids.add(entity_id)
                            prepared.append({"row": row, "raw_record": record, "source_name": source})
                    except Exception:
                        reports["failed"] += 1
                    if max_records is not None and fetched >= max_records:
                        break
                if prepared:
                    _save_prepared_entities(store, entity_type, prepared, batch_id, reports)
                if max_records is not None and fetched >= max_records:
                    break
            if max_records is not None and fetched >= max_records:
                break
        if require_nonempty and not saw_record:
            report = SyncReport(batch_id, 0, 0, 0, 0, 0, 0, "failed")
            _finish_batch(store, report, error_message="API returned no records")
            return report
        status = "success" if reports["failed"] == 0 else "partial"
        report = SyncReport(batch_id, fetched, status=status, **reports)
        _finish_batch(store, report)
        return report
    except Exception as exc:
        report = SyncReport(batch_id, fetched, 0, 0, 0, 0, 1, "failed")
        _finish_batch(store, report, error_message=type(exc).__name__)
        return report
    finally:
        if owns_store:
            store.close()


def _iter_source_pages(client: Any, entity_type: str, source: str) -> Iterator[list[dict[str, Any]]]:
    methods = {
        ("customer", "companies"): ("iter_companies", "fetch_companies"),
        ("customer", "customers"): ("iter_companies", "fetch_customers"),
        ("contact", "users"): ("iter_contacts", "fetch_contacts"),
        ("contact", "contacts"): ("iter_contacts", "fetch_contacts"),
        ("contact", "company_contacts"): ("iter_contacts", "fetch_company_contacts"),
    }
    method_names = methods.get((entity_type, source))
    if method_names is None:
        raise ValueError(f"Unsupported {entity_type} source: {source}")
    iter_method, fetch_method = method_names
    if hasattr(client, iter_method):
        yield from getattr(client, iter_method)()
        return
    yield list(getattr(client, fetch_method)())


def _save_prepared_entities(
    store: Any,
    entity_type: str,
    prepared: list[dict[str, Any]],
    batch_id: str,
    reports: dict[str, int],
) -> None:
    for chunk in _chunks(prepared, WRITE_BATCH_SIZE):
        if hasattr(store, "save_entities"):
            try:
                outcomes = store.save_entities(entity_type=entity_type, records=chunk, batch_id=batch_id)
                for key in ("raw_saved", "inserted", "changed", "unchanged"):
                    reports[key] += int(outcomes.get(key, 0))
                continue
            except Exception:
                # A bad row should not discard an otherwise healthy page.
                pass
        for item in chunk:
            try:
                outcome = store.save_entity(entity_type=entity_type, batch_id=batch_id, **item)
                reports["raw_saved"] += 1
                reports[outcome] += 1
            except Exception:
                reports["failed"] += 1


def _chunks(items: list[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _build_row(entity_type: str, record: dict[str, Any], source: str) -> dict[str, Any]:
    if entity_type == "customer":
        row = build_customer_row(record, source)
        row["row_hash"] = entity_row_hash(row, CUSTOMER_HASH_FIELDS)
    else:
        row = build_contact_row(record, source)
        row["row_hash"] = entity_row_hash(row, CONTACT_HASH_FIELDS)
    return row


def _finish_batch(store: Any, report: SyncReport, error_message: str | None = None) -> None:
    store.finish_batch(
        report.batch_id,
        report.status,
        fetched=report.fetched,
        raw_saved=report.raw_saved,
        inserted=report.inserted,
        changed=report.changed,
        unchanged=report.unchanged,
        failed=report.failed,
        error_message=error_message,
    )
