#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

readonly BACKUP_DIR="${BACKUP_DIR:-/var/backups/work-order-process}"
readonly DATABASE_NAME="${DATABASE_NAME:-work_order_datalake}"
readonly RETENTION_DAYS="${RETENTION_DAYS:-14}"
readonly TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
readonly FINAL_PATH="${BACKUP_DIR}/${DATABASE_NAME}_${TIMESTAMP}.sql.gz"
readonly TEMP_PATH="${FINAL_PATH}.tmp"

if [[ ! "${RETENTION_DAYS}" =~ ^[0-9]+$ ]]; then
    echo "RETENTION_DAYS must be a non-negative integer" >&2
    exit 2
fi

install -d -m 0700 "${BACKUP_DIR}"
trap 'rm -f "${TEMP_PATH}"' EXIT

mysqldump \
    --defaults-extra-file=/etc/work-order-process/mysql-backup.cnf \
    --single-transaction \
    --quick \
    --routines \
    --triggers \
    --events \
    --set-gtid-purged=OFF \
    "${DATABASE_NAME}" \
    | gzip -9 > "${TEMP_PATH}"

test -s "${TEMP_PATH}"
mv "${TEMP_PATH}" "${FINAL_PATH}"
trap - EXIT

find "${BACKUP_DIR}" \
    -type f \
    -name "${DATABASE_NAME}_*.sql.gz" \
    -mtime "+${RETENTION_DAYS}" \
    -delete

echo "Created ${FINAL_PATH}"
