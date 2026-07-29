"""Discover and coordinate explicit, versioned MySQL schema migrations."""

from __future__ import annotations

import hashlib
import pkgutil
import re
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Callable

import pymysql

from .config import MySQLConfig
from . import migrations as migrations_package


SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
  version INT NOT NULL,
  name VARCHAR(200) NOT NULL,
  checksum CHAR(64) NOT NULL,
  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    is_satisfied: Callable[[Any, str], bool]
    apply: Callable[[Any, str], None]


@dataclass(frozen=True)
class SchemaStatus:
    current_version: int
    target_version: int
    pending_versions: tuple[int, ...]
    drifted_versions: tuple[int, ...]

    @property
    def is_current(self) -> bool:
        return not self.pending_versions and not self.drifted_versions


class SchemaMigrationError(RuntimeError):
    """Raised when migration metadata or schema state is unsafe."""


def discover_migrations() -> tuple[Migration, ...]:
    """Load migration modules and return strictly increasing versions."""

    migrations = []
    module_names = sorted(
        module_info.name
        for module_info in pkgutil.iter_modules(
            migrations_package.__path__,
            f"{migrations_package.__name__}.",
        )
        if re.search(r"\.v\d{4}_[a-z0-9_]+$", module_info.name)
    )
    for module_name in module_names:
        module = import_module(module_name)
        module_path = Path(module.__file__ or "")
        if not module_path.is_file():
            raise SchemaMigrationError(
                f"Migration module {module_name} has no readable source file."
            )
        migrations.append(
            Migration(
                version=module.VERSION,
                name=module.NAME,
                checksum=hashlib.sha256(module_path.read_bytes()).hexdigest(),
                is_satisfied=module.is_satisfied,
                apply=module.apply,
            )
        )

    versions = [migration.version for migration in migrations]
    if versions != sorted(versions) or len(versions) != len(set(versions)):
        raise SchemaMigrationError(
            "Migration versions must be unique and strictly increasing."
        )
    return tuple(migrations)


def inspect_schema_status(
    cursor: Any,
    migrations: tuple[Migration, ...],
) -> SchemaStatus:
    """Compare recorded migration metadata with discovered migrations."""

    cursor.execute(
        "SELECT version, name, checksum FROM schema_version ORDER BY version"
    )
    applied = {
        int(version): (str(name), str(checksum))
        for version, name, checksum in cursor.fetchall()
    }
    known = {migration.version: migration for migration in migrations}
    unknown = sorted(set(applied) - set(known))
    if unknown:
        raise SchemaMigrationError(
            f"Unknown applied migration versions: {unknown}."
        )

    drifted = []
    for version, (name, checksum) in applied.items():
        migration = known[version]
        if name != migration.name or checksum != migration.checksum:
            drifted.append(version)
    if drifted:
        raise SchemaMigrationError(
            "Applied migration checksum or name drift detected for versions "
            f"{drifted}."
        )

    pending = tuple(
        migration.version
        for migration in migrations
        if migration.version not in applied
    )
    return SchemaStatus(
        current_version=max(applied, default=0),
        target_version=max(known, default=0),
        pending_versions=pending,
        drifted_versions=(),
    )


def apply_pending_migrations(
    connection: Any,
    *,
    database: str,
    migrations: tuple[Migration, ...],
) -> SchemaStatus:
    """Apply and record pending migrations on an existing connection.

    MySQL DDL can auto-commit even when the later version-record insert fails.
    Each retry therefore calls ``is_satisfied`` before applying DDL and can
    reconcile a completed schema change by recording only its version.
    """

    with connection.cursor() as cursor:
        cursor.execute(SCHEMA_VERSION_DDL)
        status = inspect_schema_status(cursor, migrations)
        pending = set(status.pending_versions)
        for migration in migrations:
            if migration.version not in pending:
                continue
            try:
                if not migration.is_satisfied(cursor, database):
                    migration.apply(cursor, database)
                cursor.execute(
                    "INSERT INTO schema_version (version, name, checksum) "
                    "VALUES (%s, %s, %s)",
                    (migration.version, migration.name, migration.checksum),
                )
                connection.commit()
            except Exception as exc:
                connection.rollback()
                raise SchemaMigrationError(
                    f"Migration {migration.version} ({migration.name}) failed: "
                    f"{exc}. MySQL DDL may auto-commit; rerun mysql-migrate "
                    "to reconcile completed DDL through is_satisfied."
                ) from exc
        return inspect_schema_status(cursor, migrations)


def _unapplied_status(
    migrations: tuple[Migration, ...],
) -> SchemaStatus:
    versions = tuple(migration.version for migration in migrations)
    return SchemaStatus(
        current_version=0,
        target_version=max(versions, default=0),
        pending_versions=versions,
        drifted_versions=(),
    )


def _schema_version_exists(cursor: Any, database: str) -> bool:
    cursor.execute(
        "SELECT TABLE_NAME FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name = %s",
        (database, "schema_version"),
    )
    return cursor.fetchone() is not None


def schema_status(config: MySQLConfig) -> SchemaStatus:
    """Inspect migration state without creating or altering schema objects."""

    migrations = discover_migrations()
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
            if not _schema_version_exists(cursor, config.database):
                return _unapplied_status(migrations)
            return inspect_schema_status(cursor, migrations)


def migrate_schema(config: MySQLConfig) -> SchemaStatus:
    """Explicitly apply every pending migration."""

    with pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=False,
    ) as connection:
        return apply_pending_migrations(
            connection,
            database=config.database,
            migrations=discover_migrations(),
        )


def record_satisfied_migrations(
    connection: Any,
    *,
    database: str,
    migrations: tuple[Migration, ...],
) -> SchemaStatus:
    """Record satisfied baselines without applying any migration DDL."""

    with connection.cursor() as cursor:
        cursor.execute(SCHEMA_VERSION_DDL)
        status = inspect_schema_status(cursor, migrations)
        pending = set(status.pending_versions)
        for migration in migrations:
            if (
                migration.version not in pending
                or not migration.is_satisfied(cursor, database)
            ):
                continue
            try:
                cursor.execute(
                    "INSERT INTO schema_version (version, name, checksum) "
                    "VALUES (%s, %s, %s)",
                    (migration.version, migration.name, migration.checksum),
                )
                connection.commit()
            except Exception as exc:
                connection.rollback()
                raise SchemaMigrationError(
                    f"Could not record satisfied migration "
                    f"{migration.version} ({migration.name}): {exc}."
                ) from exc
        return inspect_schema_status(cursor, migrations)


def record_satisfied_schema(config: MySQLConfig) -> SchemaStatus:
    """Record migrations already satisfied after an explicit mysql-init."""

    with pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=False,
    ) as connection:
        return record_satisfied_migrations(
            connection,
            database=config.database,
            migrations=discover_migrations(),
        )


def assert_schema_current(config: MySQLConfig) -> None:
    """Refuse runtime work when explicit migrations remain pending."""

    status = schema_status(config)
    if status.is_current:
        return
    raise SchemaMigrationError(
        "Database schema is not current; "
        f"pending migrations: {list(status.pending_versions)}, "
        f"drifted migrations: {list(status.drifted_versions)}. "
        "Run `work_order_process mysql-schema-status`, then "
        "`work_order_process mysql-migrate`."
    )
