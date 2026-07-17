# ERP 标准 Sheet1 与分摊字段说明

## 输入和输出

ERP 合并需要三份输入文件：新 ERP Excel、旧 ERP Excel 和字段对照 Excel。命令会先生成可导入的标准 Sheet1；可选的文档版用于阅读和核对，不能作为导入文件。

```powershell
uv run erp-merge `
  --input-new .\input\new_erp.xlsx `
  --input-old .\input\old_erp.xlsx `
  --config .\input\字段对照.xlsx `
  --output .\output\erp_standard.xlsx `
  --document-output .\output\erp_document.xlsx
```

`--output` 生成的工作簿只有 `Sheet1`，是唯一可导入的标准数据文件。`--document-output` 生成的工作簿包含 `说明` 和 `文档数据`，用于业务阅读。

## 78 列标准和 69 列兼容

当前标准 Sheet1 固定为 78 列：原有 69 列加上 9 个年度分摊字段。导入程序仍兼容历史 69 列标准文件；历史文件没有的 9 个分摊字段会按空值处理。

文档版的 `文档数据` 在 78 列标准字段之前额外增加 `文档类别` 列，因此共有 79 列。它不满足标准 Sheet1 的完整列头契约，导入程序会明确拒绝该文件，避免误把阅读文件导入。

## 分摊字段口径

`当年应分摊金额`（`cur_year_amort`）和 `去年同期应分摊金额`（`prev_year_amort`）分别保留原 ERP 的 BQ、BR 原始值。它们不等同于本程序计算出的年度分摊结果，也不会被计算结果覆盖。

新增的计算字段包括合同天数、去年/今年统计起止日期、去年/今年按期分摊服务费和倒签调整后分摊服务费。计算时服务期与统计区间取交集，起始日和结束日都计入：

```text
overlap_days = max(0, min(服务结束日, 统计结束日) - max(服务开始日, 统计开始日) + 1)
分摊金额 = 产品金额 * overlap_days / 合同天数
```

其中合同天数同样按首尾两日均计入计算。

## 导入边界

不带 `--import` 时，命令只生成 Excel 文件，不会连接或写入 MySQL。即使同时指定 `--document-output`，也仍是生成模式。

只有明确传入 `--import` 时，才会将 `--output` 指定的标准 Sheet1 导入 MySQL；文档版始终不会导入。

```powershell
uv run erp-merge `
  --input-new .\input\new_erp.xlsx `
  --input-old .\input\old_erp.xlsx `
  --config .\input\字段对照.xlsx `
  --output .\output\erp_standard.xlsx `
  --import
```
