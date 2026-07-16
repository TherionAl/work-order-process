# ERP Standard Import And Allocation Design

## Goal

Restore the established new/old ERP merge flow, generate a database-ready
standard Sheet1, preserve the original ERP amortization values, and store the
new Excel-compatible annual allocation values as separate database fields.

## Inputs And Outputs

### Inputs

- New ERP source workbook.
- Old ERP source workbook.
- Existing TOML mapping rules for old/new ERP normalization, sales platform,
  and system engineer assignment.

### Standard Sheet1

The generated Sheet1 is the only database import source. It contains:

- The established 69 columns in their existing order.
- Nine allocation columns appended after the 69 legacy columns.
- One data sheet named `Sheet1`; no mapping, filter, or documentation sheets
  may precede it.

The 69 legacy columns retain their current meanings. In particular:

- `当年应分摊金额` continues to receive the source ERP value from BQ.
- `去年同期应分摊金额` continues to receive the source ERP value from BR.

### Optional Documentation Workbook

The optional documentation workbook is generated from the standard data after
the import workbook has been created. It may include display sheets and
explanatory content, but it is never an import source.

## Allocation Fields

The following fields are added to both the generated Sheet1 and `erp_data`.

| English column | Chinese meaning | Excel source |
| --- | --- | --- |
| `contract_days` | 合同天数 | AU |
| `prev_year_period_start` | 去年统计起始日期 | AV |
| `prev_year_period_end` | 去年统计截止日期 | AW |
| `prev_year_calc_amort` | 去年按期分摊服务费 | AX |
| `prev_year_adjusted_amort` | 去年倒签调整后分摊服务费 | AY |
| `cur_year_period_start` | 当年统计起始日期 | AZ |
| `cur_year_period_end` | 当年统计截止日期 | BA |
| `cur_year_calc_amort` | 当年按期分摊服务费 | BB |
| `cur_year_adjusted_amort` | 当年倒签调整后分摊服务费 | BC |

The original ERP BQ/BR values and the calculated AY/BC values are separate
business measures and must never overwrite one another.

## Calculation Contract

The calculation implementation must reproduce the supplied Excel AU:BC
results, not replace them with a different inclusive-overlap interpretation.

1. `contract_days` is `DAYS(ops_end_date, ops_start_date) + 1`.
2. The previous and current statistical periods come from configuration.
3. Previous/current unadjusted allocation follows the Excel `IFS` branches:
   no overlap is zero; a fully contained service period receives the complete
   product amount; a service period containing the full statistical year uses
   `365 / contract_days * product_amount`; partial overlap uses Excel `DAYS`
   semantics without an additional day.
4. Previous adjusted allocation is zero when contract application year equals
   the current statistical year; otherwise it equals previous unadjusted
   allocation.
5. Current adjusted allocation equals current unadjusted allocation, plus the
   previous unadjusted allocation only when the contract application year is
   the current statistical year and previous unadjusted allocation is positive.

The tests must include formula-equivalence examples from the supplied workbook,
including a partial-year contract where the Excel `DAYS` rule differs from an
inclusive day count.

## Import Contract

The importer selects the source worksheet by required column headers, not by
worksheet order. It maps values by header name, validates that every required
column appears once, and rejects a workbook with duplicate or missing required
headers.

The importer accepts the generated 78-column standard Sheet1. It may continue
to accept the legacy 69-column standard Sheet1; in that case the nine new
allocation fields are imported as NULL. It must not import the 81-column
presentation workbook directly.

The business key remains:

`(contract_id, item_code, exec_detail_id, create_date)`.

Repeated keys within a snapshot retain the existing upsert behavior: the last
input row updates the earlier row. The import summary reports duplicate-key
updates separately from inserts and unchanged rows.

## Database Migration

Add the nine allocation columns to `erp_data` using an idempotent migration.
Use `INT` for `contract_days`, `DATE` for the four period boundary fields, and
`DECIMAL(18,2)` for the four calculated amount fields. Existing snapshots are
not rewritten by the migration; their new columns remain NULL until an
explicit re-import/backfill is run.

## Verification

1. Unit tests verify the Excel-compatible AU:BC calculation branches.
2. Workbook tests verify 69-column legacy and 78-column standard Sheet1
   header mapping and worksheet selection.
3. Import tests verify BQ/BR values remain mapped to the existing amortization
   columns while AY/BC values map to the new fields.
4. Migration tests verify the generated SQL is idempotent and the live schema
   contains all new columns after it is applied.
5. A small end-to-end fixture verifies standard Sheet1 generation, optional
   documentation workbook generation, and database row values by business key.
