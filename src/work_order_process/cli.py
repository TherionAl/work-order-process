"""命令行入口。

通过 `uv run work-order-process <command>` 执行不同的数据获取和转换任务。
常用命令：
- customers / contacts：导出客户和联系人；
- companies / company-contacts：导出公司和公司联系人；
- ticket-details-refresh：重新获取工单详情并生成 raw、value_resolved、chinese 三份文件；
- ticket-month-counts / ticket-month-ids / ticket-month-details：按创建月份统计、导出 ID、导出详情。
"""

from __future__ import annotations

import argparse
import json

from rich.console import Console
from rich.table import Table

from .api import ApiError, WorkOrderClient
from .config import load_settings
from .dictionary import DataDictionary
from .io import write_json
from .monthly_export import count_ticket_months, export_month_ticket_details, export_month_ticket_ids
from .resolver import refresh_ticket_details_from_api, resolve_ticket_details, resolve_ticket_sample_refs
from .transform import enrich_tickets, translate_many


console = Console()


def main() -> None:
    """解析命令行参数并分发到对应任务函数。"""

    parser = argparse.ArgumentParser(description="Fetch and normalize Bosssoft work-order data.")
    parser.add_argument(
        "command",
        choices=[
            "dictionary",
            "probe",
            "customers",
            "contacts",
            "companies",
            "company-contacts",
            "company-relation",
            "tickets-resolved",
            "ticket-details-resolved",
            "ticket-details-refresh",
            "ticket-month-counts",
            "ticket-month-ids",
            "ticket-month-details",
            "tickets",
            "run",
        ],
        nargs="?",
        default="run",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed for ticket sampling.")
    parser.add_argument("--year", type=int, default=2025, help="Year for monthly ticket commands.")
    parser.add_argument("--month", type=int, default=None, help="Month number for monthly ticket commands.")
    parser.add_argument("--per-page", type=int, default=1000, help="Page size for monthly ticket search.")
    parser.add_argument("--limit", type=int, default=None, help="Optional max rows for monthly commands.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing monthly output files.")
    args = parser.parse_args()

    settings = load_settings()
    dictionary = DataDictionary.from_pdf(settings.dictionary_path)

    if args.command == "dictionary":
        output = settings.output_dir / "dictionary.json"
        dictionary.save_json(output)
        console.print(f"Saved dictionary to {output}")
        _print_dictionary_summary(dictionary)
        return

    if args.command == "probe":
        with WorkOrderClient(settings) as client:
            _probe_auth(client)
            try:
                client.authenticate()
            except ApiError:
                console.print("[yellow]Basic Auth is missing username or password; check agents.md or .env.[/yellow]")
                return
            _probe(client)
        return

    try:
        with WorkOrderClient(settings) as client:
            client.authenticate()
            if args.command == "customers":
                _customers(client, dictionary, settings.output_dir)
            elif args.command == "contacts":
                _contacts(client, dictionary, settings.output_dir)
            elif args.command == "companies":
                _companies(client, dictionary, settings.output_dir)
            elif args.command == "company-contacts":
                _company_contacts(client, dictionary, settings.output_dir)
            elif args.command == "company-relation":
                _company_relation(settings.output_dir)
            elif args.command == "tickets-resolved":
                _tickets_resolved(client, dictionary, settings.output_dir)
            elif args.command == "ticket-details-resolved":
                _ticket_details_resolved(client, dictionary, settings.output_dir)
            elif args.command == "ticket-details-refresh":
                _ticket_details_refresh(client, dictionary, settings.output_dir, settings.ticket_since, settings.sample_size, args.seed)
            elif args.command == "ticket-month-counts":
                _ticket_month_counts(client, args.year)
            elif args.command == "ticket-month-ids":
                _require_month(args.month)
                _ticket_month_ids(client, settings.output_dir, args.year, args.month, args.per_page, args.limit, args.overwrite)
            elif args.command == "ticket-month-details":
                _require_month(args.month)
                _ticket_month_details(
                    client,
                    dictionary,
                    settings.output_dir,
                    args.year,
                    args.month,
                    args.per_page,
                    args.limit,
                    args.overwrite,
                )
            elif args.command == "tickets":
                _tickets(client, dictionary, settings.output_dir, settings.ticket_since, settings.sample_size, args.seed)
            else:
                _run_all(client, dictionary, settings.output_dir, settings.ticket_since, settings.sample_size, args.seed)
    except ApiError as exc:
        console.print(f"[red]API error:[/red] {exc}")
        raise SystemExit(2) from exc


def _run_all(
    client: WorkOrderClient,
    dictionary: DataDictionary,
    output_dir,
    since: str,
    sample_size: int,
    seed: int | None,
) -> None:
    """默认任务：导出客户、联系人、抽样工单和数据字典。"""

    customers = client.fetch_customers()
    contacts = client.fetch_contacts()
    sample = client.fetch_ticket_sample_since(sample_size, since, seed)

    write_json(output_dir / "customers_raw.json", customers)
    write_json(output_dir / "contacts_raw.json", contacts)
    write_json(output_dir / "tickets_sample_raw.json", sample)
    write_json(output_dir / "customers.json", translate_many(dictionary, "user", customers))
    write_json(output_dir / "contacts.json", translate_many(dictionary, "contacter", contacts))
    write_json(output_dir / "tickets_sample.json", enrich_tickets(sample, contacts, customers, dictionary))
    dictionary.save_json(output_dir / "dictionary.json")

    console.print(f"Saved customers, contacts, and {len(sample)} sampled tickets to {output_dir}")


def _customers(client: WorkOrderClient, dictionary: DataDictionary, output_dir) -> None:
    """导出客户原始数据和中文字段版本。"""

    rows = client.fetch_customers()
    write_json(output_dir / "customers_raw.json", rows)
    write_json(output_dir / "customers.json", translate_many(dictionary, "user", rows))
    console.print(f"Saved {len(rows)} customers to {output_dir}")


def _contacts(client: WorkOrderClient, dictionary: DataDictionary, output_dir) -> None:
    """导出联系人原始数据和中文字段版本。"""

    rows = client.fetch_contacts()
    write_json(output_dir / "contacts_raw.json", rows)
    write_json(output_dir / "contacts.json", translate_many(dictionary, "contacter", rows))
    console.print(f"Saved {len(rows)} contacts to {output_dir}")


def _companies(client: WorkOrderClient, dictionary: DataDictionary, output_dir) -> None:
    """导出公司原始数据和中文字段版本。"""

    rows = client.fetch_companies()
    write_json(output_dir / "companies_raw.json", rows)
    write_json(output_dir / "companies.json", translate_many(dictionary, "user", rows))
    console.print(f"Saved {len(rows)} companies to {output_dir}")


def _company_contacts(client: WorkOrderClient, dictionary: DataDictionary, output_dir) -> None:
    """导出公司联系人原始数据和中文字段版本。"""

    rows = client.fetch_company_contacts()
    write_json(output_dir / "company_contacts_raw.json", rows)
    write_json(output_dir / "company_contacts.json", translate_many(dictionary, "contacter", rows))
    console.print(f"Saved {len(rows)} company contacts to {output_dir}")


def _company_relation(output_dir) -> None:
    """检查公司联系人中的公司 ID 是否能和公司主键关联。"""

    companies_path = output_dir / "companies.json"
    contacts_path = output_dir / "company_contacts.json"
    if not companies_path.exists() or not contacts_path.exists():
        raise ApiError("Run `companies` and `company-contacts` before `company-relation`.")

    companies = json.loads(companies_path.read_text(encoding="utf-8"))
    contacts = json.loads(contacts_path.read_text(encoding="utf-8"))
    company_id_field = "主键 ID"
    contact_company_field = "所属公司关联 user 表的 uId"

    def nonempty(value) -> bool:
        return value is not None and str(value).strip() != ""

    company_ids = {str(row.get(company_id_field)).strip() for row in companies if nonempty(row.get(company_id_field))}
    contacts_with_company = [row for row in contacts if nonempty(row.get(contact_company_field))]
    matched = [
        row
        for row in contacts_with_company
        if str(row.get(contact_company_field)).strip() in company_ids
    ]
    unmatched = [
        row
        for row in contacts_with_company
        if str(row.get(contact_company_field)).strip() not in company_ids
    ]
    strict_contacts = [
        row
        for row in contacts
        if all(nonempty(row.get(field)) for field in [company_id_field, "姓名", "手机号", contact_company_field])
    ]
    strict_matched = [
        row
        for row in strict_contacts
        if str(row.get(contact_company_field)).strip() in company_ids
    ]

    report = {
        "company_count": len(companies),
        "company_contact_count": len(contacts),
        "company_id_count": len(company_ids),
        "contacts_with_company_id": len(contacts_with_company),
        "matched_contacts": len(matched),
        "unmatched_contacts": len(unmatched),
        "match_rate": round(len(matched) / len(contacts_with_company) * 100, 2) if contacts_with_company else 0,
        "strict_contacts": len(strict_contacts),
        "strict_matched_contacts": len(strict_matched),
        "strict_match_rate": round(len(strict_matched) / len(strict_contacts) * 100, 2) if strict_contacts else 0,
        "relation": f"company_contacts.{contact_company_field} = companies.{company_id_field}",
        "unmatched_samples": [
            {
                "主键 ID": row.get("主键 ID"),
                "姓名": row.get("姓名"),
                "手机号": row.get("手机号"),
                contact_company_field: row.get(contact_company_field),
            }
            for row in unmatched[:20]
        ],
    }
    write_json(output_dir / "company_relation_report.json", report)

    table = Table("Metric", "Value")
    for key, value in report.items():
        if key != "unmatched_samples":
            table.add_row(key, str(value))
    console.print(table)


def _tickets(
    client: WorkOrderClient,
    dictionary: DataDictionary,
    output_dir,
    since: str,
    sample_size: int,
    seed: int | None,
) -> None:
    """抽样导出指定日期之后的工单列表。"""

    sample = client.fetch_ticket_sample_since(sample_size, since, seed)
    write_json(output_dir / "tickets_sample_raw.json", sample)
    write_json(output_dir / "tickets_sample.json", [dictionary.translate_record("tickets", item) for item in sample])
    console.print(f"Saved {len(sample)} sampled tickets from {since} onward to {output_dir}")


def _tickets_resolved(client: WorkOrderClient, dictionary: DataDictionary, output_dir) -> None:
    """对已存在的工单列表样本做 value 替换和 key 中文化。"""

    report = resolve_ticket_sample_refs(output_dir, dictionary, client)
    table = Table("Metric", "Value")
    for key, value in report.items():
        table.add_row(key, str(value))
    console.print(table)


def _ticket_details_resolved(client: WorkOrderClient, dictionary: DataDictionary, output_dir) -> None:
    """基于已有工单详情 raw 文件，重新生成后两份转换文件。"""

    report = resolve_ticket_details(output_dir, dictionary, client)
    table = Table("Metric", "Value")
    for key, value in report.items():
        table.add_row(key, str(value))
    console.print(table)


def _ticket_details_refresh(
    client: WorkOrderClient,
    dictionary: DataDictionary,
    output_dir,
    since: str,
    sample_size: int,
    seed: int | None,
) -> None:
    """重新拉取工单详情并生成 raw/value_resolved/chinese 三份文件。"""

    report = refresh_ticket_details_from_api(output_dir, dictionary, client, since, sample_size, seed)
    table = Table("Metric", "Value")
    for key, value in report.items():
        table.add_row(key, str(value))
    console.print(table)


def _ticket_month_counts(client: WorkOrderClient, year: int) -> None:
    """按创建月份统计某一年的工单量，用于判断后续月度导出规模。"""

    report = count_ticket_months(client, year)
    table = Table("Month", "Count")
    for item in report["months"]:
        table.add_row(str(item["month"]), str(item["count"]))
    table.add_row(f"{year}-total", str(report["total"]))
    console.print(table)


def _ticket_month_ids(
    client: WorkOrderClient,
    output_dir,
    year: int,
    month: int,
    per_page: int,
    limit: int | None,
    overwrite: bool,
) -> None:
    """导出某个月的工单 ID 列表，供详情导出复用。"""

    report = export_month_ticket_ids(
        output_dir,
        client,
        year,
        month,
        per_page=per_page,
        limit=limit,
        overwrite=overwrite,
    )
    _print_report_table(report)


def _ticket_month_details(
    client: WorkOrderClient,
    dictionary: DataDictionary,
    output_dir,
    year: int,
    month: int,
    per_page: int,
    limit: int | None,
    overwrite: bool,
) -> None:
    """按某个月的工单 ID 拉详情，并生成 raw/value_resolved/chinese 三份月度文件。"""

    report = export_month_ticket_details(
        output_dir,
        dictionary,
        client,
        year,
        month,
        per_page=per_page,
        limit=limit,
        overwrite=overwrite,
    )
    _print_report_table(report)


def _require_month(month: int | None) -> None:
    """月度 ID 和详情导出必须明确指定月份，避免误跑大任务。"""

    if month is None:
        raise ApiError("Please pass --month 1..12 for this command.")


def _print_report_table(report: dict) -> None:
    """把任务报告以两列表格输出。"""

    table = Table("Metric", "Value")
    for key, value in report.items():
        if isinstance(value, list):
            value = json.dumps(value[:10], ensure_ascii=False)
        table.add_row(str(key), str(value))
    console.print(table)


def _probe(client: WorkOrderClient) -> None:
    """探测配置中的候选接口路径是否可用。"""

    paths = (
        client.settings.endpoint.customer_paths
        + client.settings.endpoint.contact_paths
        + client.settings.endpoint.ticket_paths
    )
    table = Table("Path", "Status", "OK", "Detail")
    for result in client.probe_paths(paths):
        table.add_row(result.path, str(result.status_code), "yes" if result.ok else "no", result.detail[:90])
    console.print(table)


def _probe_auth(client: WorkOrderClient) -> None:
    """显示 Basic Auth 参数是否已配置。"""

    table = Table("Auth", "Status", "OK", "Detail")
    for result in client.probe_auth_paths():
        table.add_row(result.path, str(result.status_code), "yes" if result.ok else "no", result.detail[:90])
    console.print(table)


def _print_dictionary_summary(dictionary: DataDictionary) -> None:
    """输出各张数据字典表解析到的字段数量。"""

    table = Table("Table", "Fields")
    for name, fields in dictionary.tables.items():
        table.add_row(name, str(len(fields)))
    console.print(table)
