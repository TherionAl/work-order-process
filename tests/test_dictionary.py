from pathlib import Path

from work_order_process.dictionary import DataDictionary


def test_dictionary_extracts_target_tables() -> None:
    dictionary = DataDictionary.from_pdf(Path("数据字典-帮我吧.pdf"))

    assert dictionary.label("user", "companyName") == "公司名称"
    assert dictionary.label("contacter", "realName") == "姓名"
    assert dictionary.label("tickets", "subject") == "标题"


def test_translate_record_uses_dictionary_labels() -> None:
    dictionary = DataDictionary.from_pdf(Path("数据字典-帮我吧.pdf"))

    translated = dictionary.translate_record(
        "tickets",
        {"ticketId": 1, "subject": "测试工单", "unknown": "kept"},
    )

    assert translated["主键 ID"] == 1
    assert translated["标题"] == "测试工单"
    assert translated["unknown"] == "kept"


def test_translate_record_uses_api_aliases() -> None:
    dictionary = DataDictionary.from_pdf(Path("数据字典-帮我吧.pdf"))

    translated = dictionary.translate_record(
        "contacter",
        {"name": "张三", "fixnumber": "0591-123456", "companyId": 10},
    )

    assert translated["姓名"] == "张三"
    assert translated["座机号"] == "0591-123456"
    assert translated["所属公司关联 user 表的 uId"] == 10
