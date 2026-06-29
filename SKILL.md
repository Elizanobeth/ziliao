---
name: pptx-fill-placeholders-zh
description: 从真实 PowerPoint .pptx 中提取可复用模板结构、文本清单和可填充 token，并复制原 PPTX 作为视觉底板替换文本内容，同时尽量保留版式、母版、主题、图片、图表和原有格式。适用于用户提供成品 PPT、参考 PPT 或模板 PPT，并希望抽取其页面结构、定位可替换文本、生成模板副本、填充新内容，而不是重新绘制整套 PPT 的场景。
---

# PPTX 模板提取与占位符填充

## 概览

使用这个 skill 时，优先从用户给的真实 `.pptx` 中抽取模板结构，而不是假设文件已经有 `{{...}}` 占位符。标准路径是：先提取页面和文本框清单，再生成 token 化模板副本，最后复制这个模板副本并替换 token。

## 工作流

1. 找到源 `.pptx` 和工作目录。
2. 先抽取真实 PPTX 的模板结构：

```bash
python scripts/extract_pptx_template.py \
  /path/source.pptx \
  --out-dir /path/template_extract \
  --make-template-pptx \
  --pretty
```

3. 查看输出文件：

- `template_manifest.json`：每页文本、形状名、位置、建议角色、token。
- `extracted_template.pptx`：把可替换文本改成 `{{S01_T01}}` 这类 token 的模板副本。
- `token_original_values.json`：token 到原文本的映射，用于理解每个 token 原来是什么。
- `fill_values.blank.json`：空白填充表，复制后填入新内容。
- `literal_unique_replacements.blank.json`：只包含唯一文本的精确替换表，适合不想生成 token 模板时使用。

4. 根据 `template_manifest.json` 和 `token_original_values.json` 填写新的 `values.json`：

```json
{
  "{{S01_T01}}": "新的封面标题",
  "{{S01_T02}}": "新的副标题"
}
```

5. 复制 token 化模板并填充新内容：

```bash
python scripts/fill_pptx_placeholders.py \
  /path/template_extract/extracted_template.pptx \
  /path/output.pptx \
  --map /path/values.json \
  --fail-on-unreplaced
```

6. 交付前打开或渲染输出 PPT，检查文字是否替换完整、是否有溢出、换行或样式异常。

## 真实 PPTX 提取规则

- 默认把真实 PPTX 当作视觉来源，保留原文件，不直接改源文件。
- 对没有 `{{...}}` 占位符的成品 PPT，优先运行 `extract_pptx_template.py --make-template-pptx`，生成可复用模板副本。
- `extract_pptx_template.py` 默认跳过 1 个字符的短文本和纯数字/页码类文本；如需要保留数字指标，使用 `--include-numeric` 或调低 `--min-chars`。
- 如果页脚、引用编号、固定标签不该被替换，使用 `--skip-regex` 排除，例如 `--skip-regex '^参考'`。
- 如果多个地方出现完全相同的文本，不要优先用精确文本替换，因为会一起替换；改用 token 化模板，按 `{{S页码_T序号}}` 分别填。
- 如果 PowerPoint 把一个段落拆成多个文本 run，提取脚本会把这个段落生成一个 token；填充后通常继承该段第一个 run 的样式。

## 填充规则

- 只替换文本 token 或明确指定的文本，不改背景、图片、母版、主题色、图表、动画或版式装饰。
- 用 token 模板时，JSON key 保持 `{{S01_T01}}` 形式，不需要 `--literal-keys`。
- 直接替换真实文本时，使用 `--literal-keys`，并优先使用 `literal_unique_replacements.blank.json` 里唯一出现的文本。
- 只有确认占位符位于备注、版式、母版或图表中时，才使用 `--include-notes`、`--include-layouts`、`--include-masters` 或 `--include-charts`。替换版式和母版文字会影响继承它们的页面。

## 辅助脚本

使用 `scripts/extract_pptx_template.py` 抽取真实 PPTX 模板：

```bash
# 抽取结构并生成 token 化模板
python scripts/extract_pptx_template.py source.pptx \
  --out-dir template_extract \
  --make-template-pptx \
  --pretty

# 抽取时保留数字类文本作为可替换项
python scripts/extract_pptx_template.py source.pptx \
  --out-dir template_extract \
  --make-template-pptx \
  --include-numeric
```

使用 `scripts/fill_pptx_placeholders.py` 填充模板：

```bash
# 填充 token 化模板
python scripts/fill_pptx_placeholders.py extracted_template.pptx output.pptx \
  --map values.json \
  --fail-on-unreplaced

# 不生成 token 模板时，直接精确替换唯一文本
python scripts/fill_pptx_placeholders.py source.pptx output.pptx \
  --literal-keys \
  --map literal_values.json
```

当用户提供的是表格、JSON 或多页结构化内容时，先把这些内容映射到 `template_manifest.json` 中的 token，再运行填充脚本。如果需求涉及新增/删除页面、重排版式、插入复杂图表或新增视觉元素，请改用更完整的 PowerPoint 编辑流程，而不是这个以模板提取和文本替换为主的 skill。
