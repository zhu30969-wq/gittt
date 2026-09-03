# CUMCM 工作方式

仅在选择执行范围、速度与审计强度时读取本文件。这里的工作方式复用现有 `manifest_type: project | run | release` 和 G0–G7 语义，不增加新的审计状态。

## 模式选择

| 模式 | 适用请求 | manifest 映射 | 可达到的最远状态 |
|---|---|---|---|
| `focused` | 只做题意、模型、代码、验证、图表、摘要或论文中的指定阶段 | 可以不初始化；若已有项目则保持其现有类型 | 仅声明本轮局部产物完成 |
| `competition-fast` | 比赛过程中的限时迭代和阶段性交接 | 一般工作用 `project`；需要固化一次运行证据时用 `run` | 最多 `RELEASE_QA`，不能称为可提交 |
| `release-strict` | 构建、冻结和审计最终交付包 | `release` | G0–G7 全部有效后才是 `SUBMISSION_READY` |
| `audit` | 查错、验收、恢复或判断应回退位置 | 保持被审计项目的原类型 | 只报告实际派生状态 |

工作方式是本轮执行策略，不是写入 Schema 的新枚举。不要在 manifest 中写 `focused`、`competition-fast`、`release-strict` 或 `audit`。

## `focused`

- 只完成用户点名阶段，明确哪些上游材料视为给定。
- 不强制初始化完整项目；已有项目先核对当前证据，避免覆盖已确认文件。
- 不生成未实际经历的门禁、review 或 approval set。
- 输出局部结果、依赖条件和接回完整流程所缺的契约。
- 局部产物通过检查不等于全项目通过，不得声称达到 `SUBMISSION_READY`。

## `competition-fast`

competition-fast 只压缩时间与交付节奏；题意、建模、验证、追溯和证据分级标准与 release-strict 完全相同。不得以时间紧为由降低验证强度或省略适用的条件性检查。

- 先保证题意、可运行基线、核心验证、追溯和论文闭环，再考虑增加复杂度。
- `project` 用于组织持续变化的项目证据；`run` 用于固定一次执行所需的代码、环境、输入和输出范围。两者仍接受同一套契约检查。
- 缺少人工 approval set 时，现有审计器按 project/run 语义报告 `WARN`。`WARN` 表示人工复核仍待完成，不表示门禁已通过，更不表示项目可提交。
- 为减少沟通切换，可以把人工复核提示集中为 `G0+G1`、`G2+G3`、`G4+G5`、`G6+G7` 四组；落盘时仍为各门禁使用独立 `approval_set_id` 和独立 review，不得合并签名记录。G4+G5 合并复核时必须先确认 `result_eligibility` 成立，再评估对应 claim，两者不得在同一判断中同时形成；若复核过程中调整了结果口径，相关 claim 必须重新形成，不能沿用或顺延原判断。
- competition-fast 完成后若要发布，必须显式转入 release-strict，补齐 release manifest、完整角色覆盖、当前审批和发布快照。

## `release-strict`

- 使用 `manifest_type: release`。
- 保留现有全部 G0–G7、三人团队角色覆盖、完整 approval set、哈希、依赖闭包、构建收据、最终 PDF 和发布快照要求。
- 人工 PASS 不能抵消 `BLOCK`、`ENV_BLOCK` 或 `STALE`；机器 PASS 也不能替代数学与语义复核。
- 只有 release 审计中 G0–G7 全部有效时才可派生 `SUBMISSION_READY`。

## `audit`

- 默认只读，不刷新散列、不运行实验、不编译论文、不改变 review。
- 报告证据位置、严重度、根因、失效传播、`workflow_state`、`last_valid_gate`、`rollback_target` 和 `next_legal_action`。
- 只有用户明确要求修复时才编辑；修复后重新运行受影响范围的检查。

## 模式切换

- `focused` 或 `competition-fast` 转入 `release-strict` 时，不补造历史审批；从当前证据重新审计并收集所缺门禁。
- `release-strict` 中出现上游变化时，按工作流回退并使下游证据失效，不能临时降级为 competition-fast 来保留提交状态。
- 任意模式都可进入 `audit`；审计完成后是否修改由用户决定。
