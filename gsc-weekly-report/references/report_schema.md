# GSC 周报字段与模板规范

## 表格结构

一个工作簿包含多个 sheet。每个 sheet 的名称就是一个周报 section 名称。

固定任务字段通常包括：

- `序号`
- `归口部门`
- `任务类型`
- `任务名称`
- `任务内容`
- `任务价值点`
- `任务级别`
- `优先级`
- `计划开始时间`
- `计划完成时间`
- `实际完成时间`
- `计划完成比例`
- `任务状态`
- `责任人`
- `业务接口人`
- `IT接口人`

周报字段按周次重复，例如：

- `W28-主要进展`
- `w28-存在问题`
- `w28-需要帮助`
- `w28-下周计划`

提取脚本会规范化大小写、空格、常见全角符号和不同连字符。以下写法都可以识别：`W28-主要进展`、`w28 主要进展`、`W28－主要进展`。

## 原始提取 JSON

`extract_weekly_data.py` 输出：

```json
{
  "week": "W28",
  "source_file": "/path/to/workbook.xlsx",
  "selected_sections": ["sectionA", "sectionB"],
  "available_sections": ["sectionA", "sectionB", "sectionC"],
  "raw_items": {
    "main_progress": [],
    "issues": [],
    "help_needed": [],
    "next_plan": []
  },
  "section_stats": []
}
```

每条 `raw_items` 记录包含：

- `section`
- `row_number`
- `task_name`
- `content`
- `owner`
- `department`
- `task_type`
- `task_status`
- `priority`
- `business_contact`
- `it_contact`

## Agent 整理 JSON

提取完成后，agent 必须把原始 JSON 整理为：

```json
{
  "week": "W28",
  "title": "W28 周报",
  "scope": "sectionA、sectionB",
  "main_progress": [],
  "issues": [],
  "help_needed": [],
  "next_plan": []
}
```

整理规则：

- 所有选中的 section 必须合并成一份周报。
- 除非用户明确要求，否则不要为每个 sheet 单独生成页面。
- 不要直接粘贴原始单元格内容。
- 生成 PPT 时使用轻量总结，统一口径和表达，让内容适合当前模板阅读。
- 合并多个原始内容时，要保留关键事实：任务名、系统名、里程碑、责任人、阻塞点、依赖方、需要决策或协调的事项、下周动作。
- PPT 生成脚本不自动分页；如果用户明确要求 HTML 长文版或无损整理，再保留全部有效事项。
- 不要编造缺失信息。

## PPT 占位符

默认版式：

- 无封面。
- 四个部分从上到下排列：`主要进展`、`存在问题`、`需要帮助`、`下周计划`。
- 正文字号应保持偏小，适合周管理汇报页面。
- 脚本只替换模板中实际存在的占位符，不自动复制页面、不自动分页、不隐藏或移动栏目。
- 周报标题保持原始标题，例如 `W28 周报`，不要追加页码或总页数。
- 替换正式模板时，要保留需要填充的占位符。

PPT 生成脚本会替换：

- `{{REPORT_WEEK}}`：来自 `week`
- `{{REPORT_TITLE}}`：来自 `title`
- `{{SCOPE}}`：来自 `scope`
- `{{GENERATED_DATE}}`：默认使用当天日期，也可使用 JSON 中的 `generated_date`
- `{{MAIN_PROGRESS}}`：来自 `main_progress`
- `{{ISSUES}}`：来自 `issues`
- `{{HELP_NEEDED}}`：来自 `help_needed`
- `{{NEXT_PLAN}}`：来自 `next_plan`

数组内容会直接替换到对应占位符段落中，并沿用该占位符段落的原格式；如果一个占位符替换为多行内容，新增行复制该占位符所在段落的字号、缩进、项目符号等格式。当前默认模板中，栏目标题保留模板自带项目符号，正文占位符段落为无项目符号缩进。PPT 版不自动拆分到多页；内容较长时，先由 agent 做轻量总结，或建议用户使用 HTML 版。

## HTML 输出

`generate_weekly_html.py` 使用同一份 Agent 无损整理 JSON 和 `assets/weekly_template.html` 生成自包含 HTML 文件。

HTML 规则：

- 使用独立 HTML 模板文件，默认路径是 `assets/weekly_template.html`。
- 不依赖外部 CSS、图片、字体或网络资源。
- 按栏目顺序自然流式排版：`主要进展`、`存在问题`、`需要帮助`、`下周计划`。
- 只显示有内容的栏目；空数组对应的栏目不显示。
- 每个条目保持完整，不拆分、不删减。
- 标题保持原始标题，例如 `W28 周报`，不追加页码或总页数。
- 支持浏览器查看、复制、打印或另存为 PDF。

HTML 模板占位符：

- `{{REPORT_TITLE}}`：报告标题
- `{{REPORT_WEEK}}`：周次
- `{{SCOPE}}`：汇报范围
- `{{GENERATED_DATE}}`：生成日期
- `{{REPORT_SECTIONS}}`：已渲染好的栏目 HTML

改 HTML 版式时，只替换或编辑 `assets/weekly_template.html`。脚本只负责把占位符替换为内容。
