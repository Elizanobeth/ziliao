---
name: die-lot-planner
description: 用于根据 Excel 或 CSV 中的 Die Bin 数据规划母批。适用于用户需要按 PACKAGE、供应商、Fab LotID、Bin Grade、Bin Quanity、T7 Code，以及“配 Die 规则表”中的层数配比生成母批 Lot 清单；支持每个母批浪费上限、Fab LotID 是否允许复用、以及不满足时输出优化建议。
---

# Die Lot Planner

## 概述

使用本 skill 从原始 Bin 数据和 `配 Die 规则表` 中生成母批分配结果。执行时优先使用内置脚本 `scripts/plan_die_lots.py`，不要临时重写算法。

## 核心规则

- 规则 sheet 默认名为 `配 Die 规则表`。
- `配 Die 规则表` 只有三列：`PACKAGE`、`供应商`、`层数配比`。
- `层数配比` 是两个数字的比例，例如 `2:6`。
- 如果比例为 `2:6`，表示每完成 `1 Unit`，需要配比左侧 `2` 颗 Die，配比右侧 `6` 颗 Die。
- 原始数据不需要、也不应该要求 A/B 工艺字段。A/B 只是两类不同工艺的示例概念。
- 算法会把每一行 `T7 Code + Bin Grade + Bin Quanity` 作为一个整体，分配到配比左侧或配比右侧。
- 一个原始数据行不能被拆分到多个母批，也不能被拆分到配比两侧。
- 每个母批必须满足 `Unit <= target_units`。
- 对每个 `PACKAGE + 供应商` 分组，满足目标 Unit 的必要条件是：`target_units * (配比左侧比例 + 配比右侧比例) <= sum(Bin Quanity)`。如果不满足，目标 Unit 在总量上不可能达成。
- 每个母批最多浪费 `max_waste` 颗 Die，默认 `30`。
- 优先级固定为：先尽量满足 Unit 数，再最小化浪费。
- 如果 Fab LotID 不允许复用，同一个 `Fab LotID` 只能出现在一个母批里。
- 如果 Fab LotID 允许复用，同一个 `Fab LotID` 可以出现在不同母批里，但同一行数据仍然只能被使用一次。

## 原始数据字段

原始数据必须包含以下六列：

| 字段 | 含义 |
|---|---|
| `PACKAGE` | 产品类型 |
| `供应商` | 供应商名称 |
| `Fab LotID` | 批次 ID |
| `Bin Grade` | Die 好坏等级，`1` 是最好的等级 |
| `Bin Quanity` | Bin 的数量，作为可用 Die 数量使用 |
| `T7 Code` | Wafer 的唯一识别码 |

注意：字段名以用户提供的原始表为准，其中 `Bin Quanity` 保持这个拼写。

## 快速使用

```bash
python scripts/plan_die_lots.py \
  --input input.xlsx \
  --output planned_mother_lots.xlsx \
  --target-units 1000
```

常用参数：

- `--data-sheet SHEET`：原始数据 sheet 名称。不传时，默认使用第一个不是 `配 Die 规则表` 的 sheet。
- `--rules-sheet SHEET`：规则 sheet 名称，默认 `配 Die 规则表`。
- `--target-units N`：每个母批的目标 Unit 上限。母批结果必须满足 `Unit <= N`。
- `--max-waste N`：每个母批最大浪费 Die 数，默认 `30`。
- `--allow-lot-reuse`：允许同一个 `Fab LotID` 出现在不同母批。
- `--max-bin-grade N`：只使用 `Bin Grade <= N` 的数据。例如只允许 Grade 1 时传 `--max-bin-grade 1`。
- `--beam-width N`：搜索宽度。数据复杂、组合难找时可以调大。

字段名不标准时可用这些参数指定：

- `--package-col COL`
- `--supplier-col COL`
- `--lot-col COL`
- `--bin-grade-col COL`
- `--bin-quantity-col COL`
- `--t7-col COL`
- `--ratio-col COL`

## 算法逻辑

对每个 `PACKAGE + 供应商` 组合分别处理：

1. 从 `配 Die 规则表` 查找该 `PACKAGE + 供应商` 的 `层数配比`。
2. 如果找不到规则，该组合不参与配批，并在 `optimization_suggestions` 中输出补规则建议。
3. 将该组合下的原始数据行作为候选库存。
4. 先做总量检查：

```text
AvailableDie = sum(Bin Quanity)
TargetRequiredDie = target_units * (配比左侧比例 + 配比右侧比例)
```

如果 `AvailableDie < TargetRequiredDie`，说明该分组在总数量上不足以满足目标 Unit，必须在 `optimization_suggestions` 中建议降低 `target_units` 或补充库存。

5. 每一行按照 `T7 Code + Bin Grade + Bin Quanity` 整体参与组合。
6. 算法尝试把每一行分配到配比左侧或配比右侧。
7. 对一个候选母批，计算：

```text
Unit = min(
  floor(配比左侧 Bin Quanity 总数 / 配比左侧比例),
  floor(配比右侧 Bin Quanity 总数 / 配比右侧比例)
)

RequiredDie = Unit * (配比左侧比例 + 配比右侧比例)
SelectedDie = 选中行的 Bin Quanity 总和
WasteDie = SelectedDie - RequiredDie
```

8. 候选母批必须满足：

```text
Unit > 0
Unit <= target_units
WasteDie <= max_waste
```

9. 在所有候选中，先选 `Unit` 最大的组合。
10. 如果 `Unit` 相同，选 `WasteDie` 最小的组合。
11. 生成一个母批后，从可用库存中移除已使用的数据行。
12. 如果不允许 Lot 复用，同时移除同 `Fab LotID` 的其他剩余行。
13. 重复以上步骤，直到无法继续形成有效母批。

## 输出结果

脚本会输出一个 Excel、JSON 或 CSV 结果，推荐输出 Excel。

Excel 中包含以下 sheet：

- `mother_lots`：母批汇总结果。
- `lot_assignments`：每个母批包含的 Lot、T7 Code、Bin Grade、Bin Quanity 明细。
- `unused_inventory`：未被使用的数据行及原因。
- `diagnostics`：运行参数和诊断信息。
- `optimization_suggestions`：不满足时的优化建议。

## mother_lots 字段

`mother_lots` 至少包含：

- `MotherLotID`
- `PACKAGE`
- `供应商`
- `层数配比`
- `Unit`
- `RequiredDie`
- `SelectedDie`
- `WasteDie`
- `Bucket1Die`
- `Bucket2Die`
- `LotCount`
- `FabLotIDList`

## lot_assignments 字段

`lot_assignments` 至少包含：

- `MotherLotID`
- `PACKAGE`
- `供应商`
- `Fab LotID`
- `T7 Code`
- `Bin Grade`
- `AssignedBucket`
- `Bin Quanity`
- `SourceRow`

其中：

- `AssignedBucket = 1` 表示被分配到 `层数配比` 左侧。
- `AssignedBucket = 2` 表示被分配到 `层数配比` 右侧。

## 不满足时的优化建议

如果无法满足目标，必须输出具体建议，不要只说“无法满足”。

常见建议包括：

- 如果 `PACKAGE + 供应商` 找不到规则：建议补充 `配 Die 规则表`。
- 如果 `sum(Bin Quanity) < target_units * (配比左侧比例 + 配比右侧比例)`：建议降低 `target_units` 或补充该分组库存。
- 如果 Unit 达不到目标：建议降低 `target_units`，或补充该 `PACKAGE + 供应商` 的库存。
- 如果浪费超过上限：建议在业务允许时提高 `max_waste`。
- 如果 Lot 不允许复用导致大量数据被排除：建议尝试 `--allow-lot-reuse`。
- 如果只使用高等级 Bin 导致库存不足：建议放宽 `--max-bin-grade`，例如从只用 Grade 1 改成允许 Grade 1-2。

## Mock 示例

规则表：

| PACKAGE | 供应商 | 层数配比 |
|---|---|---|
| PKG-A | VENDOR-1 | 2:6 |

原始数据：

| PACKAGE | 供应商 | Fab LotID | Bin Grade | Bin Quanity | T7 Code |
|---|---|---|---:|---:|---|
| PKG-A | VENDOR-1 | LOT-001 | 1 | 20 | T7-001 |
| PKG-A | VENDOR-1 | LOT-002 | 1 | 60 | T7-002 |
| PKG-A | VENDOR-1 | LOT-003 | 2 | 21 | T7-003 |
| PKG-A | VENDOR-1 | LOT-004 | 2 | 59 | T7-004 |
| PKG-B | VENDOR-2 | LOT-005 | 1 | 80 | T7-005 |

目标：

```text
target_units = 10
max_waste = 30
Fab LotID 不允许复用
```

第一个母批：

| Fab LotID | T7 Code | Bin Grade | Bin Quanity | 分配 |
|---|---|---:|---:|---|
| LOT-001 | T7-001 | 1 | 20 | 配比左侧 |
| LOT-002 | T7-002 | 1 | 60 | 配比右侧 |

计算：

```text
Unit = min(floor(20 / 2), floor(60 / 6)) = 10
RequiredDie = 10 * (2 + 6) = 80
SelectedDie = 20 + 60 = 80
WasteDie = 0
```

第二个母批：

| Fab LotID | T7 Code | Bin Grade | Bin Quanity | 分配 |
|---|---|---:|---:|---|
| LOT-003 | T7-003 | 2 | 21 | 配比左侧 |
| LOT-004 | T7-004 | 2 | 59 | 配比右侧 |

计算：

```text
Unit = min(floor(21 / 2), floor(59 / 6)) = 9
RequiredDie = 9 * (2 + 6) = 72
SelectedDie = 21 + 59 = 80
WasteDie = 8
```

`PKG-B + VENDOR-2` 因为规则表中没有对应行，进入 `unused_inventory`，并在 `optimization_suggestions` 中建议补充该组合的 `层数配比`。

## 维护说明

如果业务规则变化，同步修改：

- 本文件中的规则说明。
- `references/input_contract.md`。
- `scripts/plan_die_lots.py` 中的字段、参数和默认规则。

不要让脚本重新引入 A/B 工艺字段。A/B 只是示例概念，不是原始数据字段。
