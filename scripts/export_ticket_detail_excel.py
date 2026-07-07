"""把单条工单详情导出为两张结构化 Excel 表。

输出文件包含：
1. ticket_detail_main：顶层字段，一行一个字段；
2. ticket_detail_custom_fields：custom_fields 动态字段，一行一个字段。

为避免额外依赖，本脚本使用 Python 标准库直接写入 xlsx 文件。
"""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from work_order_process.api import WorkOrderClient
from work_order_process.config import load_settings
from work_order_process.dictionary import DataDictionary
from work_order_process.resolver import TicketFieldResolver, resolve_ticket_detail_values
from work_order_process.structured_ticket import build_custom_field_excel_rows, build_main_excel_rows


def main() -> None:
    """读取指定工单详情并导出 Excel。"""

    parser = argparse.ArgumentParser(description="Export one ticket detail as structured xlsx.")
    parser.add_argument("ticket_id", help="工单 ID，例如 22256891。")
    parser.add_argument("--output", default=None, help="输出 xlsx 路径。")
    args = parser.parse_args()

    settings = load_settings()
    dictionary = DataDictionary.from_pdf(settings.dictionary_path)
    output_path = Path(args.output) if args.output else settings.output_dir / f"ticket_{args.ticket_id}_structured.xlsx"

    with WorkOrderClient(settings) as client:
        client.authenticate()
        raw_detail = client.fetch_ticket_detail(args.ticket_id)
        if not raw_detail:
            raise SystemExit(f"Ticket detail not found: {args.ticket_id}")
        field_resolver = TicketFieldResolver(client.fetch_ticket_fields(), client.fetch_company_fields())
        value_detail = resolve_ticket_detail_values(raw_detail, client, field_resolver)

    main_rows = build_main_excel_rows(raw_detail, value_detail, dictionary)
    custom_rows = build_custom_field_excel_rows(raw_detail, value_detail)
    write_xlsx(
        output_path,
        {
            "ticket_detail_main": main_rows,
            "ticket_detail_custom_fields": custom_rows,
        },
    )
    print(output_path)


def write_xlsx(path: Path, sheets: dict[str, list[list[Any]]]) -> None:
    """使用 xlsx 基础 XML 格式写入多 sheet 工作簿。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_names = list(sheets)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml(len(sheet_names)))
        archive.writestr("_rels/.rels", _root_rels_xml())
        archive.writestr("xl/workbook.xml", _workbook_xml(sheet_names))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml(len(sheet_names)))
        archive.writestr("xl/styles.xml", _styles_xml())
        for index, rows in enumerate(sheets.values(), start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet_xml(rows))


def _worksheet_xml(rows: list[list[Any]]) -> str:
    """生成单个 sheet 的 XML。"""

    xml_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            ref = f"{_column_name(col_index)}{row_index}"
            text = _clean_xml_text(str(value if value is not None else ""))
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(text)}</t></is></c>')
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    dimension = f"A1:{_column_name(max((len(row) for row in rows), default=1))}{len(rows)}"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        '<sheetData>'
        f'{"".join(xml_rows)}'
        '</sheetData>'
        '</worksheet>'
    )


def _content_types_xml(sheet_count: int) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f"{overrides}"
        "</Types>"
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def _workbook_xml(sheet_names: list[str]) -> str:
    sheets = "".join(
        f'<sheet name="{escape(name[:31])}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets>"
        "</workbook>"
    )


def _workbook_rels_xml(sheet_count: int) -> str:
    rels = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    rels += (
        f'<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels}"
        "</Relationships>"
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
        '<cellXfs count="1"><xf xfId="0"/></cellXfs>'
        "</styleSheet>"
    )


def _column_name(index: int) -> str:
    """把 1-based 列号转成 Excel 列名。"""

    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _clean_xml_text(value: str) -> str:
    """移除 Excel XML 不允许的控制字符。"""

    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)


if __name__ == "__main__":
    main()
