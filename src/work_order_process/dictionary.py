"""数据字典解析与字段中文化。

本模块读取《数据字典-帮我吧.pdf》，提取 user、contacter、tickets、
user_ticket_reply 等表的字段说明，并提供英文字段名到中文标签的转换能力。
如果 PDF 文本抽取失败或缺少某些表，会使用内置 fallback 字典兜底。

首次解析 PDF 后会自动在同目录写入 .parsed.json 缓存文件，后续启动时
若 PDF 未修改则直接读取缓存，避免重复解析 4MB PDF 的耗时。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf import PdfReader


TARGET_TABLES = ("user", "contacter", "tickets", "user_ticket_reply")
FIELD_ALIASES = {
    "contacter": {
        "name": "realName",
        "fixnumber": "phone",
        "companyId": "userId",
        "supportId": "sId",
    },
    "user": {
        "province": "area",
        "city": "area2",
        "supportId": "sId",
    },
}
EXTRA_FIELD_LABELS = {
    "tickets": {
        "ccUserIdList": "抄送人",
        "ccGroupIdList": "抄送客服组",
        "servicerGroupId": "客服组",
        "createrId": "创建人",
        "createrType": "创建人类型",
        "currentNodeField": "当前流程节点字段",
        "currentNodeFieldValue": "当前流程节点值",
        "nodeFieldIntoTime": "进入节点时间",
        "queryIDs": "查询器 ID",
        "workflow_node_id": "工作流节点 ID",
        "workflow_id": "工作流 ID",
        "isDeleted": "是否删除",
        "deleterId": "删除人",
        "deleteDT": "删除时间",
        "descriptattachments": "描述附件",
        "customTemplateId": "自定义模板 ID",
        "custom_fields": "自定义字段",
    }
}
FIELD_TYPE_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?P<type>bigint|smallint|tinyint|int|varchar|datetime|timestamp|decimal|text|date|time|char)"
    r"(?P<suffix>\s*\([^)]+\)|\s+unsigned| unsigned|\([^)]+\)\s+unsigned)?\s*"
    r"(?P<comment>.*)$",
    re.IGNORECASE,
)
TABLE_HEADER_RE = re.compile(
    r"^(?:h3\.\s*)?(?P<table>[A-Za-z][A-Za-z0-9_]+)\s+(?P<title>.+)$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class FieldDefinition:
    """PDF 数据字典中单个字段的结构化定义。"""

    table: str
    table_title: str
    field: str
    field_type: str
    comment: str
    label: str


def normalize_text(value: str) -> str:
    """统一全角/半角和空白字符，减少 PDF 抽取文本带来的噪声。"""

    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\x00", "")
    return re.sub(r"[ \t]+", " ", value).strip()


def _normalize_key(value: str) -> str:
    """把字段名归一化为只含小写字母数字，便于宽松匹配。"""

    return re.sub(r"[^a-z0-9]", "", value.lower())


def label_from_comment(comment: str, fallback: str) -> str:
    """从字段备注中抽取适合作为 JSON key 的中文标签。"""

    comment = normalize_text(comment)
    if not comment:
        return fallback
    label = re.split(r"\s+\(?\d+\s*[-=:>]|[;(（]", comment, maxsplit=1)[0].strip()
    return label or comment or fallback


class DataDictionary:
    """保存数据字典，并提供字段查找、记录翻译和导出能力。"""

    def __init__(self, tables: dict[str, list[FieldDefinition]]) -> None:
        self.tables = tables
        self._field_maps = {
            table: {field.field.lower(): field for field in fields}
            for table, fields in tables.items()
        }
        self._normalized_field_maps = {
            table: {_normalize_key(field.field): field for field in fields}
            for table, fields in tables.items()
        }

    @classmethod
    def from_pdf(cls, path: Path, target_tables: tuple[str, ...] = TARGET_TABLES) -> "DataDictionary":
        """从 PDF 中读取目标表字段定义。

        PDF 是最终中文字段名的主要来源；接口返回字段和 PDF 字段存在少量命名差异时，
        会通过 FIELD_ALIASES 做别名匹配。

        首次解析后会在 PDF 同目录写入 .parsed.json 缓存，后续若 PDF 未修改
        则直接读取缓存，避免重复解析。
        """

        cache_path = path.with_suffix(path.suffix + ".parsed.json")
        if cache_path.exists() and cache_path.stat().st_mtime >= path.stat().st_mtime:
            try:
                return cls.from_json(cache_path)
            except (json.JSONDecodeError, KeyError):
                pass  # 缓存损坏，重新解析

        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        text = normalize_text(text.replace("\r\n", "\n").replace("\r", "\n"))
        lines = text.splitlines()

        tables: dict[str, list[FieldDefinition]] = {}
        for table, title, start, end in _iter_table_sections(lines):
            if table not in target_tables:
                continue
            fields = _parse_fields(table, title, lines[start:end])
            if fields:
                tables[table] = fields

        missing = [table for table in target_tables if table not in tables]
        if missing:
            fallback = fallback_dictionary()
            for table in missing:
                tables[table] = fallback.tables.get(table, [])

        instance = cls(tables)
        # 写入缓存
        try:
            instance.save_json(cache_path)
        except OSError:
            pass  # 缓存写入失败不影响主流程
        return instance

    @classmethod
    def from_json(cls, path: Path) -> "DataDictionary":
        """从缓存 JSON 文件读取数据字典。"""

        raw = json.loads(path.read_text(encoding="utf-8"))
        tables: dict[str, list[FieldDefinition]] = {}
        for table_name, fields in raw.items():
            tables[table_name] = [
                FieldDefinition(
                    table=f.get("table", table_name),
                    table_title=f.get("table_title", table_name),
                    field=f["field"],
                    field_type=f.get("field_type", ""),
                    comment=f.get("comment", ""),
                    label=f.get("label", f["field"]),
                )
                for f in fields
            ]
        return cls(tables)

    def field(self, table: str, key: str) -> FieldDefinition | None:
        """在指定表中查找英文字段对应的数据字典定义。"""

        lookup_key = FIELD_ALIASES.get(table, {}).get(key, key)
        return self._field_maps.get(table, {}).get(lookup_key.lower()) or self._normalized_field_maps.get(table, {}).get(
            _normalize_key(lookup_key)
        )

    def label(self, table: str, key: str) -> str:
        """返回字段中文标签；找不到时保留原英文 key。"""

        field = self.field(table, key)
        if field:
            return field.label
        return EXTRA_FIELD_LABELS.get(table, {}).get(key, key)

    def translate_record(self, table: str, record: dict) -> dict:
        """把单条记录的 key 从英文替换为中文标签，value 保持不变。"""

        translated: dict[str, object] = {}
        used: set[str] = set()
        for key, value in record.items():
            out_key = self.label(table, str(key))
            if out_key in used:
                out_key = f"{out_key}({key})"
            used.add(out_key)
            translated[out_key] = value
        return translated

    def to_jsonable(self) -> dict[str, list[dict[str, str]]]:
        """把数据字典转换成可 JSON 序列化的结构。"""

        return {
            table: [asdict(field) for field in fields]
            for table, fields in self.tables.items()
        }

    def save_json(self, path: Path) -> None:
        """把解析后的数据字典保存为 JSON，便于人工核对字段映射。"""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_jsonable(), ensure_ascii=False, indent=2), encoding="utf-8")


def _iter_table_sections(lines: list[str]):
    """在 PDF 文本行中定位每张表的起止范围。"""

    starts: list[tuple[str, str, int]] = []
    for index, line in enumerate(lines):
        match = TABLE_HEADER_RE.match(line)
        if not match:
            continue
        nearby = "\n".join(lines[index + 1 : index + 5])
        if "字段" not in nearby:
            continue
        starts.append((match.group("table"), normalize_text(match.group("title")), index))

    for pos, (table, title, start) in enumerate(starts):
        end = starts[pos + 1][2] if pos + 1 < len(starts) else len(lines)
        yield table, title, start, end


def _parse_fields(table: str, title: str, lines: list[str]) -> list[FieldDefinition]:
    """解析单张表中的字段行。"""

    fields: list[FieldDefinition] = []
    for raw_line in lines:
        line = normalize_text(raw_line)
        if not line or line.startswith("----") or line.startswith("字段 "):
            continue
        match = FIELD_TYPE_RE.match(line)
        if not match:
            continue
        field = match.group("name")
        field_type = normalize_text(match.group("type") + (match.group("suffix") or ""))
        comment = normalize_text(match.group("comment"))
        fields.append(
            FieldDefinition(
                table=table,
                table_title=title,
                field=field,
                field_type=field_type,
                comment=comment,
                label=label_from_comment(comment, field),
            )
        )
    return fields


def fallback_dictionary() -> DataDictionary:
    """内置兜底字典，避免 PDF 抽取失败时核心字段无法中文化。"""

    raw = {
        "user": [
            ("uId", "int(10)", "主键 ID"),
            ("companyName", "varchar(245)", "公司名称"),
            ("area", "tinyint(1)", "区域省"),
            ("address", "varchar(245)", "地址"),
            ("contactor", "varchar(245)", "联系人"),
            ("mobile", "varchar(30)", "手机号码"),
            ("phone", "varchar(30)", "固定电话"),
            ("rank", "tinyint(1)", "客户级别"),
            ("remark", "text", "备注"),
            ("createTime", "datetime", "创建时间"),
            ("aId", "int(11)", "关联 agents 表的 aId"),
            ("email", "varchar(145)", "邮箱"),
            ("URL", "varchar(245)", "网址"),
            ("updateTime", "datetime", "修改时间"),
            ("sId", "int(11)", "受理客服"),
        ],
        "contacter": [
            ("cId", "int(10)", "主键 ID"),
            ("mobile", "varchar(30)", "手机号"),
            ("phone", "varchar(30)", "座机号"),
            ("position", "varchar(45)", "职位"),
            ("realName", "varchar(30)", "姓名"),
            ("QQ", "varchar(20)", "qq 号"),
            ("email", "varchar(45)", "邮箱"),
            ("agentId", "int(11)", "所属服务商"),
            ("userId", "int(11)", "所属公司"),
            ("note", "varchar(245)", "备注"),
            ("createTime", "timestamp", "创建时间"),
            ("updateTime", "datetime", "更新时间"),
            ("status", "tinyint(4)", "联系人状态"),
        ],
        "tickets": [
            ("ticketId", "int(11)", "主键 ID"),
            ("custUserId", "int(11)", "联系人关联 contacter 表 cId"),
            ("subject", "varchar(100)", "标题"),
            ("descript", "text", "描述"),
            ("servicerUserId", "int(11)", "客服"),
            ("ticketType", "int(11)", "工单类型"),
            ("priorityLevel", "int(11)", "优先级"),
            ("ticketStatus", "tinyint(1)", "工单状态"),
            ("createDT", "datetime", "创建时间"),
            ("updateDT", "datetime", "修改时间"),
            ("waitDT", "datetime", "等待时间"),
            ("solveDT", "datetime", "解决时间"),
            ("openDT", "datetime", "开启时间"),
            ("closeDT", "datetime", "关闭时间"),
            ("agentId", "int(11)", "关联 agents 表 aId"),
            ("ticketSource", "int(11)", "工单来源"),
            ("ticketTemplateId", "int(11)", "工单模板"),
        ],
        "user_ticket_reply": [
            ("ticketReplyId", "int(11)", "主键 ID"),
            ("ticketId", "int(11)", "关联 tickets 的 ticketId"),
            ("replyUserId", "int(11)", "关联 servicers 的 sId"),
            ("replyType", "tinyint(4)", "回复类型"),
            ("replyDT", "datetime", "回复时间"),
            ("replyStatus", "tinyint(1)", "回复状态"),
            ("replyMsg", "text", "回复信息"),
        ],
    }
    return DataDictionary(
        {
            table: [
                FieldDefinition(
                    table=table,
                    table_title=table,
                    field=field,
                    field_type=field_type,
                    comment=comment,
                    label=label_from_comment(comment, field),
                )
                for field, field_type, comment in fields
            ]
            for table, fields in raw.items()
        }
    )
