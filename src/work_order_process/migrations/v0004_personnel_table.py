"""Create the personnel import table through an explicit migration."""

from __future__ import annotations

import re
from typing import Any

VERSION = 4
NAME = "personnel_table"

# This payload is intentionally frozen here. Do not import PERSONNEL_DDL from
# the runtime importer; doing so would let business-code edits bypass checksum
# drift protection.
_PERSONNEL_DDL = """
CREATE TABLE IF NOT EXISTS personnel (
  employee_no VARCHAR(64) NOT NULL,
  person_name VARCHAR(255) NULL,
  province VARCHAR(100) NULL,
  role_names TEXT NULL,
  group_name VARCHAR(255) NULL,
  last_sync_at TIMESTAMP NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (employee_no),
  KEY idx_person_name (person_name),
  KEY idx_province (province),
  KEY idx_group_name (group_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def _required_columns() -> frozenset[str]:
    ignored = {"PRIMARY", "UNIQUE", "KEY", "INDEX"}
    columns: set[str] = set()
    for line in _PERSONNEL_DDL.splitlines():
        match = re.match(r"\s{2}`?([A-Za-z_][A-Za-z0-9_]*)`?\s+", line)
        if match and match.group(1).upper() not in ignored:
            columns.add(match.group(1))
    return frozenset(columns)


_REQUIRED_COLUMNS = _required_columns()


def _required_indexes() -> dict[str, tuple[int, tuple[str, ...]]]:
    indexes: dict[str, tuple[int, tuple[str, ...]]] = {}
    for line in _PERSONNEL_DDL.splitlines():
        match = re.match(
            r"\s{2}(PRIMARY KEY|UNIQUE KEY|KEY|INDEX)"
            r"(?:\s+`?([A-Za-z_][A-Za-z0-9_]*)`?)?\s*\(([^)]+)\)",
            line,
            re.IGNORECASE,
        )
        if match is None:
            continue
        kind, name, raw_columns = match.groups()
        index_name = "PRIMARY" if kind.upper() == "PRIMARY KEY" else str(name)
        non_unique = 0 if kind.upper() in {"PRIMARY KEY", "UNIQUE KEY"} else 1
        columns = tuple(
            value.strip().strip("`").split("(", maxsplit=1)[0] for value in raw_columns.split(",")
        )
        indexes[index_name] = (non_unique, columns)
    return indexes


_REQUIRED_INDEXES = _required_indexes()


def _existing_columns(cursor: Any, database: str) -> set[str]:
    cursor.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s",
        (database, "personnel"),
    )
    return {str(row[0]) for row in cursor.fetchall()}


def _existing_indexes(cursor: Any, database: str) -> dict[str, tuple[int, tuple[str, ...]]]:
    cursor.execute(
        "SELECT index_name, non_unique, seq_in_index, column_name "
        "FROM information_schema.statistics "
        "WHERE table_schema = %s AND table_name = %s "
        "ORDER BY index_name, seq_in_index",
        (database, "personnel"),
    )
    grouped: dict[str, tuple[int, list[tuple[int, str]]]] = {}
    for name, non_unique, sequence, column in cursor.fetchall():
        index_name = str(name)
        if index_name not in grouped:
            grouped[index_name] = (int(non_unique), [])
        grouped[index_name][1].append((int(sequence), str(column)))
    return {
        name: (
            non_unique,
            tuple(column for _, column in sorted(columns)),
        )
        for name, (non_unique, columns) in grouped.items()
    }


def _index_issues(cursor: Any, database: str) -> list[str]:
    actual = _existing_indexes(cursor, database)
    return [
        f"index {name} expected={signature} actual={actual.get(name)}"
        for name, signature in _REQUIRED_INDEXES.items()
        if actual.get(name) != signature
    ]


def is_satisfied(cursor: Any, database: str) -> bool:
    """Return true only when personnel columns and indexes are functional."""

    return _REQUIRED_COLUMNS.issubset(_existing_columns(cursor, database)) and not _index_issues(
        cursor, database
    )


def apply(cursor: Any, database: str) -> None:
    """Create a missing table or reject a partial structure for manual repair."""

    existing = _existing_columns(cursor, database)
    if not existing:
        cursor.execute(_PERSONNEL_DDL)
        return
    missing = sorted(_REQUIRED_COLUMNS - existing)
    if missing:
        raise RuntimeError(
            f"personnel is missing required columns {missing}; manual repair is required"
        )
    issues = _index_issues(cursor, database)
    if issues:
        raise RuntimeError(
            "personnel has incompatible functional structure: "
            f"{'; '.join(issues)}; manual repair is required"
        )
