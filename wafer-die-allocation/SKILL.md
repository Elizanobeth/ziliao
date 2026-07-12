---
name: wafer-die-allocation
description: 从 CSV、TSV、JSON、XLS、XLSX 或 URL 表格中预处理半导体晶圆 Die 数据，并将 Wafer 分配为 Custom Lot。适用于需要下载并清洗三张 URL 表格、筛选 Bin Grade、匹配 PACKAGE 与供应商配比、优先使用 Wafer Sale=N 和较早生产日期、遵守整片 Wafer 与完整 Lot 规则、控制 Custom Lot 的 Unit 数和损耗、支持 Fab LotID 复用但禁止重复使用 T7 Code，以及解释无解原因和生成可审计分配结果的场景。
---

# 晶圆 Die 分配

使用本 Skill 完成晶圆到 Unit 的规划。由 Agent 负责收集参数、检查假设、解释结果和整理报告；由随附的 Python 脚本负责 URL 表格预处理、字段校验、配比匹配和分配计算。默认使用不依赖第三方库的纯 Python 确定性启发式算法；如果平台已经安装 OR-Tools，可以额外使用 CP-SAT 精确求解。

## URL 表格预处理

Agent 平台可能以 URL 而不是附件的形式提供三张表。构造 JSON 请求，将三张表放入 `table_urls.table1`、`table_urls.table2` 和 `table_urls.table3`。每个值可以是 URL 字符串，也可以是包含 `url`、可选 Excel `sheet` 和可选请求 `headers` 的对象。测试时也可以使用 `file://` URL 或本地路径。

先执行表格预处理：

```text
python3 scripts/preprocess_tables.py --input url_payload.json --output normalized_payload.json
```

再执行分配计算：

```text
python3 scripts/allocate_die.py --input normalized_payload.json --output result.json
```

要求预处理脚本完成以下工作：

- 下载 HTTP/HTTPS 表格，并执行超时和文件大小限制；
- 识别 CSV、TSV、JSON、XLSX 和 XLS；
- 读取指定的 Excel Sheet；未指定时读取第一个 Sheet；
- 按 UTF-8、GB18030、Big5、Latin-1 顺序尝试解码文本；
- 统一中英文列名别名；
- 删除空行和完全重复行；
- 记录文件格式、脱敏后的来源路径、字节数、SHA-256、警告和错误；
- 不输出 URL 查询参数，避免泄露签名或凭证；
- 不得静默修复缺失字段或有歧义的数据。

如果 XLSX/XLS 缺少 Python 依赖，使用 Agent 平台提供的表格运行时将工作簿转换为相同的标准 JSON，或者安装对应依赖。不要让语言模型手工复制大规模表格数据。

## 必需输入

在求解前收集以下参数：

- `target_units`：目标 Unit 数量；
- `tolerance`：总 Unit 允许浮动比例，默认 `0.20`；
- `bin_grades`：选定的 Bin Grade，只允许 `1`–`9` 和 `X`；
- `max_waste_per_custom_lot`：每个 Custom Lot 的最大 Die 损耗；
- `allow_lot_reuse`：是否允许 Fab LotID 复用，默认 `false`；
- `max_units_per_custom_lot`（`a`）：每个 Custom Lot 的最大 Unit 数，默认 `20000`。用户未提供时使用该默认值；即使需要放宽 Lot 数上限，也必须保持该约束；
- `max_lots_per_custom_lot`（`b`）：每个 Custom Lot 的最大 Fab LotID 数。先作为硬约束求解；无解时只能放宽该约束，并报告放宽结果；
- `package` 和 `supplier`：除非输入中只有唯一且无歧义的组合；
第一张表不需要提供工艺或厚度字段。每片 Wafer 只有一个可用的 Die 数量，后续由求解器决定该整片 Wafer 在某个 Custom Lot 中承担 A 厚度还是 B 厚度。不能事先把 Wafer 固定为 A 或 B。

## 分配规则

严格执行以下规则：

1. 按供应商筛选，并将 `PACKAGE` 与第三张表进行模糊匹配。供应商在去除空格并统一大小写后精准匹配。必须得到唯一配比；匹配歧义时停止求解并要求确认。
2. 通过 `Fab LotID` 关联 `Wafer Sale`。同一个 Lot 有多行时，只要任意一行是 `N`，就按 `N` 处理；否则按 `Y` 处理。报告缺失的 Lot。
3. 按 `T7 Code` 聚合第一张表。`Lot Wafer QTY` 是 Lot 级字段，可能在明细行中重复，禁止逐行累加。检查同一个 T7 Code 是否存在冲突元数据。
4. 对每片选中的 Wafer，累加 `Bin Grade` 属于 `bin_grades` 的所有 `Bin Quanity`。分配粒度是整片 Wafer；一片 Wafer 上选定 Bin Grade 的 Die 必须整体进入同一个 Custom Lot。A/B 厚度角色由求解器在分配过程中决定。
5. 一个 Lot 参与分配后，必须完成该 Lot 中所有选定 Bin Grade 的相关 Wafer 分配。不允许 Lot 复用时，该 Lot 的相关 Wafer必须全部进入同一个 Custom Lot。允许 Lot 复用时，同一个 Lot 可以分散到多个 Custom Lot，但每个相关 T7 Code 最多分配一次，不能重复计算同一片 Wafer。
6. 对于 `rA:rB` 配比，计算每个 Custom Lot 的 Unit 数：`min(floor(A/rA), floor(B/rB))`。剩余 Die 数为 `A + B - Unit*(rA+rB)`，且不得超过损耗上限。
7. 由于 A/B 角色由求解器决定，预检查先检查选定 Bin Grade 的总 Die 供应量是否至少达到 `最低Unit数 × (rA+rB)`；正式求解时再检查 A/B 两侧的实际分配量。
8. 将 `Wafer Sale=N` 和较早的 `Create Date` 作为优先级，而不是硬约束；除非用户明确要求必须满足。
9. 使用人工配比 Tip 作为候选组合的启发式优先级：当候选组合的去重 Wafer 数量，或可靠的 Lot 级 `Lot Wafer QTY` 总数，可以被 `rA+rB` 整除时，优先尝试该组合。该 Tip 不是硬约束，不能因此拒绝一个实际 Unit、损耗或 Lot 规则更好的方案。

## 求解流程

1. 使用 `scripts/preprocess_tables.py` 处理三张 URL 或文件表格。
2. 预处理存在错误时停止；检查必需字段、Bin Grade、重复 T7 Code、缺失 Lot 关联和配比匹配。
3. 预检查选定 Bin Grade 的总 Die 供应量，并解释立即可见的短缺。
4. 按 `Wafer Sale=N` 优先、`Create Date` 较早优先和 Wafer 数量倍数 Tip 的顺序生成候选 Lot，同时检查 A/B 角色平衡和损耗限制。
5. 使用 `scripts/allocate_die.py` 构造 Custom Lot。默认使用纯 Python 启发式算法；只有平台已有 OR-Tools 且用户需要精确求解时，才指定 `--solver cp-sat`。
6. 按以下顺序优化：总 Unit 数与目标的绝对偏差、总损耗、`N` Lot 优先级、生产日期、Custom Lot 数量、Lot 数超限程度。
7. 同时使用 `a` 和 `b` 作为硬约束无解时，保持 `a`、损耗、整片 Wafer、T7 Code 唯一、配比和总 Unit 浮动约束不变，只放宽 `b`，并报告放宽前后的限制。
8. 独立复核每个 Custom Lot。不能只依据求解器状态判断结果正确。

## 校验公式

对 Custom Lot `c` 计算：

```text
A_c = Custom Lot中被求解器分配为A角色的Wafer的选定Bin Grade Die数之和
B_c = Custom Lot中被求解器分配为B角色的Wafer的选定Bin Grade Die数之和
u_c = min(floor(A_c / rA), floor(B_c / rB))
waste_c = A_c + B_c - u_c * (rA + rB)
```

必须满足：

```text
u_c <= a
waste_c <= max_waste_per_custom_lot
Custom Lot中的不同Fab LotID数量 <= b  # 除非已经明确放宽b
target*(1-tolerance) <= sum(u_c) <= target*(1+tolerance)
```

## 输出要求

返回或保存以下内容：

- 汇总信息：目标 Unit、允许区间、实际 Unit、偏差、Custom Lot 数量、总损耗、求解后端和被放宽的约束；
- 每个 Custom Lot 的明细：Unit 数、A/B Die 数、A/B 理论需求、损耗、不同 Lot 数、Fab LotID、T7 Code、每片 Wafer 被分配的 A/B 角色、Wafer Sale、最早和最晚生产日期；
- 未使用库存及未使用原因；
- 警告和无解原因；
- 可审计记录：匹配到的配比、选定 Bin Grade、复用模式、输入来源摘要和使用的计算公式。
- 候选组合 Tip 记录：`rA+rB`、实际去重 Wafer 数量、余数，以及是否命中倍数优先级。

使用 `scripts/preprocess_tables.py` 负责 URL/文件读取，使用 `scripts/allocate_die.py` 负责分配。默认不需要安装 OR-Tools，也不需要联网；脚本会使用纯 Python 启发式算法，并将结果标记为 `heuristic_feasible`。该方案会严格校验所有硬约束，但不能证明全局最优。只有平台已经安装 OR-Tools 时，才允许使用 `--solver cp-sat` 获取精确求解结果。

构造或校验标准 JSON 时，读取 `references/input_schema.md`。
