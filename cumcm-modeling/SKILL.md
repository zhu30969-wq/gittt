---
name: cumcm-modeling
description: "Orchestrate, continue, or audit CUMCM（全国大学生数学建模竞赛，简称国赛） projects with problem framing, baseline-aware modeling, reproducible experiments, traceable claims, figures, Chinese papers, three-person handoffs, and paper QA. Use for explicit CUMCM/全国大学生数学建模竞赛/国赛 tasks; do not use for COMAP MCM/ICM, generic homework, or unrelated data analysis."
---

# CUMCM 数学建模

把一道 CUMCM 赛题推进为可解释、可运行、可复现、可审查的模型、结果和中文论文。Skill 负责组织推理和交付，不替人宣称模型必然正确，也不把算法数量或论文篇幅当作质量。

## 先确定工作方式

按请求选择一种方式；具体映射和切换条件见 [工作方式](references/profiles.md)：

- `focused`：只完成用户点名阶段，不强制初始化或伪造门禁。
- `competition-fast`：使用 `project`/`run` 语义做限时迭代；缺审批的 `WARN` 不等于通过或可提交。
- `release-strict`：使用 `release` 语义和完整 G0–G7；只有此模式可到 `SUBMISSION_READY`。
- `audit`：默认只读，报告证据、根因、失效传播和回退；用户明确要求后才修复。

续做任何已有项目时，先读取现有状态、产物和散列，只从 `next_legal_action` 继续；不得重新初始化或覆盖已确认工作。

## 启动检查

1. 识别题目来源、顶层问题、附件、结果填写模板、说明文件、参考资料、当前目录、已有代码与论文；按内容确认角色，文件名只能用于提出候选分类。
2. 若存在项目状态，先运行或读取审计报告，使用派生的 `workflow_state`、`last_valid_gate`、`rollback_target` 和 `next_legal_action` 核对产物散列、approval set 与失效标记；先报告实际状态，再从下一合法动作继续，不重新初始化。
3. 若缺少会改变问题含义的材料，记录为待输入；仍可完成不依赖该材料的安全分析。
4. 选择本轮真正需要的参考文件，不要一次加载全部知识和案例。
5. 给出本轮目标、预计产物、当前假设和下一人工节点。

创建或更新结构化产物时读取 [产物契约](references/contracts.md)。状态转换和回退规则以 [工作流](references/workflow.md) 为准。

## 不可破坏的质量约束

- **先定义问题，后命名算法。** 先写目标、输入输出、变量、单位、约束、机制、数据结构和成功判据。
- **保留简单基线。** 复杂模型必须说明它相对基线增加了什么可验证价值；不能为“创新”堆叠方法。
- **模型、代码、结果和论文使用稳定 ID 连接。** 关键数值只能来自已记录运行；论文不得重新估算或补造结果。
- **论文中的每个数字都要注册。** 百分比、差值、比值、排序变化和敏感性增量也必须成为 result metric，并由 claim 的 numeric assertion 绑定单位与容差；不得只登记原始成本却在论文中额外手算派生数字。
- **原始附件保持不变。** 清洗和转换进入派生数据，并记录来源、规则和散列。
- **输入角色先过门禁。** 只有经内容检查或人工确认的原始数据/派生数据可以进入实验；结果模板、说明、参考资料和旧产物不得冒充观测数据。
- **把假设变成可检查对象。** 每条重要假设必须进入方程、代码、验证或局限说明；无实际作用的假设应删除。
- **条件性验证。** 预测、优化、排序评价、机理仿真和随机算法采用各自适用的检查，不能用一张通用敏感性图代替验证。
- **验证计划必须可执行。** 每项题型风险使用稳定 check ID、适用性、通过规则和失败响应；成功运行必须产生一对一 diagnostic，数值阈值由审计器重新计算。
- **人不能被 Agent 代签。** Agent 可以生成待审批记录，但只有明确的人类确认才能把门禁标为通过。
- **release 必须满足真实人工会签。** Agent 不得代签；具体成员、角色、approval set 和发布快照规则只以 [工作流](references/workflow.md) 与 [团队协作](references/team-collaboration.md) 为准。
- **上游变化使下游证据失效。** 标记旧产物为过期并保留历史，不静默复用，也不批量删除。
- **备用路线不得改写历史。** fallback 切换使用独立、不可变的晋升事件；触发、指纹和重跑规则见 [产物契约](references/contracts.md)。
- **不把案例当答案。** 案例用于发现可迁移结构和缺陷；当前题目的数据、机制和验证结果始终优先。
- **结论按证据分级。** 区分“非常确定”“需验证”“推测”，并说明分级依据。

## 状态与门禁

完整 G0–G7 release 控制只在 `release-strict` 工作中应用。单入口状态、G0–G7 定义、project/run 与 release 的门禁分层、角色要求、回退和失效传播统一由 [工作流](references/workflow.md) 定义。`focused` 不补造完整状态，`competition-fast` 不能把 `WARN` 解释为门禁通过，`release-strict` 只有完整 release 审计才能达到 `SUBMISSION_READY`。

## 按需读取参考文件

- 选择 `focused`、`competition-fast`、`release-strict` 或 `audit`：读取 [工作方式](references/profiles.md)。
- 新建、续做、回退或查询状态：读取 [工作流](references/workflow.md)。
- 创建或检查项目文件、ID、散列与追溯关系：读取 [产物契约](references/contracts.md)。
- 形成候选模型或判断是否套模：读取 [模型选择](references/model-selection.md)。确定 `model_family`、`validation_facets` 与 `task_type` 后，先读其中的验证覆盖表并把对应 check 写入 `validation_plan.checks`；`hybrid` 与 `other` 必须声明非空 facets，不要等审计出现 `BLOCK` 后再补。
- 使用历年题目、优秀论文或案例元数据：读取 [案例使用](references/case-use.md)。只有案例检索或比较任务才读取 `references/cases/`。
- 编写代码、运行实验、检查结果可信度：读取 [验证规范](validation.md)。
- 求解优化与几何/运动仿真问题：按结构选择 [连续黑箱优化](references/recipes/continuous-blackbox-global-optimization.md)、[资源时序 MILP](references/recipes/resource-timing-milp.md) 或 [几何运动覆盖](references/recipes/geometric-kinematic-coverage.md)，并复用其中的统一求解接口与小规模 oracle；论文数值只作参照。
- 处理预测、分类或时间序列任务：读取 [预测与分类](references/recipes/prediction-and-classification.md)，使用嵌套样本外验证、特征可用时点、校准与漂移边界；处理综合评价、权重或排序任务时改读 [评价与排序](references/recipes/evaluation-and-ranking.md)，不要同时加载无关配方。
- 处理 ODE、传热、参数辨识或蒙特卡洛不确定性传播：读取 [ODE、传热与参数辨识](references/recipes/mechanism-ode-and-identification.md)，先验证边界、收敛阶和可识别性；没有有效证书时不得把最细网格值或病态参数点估计升级为论文结论。
- 组织三人分工、交接、冲突解决或审批：读取 [团队协作](references/team-collaboration.md)；评估竞赛时限内的登记成本、维护瓶颈或复跑自动化时，再读 [真实走查维护成本](references/field-run-maintainability.md)。
- 阶段评审、质量评分或决定是否前进：读取 [能力量表](references/rubric.md)。
- 写作、编译、图表和最终 PDF：读取 [论文交付](references/paper-delivery.md)。
- 起草、压缩或审计摘要：在论文证据稳定后读取 [摘要专项指南](references/abstract.md)。
- 只有在测试或迭代本 Skill 本身时读取 [独立前向测试](references/forward-testing.md)；解决具体赛题时不要加载。

只读取当前阶段相关的知识分支。不要因为看到“预测”“评价”或“优化”等词，就加载并套用整套算法目录。

## 执行与停止规则

- 每轮先检查已有证据，再执行当前阶段；不要用新叙述覆盖旧失败。
- 比较模型时固定数据划分、指标和计算预算，避免只对某个模型有利的比较。
- 随机算法按预先声明的种子和重复方案运行，不得反复运行直到出现满意结果。
- 随机情景优化把选方案/调参用的 `selection` 情景与最终结论用的 `holdout` 情景分开登记，二者不得复用情景字节，final claim 只能绑定 holdout metric。
- acceptance rule 必须记录 `registration_timing`。看到相关结果后才形成的规则只能标为 `post_result`，不得在论文中称为“预先”“预注册”或当作确认性检验；若需确认，应建立新的 confirmatory experiment 再运行。
- 同一确定性技术错误可修复后重试；同一根因连续出现两次，或修改会影响已冻结解释和模型时，停止自动迭代并请求人工判断。
- 声明 baseline 时必须实际建立同子问、同指标口径的实验与 eligible result；主结果必须绑定所采用的 baseline result，不能只在文字中写“优于基线”。
- release 的代码闭包、证明包装、构建收据、LaTeX recorder 和最终 PDF 规则按需读取 [产物契约](references/contracts.md) 与 [论文交付](references/paper-delivery.md)，不要在入口重复维护。
- 时间不足时优先保证：题意正确、基线可运行、核心结果经验证、结论可追溯、论文完整。不得用新增算法挤占验证和交付时间。
- 局部任务完成后，说明哪些结论依赖尚未提供的上游材料；不要把局部完成写成全项目通过。
- 发布前必须交付已哈希且可读取的最终 PDF，并让 G6/G7 人工复核绑定它；只有 `.tex`/`.typ` 源码不能标记为 submission ready。

## 交付本轮结果

最终回复应包含：

1. 当前状态和本轮实际完成的产物；
2. 已运行的关键检查及结果；
3. 仍然过期、缺失或待人工批准的项目；
4. 下一合法动作；
5. 对核心结论的置信度评级。
