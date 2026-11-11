# 第三方技能来源登记（PROVENANCE）

本文件登记 `skills/vendor/` 下所有第三方内容。**新增任何第三方技能都必须在此追加一段**，
字段不全视为未审核。

## anthropic/skills · document-skills（pdf、xlsx）

| 字段 | 值 |
| --- | --- |
| GitHub 地址 | https://github.com/anthropics/skills |
| 上游子目录 | `skills/pdf`、`skills/xlsx` |
| 许可证 | Apache License 2.0（每个目录内自带 `LICENSE.txt`，未删改） |
| 固定版本 | commit `2c5958ecac7cee2b583967a2c4981d0f4a75c579`（2026-11-06，`Move Office skills to the Apache 2.0 license (#2146)`） |
| 引入方式 | `git clone --depth 1` 后只复制上述两个目录 + `THIRD_PARTY_NOTICES.md`，未复制整个仓库 |
| 未引入 | `docx`、`pptx`（本轮不制作 Office 成品，各约 1.16 MB；需要时按同样方式登记引入） |
| 体积 | 约 1.25 MB（含 xlsx 的 OOXML XSD 校验 schema） |
| 安装依赖 | 无额外依赖即可阅读；其脚本可能用到 `pypdf`、`openpyxl`、`python-pptx`、`LibreOffice(soffice)`。本仓库已安装 `openpyxl`、`python-pptx`（由 `markitdown[pdf,docx,xlsx,pptx]` 带入）；**未安装 LibreOffice**，因此 `xlsx/scripts/recalc.py`、`xlsx/scripts/office/soffice.py` 在本机**不可用** |
| 使用方式 | 作为方法参考（提取表格、读公式、检查表单域等）；实际解析统一走 `backend/app/capabilities/adapters/markitdown_adapter.py` |
| 安全边界 | 纯文档与本地脚本，**不联网**；不读取环境变量、不发送数据；`soffice.py` 只调用本地 LibreOffice 可执行文件，本机无该程序 |
| 额外运行资源 | 无模型、无 API Key、无二进制大文件；xlsx 目录内的 `.xsd` 是标准 schema 文本 |
| 已做的本地改动 | **无**（内容原样保留，仅复制） |

### 许可证要点（照抄上游，未改动）

- Apache 2.0 允许商用、修改、再分发，需保留版权与许可声明、标明修改。
- 我们未修改内容，因此不需要逐文件标注修改；保留 `LICENSE.txt` 与 `THIRD_PARTY_NOTICES.md` 即可满足声明义务。
