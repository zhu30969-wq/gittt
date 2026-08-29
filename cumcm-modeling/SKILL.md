---
name: cumcm-modeling
description: "Solve, continue, or audit China Undergraduate Mathematical Contest in Modeling (CUMCM/全国大学生数学建模竞赛/国赛) projects, including problem framing, model design, code and experiments, validation, figures, Chinese papers, and three-person handoffs. Use when the user explicitly names CUMCM/国赛 or supplies an identifiable CUMCM problem/project; do not use for COMAP MCM/ICM, generic math homework, or unrelated data analysis."
---

# CUMCM 数学建模

把一道 CUMCM 赛题推进为可解释、可运行、可复现、可审查的模型、结果和中文论文。Skill 负责组织推理和交付，不替人宣称模型必然正确，也不把算法数量或论文篇幅当作质量。

## 先确定工作方式

根据请求选择一种方式，不要强迫局部任务运行完整流水线：

- **完整推进**：从题面和附件开始，依次完成建模、实验、验证和论文。
- **续做项目**：先读取现有状态、产物和散列，只从下一个合法状态继续；不得重新初始化或覆盖已经确认的工作。
- **聚焦阶段**：只完成用户点名的题意分析、模型设计、代码、验证、图表或论文阶段，并明确缺失的上游依据。
- **审计评审**：默认只读，给出证据化问题、严重度和应回退的阶段；只有用户同时要求修复时才编辑。

再记录运行模式：

- **训练模式**允许更宽的候选比较、盲评、反事实实验和赛后复盘。
- **正式赛模式**以截止时间、阶段冻结和完整交付为优先，达到可靠基线后再增加复杂度。

两种模式采用相同的建模、验证和证据标准；正式赛模式只调整时间和交付节奏，不降低可用能力。

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
- **release 使用三人多签 approval set。** 不同成员必须以稳定 `member_id` 对同一证据快照签核；G7 共同绑定当前 `snapshot:release`，单人 PASS 不能冻结发布包。
- **上游变化使下游证据失效。** 标记旧产物为过期并保留历史，不静默复用，也不批量删除。
- **备用路线通过不可变事件晋升。** 不改写旧主模型或条件 fallback；以绑定原路线、fallback、partial 触发结果及当前指纹的 `model_promotion` 记录切换。partial 结果只有在运行、输入输出、指标、完整诊断集、指纹和接受规则全部通过，且仅有预声明触发项 BLOCK 时才能控制路线；它不能支持最终结论。一个 fallback 只能由一个不可变事件激活，晋升后必须重新运行并产生 eligible result。
- **不把案例当答案。** 案例用于发现可迁移结构和缺陷；当前题目的数据、机制和验证结果始终优先。
- **结论按证据分级。** 区分“非常确定”“需验证”“推测”，并说明分级依据。

## 单入口状态机

完整路径只有一个入口：

~~~text
INTAKE → WAIT_G0
  → FRAMING → WAIT_G1
  → MODELING → WAIT_G2
  → EXPERIMENT_DESIGN → WAIT_G3
  → COMPUTING → VALIDATING → WAIT_G4
  → CLAIMING → WAIT_G5
  → WRITING → FINAL_QA → WAIT_G6
  → RELEASE_QA → WAIT_G7
  → SUBMISSION_READY
~~~

验证失败必须按原因回退：

- 题意、单位、字段或数据定义错误：回到 FRAMING。
- 假设、变量、目标、约束或模型结构错误：回到 MODELING。
- 实现、求解器、实验或绘图错误：回到 COMPUTING。
- 只有表述、排版或引用错误：回到 WRITING。

八个人工门禁依次确认输入、题意、模型、实验、结果、结论与图表、论文、发布包。门禁内容和散列失效规则见 [工作流](references/workflow.md)；三人签核与交接见 [团队协作](references/team-collaboration.md)。

## 按需读取参考文件

- 新建、续做、回退或查询状态：读取 [工作流](references/workflow.md)。
- 创建或检查项目文件、ID、散列与追溯关系：读取 [产物契约](references/contracts.md)。
- 形成候选模型或判断是否套模：读取 [模型选择](references/model-selection.md)。
- 使用历年题目、优秀论文或案例元数据：读取 [案例使用](references/case-use.md)。除非请求本身是案例比较，否则先保存独立的问题结构和初步候选，再检索案例。
- 编写代码、运行实验、检查结果可信度：读取 [验证规范](validation.md)。
- 组织三人分工、交接、冲突解决或审批：读取 [团队协作](references/team-collaboration.md)。
- 阶段评审、质量评分或决定是否前进：读取 [能力量表](references/rubric.md)。
- 写作、编译、图表和最终 PDF：读取 [论文交付](references/paper-delivery.md)。
- 只有在测试或迭代本 Skill 本身时读取 [独立前向测试](references/forward-testing.md)；解决具体赛题时不要加载。

只读取当前阶段相关的知识分支。不要因为看到“预测”“评价”或“优化”等词，就加载并套用整套算法目录。

## 执行与停止规则

- 每轮先检查已有证据，再执行当前阶段；不要用新叙述覆盖旧失败。
- 比较模型时固定数据划分、指标和计算预算，避免只对某个模型有利的比较。
- 随机算法按预先声明的种子和重复方案运行，不得反复运行直到出现满意结果。
- acceptance rule 必须记录 `registration_timing`。看到相关结果后才形成的规则只能标为 `post_result`，不得在论文中称为“预先”“预注册”或当作确认性检验；若需确认，应建立新的 confirmatory experiment 再运行。
- 同一确定性技术错误可修复后重试；同一根因连续出现两次，或修改会影响已冻结解释和模型时，停止自动迭代并请求人工判断。
- 声明 baseline 时必须实际建立同子问、同指标口径的实验与 eligible result；主结果必须绑定所采用的 baseline result，不能只在文字中写“优于基线”。
- release 中每个支持结论的实验 `code_files` 都必须作为 required code deliverable 交付；只交入口脚本而遗漏其辅助模块不能通过。
- 理论证明必须是可读取、哈希一致且人工复核的结构化论证：显式绑定 claim ID 与登记命题，包含推理步骤和结论；`formally_proved` 的复核回执还要绑定准确 proof SHA-256。PDF 必须有可提取文本。证明必须进入经实际构建收据/recorder 验证的源码资源闭包或 required appendix；单词、空白页或仅在收据中手填但编译器未消费的路径不能替代计算证据。
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
