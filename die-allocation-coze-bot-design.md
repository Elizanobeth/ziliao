# 扣子 Bot 迁移方案：半导体 Die 母批分配助手

本文档是一份可直接照着配置的扣子设计稿，覆盖 Bot 提示词、需要接入的插件/API 工具、Workflow 节点、变量、异常分支和测试用例。

核心原则：扣子负责交互、参数收集、流程编排和结果解释；原 Python 求解器负责 Excel 校验、规则匹配、供应单元聚合、优化求解和报告导出。

---

## 1. 推荐总体架构

```text
用户
  ↓ 上传 Excel / 输入约束
扣子 Bot：半导体 Die 母批分配助手
  ↓ 调用 Workflow
扣子 Workflow：die_allocation_main
  ↓ 调用插件工具
自定义插件：die_allocation_service
  ↓ HTTP API
Python 后端服务：封装现有 scripts/semidie 求解器
  ↓
JSON 审计结果 + Excel 报告下载链接
```

不建议把分配逻辑写进 Bot 提示词，也不建议让大模型直接生成母批方案。Bot 只能解释、追问、确认和展示工具返回的结构化结果。

---

## 2. 扣子 Bot 基础配置

### 2.1 Bot 名称

```text
半导体 Die 母批分配助手
```

### 2.2 Bot 描述

```text
根据上传的 Excel 工作簿和用户输入的 PACKAGE、供应商、目标 Unit、Bin Grade、损耗上限、单母批 Unit 上限、Lot 数上限、Lot 复用规则，调用后端求解器生成可审计的半导体晶圆 Die 母批分配方案，并返回摘要、Excel 报告和 JSON 审计结果。
```

### 2.3 开场白

```text
请上传包含「原始数据」和「配die 规则表」的 Excel 工作簿，并告诉我 PACKAGE、供应商、目标 Unit、Bin Grade、单母批损耗上限、单母批 Unit 上限、单母批 Lot 数上限，以及 Lot 是否允许复用。我会先校验数据，再生成可审计的母批分配方案。
```

### 2.4 建议快捷问题

```text
上传 Excel 后生成母批分配方案
解释报告里的 loss、lot_overflow 和兜底方案
检查为什么当前条件无法达到目标 Unit
对比允许复用和不允许复用的影响
```

---

## 3. Bot 系统提示词，可直接复制

```text
# 角色
你是「半导体 Die 母批分配助手」，服务对象是半导体供应链、生产计划、封测计划或工程团队。你的任务是帮助用户基于 Excel 工作簿生成晶圆 Die 母批分配方案。

# 最重要的原则
1. 你不能自行脑算、猜测或编造母批分配结果。
2. 所有 Excel 校验、规则匹配、供应聚合、优化求解、报告导出，必须通过 die_allocation_main 工作流或 die_allocation_service 插件完成。
3. 你可以解释工具返回的结果，但不能修改工具返回的数值。
4. 当缺少必要输入时，只追问缺失项，不要自行假设。
5. 当工具返回错误、歧义或无解原因时，必须如实转述，并给出用户下一步可以调整的参数方向。
6. 如果结果是兜底方案，必须明确说明「这是在损耗约束下的最佳可行兜底方案，不代表达到原目标」。
7. 如果后端使用 large_batch 模式，必须说明「该结果是稳定可复核的启发式可行解，不声明全局最优」。

# 必须收集的输入
每次正式求解前，必须具备以下信息：
- Excel 工作簿：必须包含「原始数据」和「配die 规则表」
- PACKAGE
- 供应商
- 目标 Unit 数 target_units，例如 40000 或 40k
- Bin Grade 列表，合法值为 1 到 9 以及 X
- 单个母批最大 Die 损耗 loss_cap
- 单个母批最大 Unit 数 unit_cap
- 单个母批最大不同 Fab LotID 数 lot_cap
- Lot 复用规则 reuse_rule：允许复用 或 不允许复用

# 可选输入
- backend：auto、cpsat、heuristic、large_batch；默认 auto
- time_limit：默认 300 秒
- 是否强制全局最优：如果用户要求全局最优，应优先选择 cpsat，并提醒大数据场景可能超时

# Excel 数据要求
你需要提醒用户，工作簿必须包含：
1. Sheet「原始数据」
   - PACKAGE
   - 供应商
   - Fab LotID
   - Bin Grade
   - Bin Quanity
   - T7 Code
2. Sheet「配die 规则表」
   - PACKAGE
   - 供应商
   - 层数配比

# 工作方式
1. 用户上传 Excel 或提供文件后，先检查是否已给齐必填参数。
2. 如果缺少参数，用简短清单追问缺失项。
3. 参数齐全后，调用 die_allocation_main 工作流。
4. 如果工作流返回规则匹配歧义，展示候选规则行，让用户选择，不要替用户选。
5. 如果工作流返回数据校验失败，说明失败字段、行号和修正建议。
6. 如果工作流返回求解中，告诉用户任务编号和查询方式。
7. 如果工作流返回完成，输出业务摘要和报告链接。
8. 如果工作流返回失败，按后端返回的原因解释，不要编造替代结果。

# 输出风格
回答要简洁、业务化、可执行。不要展示大量明细表，明细让用户查看 Excel 报告或 JSON 审计文件。

完成求解时，按以下结构回复：
【分配结果】
- 状态：
- 是否达到目标：
- 目标 Unit：
- 实际总 Unit：
- 超目标 Unit：
- 总损耗：
- 启用母批数：
- 求解阶段：
- 求解后端：

【关键约束】
- PACKAGE：
- 供应商：
- 层数配比：
- Bin Grade：
- 单母批损耗上限：
- 单母批 Unit 上限：
- 单母批 Lot 上限：
- Lot 复用规则：

【报告】
- Excel 报告：
- JSON 审计：

【提醒】
只在存在 warning、兜底方案、large_batch 模式或校验未通过时输出提醒。

# 禁止行为
- 禁止自行生成母批明细。
- 禁止把未达到目标的兜底方案说成目标最优方案。
- 禁止在规则匹配歧义时替用户选择规则。
- 禁止忽略工具返回的 checks 或 warnings。
- 禁止把用户未选择的 Bin Grade 纳入本次计算。
- 禁止承诺 large_batch 是全局最优。
```

---

## 4. 需要接入的工具

建议创建一个扣子自定义插件：

```text
插件名：die_allocation_service
插件说明：半导体晶圆 Die 母批分配求解服务
鉴权方式：API Key / Bearer Token
后端基础地址：https://你的域名.example.com
```

### 4.1 工具清单

| 工具名 | 用途 | 是否必需 |
|---|---|---|
| validateWorkbook | 校验 Excel 结构、字段、数据类型，返回 workbook_id 和摘要 | 必需 |
| matchRule | 根据 PACKAGE + 供应商匹配层数配比，处理精确/包含/模糊匹配 | 必需 |
| precheckSupply | 按 Bin Grade 构建供应单元，返回总量、目标需求和预警 | 必需 |
| createAllocationJob | 创建异步求解任务，生成 Excel/JSON 报告 | 必需 |
| getAllocationJob | 查询任务状态和结果摘要 | 必需 |
| getAllocationReport | 获取报告下载链接，可选，若 getAllocationJob 已返回链接则可省略 | 可选 |

### 4.2 为什么要异步任务

你的求解器默认时间上限是 300 秒，大表场景还可能进入 large_batch。扣子单次工具调用不适合长时间阻塞，所以推荐：

```text
createAllocationJob 返回 job_id
getAllocationJob 轮询状态
完成后返回 result_summary + report_urls
```

---

## 5. 插件 OpenAPI 草案，可直接改域名后导入

> 注意：不同扣子环境对文件参数的字段名可能略有差异。后端建议同时兼容 file_url、file_id 和 file_name。若扣子上传文件后只能传 file_url，就使用 file_url；若只能传 file_id，则后端用平台文件下载接口换取文件内容。

```yaml
openapi: 3.0.3
info:
  title: Die Allocation Service
  version: 1.0.0
  description: Semiconductor wafer die allocation planning service.
servers:
  - url: https://你的域名.example.com
security:
  - bearerAuth: []
components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
  schemas:
    FileInput:
      type: object
      properties:
        file_url:
          type: string
          description: Excel 文件下载地址
        file_id:
          type: string
          description: 扣子文件 ID
        file_name:
          type: string
      required: []
    ErrorDetail:
      type: object
      properties:
        code:
          type: string
        message:
          type: string
        details:
          type: array
          items:
            type: object
            additionalProperties: true
    ValidateWorkbookResponse:
      type: object
      properties:
        ok:
          type: boolean
        workbook_id:
          type: string
        summary:
          type: object
          properties:
            raw_row_count:
              type: integer
            rule_row_count:
              type: integer
            packages:
              type: array
              items:
                type: string
            suppliers:
              type: array
              items:
                type: string
        errors:
          type: array
          items:
            $ref: '#/components/schemas/ErrorDetail'
    RuleCandidate:
      type: object
      properties:
        rule_id:
          type: string
        row_number:
          type: integer
        package:
          type: string
        supplier:
          type: string
        ratio_text:
          type: string
        match_reason:
          type: string
        score:
          type: integer
    MatchRuleResponse:
      type: object
      properties:
        ok:
          type: boolean
        status:
          type: string
          enum: [matched, ambiguous, not_found, invalid_ratio]
        matched_rule:
          $ref: '#/components/schemas/RuleCandidate'
        candidates:
          type: array
          items:
            $ref: '#/components/schemas/RuleCandidate'
        ratio:
          type: object
          properties:
            text:
              type: string
            rA:
              type: integer
            rB:
              type: integer
        errors:
          type: array
          items:
            $ref: '#/components/schemas/ErrorDetail'
    PrecheckSupplyResponse:
      type: object
      properties:
        ok:
          type: boolean
        supply_id:
          type: string
        summary:
          type: object
          properties:
            selected_grades:
              type: array
              items:
                type: string
            item_count:
              type: integer
            lot_count:
              type: integer
            wafer_count:
              type: integer
            selected_die_total:
              type: integer
            required_die_total:
              type: integer
            target_units:
              type: integer
            total_quantity_enough:
              type: boolean
        warnings:
          type: array
          items:
            type: string
        errors:
          type: array
          items:
            $ref: '#/components/schemas/ErrorDetail'
    AllocationJobResponse:
      type: object
      properties:
        ok:
          type: boolean
        job_id:
          type: string
        status:
          type: string
          enum: [queued, running, succeeded, failed]
        message:
          type: string
    AllocationJobStatus:
      type: object
      properties:
        ok:
          type: boolean
        job_id:
          type: string
        status:
          type: string
          enum: [queued, running, succeeded, failed]
        progress:
          type: integer
          minimum: 0
          maximum: 100
        result_summary:
          type: object
          properties:
            solver_status:
              type: string
            phase:
              type: string
            backend:
              type: string
            reached_target:
              type: boolean
            target_units:
              type: integer
            total_units:
              type: integer
            over_target_units:
              type: integer
            total_loss:
              type: integer
            active_batch_count:
              type: integer
            total_lot_overflow:
              type: integer
        input_summary:
          type: object
          additionalProperties: true
        checks:
          type: array
          items:
            type: object
            additionalProperties: true
        warnings:
          type: array
          items:
            type: string
        report_urls:
          type: object
          properties:
            excel:
              type: string
            json:
              type: string
        error:
          $ref: '#/components/schemas/ErrorDetail'
paths:
  /v1/workbooks/validate:
    post:
      operationId: validateWorkbook
      summary: Validate uploaded Excel workbook
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                file:
                  $ref: '#/components/schemas/FileInput'
              required: [file]
      responses:
        '200':
          description: Workbook validation result
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ValidateWorkbookResponse'
  /v1/rules/match:
    post:
      operationId: matchRule
      summary: Match package and supplier to allocation ratio rule
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                workbook_id:
                  type: string
                package:
                  type: string
                supplier:
                  type: string
                selected_rule_id:
                  type: string
                  description: 用户在歧义候选中手动选择的 rule_id，可为空
              required: [workbook_id, package, supplier]
      responses:
        '200':
          description: Rule matching result
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/MatchRuleResponse'
  /v1/supply/precheck:
    post:
      operationId: precheckSupply
      summary: Build filtered supply and run quantity precheck
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                workbook_id:
                  type: string
                rule_id:
                  type: string
                package:
                  type: string
                supplier:
                  type: string
                grades:
                  type: array
                  items:
                    type: string
                target_units:
                  type: string
                  description: 支持 40000 或 40k
              required: [workbook_id, rule_id, package, supplier, grades, target_units]
      responses:
        '200':
          description: Supply precheck result
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PrecheckSupplyResponse'
  /v1/allocation/jobs:
    post:
      operationId: createAllocationJob
      summary: Create allocation solving job
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                workbook_id:
                  type: string
                rule_id:
                  type: string
                supply_id:
                  type: string
                package:
                  type: string
                supplier:
                  type: string
                target_units:
                  type: string
                grades:
                  type: array
                  items:
                    type: string
                loss_cap:
                  type: string
                unit_cap:
                  type: string
                lot_cap:
                  type: string
                reuse_rule:
                  type: string
                  enum: [allow_reuse, no_reuse]
                backend:
                  type: string
                  enum: [auto, cpsat, heuristic, large_batch]
                  default: auto
                time_limit:
                  type: integer
                  default: 300
              required:
                - workbook_id
                - rule_id
                - supply_id
                - package
                - supplier
                - target_units
                - grades
                - loss_cap
                - unit_cap
                - lot_cap
                - reuse_rule
      responses:
        '200':
          description: Job created
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AllocationJobResponse'
  /v1/allocation/jobs/{job_id}:
    get:
      operationId: getAllocationJob
      summary: Get allocation job status and result
      parameters:
        - name: job_id
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Job status
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AllocationJobStatus'
```

---

## 6. 后端工具与现有 skill 的映射

| API 工具 | 对应现有 Python 能力 | 说明 |
|---|---|---|
| validateWorkbook | `semidie.workbook.validate_workbook` | 读取 Excel，校验 sheet、列名、Bin Quanity、Bin Grade |
| matchRule | `semidie.matcher.match_rule` | PACKAGE + 供应商匹配层数配比 |
| precheckSupply | `semidie.supply.build_supply` | 按 Grade 过滤并聚合最小供应单元 |
| createAllocationJob | `semidie.solver.solve_allocation` + `semidie.report.export_report` | 后台异步求解并导出报告 |
| getAllocationJob | 任务表/缓存/对象存储 | 返回状态、摘要、checks、warnings、报告链接 |

后端要保留以下输出文件：

```text
01_validated.json
02_matched_rule.json
03_supply.json
04_solution.json
05_allocation_report.xlsx
05_allocation_report.json
```

---

## 7. Workflow 设计：die_allocation_main

### 7.1 Workflow 输入变量

| 变量名 | 类型 | 必填 | 示例 | 说明 |
|---|---|---:|---|---|
| excel_file | File | 是 | 用户上传 | Excel 工作簿 |
| package | String | 是 | ABC-123 | 目标 PACKAGE |
| supplier | String | 是 | SupplierA | 目标供应商 |
| target_units | String | 是 | 40k | 目标 Unit |
| grades | Array[String] | 是 | ["1","2","3"] | 选择的 Bin Grade |
| loss_cap | String | 是 | 30 | 单母批最大 Die 损耗 |
| unit_cap | String | 是 | 4.5k | 单母批最大 Unit |
| lot_cap | String | 是 | 5 | 单母批最大不同 Lot 数 |
| reuse_rule | String | 是 | no_reuse | allow_reuse/no_reuse |
| backend | String | 否 | auto | 默认 auto |
| time_limit | Integer | 否 | 300 | 默认 300 |
| selected_rule_id | String | 否 | R-001 | 规则歧义时用户选择 |

### 7.2 Workflow 输出变量

| 变量名 | 类型 | 说明 |
|---|---|---|
| status | String | succeeded / failed / need_user_input / running |
| message | String | 给 Bot 展示的主消息 |
| job_id | String | 求解任务 ID |
| result_summary | Object | 求解摘要 |
| report_urls | Object | Excel/JSON 下载链接 |
| warnings | Array[String] | 警告 |
| errors | Array[Object] | 错误详情 |
| candidates | Array[Object] | 规则候选 |

---

## 8. Workflow 节点表，可照着搭

### 节点 1：Start

类型：开始节点  
输入：第 7.1 节所有 Workflow 输入变量。

默认值：

```text
backend = auto
time_limit = 300
```

### 节点 2：NormalizeInputs

类型：代码节点或大模型结构化提取节点  
目标：标准化用户输入。

处理规则：

```text
reuse_rule:
  "允许复用"、"allow"、"allow_reuse" -> allow_reuse
  "不允许复用"、"no_reuse"、"禁止复用" -> no_reuse

grades:
  "1,2,3" -> ["1","2","3"]
  "1/2/3" -> ["1","2","3"]
  "1 2 3" -> ["1","2","3"]
  转大写，X 保留为 "X"

backend:
  空 -> auto

time_limit:
  空 -> 300
```

输出：

```text
normalized_package
normalized_supplier
normalized_target_units
normalized_grades
normalized_loss_cap
normalized_unit_cap
normalized_lot_cap
normalized_reuse_rule
normalized_backend
normalized_time_limit
```

### 节点 3：CheckRequiredInputs

类型：条件节点  
条件：任一必填参数为空。

缺失字段判断：

```text
excel_file is empty
package is empty
supplier is empty
target_units is empty
grades is empty
loss_cap is empty
unit_cap is empty
lot_cap is empty
reuse_rule is empty
```

如果缺失，进入节点 4。  
如果不缺失，进入节点 5。

### 节点 4：AskMissingInputs

类型：结束/回复节点  
输出：

```text
status = need_user_input
message =
还需要补充以下信息后才能开始分配：
{missing_fields}

请直接按这个格式回复：
PACKAGE：
供应商：
目标 Unit：
Bin Grade：
单母批损耗上限：
单母批 Unit 上限：
单母批 Lot 数上限：
Lot 复用规则：允许复用/不允许复用
```

### 节点 5：ValidateWorkbook

类型：插件工具节点  
调用：`die_allocation_service.validateWorkbook`

输入：

```json
{
  "file": {
    "file_url": "{{excel_file.url}}",
    "file_id": "{{excel_file.id}}",
    "file_name": "{{excel_file.name}}"
  }
}
```

输出保存：

```text
validate_ok = response.ok
workbook_id = response.workbook_id
workbook_summary = response.summary
validate_errors = response.errors
```

### 节点 6：ValidateWorkbookResult

类型：条件节点

条件：

```text
validate_ok != true
```

失败进入节点 7。  
成功进入节点 8。

### 节点 7：ReturnWorkbookErrors

类型：结束/回复节点

输出：

```text
status = failed
message =
Excel 工作簿校验没有通过。请先修正后重新上传。

主要问题：
{validate_errors}

工作簿需要包含「原始数据」和「配die 规则表」两个 Sheet。
「原始数据」需要列：PACKAGE、供应商、Fab LotID、Bin Grade、Bin Quanity、T7 Code。
「配die 规则表」需要列：PACKAGE、供应商、层数配比。
```

### 节点 8：MatchRule

类型：插件工具节点  
调用：`die_allocation_service.matchRule`

输入：

```json
{
  "workbook_id": "{{workbook_id}}",
  "package": "{{normalized_package}}",
  "supplier": "{{normalized_supplier}}",
  "selected_rule_id": "{{selected_rule_id}}"
}
```

输出保存：

```text
match_status = response.status
matched_rule = response.matched_rule
rule_candidates = response.candidates
ratio = response.ratio
rule_errors = response.errors
```

### 节点 9：MatchRuleResult

类型：条件节点

分支：

```text
match_status == "matched" -> 节点 12
match_status == "ambiguous" -> 节点 10
else -> 节点 11
```

### 节点 10：AskRuleSelection

类型：结束/回复节点

输出：

```text
status = need_user_input
candidates = rule_candidates
message =
PACKAGE 和供应商匹配到多条可能的配 Die 规则，我不能替你选择。请回复要使用的规则编号 rule_id。

候选规则：
{按 row_number、package、supplier、ratio_text、match_reason、score 展示 rule_candidates}
```

用户回复 rule_id 后，再次运行 Workflow，并把 `selected_rule_id` 传入。

### 节点 11：ReturnRuleErrors

类型：结束/回复节点

输出：

```text
status = failed
message =
没有找到可用的配 Die 规则，或层数配比无效。

问题：
{rule_errors}

请检查「配die 规则表」中的 PACKAGE、供应商和层数配比。层数配比必须是两个正整数，例如 2:3。
```

### 节点 12：PrecheckSupply

类型：插件工具节点  
调用：`die_allocation_service.precheckSupply`

输入：

```json
{
  "workbook_id": "{{workbook_id}}",
  "rule_id": "{{matched_rule.rule_id}}",
  "package": "{{normalized_package}}",
  "supplier": "{{normalized_supplier}}",
  "grades": "{{normalized_grades}}",
  "target_units": "{{normalized_target_units}}"
}
```

输出保存：

```text
precheck_ok = response.ok
supply_id = response.supply_id
supply_summary = response.summary
supply_warnings = response.warnings
supply_errors = response.errors
```

### 节点 13：PrecheckResult

类型：条件节点

条件：

```text
precheck_ok != true
```

失败进入节点 14。  
成功进入节点 15。

### 节点 14：ReturnSupplyErrors

类型：结束/回复节点

输出：

```text
status = failed
message =
按所选 Bin Grade 构建供应单元失败。

问题：
{supply_errors}

请检查原始数据中对应 PACKAGE、供应商和 Bin Grade 是否存在有效 Die 数。
```

### 节点 15：CreateAllocationJob

类型：插件工具节点  
调用：`die_allocation_service.createAllocationJob`

输入：

```json
{
  "workbook_id": "{{workbook_id}}",
  "rule_id": "{{matched_rule.rule_id}}",
  "supply_id": "{{supply_id}}",
  "package": "{{normalized_package}}",
  "supplier": "{{normalized_supplier}}",
  "target_units": "{{normalized_target_units}}",
  "grades": "{{normalized_grades}}",
  "loss_cap": "{{normalized_loss_cap}}",
  "unit_cap": "{{normalized_unit_cap}}",
  "lot_cap": "{{normalized_lot_cap}}",
  "reuse_rule": "{{normalized_reuse_rule}}",
  "backend": "{{normalized_backend}}",
  "time_limit": "{{normalized_time_limit}}"
}
```

输出保存：

```text
job_created = response.ok
job_id = response.job_id
job_status = response.status
job_message = response.message
```

### 节点 16：JobCreatedResult

类型：条件节点

条件：

```text
job_created != true
```

失败进入节点 17。  
成功进入节点 18。

### 节点 17：ReturnJobCreateError

类型：结束/回复节点

输出：

```text
status = failed
message =
求解任务创建失败。

问题：
{job_message}
```

### 节点 18：PollJob

类型：插件工具节点  
调用：`die_allocation_service.getAllocationJob`

输入：

```json
{
  "job_id": "{{job_id}}"
}
```

输出保存：

```text
poll_status = response.status
progress = response.progress
result_summary = response.result_summary
input_summary = response.input_summary
checks = response.checks
warnings = response.warnings
report_urls = response.report_urls
job_error = response.error
```

### 节点 19：PollResult

类型：条件节点

分支：

```text
poll_status == "succeeded" -> 节点 22
poll_status == "failed" -> 节点 21
else -> 节点 20
```

### 节点 20：ReturnRunning

类型：结束/回复节点

输出：

```text
status = running
job_id = job_id
message =
求解任务已提交，当前状态：{poll_status}，进度：{progress}%。

任务编号：{job_id}
如果稍后还没自动返回结果，请发送「查询任务 {job_id}」。
```

如果扣子 Workflow 支持循环和等待节点，可以将节点 20 改为：

```text
等待 10 秒 -> 回到节点 18
最多轮询 12 次
超过 12 次后返回 running
```

### 节点 21：ReturnJobFailed

类型：结束/回复节点

输出：

```text
status = failed
job_id = job_id
message =
求解失败。

任务编号：{job_id}
原因：{job_error.message}

你可以优先检查：
1. 所选 Die 总量是否低于目标需求
2. 层数配比是否匹配正确
3. 单母批损耗上限是否过小
4. 单母批 Unit 上限是否过小
5. 不允许复用是否过于严格
6. 是否需要延长求解时间或改用 large_batch
```

### 节点 22：ReturnSuccess

类型：结束/回复节点

输出：

```text
status = succeeded
job_id = job_id
result_summary = result_summary
report_urls = report_urls
warnings = warnings
message =
【分配结果】
- 状态：{result_summary.solver_status}
- 是否达到目标：{result_summary.reached_target}
- 目标 Unit：{result_summary.target_units}
- 实际总 Unit：{result_summary.total_units}
- 超目标 Unit：{result_summary.over_target_units}
- 总损耗：{result_summary.total_loss}
- 启用母批数：{result_summary.active_batch_count}
- Lot 超限总量：{result_summary.total_lot_overflow}
- 求解阶段：{result_summary.phase}
- 求解后端：{result_summary.backend}

【关键约束】
- PACKAGE：{input_summary.target_package}
- 供应商：{input_summary.supplier}
- 层数配比：{input_summary.ratio.text}
- Bin Grade：{input_summary.grades}
- 单母批损耗上限：{input_summary.loss_cap}
- 单母批 Unit 上限：{input_summary.unit_cap}
- 单母批 Lot 上限：{input_summary.lot_cap}
- Lot 复用规则：{input_summary.reuse_rule}

【报告】
- Excel 报告：{report_urls.excel}
- JSON 审计：{report_urls.json}

【提醒】
{warnings}
```

---

## 9. Bot 中再建一个查询 Workflow：die_allocation_query

用于用户说「查询任务 xxx」。

### 输入变量

| 变量名 | 类型 | 必填 |
|---|---|---:|
| job_id | String | 是 |

### 节点

```text
Start
-> getAllocationJob
-> 如果 succeeded：返回摘要和报告链接
-> 如果 failed：返回失败原因
-> 如果 queued/running：返回当前进度
```

Bot 识别到以下表达时调用：

```text
查询任务 JOB_ID
查看 JOB_ID
任务 JOB_ID 怎么样了
```

---

## 10. 知识库设计

建议创建一个知识库：

```text
知识库名：die_allocation_business_rules
用途：只用于解释业务口径和报告字段，不参与计算。
```

建议上传 3 篇文档：

### 文档 1：输入数据格式说明

内容包括：

```text
必须包含的 Sheet：
- 原始数据
- 配die 规则表

原始数据列：
- PACKAGE
- 供应商
- Fab LotID
- Bin Grade
- Bin Quanity
- T7 Code

配die 规则表列：
- PACKAGE
- 供应商
- 层数配比

Bin Grade 合法值：
- 1 到 9
- X

Bin Quanity：
- 必须是非负整数
```

### 文档 2：分配规则解释

内容包括：

```text
最小供应单元：Fab LotID + T7 Code + Bin Grade 聚合后的 Die 数。
Lot 必须配完：一个被选中的 Lot，其用户选择等级范围内的所有最小供应单元都必须被分配。
允许复用：Lot 可以跨母批，但同一个最小供应单元不能重复使用。
不允许复用：被使用的 Lot 只能出现在一个母批中。
单母批损耗：A 侧 Die + B 侧 Die - Unit * (rA + rB)。
兜底方案：目标无法满足时，在损耗约束下最大化可生产 Unit。
```

### 文档 3：报告字段解释

内容包括：

```text
summary：整体摘要
batches：母批级结果
assignments：最小供应单元分配明细
checks：求解后校验
warnings：警告
lot_overflow：放宽 Lot 数阶段中超过 lot_cap 的数量
phase：target_strict_lot、target_relaxed_lot、fallback_strict_lot、fallback_relaxed_lot 等
backend：auto、cpsat、heuristic、large_batch
```

---

## 11. 多轮交互策略

### 11.1 用户只上传文件

Bot 回复：

```text
我已收到 Excel。还需要这些参数才能开始：
- PACKAGE
- 供应商
- 目标 Unit
- Bin Grade
- 单母批损耗上限
- 单母批 Unit 上限
- 单母批 Lot 数上限
- Lot 复用规则：允许复用/不允许复用
```

### 11.2 用户参数不完整

Bot 只追问缺失项：

```text
还差 3 项：目标 Unit、Bin Grade、Lot 复用规则。
请补充，例如：目标 40k，Grade 1/2/3，不允许复用。
```

### 11.3 规则匹配歧义

Bot 回复：

```text
匹配到多条配 Die 规则，我不能替你选择。请回复要使用的 rule_id。

候选规则：
1. rule_id=R001，行号=12，PACKAGE=xxx，供应商=yyy，层数配比=2:3，匹配方式=normalized_containment
2. rule_id=R002，行号=18，PACKAGE=xxx，供应商=yyy，层数配比=1:1，匹配方式=fuzzy_score_91
```

### 11.4 总量不足

如果 precheck 返回 `total_quantity_enough=false`，Bot 可以继续提交求解，因为原 skill 会进入兜底阶段，但需要提示：

```text
所选等级 Die 总量低于目标需求，原目标从总量上看无法完全满足。我会继续求解兜底方案，也就是在损耗约束下尽量最大化可生产 Unit。
```

### 11.5 求解完成但未达到目标

Bot 必须说：

```text
这不是达到原目标的方案，而是在当前损耗约束下的最佳可行兜底方案。
```

### 11.6 使用 large_batch

Bot 必须说：

```text
本次使用 large_batch 大表模式，结果是稳定可复核的启发式可行解，不声明全局最优。如需追求全局最优，可以缩小数据规模后使用 cpsat，或延长求解时间。
```

---

## 12. 后端返回字段建议

为了让 Bot 少做推理，后端应直接返回适合展示的字段。

### 12.1 result_summary

```json
{
  "solver_status": "FEASIBLE",
  "phase": "target_strict_lot",
  "backend": "cpsat",
  "reached_target": true,
  "target_units": 40000,
  "total_units": 40120,
  "over_target_units": 120,
  "total_loss": 82,
  "active_batch_count": 9,
  "total_lot_overflow": 0
}
```

### 12.2 input_summary

```json
{
  "target_package": "ABC-123",
  "supplier": "SupplierA",
  "ratio": {
    "text": "2:3",
    "rA": 2,
    "rB": 3
  },
  "grades": ["1", "2", "3"],
  "loss_cap": 30,
  "unit_cap": 4500,
  "lot_cap": 5,
  "reuse_rule": "no_reuse"
}
```

### 12.3 warnings

```json
[
  "所选 Die 总量低于目标需求，本次结果为兜底方案。",
  "使用 large_batch 大表模式，结果为启发式可行解，不声明全局最优。"
]
```

### 12.4 checks

```json
[
  {
    "name": "selected_lots_complete",
    "ok": true,
    "detail": "所有被使用的所选等级 Lot 已配完。"
  },
  {
    "name": "no_duplicate_item_assignment",
    "ok": true,
    "detail": "没有重复分配最小供应单元。"
  }
]
```

---

## 13. 后端部署注意事项

### 13.1 文件处理

```text
1. 接收扣子传入的 file_url 或 file_id。
2. 下载文件到隔离临时目录。
3. 为每次上传生成 workbook_id。
4. workbook_id 对应 validated.json 和原始 Excel。
5. 文件和报告设置过期时间，例如 7 天。
```

### 13.2 任务处理

```text
1. createAllocationJob 创建 job_id。
2. job 状态从 queued -> running -> succeeded/failed。
3. 后台 worker 调用现有 Python 函数。
4. 完成后上传 05_allocation_report.xlsx 和 05_allocation_report.json。
5. getAllocationJob 返回摘要、checks、warnings、报告链接。
```

### 13.3 安全

```text
- 限制 Excel 文件大小。
- 限制单用户并发任务数。
- API 使用 Bearer Token。
- 报告链接使用短期签名 URL。
- 不把完整原始数据返回给大模型，只返回摘要和报告链接。
```

---

## 14. 扣子配置操作顺序

1. 部署 Python 后端服务，确认以下接口可访问：
   - `/v1/workbooks/validate`
   - `/v1/rules/match`
   - `/v1/supply/precheck`
   - `/v1/allocation/jobs`
   - `/v1/allocation/jobs/{job_id}`

2. 在扣子创建自定义插件：
   - 插件名：`die_allocation_service`
   - 导入第 5 节 OpenAPI
   - 配置 Bearer Token
   - 测试每个工具返回字段

3. 在扣子创建知识库：
   - 名称：`die_allocation_business_rules`
   - 上传第 10 节的 3 篇文档
   - 设置为仅用于解释，不用于生成计算结果

4. 在扣子创建 Workflow：
   - 名称：`die_allocation_main`
   - 按第 7-8 节创建输入变量和节点
   - 绑定插件工具
   - 设置输出变量

5. 再创建查询 Workflow：
   - 名称：`die_allocation_query`
   - 输入 job_id
   - 调用 `getAllocationJob`

6. 创建 Bot：
   - 名称：`半导体 Die 母批分配助手`
   - 粘贴第 3 节系统提示词
   - 绑定 `die_allocation_main`
   - 绑定 `die_allocation_query`
   - 绑定知识库

7. 做端到端测试：
   - 正常达标
   - 总量不足进入兜底
   - 规则歧义
   - Excel 缺列
   - Bin Quanity 非法
   - large_batch 大表模式

---

## 15. 验收测试用例

### 用例 1：参数完整，正常求解

用户输入：

```text
上传 Excel。
PACKAGE 是 ABC-123，供应商 SupplierA，目标 40k，Grade 1/2/3，单母批损耗上限 30，单母批 Unit 上限 4.5k，Lot 上限 5，不允许复用。
```

期望：

```text
Bot 调用 die_allocation_main。
返回求解摘要、Excel 报告链接、JSON 审计链接。
不展示大段 assignments 明细。
```

### 用例 2：缺少参数

用户输入：

```text
帮我用这个 Excel 配 die。
```

期望：

```text
Bot 追问 PACKAGE、供应商、目标 Unit、Bin Grade、loss_cap、unit_cap、lot_cap、reuse_rule。
不调用求解工具。
```

### 用例 3：规则歧义

后端返回：

```json
{
  "status": "ambiguous",
  "candidates": [
    {"rule_id": "R001", "row_number": 12, "ratio_text": "2:3"},
    {"rule_id": "R002", "row_number": 18, "ratio_text": "1:1"}
  ]
}
```

期望：

```text
Bot 展示候选项并要求用户选择 rule_id。
Bot 不替用户选择。
```

### 用例 4：总量不足

后端 precheck：

```json
{
  "total_quantity_enough": false,
  "selected_die_total": 100000,
  "required_die_total": 200000
}
```

期望：

```text
Bot 提醒原目标总量不足，但可以继续求解兜底方案。
最终如果返回结果，必须标记为兜底方案。
```

### 用例 5：large_batch

后端 warnings：

```json
[
  "使用 large_batch 大表模式，结果为启发式可行解，不声明全局最优。"
]
```

期望：

```text
Bot 原样提示，不承诺全局最优。
```

---

## 16. 最小 MVP 版本

如果想最快上线，先做这个版本：

```text
Bot
  ↓
单个 Workflow
  ↓
两个插件工具：
  1. createAllocationJob：内部完成 validate + match + precheck + solve + report
  2. getAllocationJob：查询结果
```

MVP 的代价：

```text
- 规则歧义时交互不够细
- Excel 校验错误不如分步清楚
- 不方便在求解前展示总量预检查
```

正式版推荐使用本文第 4 节的 5 个必需工具。

---

## 17. 推荐上线版本

```text
Bot：
半导体 Die 母批分配助手

Workflow：
die_allocation_main
die_allocation_query

插件：
die_allocation_service
  - validateWorkbook
  - matchRule
  - precheckSupply
  - createAllocationJob
  - getAllocationJob

知识库：
die_allocation_business_rules

后端：
FastAPI + 后台任务队列 + 对象存储报告链接
```

这是最稳的迁移形态：扣子侧体验像 Agent，计算侧仍然保持你现有 skill 的工程化、确定性和可审计性。
