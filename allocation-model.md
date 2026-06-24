# 分配优化模型

本文件固定半导体晶圆 Die 分配的优化模型。

## 集合

- `I`：过滤和聚合后的最小供应单元。每个单元 `i` 有 `lot(i)`、`wafer(i)`、`grade(i)` 和整数数量 `q_i`
- `L`：由 `I` 中所有单元对应的 Lot 组成
- `K`：候选母批编号集合
- `S`：工艺侧集合 `{A, B}`

层数配比 `r_A` 和 `r_B` 来自解析后的 `rA:rB`。

## 候选母批数量

候选数量必须确定性设置：

- 目标阶段：`K_min = ceil(T / A)`
- `不允许复用`：`K_hard = Lot 数`
- `允许复用`：`K_hard = 最小供应单元数`
- `K_cap = min(K_hard, max(K_min + 10, 30))`

目标阶段尝试 `K = K_min, K_min + 1, ..., K_cap`，并按照 `SKILL.md` 中的目标优先级保留最佳结果。

兜底阶段尝试 `K = 1, 2, ..., K_cap`，并按照目标优先级保留总 Unit 数最高的结果。

如果没有找到方案，而用户希望更深入搜索，可以在用户同意后提高 `K_cap` 或延长求解时间。

## 变量

- `x[i,k,s] in {0,1}`：最小供应单元 `i` 是否分配给母批 `k` 的工艺侧 `s`
- `z[l] in {0,1}`：Lot `l` 是否被选中使用
- `batch_active[k] in {0,1}`：母批 `k` 是否启用
- `u[k] integer >= 0`：母批 `k` 生产的 Unit 数
- `lot_in_batch[l,k] in {0,1}`：Lot `l` 是否出现在母批 `k`
- `loss[k] integer >= 0`：母批 `k` 的 Die 损耗
- `lot_overflow[k] integer >= 0`：母批 `k` 不同 Lot 数超过用户上限 `B` 的数量；仅在放宽 Lot 数阶段使用

## 核心约束

最小供应单元分配：

```text
对每个单元 i：
  sum_k sum_s x[i,k,s] = z[lot(i)]
```

这条约束和 Lot 级一致性约束共同保证：如果一个 Lot 被选中，该 Lot 下所有属于用户所选 Bin Grade 的最小供应单元都必须被分配一次；如果 Lot 没被选中，则该 Lot 没有任何单元被分配。

母批启用：

```text
u[k] <= A * batch_active[k]
sum_i sum_s x[i,k,s] <= M * batch_active[k]
```

其中 `M = 最小供应单元数`。

工艺侧 Die 数：

```text
A_qty[k] = sum_i q_i * x[i,k,A]
B_qty[k] = sum_i q_i * x[i,k,B]

A_qty[k] >= r_A * u[k]
B_qty[k] >= r_B * u[k]
```

损耗：

```text
loss[k] = A_qty[k] + B_qty[k] - (r_A + r_B) * u[k]
0 <= loss[k] <= L
```

启用母批必须至少生产一个 Unit：

```text
u[k] >= batch_active[k]
```

Lot 是否出现在母批：

```text
对每个单元 i、母批 k：
  lot_in_batch[lot(i), k] >= x[i,k,A]
  lot_in_batch[lot(i), k] >= x[i,k,B]

对每个 Lot l、母批 k：
  lot_in_batch[l,k] <= sum_{i: lot(i)=l} sum_s x[i,k,s]
```

严格 Lot 数上限：

```text
sum_l lot_in_batch[l,k] <= B
```

放宽 Lot 数上限：

```text
sum_l lot_in_batch[l,k] <= B + lot_overflow[k]
```

不允许复用：

```text
对每个 Lot l：
  sum_k lot_in_batch[l,k] <= 1
```

允许复用：

```text
不增加跨母批 Lot 约束。
最小供应单元分配等式仍然保证同一个 wafer+grade 单元不会被重复使用。
```

目标阶段：

```text
sum_k u[k] >= T
```

兜底阶段：

```text
不设置目标下界；最大化 sum_k u[k]。
```

## 可选 wafer 约束

仅当用户明确说明同一片 wafer 在同一个母批内不能拆到不同工艺侧时，才增加以下约束：

```text
wafer_side[w,k,s] in {0,1}
x[i,k,s] <= wafer_side[wafer(i),k,s]
sum_s wafer_side[w,k,s] <= 1
```

默认不要启用该约束，因为已确认允许复用时，同一片 wafer 可以通过不同 Bin Grade 参与不同母批。

## 字典序优化

通过分阶段求解并固定上一阶段最优值，或在计算安全上界后使用足够大的权重基数，实现字典序优化。

目标阶段目标顺序：

```text
最小化 total_units - T
固定最优值
最小化 sum_k loss[k]
固定最优值
如果是放宽 Lot 数阶段：最小化 sum_k lot_overflow[k] 并固定最优值
最小化 sum_k batch_active[k]
```

兜底阶段目标顺序：

```text
最大化 sum_k u[k]
固定最优值
最小化 sum_k loss[k]
固定最优值
如果是放宽 Lot 数阶段：最小化 sum_k lot_overflow[k] 并固定最优值
最小化 sum_k batch_active[k]
```

## 求解后校验

取出结果后，必须在求解器外部再次校验：

1. 每个被分配的单元都属于用户选择的 Bin Grade
2. 每个被选中的 Lot，其所选等级单元都恰好分配一次
3. 每个未选中的 Lot 没有任何单元被分配
4. 每个母批满足 `u[k] <= A`
5. 每个母批满足 `loss[k] <= L`
6. 严格 Lot 数阶段，每个母批不同 Lot 数 `<= B`
7. 不允许复用时，每个被选中的 Lot 只出现在一个母批
8. 允许复用时，不存在重复单元分配；但 Lot 和 wafer 可通过不同单元跨母批出现
9. 目标阶段，总 Unit 数 `>= T`
10. 兜底阶段，结果只能表述为最佳可行兜底方案，不能表述为目标满足
