"""Shared column contracts for legacy and standard ERP snapshots."""

from __future__ import annotations


LEGACY_ERP_COLUMN_MAP = [
    ("序号", "seq_no"),
    ("合同编号", "contract_id"),
    ("销售组织", "sales_org"),
    ("是否初始化", "is_initialized"),
    ("合同名称", "contract_name"),
    ("合同申请日期", "contract_apply_date"),
    ("销售业绩部门", "sales_dept"),
    ("申请人", "applicant"),
    ("销售员", "sales_person"),
    ("签约客户", "sign_customer"),
    ("最终客户", "final_customer"),
    ("第三方", "third_party"),
    ("合同类型", "contract_type"),
    ("暂估运维运营", "is_estimated_ops"),
    ("虚拟合同", "is_virtual"),
    ("2026运维saas续签合同", "is_2026_saas_renew"),
    ("单据状态", "doc_status"),
    ("关闭状态", "close_status"),
    ("合同执行状态", "exec_status"),
    ("归档状态", "archive_status"),
    ("归档日期", "archive_date"),
    ("合同总金额", "total_amount"),
    ("免费运维期（月）", "free_ops_months"),
    ("年运维约定金额", "annual_ops_amount"),
    ("城市", "city"),
    ("省份", "province"),
    ("企业版销售合同明细id", "sales_contract_detail_id"),
    ("标的行编码", "item_code"),
    ("标的", "item_name"),
    ("业务类型", "business_type"),
    ("交付项目编码", "project_code"),
    ("交付项目", "project_name"),
    ("运维签约类型", "ops_sign_type"),
    ("明细数量", "detail_qty"),
    ("销售单价", "unit_price"),
    ("明细价税合计", "detail_amount_with_tax"),
    ("明细运维开始开始日期", "ops_start_date"),
    ("明细运维结束日期", "ops_end_date"),
    ("执行明细id", "exec_detail_id"),
    ("产品物料", "product_material"),
    ("产品占比", "product_ratio"),
    ("云服务类型", "cloud_service_type"),
    ("产品金额", "product_amount"),
    ("一级产品线", "product_line1"),
    ("二级产品线", "product_line2"),
    ("产品公司", "product_company"),
    ("所属事业部", "division"),
    ("累计开票金额", "cum_billing"),
    ("累计回款金额", "cum_collection"),
    ("累计确收金额", "cum_revenue"),
    ("当年开票金额", "cur_year_billing"),
    ("去年同期开票金额", "prev_year_billing"),
    ("当年回款金额", "cur_year_collection"),
    ("去年同期回款金额", "prev_year_collection"),
    ("当年收入金额", "cur_year_revenue"),
    ("去年同期收入金额", "prev_year_revenue"),
    ("当年应分摊金额", "cur_year_amort"),
    ("去年同期应分摊金额", "prev_year_amort"),
    ("营销平台", "sales_platform"),
    ("体系工程师", "system_engineer"),
    ("是否公有云", "is_public_cloud"),
    ("是否一次性收入", "is_one_time_revenue"),
    ("合同分类", "contract_category"),
    ("业务类别", "business_category"),
    ("其他业务类型", "other_business_type"),
    ("无效合同类型", "invalid_contract_type"),
    ("数据来源", "data_source"),
    ("文件生成时间戳", "create_date"),
    ("文件来源时间戳", "file_source_date"),
]

ALLOCATION_COLUMN_MAP = [
    ("合同天数", "contract_days"),
    ("去年统计起始日期", "prev_year_period_start"),
    ("去年统计截止日期", "prev_year_period_end"),
    ("去年按期分摊服务费", "prev_year_calc_amort"),
    ("去年倒签调整后分摊服务费", "prev_year_adjusted_amort"),
    ("今年统计起始日期", "cur_year_period_start"),
    ("今年统计截止日期", "cur_year_period_end"),
    ("今年按期分摊服务费", "cur_year_calc_amort"),
    ("今年倒签调整后分摊服务费", "cur_year_adjusted_amort"),
]

STANDARD_ERP_COLUMN_MAP = LEGACY_ERP_COLUMN_MAP + ALLOCATION_COLUMN_MAP


def legacy_headers() -> list[str]:
    return [header for header, _ in LEGACY_ERP_COLUMN_MAP]


def standard_headers() -> list[str]:
    return [header for header, _ in STANDARD_ERP_COLUMN_MAP]
