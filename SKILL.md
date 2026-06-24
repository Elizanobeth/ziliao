---
name: semiconductor-die-allocation-cn
description: 用于半导体晶圆 Die 分配和母批规划的稳定求解 Skill，内置工程化 Python 脚本。适用于包含“原始数据”和“配die 规则表”的 Excel 工作簿，需要按 PACKAGE/供应商匹配层数配比，按用户选择的 Bin Grade 过滤 Die，处理母批 Unit 上限、Lot 数上限、Lot 是否复用、所选等级 Lot 必须配完、wafer/grade 可复用、单母批损耗约束，并输出可审计的 JSON/Excel 配 Die 方案。
---

# 半导体晶圆 Die 分配

## 工程化入口

优先使用总入口脚本端到端运行；只有在排查某一步时才单独运行 01-05 脚本。

```bash
python scripts/run_allocation.py \
  --workbook input.xlsx \
  --package "目标PACKAGE" \
  --supplier "目标供应商" \
  --target-units 40k \
  --grades 1,2,3 \
  --loss-cap 30 \
  --unit-cap 4.5k \
  --lot-cap 5 \
  --reuse-rule allow_reuse \
  --out-dir outputs/run-001
```

固定输出：

- `01_validated.json`：工作簿校验和标准化结果
- `02_matched_rule.json`：规则表匹配结果和层数配比
- `03_supply.json`：过滤后的最小供应单元和总量校验
- `04_solution.json`：求解结果、母批分配和校验结果
- `05_allocation_report.xlsx`：业务可读 Excel 报告
- `05_allocation_report.json`：扁平化审计报告

单步脚本：

- `scripts/01_validate_workbook.py`：读取并校验 Excel
- `scripts/02_match_rule.py`：匹配 `PACKAGE + 供应商` 对应的 `层数配比`
- `scripts/03_build_supply.py`：按 Bin Grade 构建 `Fab LotID + T7 Code + Bin Grade` 最小供应单元
- `scripts/04_solve_allocation.py`：按目标阶段和兜底阶段求解
- `scripts/05_export_report.py`：导出可审计报告

求解后端：

- 默认 `--backend auto`：当最小供应单元数或 Lot 数超过阈值时自动使用 `large_batch`；否则优先使用 OR-Tools CP-SAT，缺少 OR-Tools 时使用候选搜索兜底
- `--backend large_batch`：大表模式，先生成高质量候选母批/计划，再选择非冲突计划；适合数据很多时稳定产出可行解
- `--backend cpsat`：强制使用 OR-Tools CP-SAT，适合小到中等规模、需要精确优化的场景
- `--backend heuristic`：强制使用候选搜索，适合小样例、调试和依赖不完整环境

大表参数：

- `--large-item-threshold 800`：`auto` 模式下，最小供应单元数超过该值时走 `large_batch`
- `--candidate-limit 20000`：大数据候选母批/计划上限
- `--max-combo-lots 5`：大数据模式下单个候选母批最多组合的连续 Lot 数；严格 Lot 数阶段仍受 `--lot-cap` 限制
- `--max-side-items 18`：工艺 A/B 侧精确枚举分配的最大单元数，超过后使用确定性贪心侧分配

## 固定执行流程

严格按以下步骤执行晶圆 Die 分配。除非用户明确只要求说明、评审或改文档，否则不要跳过步骤。

1. 收集必要输入：
   - Excel 工作簿，必须包含 `原始数据` 和 `配die 规则表`
   - 目标 `PACKAGE` 和 `供应商`；如果用户允许，也可以对所有无歧义组合分别求解
   - 目标 Unit 数 `T`
   - 用户选择的 Bin Grade，合法值为 `1` 到 `9` 以及 `X`
   - 单个母批最大 Die 损耗 `L`
   - 单个母批最大 Unit 数 `A`
   - 单个母批最大不同 `Fab LotID` 数 `B`
   - Lot 复用规则：`允许复用` 或 `不允许复用`

2. 只在缺少必要输入时询问用户。不要自行推断目标 Unit 数、Bin Grade、损耗上限、母批 Unit 上限、Lot 数上限或复用规则。

3. 读取并校验工作簿：
   - `原始数据` 必须包含列：`PACKAGE`、`供应商`、`Fab LotID`、`Bin Grade`、`Bin Quanity`、`T7 Code`
   - `配die 规则表` 必须包含列：`PACKAGE`、`供应商`、`层数配比`
   - `Bin Quanity` 必须是非负整数；为空、负数或非数字的行必须报错
   - `Bin Grade` 先去除首尾空格并转大写；合法等级只能是 `1` 到 `9` 或 `X`

4. 匹配配 Die 规则：
   - `供应商` 使用去除首尾空格后的精确匹配
   - `PACKAGE` 按固定顺序匹配：
     1. 归一化精确匹配：转大写，并移除空格、连字符、下划线、斜杠、括号和标点
     2. 归一化包含匹配：仅当唯一候选存在互相包含关系时接受
     3. 模糊匹配：使用 `WRatio` 或 `token_set_ratio`；仅接受最高分 `>= 85` 且比第二名至少高 `5` 分的结果
   - 如果没有候选，或存在并列/歧义，停止并让用户选择规则行
   - 将 `层数配比` 解析成两个正整数 `rA:rB`；不接受 0、负数、小数或超过两段的比例

5. 构建最小供应单元：
   - 将 `原始数据` 过滤到匹配的 `PACKAGE` 范围、匹配的 `供应商`、用户选择的 Bin Grade
   - 按 `Fab LotID + T7 Code + Bin Grade` 聚合重复行，并对 `Bin Quanity` 求和
   - 每条聚合后的记录是一个最小供应单元，字段为 `lot`、`wafer`、`grade`、`qty`
   - 默认以该最小供应单元整体分配：一个单元要么分配给一个母批的一种工艺侧，要么在其 Lot 未被选中时不分配。除非用户明确允许按 Die 颗数拆分，否则不要把同一个 `wafer + Bin Grade` 拆成多份

6. 执行“所选等级 Lot 必须配完”规则：
   - 一个 Lot 要么完全不用，要么该 Lot 下属于用户所选 Bin Grade 的所有最小供应单元都必须被分配
   - 用户未选择的 Bin Grade 不参与本次计算，也不会触发 Lot 必须配完

7. 做前置校验：
   - 计算所选等级总 Die 数 `Q`
   - 计算目标需求总 Die 数 `T * (rA + rB)`
   - 如果 `Q < T * (rA + rB)`，说明仅从总量看目标也不可能满足，直接进入兜底最大化求解
   - 否则进入目标满足求解

8. 根据 `references/allocation-model.md` 建立优化模型。实现或解释求解逻辑前，必须先读取该文件；工程实现位于 `scripts/semidie/solver.py`。

9. 按固定顺序求解：
   - 目标阶段，严格 Lot 数上限：要求总 Unit 数 `>= T`、单母批损耗 `<= L`、单母批 Unit 数 `<= A`、单母批不同 Lot 数 `<= B`
   - 目标阶段，放宽 Lot 数上限：仅当严格 Lot 数上限无解时使用；总 Unit、损耗和 Unit 上限仍是硬约束，允许 Lot 数超限并最小化超限量
   - 兜底阶段，严格 Lot 数上限：仅当目标阶段无法达到 `T` 时使用；在单母批损耗 `<= L` 前提下最大化总 Unit 数
   - 兜底阶段，放宽 Lot 数上限：仅当严格兜底也无解时使用
   - 大表时使用同样阶段顺序，但候选母批由 `large_batch` 生成；结果是可行启发式方案，不声明全局最优

10. 使用确定性求解设置：
    - 优先使用 OR-Tools CP-SAT 处理整数模型
    - 随机种子固定为 `0`
    - 搜索线程数固定为 `1`
    - 默认时间上限为 `300` 秒，除非用户指定其他时间
    - 输出求解状态：`OPTIMAL`、`FEASIBLE`、`INFEASIBLE` 或求解器返回的等价状态
    - 大表 `large_batch` 后端必须输出 warning，说明其为两阶段启发式可行解

11. 输出必须可审计：
    - 输入摘要：产品、供应商、层数配比、目标 Unit、Bin Grade、损耗上限、Unit 上限、Lot 数上限、复用规则
    - 规则匹配详情：匹配到的规则行和匹配方式
    - 可行性摘要：所选 Die 总量、目标所需 Die 总量、是否达到目标、是否使用兜底方案
    - 每个母批：Unit 数、A 侧 Die 数、B 侧 Die 数、损耗、不同 Lot 数、Lot 超限量、Lot 列表、wafer 列表、grade 列表、分配的最小供应单元
    - 跨母批校验：所有被使用的所选等级 Lot 已配完；没有不允许的重复分配；没有硬损耗约束违规
    - 最终汇总：总 Unit 数、超目标 Unit 数、总损耗、启用母批数、未使用 Lot

## 复用规则

必须按以下口径处理：

- `不允许复用`：一个被使用的 Lot 只能出现在一个母批中。由于该 Lot 内所选等级的单元必须全部分配到该母批，所以同一片 wafer 也只能出现在这个母批中。
- `允许复用`：一个被使用的 Lot 可以跨多个母批；同一片 wafer 也可以因为不同 `wafer + Bin Grade` 单元被分配到不同母批。但同一个最小供应单元绝不能重复分配。
- 两种模式下，只要 Lot 被使用，都必须配完该 Lot 在用户选择 Bin Grade 范围内的 Die；用户未选择的等级不参与配完约束。

## 目标优先级

不要一开始就用任意权重把所有业务目标混在一起。必须先固定字典序优化顺序：

1. 优先满足目标 Unit 数
2. 如果目标已满足，最小化超目标 Unit 数
3. 最小化总 Die 损耗
4. 仅在放宽 Lot 数阶段，最小化 Lot 数超限量
5. 最小化启用母批数
6. 只有当用户明确要求偏好高等级 Die 时，才按 `1 < 2 < ... < 9 < X` 最小化低等级使用；默认不要加入等级偏好

## 失败说明

如果找不到有效方案，按以下顺序说明阻塞原因：

1. 所选 Die 总量低于目标需求
2. 找不到匹配的层数配比
3. 所选等级 Lot 必须配完导致单母批损耗必然超过 `L`
4. 单母批 Unit 上限 `A` 太小，无法容纳任何可行 Lot 组合
5. 不允许复用规则阻断了原本在可复用场景下可行的分配
6. 求解器超时且没有找到可行解

如果目标无法满足但兜底阶段找到方案，不要称它为原目标的最优解。必须标注为：`在损耗约束下的最佳可行兜底方案`。

## 参考文件

- 在建立、修改或解释数学模型前，读取 `references/allocation-model.md`。
