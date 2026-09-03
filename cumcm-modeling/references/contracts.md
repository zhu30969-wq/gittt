# CUMCM 科学证据契约

本文件定义项目中可机器检查的事实边界。契约的作用是让题意、模型、实验、结果、结论和图表之间具有稳定、可复核的证据链；契约通过不等于数学推导正确、模型适切或结论真实。

## 契约格式

项目契约以 YAML 编写，按 `references/schemas/` 中的 JSON Schema Draft 2020-12 校验。所有核心文件包含以下公共字段：

- `schema_version`：当前只接受 `2.x.x`。
- `kind`：选择对应 schema。
- `id`：稳定 typed ID，例如 `model:main`；移动文件时不改变 ID。
- `revision`：内容发生实质变化时递增。
- `lifecycle_status`：`draft`、`reviewed`、`frozen`、`stale` 或 `superseded`。
- `depends_on`：直接上游 artifact ID；不填写传递依赖。
- `provenance`：记录内容由人、agent、脚本或混合过程产生。

关键对象默认拒绝未知字段。需要扩展时使用顶层 `extensions`，并在项目说明中解释扩展语义，不要利用扩展绕过必填证据。

## ID、路径和哈希

ID 采用 `类别:局部标识`，只使用小写字母、数字、点、下划线和连字符。引用应指向 ID，不应依赖文件名或论文行号。

路径必须相对于项目根目录，统一使用 `/`，不得包含盘符、绝对路径、反斜杠或 `..`。运行时还会解析 symlink，防止路径表面合法但实际逃逸项目根目录。

SHA-256 对原始字节计算。换行符、图片元数据或 PDF 构建时间变化都会改变哈希；这表示文件已变化，不表示科学内容一定变化。反过来，哈希相同只能证明字节相同，不能证明来源真实。

## 核心证据链

项目应形成以下可达路径：

```text
question → model → experiment → result → claim → figure/table/paper
```

审计器检查：

- ID 是否唯一，引用是否存在，依赖图是否有环。
- 每个题目子问是否至少被一个模型覆盖。
- result 是否记录当前 experiment/model/data/code 的指纹。
- final claim 是否引用成功且未过期的 result 或明确的证明文件。
- derived figure 是否记录源结果、源文件、生成脚本和输出哈希。
- manifest 中的 artifact 路径、ID、kind 和哈希是否与实际文件一致。

审计器不能检查：

- 子问是否被语义上正确理解。
- 模型是否适合真实问题。
- 公式推导、证明或代码算法是否正确。
- 数据来源陈述是否真实。
- 结论是否存在遗漏的反例或混杂因素。

## 各契约职责

### `problem_spec`

保存题面文件哈希、子问、要求输出、数据来源、假设、歧义和约束。规则文件可按项目需要登记，但不作为建模能力的强制输入。题面给定条件与自设假设必须区分。高严重度歧义必须有解决方案或并行分支，不能静默选择解释。

每个 `data_assets` 条目还必须显式声明：

- `role`：`raw_data`、`result_template`、`instruction`、`reference`、`generated_intermediate` 或 `other`；
- `classification_basis`：角色来自明确声明、内容检查、文件名启发式还是人工确认；
- `usable_for_modeling`、`immutable_raw`、用途和关联子问；
- 不可用于建模时的 `exclusion_reason`。

文件名启发式只能生成候选分类，不能把文件自动批准为模型输入。结果填写模板、说明文件和参考资料不得进入 `experiment.data_refs`；正式原始数据被批准后必须保持字节不变，清洗结果登记为新的派生资产。审计器还会阻断同一组 bundled 字节被多个资产 ID 重复登记。

### `model_spec`

保存模型覆盖的子问、符号、方程的 `defines/uses`、数据绑定、算法、验证计划、适用范围、失败模式和备选模型。原始 LaTeX/Typst 公式不能被 schema 语义验证，因此符号闭包只是一项一致性检查。

`method_selection` 把路线选择变成契约：记录选中、条件保留或拒绝状态，选择理由，基线是 `required` 还是有理由 `waived`，以及被拒或备用方案。被选基线必须是覆盖同一子问的 `role: baseline` 模型，并进入 primary model 的 `depends_on`，否则基线变化不会正确使下游证据失效。G2 人工 review 必须绑定所有当前 model spec，因此基线豁免不是 Agent 自行放行。

`validation_plan.checks` 不再接受一组无法执行的自由文本。每项检查具有稳定 ID、类型、适用性、阻断等级、过程、通过规则、可选数值阈值和失败响应。审计器要求各模型族至少显式考虑下列检查面；确实不适用时可以登记 `not_applicable` 及理由，但 primary model 至少保留一项实际检查：

| 模型族 | 必须显式考虑的检查 |
|---|---|
| descriptive | 输入完整性 |
| statistical | 残差诊断、不确定性 |
| prediction | 基线、泄漏、预测误差、不确定性 |
| optimization | 约束可行性、求解状态/最优性、固定主决策后的目标对账、基线、情景泄漏、敏感性 |
| simulation | 边界情形、收敛、守恒/平衡、数值稳定性 |
| evaluation | 基线、敏感性、排名稳定性 |
| causal | 可识别性、反证检查、不确定性 |

`hybrid` 和 `other` 不能由单一标签决定检查面，必须用非空 `validation_facets` 选择上述一个或多个模型族；审计器对所选 facet 的必需检查取并集。其他模型族省略该字段时等价于只选择自身模型族。G2 人工 review 仍须核对这些结构化检查是否覆盖真实失败方式。

从 `2.4.0` 起，任何带 `numeric_assertions` 的 final claim 只要直接引用一个模型，或引用由该模型实验产生的 result，该模型的 `formulation.equations`、`objectives`、`constraints` 三者至少有一项非空；空骨架不能支撑定量论文结论。审计器在 G2 报告模型 ID 和相关 claim ID，但不会要求纯描述或纯统计模型虚构决策变量。

模型族还要与已经登记的证据一致。若某个绑定实验包含 `direction: minimize/maximize` 的 metric，或绑定 result 的 diagnostic 出现成对 `objective_incumbent/objective_bound`、`objective_reconciliation`，则 `effective_validation_facets` 必须包含 `optimization`。实验信号在 G3 检查，结果信号在 G4 检查；预先声明更严格的 `optimization` facet 而尚未产生这些信号是合法的，不作反向推断。

优化模型的启发式结果默认只能支持“当前找到的最好解”。若要使用“全局最优”，必须提供严格证明、可核验证书或有效的上下界，并经过人工复核。

`objective_reconciliation` 与 `solver_optimality` 正交：前者固定已经输出的主决策变量，再用独立代码重新优化其余辅助变量，检查当前实现是否遗漏了可改善的最优响应；后者只描述求解器对其所接收模型的求解质量。对账 diagnostic 必须声明非空且不相交的主决策/辅助变量标识，绑定实验中独立于主入口的代码文件及 SHA-256，记录求解方法、目标 metric、原目标、最优响应目标、方向化 `repair_gain` 和预登记容差。审计器只能强制这些结构并重算差值，不能证明独立脚本确实实现了正确的重新优化。

### `model_promotion`

保存一次不可变的 fallback 晋升事件。不得把原 primary model 改写成 rejected，也不得把 fallback model 原地改成 primary；事件必须精确绑定原 effective primary、预先声明且仍为 `role: fallback / decision: conditional` 的模型，以及一个保留的 partial trigger result。

触发 diagnostic 必须对应原模型预先声明的 blocking check，条件检查必须实际激活，数值失败必须能从该次运行已哈希的输入、输出或日志重新计算。partial trigger 还必须通过完整 result 契约：预定运行框架、输入输出、全部指标、除精确触发项外的完整诊断集、依赖闭包指纹和接受规则均有效；缺少另一项 required diagnostic、出现第二个 active failure 或用手填状态替代实际字节都会使晋升失效。事件的 direct dependencies 和 fingerprints 只包含原 primary、fallback 与 trigger result，`promoted_at` 不得早于触发运行完成时间。

晋升后必须建立依赖该事件和 fallback model 的新 experiment；新运行开始时间必须晚于 `promoted_at`。partial trigger 只作为路线控制证据，始终保持 `result_eligibility=false`，不能支持 final claim；只有新路线产生 eligible result 后，完整晋升事务才可标为 verified。同一 fallback 只能由一个不可变事件激活，第二个事件也不能再次替换已经失效的 effective route，避免事件映射被覆盖。

### `experiment`

保存执行参数数组、相对工作目录、代码和环境哈希、随机种子、切分、基线、指标、接受规则、输出及比较器。`data_refs` 只能引用已经批准用于建模的数据；`baseline_refs` 必须与所选模型的 baseline policy 一致。基线模型还要有同子问、同指标定义的可比实验，不能只在文字中出现。

`mode` 表示实验在证据流程中的用途（探索、确认或验证），`decision_timing` 表示决策相对于不确定信息何时作出，二者不重叠。`here_and_now` 在不确定性揭示前固定决策，`wait_and_see` 在情景揭示后再决策，`recourse` 允许获得部分信息后采取补救决策。跨结果的差值、排名或基线优越性判断只能比较相同 `decision_timing` 的实验。

随机情景优化必须在 `scenario_sets` 中分别登记 `role: selection` 与 `role: holdout` 的非空情景集，并为每组保存种子、生成器 SHA-256 和情景字节 SHA-256。selection 用于选方案或调参，holdout 只用于冻结方案后的独立评价；两类集合不得复用相同的 `scenario_sha256`。每个定量 metric 用 `scenario_set_ref` 绑定其实际来源，final claim 只能引用来自本实验 holdout 情景集的 metric。确定性优化显式写 `scenario_sets: []`，并把 `holdout_leakage` 记为 `not_applicable` 且说明理由，不能伪造情景集。

`code_files` 必须包含实际入口和本次实验依赖的项目代码；任何支持 release claim 的 eligible result，其全部 `code_files` 都必须在 manifest 中登记为 `required: true / role: code` 的 deliverable，不能只交入口脚本。

每条接受规则用 `registration_timing` 区分看到相关结果前登记的 `pre_result` 与看到结果后形成的 `post_result`；后者只能作为探索性判断，不能改写成预注册证据。`network_access` 仅记录复现条件，不用于规定参赛者能否联网，也不会自动禁用检索。

探索性实验允许不设置接受阈值，但不得在看到结果后把探索性实验追记为预注册验证。

### `results`

保存一次运行实际使用的命令、环境、指纹、输入输出哈希、指标、不确定性、诊断和失败信息。结果中的 `output_ref` 指向实验预先声明的输出 ID，不重复定义新 ID。失败运行应保留。缺失数值用 `null` 和 `missing_reason` 表示，不得用 0、NaN 或无穷值伪装。

成功结果必须记录实际完成的重复次数，并为每项 `required` 或 `conditional` validation check 提供唯一的结构化 diagnostic。diagnostic 绑定 `check_ref`，保存类型、状态、严重度、过程、观察、结论和证据文件；若计划预先声明了数值阈值，审计器用实际观测值、单位和运算符重新计算 PASS/BLOCK，不能接受手填状态。缺失 required diagnostic、阻断性诊断未通过、重复次数不足或 bundled 输入没有进入运行输入清单时，该结果不能支持 final claim。

求解器诊断可成对填写 `objective_incumbent` 与 `objective_bound`，两者都是带单位的 measurement，任一出现时另一项也必须出现。incumbent 表示当前可行候选的目标值，bound 表示对全局最优值有效的界；最小化问题的质量区间为 `[bound, incumbent]`，最大化问题为 `[incumbent, bound]`。候选区间重叠时不能据此声称严格排名或优于关系。

目标对账诊断使用 `objective_reconciliation` 对象。`objective_metric_ref` 确定目标方向和单位；最大化的 `repair_gain = best_response_objective - solver_objective`，最小化则反向相减，正值表示固定主决策后仍存在可修复改善。审计器还要求 `solver_objective` 等于该 result 已登记的 metric，并按 `max(absolute_tolerance, relative_tolerance × max(|solver_objective|, |best_response_objective|))`（只使用实际提供的容差项）判断差值幅度。`registration_timing: post_result` 沿用接受规则的证据限制：探索性运行只给出事后提示，确认性或验证性运行不能据此形成 eligible 证据。

当主方法声明基线时，基线必须有可比且 eligible 的结果。主结果还必须在 `depends_on` 和完整指纹闭包中绑定实际采用的 baseline result；否则基线结果变化不会传播失效，主结果也不得进入结论。

### `claims`

保存准备进入论文的准确陈述、证据、假设、适用范围、限制、反证和人工复核。论文出现的每个定量值——包括百分比、差值、比值、排名变化和敏感性增量——都必须先成为结果中的唯一 metric，再由 `numeric_assertions` 显式绑定单位、展示值和容差。图表是结果的表现形式，不是独立原始证据。因果、全局最优和形式证明类结论必须人工复核；schema 只能检查已登记证据，不能发现作者遗漏登记的数字。

final theoretical claim 的 epistemic status 只能是 `analytically_derived` 或 `formally_proved`，并必须绑定非空、可读取、哈希一致且人工 PASS 的 proof artifact。可检查的证明文本必须显式包含 claim ID、登记的命题/结论、Proof/Derivation/Argument 结构、至少一个推理步骤和明确结论；单词、标题或任意非空文件不能形成纯证明 release。PDF 证明还必须有可提取文本，只有空白页或不可检查的内容流不构成证明。`formally_proved` 的 `human_review` rationale 必须同时引用 claim ID 与该 proof artifact 的准确 SHA-256，使验证回执绑定具体命题和字节。

证明还必须进入经验证的 `paper_build` 实际 source/resource/recorder 闭包，或成为 manifest 中 required appendix deliverable；游离文件和只在无效收据中手填的路径不能构成 release 证明路径。

### `figures`

保存 derived、conceptual 或 external 图的来源。定量图必须追溯到 eligible 结果和生成程序；release 中图表引用的 claim 必须是 final。概念图不得作为数值结论的唯一证据；外部图必须登记来源与许可。机器不能可靠判断截断坐标、颜色、标注或视觉叙事是否误导。

### `paper_build`

保存论文构建收据，而不是只登记一个已经存在的 PDF。收据绑定 canonical source entrypoint、完整项目内 source/resource files、claims/figures 及其依赖闭包指纹、编译器版本、命令、工作目录、开始/完成时间、非空日志、依赖记录和最终 PDF。

构建 `cwd` 必须等于论文源码目录，命令必须真实消费 source entrypoint 并输出 `entrypoints.pdf`。LaTeX 构建使用 recorder 依赖日志并保持源码默认 PDF 位置；Typst 命令必须显式写入登记的 PDF。不得用 output-directory 等参数改变默认输出位置，也不得启用 shell execution。构建时间必须晚于最新 fallback promotion；收据中的 PDF 路径与哈希必须等于 release PDF。非空日志仍会检查 LaTeX、Typst 和常见 wrapper 的明确失败诊断；日志记录失败时，即使手填 `exit_code: 0` 且 PDF 可读，也不得签发 `PAPER_BUILD_RECEIPT_VERIFIED`。

只有源码、静态资源与 LaTeX recorder 实际输入闭包和登记哈希完全一致时，收据才会验证；证明打包状态在此后计算，不能由 receipt 的声明列表提前产生 PASS。

### `manifest`

保存 artifact 文件清单、哈希、直接依赖、环境文件和交付物。manifest 不对自身做循环哈希。`competition_profile` 是可选格式检查配置；是否启用由项目决定，内容应依据当年官方规则人工确认。

release 必须同时登记 canonical `.tex`/`.typ` 论文源码和唯一的最终 PDF：`entrypoints.pdf` 要精确匹配一个 `required: true`、`role: paper_pdf`、`media_type: application/pdf` 的 hashed deliverable。只有源码、没有可读取最终 PDF 的项目不能通过 G6/G7；逐页视觉质量仍由绑定该 PDF 指纹的人工 review 判断。

release 只使用 `lifecycle_status` 为 frozen/reviewed 且 required 的 active artifacts；draft、stale、superseded 和可选历史产物可以保留，但 active artifact 不得依赖或引用它们。发布包至少包含一个 active problem 和一个 final claim。

### `gate_review`

保存团队成员、人工或混合门禁评审及其 approval set。每条 review 以 `member_id` 绑定已登记成员，并以相同的 `approval_set_id` 聚合为同一门禁、同一证据快照上的多签决定；同一成员不能通过重复记录满足人数要求。release 的关键门禁必须满足当前三人多签规则，单条 PASS 或同一人签完全部门禁不能替代交叉复核。

每名签核者必须绑定当时的完整门禁证据指纹。G7 approval set 还必须绑定审计器计算的 `snapshot:release`，该摘要覆盖最终 release manifest 的发布范围和当前包索引；release 范围变化后，旧 G7 多签失效。上游文件改变后，旧评审只能视为 `STALE`。评审字段存在不代表评审本身可靠，审计器不会允许人工 `PASS` 覆盖自动发现的硬错误。

## 生命周期和失效传播

审计器按依赖图计算反向闭包：

- 题面、规则或数据改变：问题规格及全部下游需要复核。
- 模型或代码改变：实验、结果、结论、图和论文需要复核。
- 实验配置或环境改变：结果及下游需要复核。
- 结果改变：结论、图和论文需要复核。
- 仅格式修改：通常只影响论文门禁；若结论措辞改变，则必须回到结论门禁。

科学结果不理想不是 schema 错误。不得为了通过门禁自动降低阈值、扩大容差、删除失败运行或静默切换模型。预先登记的 fallback 被触发时，原 primary 与 fallback model spec 保持不变，通过独立 `model_promotion` 事件切换 effective route，并为晋升后的 experiment、result、claim 和论文构建建立新 revision。只有模型定义本身发生实质修改时才新建 model spec revision；不得用 revision 改写或掩盖原选择、触发结果和晋升事件。

## 状态词汇

所有审计和门禁报告只使用：

- `PASS`
- `WARN`
- `BLOCK`
- `ENV_BLOCK`
- `STALE`
- `NOT_APPLICABLE`

这些状态表示流程和证据就绪度，不得改写为“数学正确性已验证”。
