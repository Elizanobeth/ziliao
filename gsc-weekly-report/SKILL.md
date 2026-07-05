---
name: gsc-weekly-report
description: 从固定格式的多 sheet 团队周报表格生成 GSC 周报 PPT 或 HTML。用户要求生成 GSC 周报、指定周次周报，例如“28周周报”“W28周报”，或要求从本地 Excel、WPS 导出 Excel 中提取并整理指定 section 内容生成 PPT/HTML 周报时使用。
---

# GSC 周报小助手

使用此 skill 将团队固定格式的周报表格生成 PPT 周报。每个 sheet 代表一个可选择的 section；当用户选择多个 section 时，先合并这些 section 的内容，再生成一份统一周报，不要按 section 分开写。

## 工作流程

1. 识别用户要求的周次。
   - 支持“28周”“W28”“w28”等表达。
   - 传给脚本时使用整数周次，例如 `28`。

2. 确认周报数据源。
   - 优先使用本地 `.xlsx` 文件。
   - 如果来源是 WPS 在线文档，先让用户提供或使用 WPS 导出的 `.xlsx` 文件；导出后按本地 Excel 处理。

3. 确认用户选择的 section。
   - sheet 名称就是 section 名称。
   - 如果用户指定一个或多个 section，用 `--sections` 传入。
   - 如果用户未指定 section，默认使用所有包含目标周次字段的 sheet。
   - 如果用户选择多个 section，必须先把这些 sheet 的行内容合并到同一个内容池，再统一总结。

4. 提取结构化原始数据：

```bash
python scripts/extract_weekly_data.py \
  --input "/path/to/weekly.xlsx" \
  --week 28 \
  --sections "sectionA,sectionB" \
  --output "/path/to/raw_weekly_data.json"
```

不需要筛选 section 时，省略 `--sections`。

5. 由 agent 做轻量整理。
   - 读取 `raw_weekly_data.json`。
   - 生成结构清晰的 `weekly_report_summary.json`。
   - 不要把表格单元格内容直接复制到 PPT。
   - 整理重点是统一表达、合并重复说法、保留核心事实，并让内容适合当前 PPT 模板阅读。
   - 如果多个原始单元格表达的是同一事项，可以合并，但要保留关键事实：任务名、系统名、里程碑、责任人、阻塞点、依赖方、需要决策的事项、下周动作。
   - 按四类内容统一整理：`main_progress`、`issues`、`help_needed`、`next_plan`。
   - 使用正式、简洁的中文周报口吻。
   - 生成 PPT 时不要依赖脚本自动分页；内容很多时应先做轻量总结，或建议用户改用 HTML 版。
   - 如果用户明确要求 HTML 长文版或无损整理，再保留全部有效事项。
   - 如果某类没有来源内容，写真实的空状态，例如“暂无明显问题。”或“暂无需额外协调事项。”
   - 不要编造进展、风险、依赖、人员或日期。

总结 JSON 使用以下结构：

```json
{
  "week": "W28",
  "title": "W28 周报",
  "scope": "sectionA、sectionB",
  "main_progress": [
    "围绕 xxx 完成核心流程推进，关键事项已进入联调或验证阶段。"
  ],
  "issues": [
    "部分事项受接口联调和数据口径确认影响，存在推进节奏不一致的问题。"
  ],
  "help_needed": [
    "需要业务侧确认 xxx 口径，并协调 IT 接口人支持 xxx。"
  ],
  "next_plan": [
    "下周重点推进 xxx 上线准备、xxx 验证和遗留问题闭环。"
  ]
}
```

6. 基于总结 JSON 生成最终周报。

生成 PPT：

```bash
python scripts/generate_weekly_ppt.py \
  --summary "/path/to/weekly_report_summary.json" \
  --template "assets/weekly_template.pptx" \
  --output "/path/to/GSC_W28_周报.pptx"
```

生成 HTML：

```bash
python scripts/generate_weekly_html.py \
  --summary "/path/to/weekly_report_summary.json" \
  --template "assets/weekly_template.html" \
  --output "/path/to/GSC_W28_周报.html"
```

省略 `--template` 时，默认使用 `assets/weekly_template.html`。

如果用户没有明确要求格式，优先生成 HTML；HTML 对长内容、顺序流式排版、打印和复制更友好。

7. 检查输出结果。
   - 确认生成的 PPT 中没有未替换的占位符，或生成的 HTML 可以直接打开。
   - 确认用户选择多个 section 时，内容已经合并为一份周报。
   - 确认最终内容是整理后的周报表达，不是表格原文逐行搬运。
   - PPT 只填充模板中的现有页面，不自动复制模板页，不在标题后追加 `(1/4)`、`第1页` 等页数标记。

## 表格字段

需要了解表格列、周次字段匹配、JSON 字段和 PPT 占位符时，读取 `references/report_schema.md`。

## PPT 模板

PPTX 模板需要包含以下占位符：

- `{{REPORT_WEEK}}`
- `{{REPORT_TITLE}}`
- `{{SCOPE}}`
- `{{GENERATED_DATE}}`
- `{{MAIN_PROGRESS}}`
- `{{ISSUES}}`
- `{{HELP_NEEDED}}`
- `{{NEXT_PLAN}}`

默认 PPT 无封面。模板中的正文页从上到下依次写：`主要进展`、`存在问题`、`需要帮助`、`下周计划`。`generate_weekly_ppt.py` 只替换模板中实际存在的占位符，不主动更改字号、缩进、项目符号或段落样式；如果一个占位符替换为多行内容，新增行复制该占位符所在段落的格式。当前默认模板中，栏目标题保留模板自带项目符号，`{{MAIN_PROGRESS}}`、`{{ISSUES}}`、`{{HELP_NEEDED}}`、`{{NEXT_PLAN}}` 所在段落为无项目符号缩进。脚本不自动复制页面、不自动分页、不隐藏或移动栏目。周报标题不要追加页数。后续可以用团队正式模板替换 `assets/weekly_template.pptx`，但需要保留需要填充的占位符。

## HTML 输出

HTML 输出使用独立模板文件 `assets/weekly_template.html`。`generate_weekly_html.py` 会读取模板并生成一个自包含 `.html` 文件，无需网络和服务器。HTML 按 `主要进展`、`存在问题`、`需要帮助`、`下周计划` 的顺序自然流式排版，只显示有内容的栏目，不显示空栏目，不追加页码，不拆分单个条目。

HTML 模板需要保留以下占位符：

- `{{REPORT_TITLE}}`
- `{{REPORT_WEEK}}`
- `{{SCOPE}}`
- `{{GENERATED_DATE}}`
- `{{REPORT_SECTIONS}}`

如果要改 HTML 版式，直接替换或编辑 `assets/weekly_template.html`，不要改生成脚本。
