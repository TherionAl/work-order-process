"""Manage the monthly revenue table and total view explicitly."""

from __future__ import annotations

import base64
import re
from typing import Any

VERSION = 5
NAME = "revenue_summary_objects"

# Exact table SQL and view-template bytes are frozen here so the source checksum
# covers every CREATE statement, independent of mutable runtime SQL files.
_TABLE_DDL_B64 = (
    "Q1JFQVRFIFRBQkxFIElGIE5PVCBFWElTVFMgb3BzX3NlcnZpY2VfcmV2ZW51ZV9tb250aGx5ICgNCiAgc3RhdF95"
    "ZWFyIFNNQUxMSU5UIE5PVCBOVUxMIENPTU1FTlQgJ+e7n+iuoeW5tCcsDQogIHN0YXRfbW9udGggVElOWUlOVCBO"
    "T1QgTlVMTCBDT01NRU5UICfnu5/orqHmnIgnLA0KICBzYWxlc19wbGF0Zm9ybSBWQVJDSEFSKDEwMCkgTk9UIE5V"
    "TEwgQ09NTUVOVCAn6JCl6ZSA5bmz5Y+wJywNCiAgcmV2ZW51ZV90YXJnZXQgREVDSU1BTCgxOCwwKSBOT1QgTlVM"
    "TCBDT01NRU5UICfmlLblhaXnm67moIflgLzvvIjlhYPvvIknLA0KICByZWNvZ25pemVkX3JldmVudWUgREVDSU1B"
    "TCgxOCwwKSBOT1QgTlVMTCBDT01NRU5UICfnoa7mlLblrozmiJDlgLzvvIjlhYPvvIknLA0KICByZXZlbnVlX2Nv"
    "bXBsZXRpb25fcmF0ZSBERUNJTUFMKDE4LDYpIE5VTEwgQ09NTUVOVCAn5pS25YWl5a6M5oiQ546HJywNCiAgY29u"
    "dHJhY3RzX29uX2hhbmRfYW1vdW50IERFQ0lNQUwoMTgsMCkgTk9UIE5VTEwgQ09NTUVOVCAn5Zyo5omL5ZCI5ZCM"
    "6aKd77yI5YWD77yJJywNCiAgcHJpb3JfeWVhcl9jb250cmFjdHNfb25faGFuZF9hbW91bnQgREVDSU1BTCgxOCww"
    "KSBOT1QgTlVMTCBDT01NRU5UICfljrvlubTlkIzmnJ/lnKjmiYvlkIjlkIzpop3vvIjlhYPvvIknLA0KICBjb250"
    "cmFjdHNfb25faGFuZF95b3lfYW1vdW50IERFQ0lNQUwoMTgsMCkgTk9UIE5VTEwgQ09NTUVOVCAn5Zyo5omL5ZCI"
    "5ZCM5ZCM5q+U5aKe6ZW/5YC877yI5YWD77yJJywNCiAgY29udHJhY3RzX29uX2hhbmRfeW95X3JhdGUgREVDSU1B"
    "TCgxOCw2KSBOVUxMIENPTU1FTlQgJ+WcqOaJi+WQiOWQjOWQjOavlOWinumVv+eOhycsDQogIHJlY29nbml6ZWRf"
    "cmV2ZW51ZV9leGNsdWRpbmdfZXN0aW1hdGUgREVDSU1BTCgxOCwwKSBOT1QgTlVMTCBDT01NRU5UICfkuI3lkKvm"
    "moLkvLDnoa7mlLblgLzvvIjlhYPvvIknLA0KICBwcmlvcl95ZWFyX3JlY29nbml6ZWRfcmV2ZW51ZSBERUNJTUFM"
    "KDE4LDApIE5PVCBOVUxMIENPTU1FTlQgJ+WOu+W5tOWQjOacn+ehruaUtuWAvO+8iOWFg++8iScsDQogIHJlY29n"
    "bml6ZWRfcmV2ZW51ZV95b3lfYW1vdW50IERFQ0lNQUwoMTgsMCkgTk9UIE5VTEwgQ09NTUVOVCAn56Gu5pS25ZCM"
    "5q+U5aKe6ZW/5YC877yI5YWD77yJJywNCiAgcmVjb2duaXplZF9yZXZlbnVlX3lveV9yYXRlIERFQ0lNQUwoMTgs"
    "NikgTlVMTCBDT01NRU5UICfnoa7mlLblkIzmr5Tlop7plb/njocnLA0KICBzaWduaW5nX2NvbXBsZXRlZF9hbW91"
    "bnQgREVDSU1BTCgxOCwwKSBOT1QgTlVMTCBDT01NRU5UICfnrb7nuqblrozmiJDlgLzvvIjlhYPvvIknLA0KICBw"
    "cmlvcl95ZWFyX3NpZ25pbmdfYW1vdW50IERFQ0lNQUwoMTgsMCkgTk9UIE5VTEwgQ09NTUVOVCAn5Y675bm05ZCM"
    "5pyf562+57qm5YC877yI5YWD77yJJywNCiAgc2lnbmluZ195b3lfYW1vdW50IERFQ0lNQUwoMTgsMCkgTk9UIE5V"
    "TEwgQ09NTUVOVCAn562+57qm5ZCM5q+U5aKe6ZW/5YC877yI5YWD77yJJywNCiAgc2lnbmluZ195b3lfcmF0ZSBE"
    "RUNJTUFMKDE4LDYpIE5VTEwgQ09NTUVOVCAn562+57qm5ZCM5q+U5aKe6ZW/546HJywNCiAgZXJwX2NyZWF0ZV9k"
    "YXRlIFZBUkNIQVIoOCkgTk9UIE5VTEwgQ09NTUVOVCAnRVJQ5b+r54Wn5pel5pyfJywNCiAgY3JlYXRlZF9hdCBU"
    "SU1FU1RBTVAgTk9UIE5VTEwgREVGQVVMVCBDVVJSRU5UX1RJTUVTVEFNUCBDT01NRU5UICfliJvlu7rml7bpl7Qn"
    "LA0KICB1cGRhdGVkX2F0IFRJTUVTVEFNUCBOT1QgTlVMTCBERUZBVUxUIENVUlJFTlRfVElNRVNUQU1QIE9OIFVQ"
    "REFURSBDVVJSRU5UX1RJTUVTVEFNUCBDT01NRU5UICfmm7TmlrDml7bpl7QnLA0KICBQUklNQVJZIEtFWSAoc3Rh"
    "dF95ZWFyLCBzdGF0X21vbnRoLCBzYWxlc19wbGF0Zm9ybSksDQogIEtFWSBpZHhfZXJwX2NyZWF0ZV9kYXRlIChl"
    "cnBfY3JlYXRlX2RhdGUpDQopIEVOR0lORT1Jbm5vREIgREVGQVVMVCBDSEFSU0VUPXV0ZjhtYjQgQ09MTEFURT11"
    "dGY4bWI0X3VuaWNvZGVfY2kgQ09NTUVOVD0n6L+Q57u05pyN5Yqh5pyI5bqm6JCl5pS257uf6K6h6KGoJzsNCg=="
)
_VIEW_DDL_B64 = (
    "Q1JFQVRFIE9SIFJFUExBQ0UgVklFVyB2X29wc19zZXJ2aWNlX3JldmVudWVfbW9udGhseV93aXRoX3RvdGFsIEFT"
    "DQpTRUxFQ1QNCiAgc3RhdF95ZWFyLA0KICBzdGF0X21vbnRoLA0KICBzYWxlc19wbGF0Zm9ybSwNCiAgcmV2ZW51"
    "ZV90YXJnZXQsDQogIHJlY29nbml6ZWRfcmV2ZW51ZSwNCiAgcmV2ZW51ZV9jb21wbGV0aW9uX3JhdGUsDQogIGNv"
    "bnRyYWN0c19vbl9oYW5kX2Ftb3VudCwNCiAgcHJpb3JfeWVhcl9jb250cmFjdHNfb25faGFuZF9hbW91bnQsDQog"
    "IGNvbnRyYWN0c19vbl9oYW5kX3lveV9hbW91bnQsDQogIGNvbnRyYWN0c19vbl9oYW5kX3lveV9yYXRlLA0KICBy"
    "ZWNvZ25pemVkX3JldmVudWVfZXhjbHVkaW5nX2VzdGltYXRlLA0KICBwcmlvcl95ZWFyX3JlY29nbml6ZWRfcmV2"
    "ZW51ZSwNCiAgcmVjb2duaXplZF9yZXZlbnVlX3lveV9hbW91bnQsDQogIHJlY29nbml6ZWRfcmV2ZW51ZV95b3lf"
    "cmF0ZSwNCiAgc2lnbmluZ19jb21wbGV0ZWRfYW1vdW50LA0KICBwcmlvcl95ZWFyX3NpZ25pbmdfYW1vdW50LA0K"
    "ICBzaWduaW5nX3lveV9hbW91bnQsDQogIHNpZ25pbmdfeW95X3JhdGUsDQogIGVycF9jcmVhdGVfZGF0ZSwNCiAg"
    "Y3JlYXRlZF9hdCwNCiAgdXBkYXRlZF9hdCwNCiAgMSBBUyBzb3J0X29yZGVyDQpGUk9NIG9wc19zZXJ2aWNlX3Jl"
    "dmVudWVfbW9udGhseQ0KVU5JT04gQUxMDQpTRUxFQ1QNCiAgc3RhdF95ZWFyLA0KICBzdGF0X21vbnRoLA0KICAn"
    "5ZCI6K6hJyBBUyBzYWxlc19wbGF0Zm9ybSwNCiAgUk9VTkQoU1VNKHJldmVudWVfdGFyZ2V0KSwgMCkgQVMgcmV2"
    "ZW51ZV90YXJnZXQsDQogIFJPVU5EKFNVTShyZWNvZ25pemVkX3JldmVudWUpLCAwKSBBUyByZWNvZ25pemVkX3Jl"
    "dmVudWUsDQogIENBU0UgV0hFTiBTVU0ocmV2ZW51ZV90YXJnZXQpID0gMCBUSEVOIE5VTEwgRUxTRSBST1VORChT"
    "VU0ocmVjb2duaXplZF9yZXZlbnVlKSAvIFNVTShyZXZlbnVlX3RhcmdldCksIDYpIEVORCBBUyByZXZlbnVlX2Nv"
    "bXBsZXRpb25fcmF0ZSwNCiAgUk9VTkQoU1VNKGNvbnRyYWN0c19vbl9oYW5kX2Ftb3VudCksIDApIEFTIGNvbnRy"
    "YWN0c19vbl9oYW5kX2Ftb3VudCwNCiAgUk9VTkQoU1VNKHByaW9yX3llYXJfY29udHJhY3RzX29uX2hhbmRfYW1v"
    "dW50KSwgMCkgQVMgcHJpb3JfeWVhcl9jb250cmFjdHNfb25faGFuZF9hbW91bnQsDQogIFJPVU5EKFNVTShjb250"
    "cmFjdHNfb25faGFuZF95b3lfYW1vdW50KSwgMCkgQVMgY29udHJhY3RzX29uX2hhbmRfeW95X2Ftb3VudCwNCiAg"
    "Q0FTRSBXSEVOIFNVTShwcmlvcl95ZWFyX2NvbnRyYWN0c19vbl9oYW5kX2Ftb3VudCkgPSAwIFRIRU4gTlVMTCBF"
    "TFNFIFJPVU5EKFNVTShjb250cmFjdHNfb25faGFuZF9hbW91bnQpIC8gU1VNKHByaW9yX3llYXJfY29udHJhY3Rz"
    "X29uX2hhbmRfYW1vdW50KSAtIDEsIDYpIEVORCBBUyBjb250cmFjdHNfb25faGFuZF95b3lfcmF0ZSwNCiAgUk9V"
    "TkQoU1VNKHJlY29nbml6ZWRfcmV2ZW51ZV9leGNsdWRpbmdfZXN0aW1hdGUpLCAwKSBBUyByZWNvZ25pemVkX3Jl"
    "dmVudWVfZXhjbHVkaW5nX2VzdGltYXRlLA0KICBST1VORChTVU0ocHJpb3JfeWVhcl9yZWNvZ25pemVkX3JldmVu"
    "dWUpLCAwKSBBUyBwcmlvcl95ZWFyX3JlY29nbml6ZWRfcmV2ZW51ZSwNCiAgUk9VTkQoU1VNKHJlY29nbml6ZWRf"
    "cmV2ZW51ZV95b3lfYW1vdW50KSwgMCkgQVMgcmVjb2duaXplZF9yZXZlbnVlX3lveV9hbW91bnQsDQogIENBU0Ug"
    "V0hFTiBTVU0ocHJpb3JfeWVhcl9yZWNvZ25pemVkX3JldmVudWUpID0gMCBUSEVOIE5VTEwgRUxTRSBST1VORChT"
    "VU0ocmVjb2duaXplZF9yZXZlbnVlX2V4Y2x1ZGluZ19lc3RpbWF0ZSkgLyBTVU0ocHJpb3JfeWVhcl9yZWNvZ25p"
    "emVkX3JldmVudWUpIC0gMSwgNikgRU5EIEFTIHJlY29nbml6ZWRfcmV2ZW51ZV95b3lfcmF0ZSwNCiAgUk9VTkQo"
    "U1VNKHNpZ25pbmdfY29tcGxldGVkX2Ftb3VudCksIDApIEFTIHNpZ25pbmdfY29tcGxldGVkX2Ftb3VudCwNCiAg"
    "Uk9VTkQoU1VNKHByaW9yX3llYXJfc2lnbmluZ19hbW91bnQpLCAwKSBBUyBwcmlvcl95ZWFyX3NpZ25pbmdfYW1v"
    "dW50LA0KICBST1VORChTVU0oc2lnbmluZ195b3lfYW1vdW50KSwgMCkgQVMgc2lnbmluZ195b3lfYW1vdW50LA0K"
    "ICBDQVNFIFdIRU4gU1VNKHByaW9yX3llYXJfc2lnbmluZ19hbW91bnQpID0gMCBUSEVOIE5VTEwgRUxTRSBST1VO"
    "RChTVU0oc2lnbmluZ19jb21wbGV0ZWRfYW1vdW50KSAvIFNVTShwcmlvcl95ZWFyX3NpZ25pbmdfYW1vdW50KSAt"
    "IDEsIDYpIEVORCBBUyBzaWduaW5nX3lveV9yYXRlLA0KICBNQVgoZXJwX2NyZWF0ZV9kYXRlKSBBUyBlcnBfY3Jl"
    "YXRlX2RhdGUsDQogIE1JTihjcmVhdGVkX2F0KSBBUyBjcmVhdGVkX2F0LA0KICBNQVgodXBkYXRlZF9hdCkgQVMg"
    "dXBkYXRlZF9hdCwNCiAgMCBBUyBzb3J0X29yZGVyDQpGUk9NIG9wc19zZXJ2aWNlX3JldmVudWVfbW9udGhseQ0K"
    "R1JPVVAgQlkgc3RhdF95ZWFyLCBzdGF0X21vbnRoOw0K"
)

_TABLE_DDL = base64.b64decode(_TABLE_DDL_B64).decode("utf-8")
_VIEW_TEMPLATE_DDL = base64.b64decode(_VIEW_DDL_B64).decode("utf-8")
_VIEW_MARKER = "work_order_process:v0005:revenue_summary_objects"
_VIEW_DDL = _VIEW_TEMPLATE_DDL.replace(
    "1 AS sort_order",
    f"1 + 0 * CHAR_LENGTH('{_VIEW_MARKER}') AS sort_order",
    1,
).replace(
    "0 AS sort_order",
    f"0 + 0 * CHAR_LENGTH('{_VIEW_MARKER}') AS sort_order",
    1,
)
if _VIEW_DDL.count(_VIEW_MARKER) != 2:
    raise RuntimeError("frozen revenue view template does not accept the v5 marker")
_TABLE = "ops_service_revenue_monthly"
_VIEW = "v_ops_service_revenue_monthly_with_total"
_MONEY_COLUMNS = frozenset(
    {
        "revenue_target",
        "recognized_revenue",
        "contracts_on_hand_amount",
        "prior_year_contracts_on_hand_amount",
        "contracts_on_hand_yoy_amount",
        "recognized_revenue_excluding_estimate",
        "prior_year_recognized_revenue",
        "recognized_revenue_yoy_amount",
        "signing_completed_amount",
        "prior_year_signing_amount",
        "signing_yoy_amount",
    }
)
_REQUIRED_TAIL = ("erp_create_date", "created_at", "updated_at")


def _required_columns() -> frozenset[str]:
    ignored = {"PRIMARY", "UNIQUE", "KEY", "INDEX"}
    columns: set[str] = set()
    for line in _TABLE_DDL.splitlines():
        match = re.match(r"\s{2}`?([A-Za-z_][A-Za-z0-9_]*)`?\s+", line)
        if match and match.group(1).upper() not in ignored:
            columns.add(match.group(1))
    return frozenset(columns)


_REQUIRED_COLUMNS = _required_columns()


def _required_indexes() -> dict[str, tuple[int, tuple[str, ...]]]:
    indexes: dict[str, tuple[int, tuple[str, ...]]] = {}
    for line in _TABLE_DDL.splitlines():
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


def _column_definition(column: str) -> str:
    match = re.search(
        rf"^\s{{2}}`?{re.escape(column)}`?\s+(.+?)(?:,)?$",
        _TABLE_DDL,
        re.MULTILINE,
    )
    if match is None:
        raise RuntimeError(f"frozen definition is missing for {column}")
    return match.group(1).rstrip(",")


def _metadata(cursor: Any, database: str) -> list[tuple[str, int | None, int]]:
    cursor.execute(
        "SELECT column_name, numeric_scale, ordinal_position "
        "FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s "
        "ORDER BY ordinal_position",
        (database, _TABLE),
    )
    return [
        (str(name), None if scale is None else int(scale), int(position))
        for name, scale, position in cursor.fetchall()
    ]


def _existing_indexes(
    cursor: Any,
    database: str,
) -> dict[str, tuple[int, tuple[str, ...]]]:
    cursor.execute(
        "SELECT index_name, non_unique, seq_in_index, column_name "
        "FROM information_schema.statistics "
        "WHERE table_schema = %s AND table_name = %s "
        "ORDER BY index_name, seq_in_index",
        (database, _TABLE),
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


def _view_is_current(cursor: Any, database: str) -> bool:
    cursor.execute(
        "SELECT view_definition FROM information_schema.views "
        "WHERE table_schema = %s AND table_name = %s",
        (database, _VIEW),
    )
    result = cursor.fetchone()
    return bool(result and result[0] and _VIEW_MARKER in str(result[0]))


def is_satisfied(cursor: Any, database: str) -> bool:
    """Check columns, integer money scale, stable tail order, and total view."""

    metadata = _metadata(cursor, database)
    columns = [name for name, _, _ in metadata]
    if not _REQUIRED_COLUMNS.issubset(columns):
        return False
    scales = {name: scale for name, scale, _ in metadata}
    if any(scales.get(column) != 0 for column in _MONEY_COLUMNS):
        return False
    if tuple(columns[-3:]) != _REQUIRED_TAIL:
        return False
    if _index_issues(cursor, database):
        return False
    return _view_is_current(cursor, database)


def apply(cursor: Any, database: str) -> None:
    """Create missing objects and correct only the known legacy table shape."""

    metadata = _metadata(cursor, database)
    if not metadata:
        cursor.execute(_TABLE_DDL)
        metadata = _metadata(cursor, database)
    columns = [name for name, _, _ in metadata]
    missing = sorted(_REQUIRED_COLUMNS - set(columns))
    if missing:
        raise RuntimeError(
            f"{_TABLE} is missing required columns {missing}; manual repair is required"
        )
    index_issues = _index_issues(cursor, database)
    if index_issues:
        raise RuntimeError(
            f"{_TABLE} has incompatible functional structure: "
            f"{'; '.join(index_issues)}; manual repair is required"
        )

    scales = {name: scale for name, scale, _ in metadata}
    money_columns_to_migrate = sorted(
        column for column in _MONEY_COLUMNS if scales.get(column) != 0
    )
    if money_columns_to_migrate:
        cursor.execute(
            "UPDATE ops_service_revenue_monthly SET "
            + ", ".join(f"{column} = ROUND({column}, 0)" for column in money_columns_to_migrate)
        )
        cursor.execute(
            "ALTER TABLE ops_service_revenue_monthly "
            + ", ".join(
                f"MODIFY COLUMN {column} {_column_definition(column)}"
                for column in money_columns_to_migrate
            )
        )

    if tuple(columns[-3:]) != _REQUIRED_TAIL:
        cursor.execute(
            "ALTER TABLE ops_service_revenue_monthly "
            f"MODIFY COLUMN erp_create_date {_column_definition('erp_create_date')} "
            "AFTER signing_yoy_rate"
        )
    cursor.execute(_VIEW_DDL)
