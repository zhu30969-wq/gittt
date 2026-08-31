# 从问题结构选择模型

模型选择的目标不是展示算法储备，而是建立“现实问题—数学结构—计算实现—验证证据”的最短可靠链。

## 禁止算法名先行

在提出具体算法前，先形成问题结构记录：

1. **对象与边界**：研究对象是什么，系统边界在哪里。
2. **任务类型**：要解释、估计、预测、评价、决策、优化还是模拟什么。
3. **输入与输出**：观测量、决策量、状态量、目标量及单位。
4. **机制与依赖**：因果、守恒、时序、空间、网络或约束关系。
5. **数据生成过程**：样本如何产生，是否有时间顺序、分组、删失或测量误差。
6. **约束与代价**：可行域、资源限制、整数性、风险和计算预算。
7. **评价标准**：什么证据会支持或否定一个方案。

没有完成这些内容时，不得仅凭“预测题”“评价题”等关键词确定模型。

## 建立最小基线

凡是可以比较的任务，都应保留一个简单、可解释、可运行的基线。基线可能是：

- 直接规则、均值或最近值；
- 简单线性关系；
- 无优化或贪心可行方案；
- 均匀权重或单指标排序；
- 可手算的小规模情形；
- 去掉某个机制后的简化模型。

基线不是为了凑模型数量，而是回答两个问题：

1. 复杂模型是否确实改善了任务目标；
2. 复杂模型失败时是否仍有可交付的可靠结果。

若某类纯推导任务不存在合理的数值基线，应给出极限情况、特殊解或独立推导作为参照。

把这项决定写入 `model_spec.method_selection.baseline_policy`：

- `required` 时引用独立的 `role: baseline` model spec；该模型必须覆盖相同子问，并有同指标口径的实验和 eligible result；
- `waived` 只用于确实不存在有区分力的参照，必须说明为什么简单规则、特殊解、极限情形或独立推导都不适用，并由 G2 人工 review 绑定当前模型指纹；
- 基线模型和实际基线结果都进入依赖闭包，防止基线变化后主方法比较仍被误当成当前证据。

## 候选模型卡

仅为存在实质不确定性的路线建立候选，不规定固定数量。每个候选至少记录：

| 字段 | 必须回答的问题 |
|---|---|
| 子问题 ID | 它具体解决哪一问或哪一环 |
| 现实对应 | 数学变量和关系分别代表什么 |
| 核心假设 | 哪些假设一旦不成立会改变结论 |
| 数据需求 | 参数和结构能否由现有数据识别 |
| 输入输出 | 如何与上下游模型连接 |
| 计算性质 | 精确、近似、随机或启发式；复杂度是否可接受 |
| 基线 | 它需要超过什么参照 |
| 反证检查 | 什么结果会让团队放弃它 |
| 验证计划 | 使用何种样本外、残差、约束、收敛或稳健性证据 |
| 失败方式 | 最可能在哪些数据或参数范围失效 |
| 论文价值 | 是否增加真实解释或决策价值 |

只写“常用、先进、精度高、适合非线性”不构成选择依据。

## 条件性判断

下表是审计器的强制口径；Agent 应在 G2 之前依据已经确定的 `model_family` 与 `task_type` 填写 `validation_plan.checks`，不适用的检查也要显式标为 `not_applicable` 并写明理由，不要等审计出现 `BLOCK` 后再补。

`validation_facets` 用于声明一个模型实际跨越的验证维度，取值只能来自下表七个有族级映射的模型族。`model_family` 为 `hybrid` 或 `other` 时必须至少填写一项，审计器对所有 facet 的必需检查取并集；其余模型族可省略该字段，省略时等价于只选择自身模型族。

<!-- BEGIN GENERATED: validation-matrix -->
### 审计器验证覆盖决策表

> 本块由 `scripts/export_validation_matrix.py` 生成；映射取自审计器常量，合法取值与行顺序取自 Schema。不要手工编辑。

#### 模型族 → 必须考虑的 checks

| `model_family` | `validation_plan.checks` 必须考虑 |
|---|---|
| `descriptive` | `input_integrity` |
| `statistical` | `uncertainty`、`residual_diagnostics` |
| `prediction` | `baseline_comparison`、`holdout_leakage`、`predictive_error`、`uncertainty` |
| `optimization` | `baseline_comparison`、`constraint_feasibility`、`solver_optimality`、`objective_reconciliation`、`sensitivity` |
| `simulation` | `convergence`、`conservation_balance`、`numerical_stability`、`boundary_case` |
| `evaluation` | `baseline_comparison`、`sensitivity`、`rank_stability` |
| `causal` | `uncertainty`、`identifiability`、`falsification` |
| `hybrid` | 由必填的 `validation_facets` 所选模型族检查取并集（仍叠加任务级与公式级约束） |
| `other` | 由必填的 `validation_facets` 所选模型族检查取并集（仍叠加任务级与公式级约束） |

#### 任务类型 → 必须考虑的 checks

| `task_type` | `validation_plan.checks` 必须考虑 |
|---|---|
| `description` | `input_integrity` |
| `prediction` | `baseline_comparison`、`holdout_leakage`、`predictive_error`、`uncertainty` |
| `evaluation` | `baseline_comparison`、`sensitivity`、`rank_stability` |
| `optimization` | `baseline_comparison`、`constraint_feasibility`、`solver_optimality`、`sensitivity` |
| `mechanism` | `dimensional_consistency`、`sensitivity`、`boundary_case` |
| `decision` | `baseline_comparison`、`sensitivity` |
| `other` | —（无任务级强制检查；仍可能受族级与公式级约束） |

当 `formulation.equations`、`objectives` 或 `constraints` 中任一列表非空时，审计器还会叠加公式级检查：`dimensional_consistency`、`domain_validity`、`formula_back_substitution`。

#### 当前覆盖边界与盲区

- **无族级映射的 `model_family`**：`hybrid`、`other`。这些取值必须声明非空 `validation_facets`，审计器按所选模型族的检查取并集；因此它们不再构成有效输入的族级覆盖盲区。
- **无族级映射的 `validation_facets`**：（无）。合法 facet 与族级映射键保持一致；该差集非空时生成器会拒绝输出。
- **无任务级映射的 `task_type`**：`other`。该任务类型不受任务级 check 覆盖约束，仍可能受模型族与公式级约束。
- **未被族、任务或公式规则自动要求的 `validationCheckType`**：`seed_stability`、`reproducibility`、`other`。其中 `seed_stability` 对随机算法是关键检查，目前完全依赖人工在 `validation_plan.checks` 中主动声明；`reproducibility` 与 `other` 同样不会被自动补入。
<!-- END GENERATED: validation-matrix -->

条件性判断必须转成 `validation_plan.checks`，不能只留在论文写作清单里。每项检查预先声明适用性、阻断性、过程、通过规则、可选数值阈值和失败响应；运行后由 `results.diagnostics` 一对一报告。`not_applicable` 表示团队明确判断该检查不适用，不等于忘记填写。

### 预测与推断

先判断：

- 预测时间点是否晚于训练数据；
- 是否存在分组、个体或空间泄漏；
- 目标是点预测、区间预测、解释变量效应还是因果效应；
- 样本量是否支持模型自由度；
- 指标是否与赛题实际损失一致。

时序数据原则上按时间验证；数据预处理只能在训练部分拟合。复杂模型必须与朴素预测或简单统计模型在同一切分上比较。

### 优化与决策

先判断：

- 决策变量是连续、整数、二元还是路径；
- 目标和约束是否可线性化、凸化或分解；
- 是需要可证明最优、可行优先，还是限时近似；
- 不确定参数是否需要情景、稳健或随机处理；
- 求解规模和剩余时间是否允许精确算法。

启发式算法不是“非线性”的默认答案。使用启发式时必须报告可行性、基线差距、重复运行波动，且不得无证据宣称全局最优。

除求解器 gap/上下界外，还要做独立的目标对账：固定最终主决策，用单独脚本重新优化辅助变量并比较目标值。对同一组辅助变量简单重新求和不属于 `objective_reconciliation`，因为它无法发现“可行但辅助响应未达最优”的实现遗漏。

### 评价与排序

先判断：

- 指标是否真的代表目标，是否重复计量；
- 正负向、单位和标准化是否明确；
- 权重来自偏好、数据、政策还是识别结果；
- 是否存在可校验的外部结果；
- 排名是否会因合理权重扰动而翻转。

多个赋权或排序方法叠加只有在分别解决不同问题时才成立。模型名称增多不提高结论可信度。

### 机理与仿真

先判断：

- 方程来自守恒、几何、行为规则还是经验关系；
- 量纲、坐标系、边初值条件和参数范围是否明确；
- 参数是否可辨识；
- 数值离散是否稳定并收敛；
- 是否有守恒量、极限解或实验数据可验证。

仅画出“合理曲线”不能证明机理模型正确。

### 图与空间结构

先判断节点、边、距离、方向、容量和时间依赖的现实含义。若图结构只是数据存储形式而非问题机制，不应为了使用图算法而构图。

## 比较和选择

候选比较必须尽量固定：

- 同一数据版本和数据切分；
- 同一目标定义和评价指标；
- 可比的参数调优预算；
- 相同随机重复规则；
- 相同运行环境或已解释的差异。

选择记录应包含：

1. 被选模型及证据；
2. 保留的基线；
3. 被拒绝候选及明确原因；
4. 当前选择仍未解决的风险；
5. 若后续验证失败，应回退到哪个候选或问题解释。

不得只展示胜出模型而隐藏失败路线。失败候选可以不进入正文，但其失败原因应保留在实验或决策记录中。

## 案例和知识的使用顺序

通常先保存独立的问题结构、基线和初步候选，再读取 [案例使用](case-use.md)。这样案例用于检查遗漏和提供反例，而不是让历史论文替当前题目做选择。

请求本身就是案例比较时，可以直接读取案例；但最终迁移建议仍必须重新经过当前问题的结构、假设和数据检查。

## 模型冻结检查

进入 G2 前逐项确认：

- 每个顶层问题都有明确输出；
- 每个子模型都有不可替代的功能；
- 模型接口、单位和误差传播明确；
- 假设在模型或验证中真正被使用；
- 基线已经定义；
- 选择标准在主要结果产生前确定；
- 关键参数有来源或识别方案；
- 验证计划能发现模型失败，而不只是生成好看的图；
- 每个模型族的关键失败面都已登记为结构化 check，数值阈值在看结果前确定；
- 每个 required/conditional check 都能由运行结果生成唯一 diagnostic；
- 复杂度符合数据量、算力和剩余时间。

任一关键项缺失时，保持在 MODELING，不进入 G2。
