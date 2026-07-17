# ERP 合并、入库与文档导出

ERP 合并读取新 ERP、旧 ERP 和字段对照文件。字段映射、数据清洗、营销平台处理、体系工程师匹配、年度分摊和重复业务键去重均在代码中完成，不需要先生成 Sheet1 再读回。

默认数据流为：

`新旧 ERP 源文件 -> 代码处理 -> erp_data 快照 -> 文档版 xlsx`

文档版始终从本次写入的 `erp_data` 快照导出，因此与数据库内容一致。

```powershell
uv run --group erp erp-merge `
  --input-new .\input\new_erp.xlsx `
  --input-old .\input\old_erp.xlsx `
  --config .\input\字段对照.xlsx `
  --document-output .\output\erp_document.xlsx
```

如需人工核对中间标准数据，可额外指定 `--standard-output`：

```powershell
uv run --group erp erp-merge `
  --input-new .\input\new_erp.xlsx `
  --input-old .\input\old_erp.xlsx `
  --config .\input\字段对照.xlsx `
  --standard-output .\output\erp_standard.xlsx `
  --document-output .\output\erp_document.xlsx
```

`--standard-output` 不参与入库，仅输出用于核对的 78 列标准 Sheet1。文档版只包含 `文档数据` 工作表，不能作为 ERP 导入源。

## 分摊字段

`cur_year_amort` 与 `prev_year_amort` 保留原 ERP 的 BQ、BR 原始分摊值。以下字段由代码按服务期与统计区间的重叠天数计算，起始日和结束日均计入：

- `contract_days`：合同天数。
- `prev_year_calc_amort`：去年按期分摊服务费。
- `prev_year_adjusted_amort`：去年倒签调整后分摊服务费。
- `cur_year_calc_amort`：今年按期分摊服务费。
- `cur_year_adjusted_amort`：今年倒签调整后分摊服务费。

生成标准数据时，完整业务键 `合同编号 + 标的行编码 + 执行明细id` 的重复行会在计算前按最后一条记录去重，与 `erp_data` 的快照唯一键行为保持一致。
