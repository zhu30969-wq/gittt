# CUMCM 数学建模 Skill 本地调用指南

## 入口位置

- Skill 名称：`cumcm-modeling`
- 本地入口：[`cumcm-modeling/SKILL.md`](cumcm-modeling/SKILL.md)
- 本机路径示例：`D:\WUMIAN\ZHUOMIAN\数学建模agent\cumcm-modeling\SKILL.md`
- 适用范围：全国大学生数学建模竞赛（CUMCM／国赛）的题意分析、模型设计、代码实验、验证、图表、中文论文、续做恢复和最终审计。
- 不适用范围：COMAP MCM/ICM、普通数学作业及与 CUMCM 无关的数据分析。

以后调用时，先完整读取入口 `SKILL.md`，再按照本轮任务选择参考文件；不要一次加载全部资料。

Codex 官方本地发现位置为：

- 用户级：`$HOME/.agents/skills/cumcm-modeling`
- 仓库级：`$REPO_ROOT/.agents/skills/cumcm-modeling`

以上是发现目录说明，本指南不执行安装、复制或目录联结。本机路径只用于当前工作区直接读取，不是可移植安装方法。

## 最短调用指令

将下面内容直接发送给 Codex：

```text
请使用本地 cumcm-modeling Skill 完成本轮任务。先完整读取：
D:\WUMIAN\ZHUOMIAN\数学建模agent\cumcm-modeling\SKILL.md

根据任务选择 focused、competition-fast、release-strict 或 audit；只按需读取入口链接的参考文件。若目录中已有项目状态，先审计并从 next_legal_action 继续，不重新初始化或覆盖已确认文件。

本轮任务：<填写赛题、项目路径和具体目标>
```

如果当前环境已注册该 Skill，也可以先写 `$cumcm-modeling`，但仍应提供赛题材料、项目路径和期望交付物。

## 调用模式

### 1. `focused`

适用于只做题意、模型、代码、验证、图表、摘要或论文中的指定阶段。

```text
请使用 $cumcm-modeling，以 focused 模式只完成：<阶段>。
输入材料：<路径或说明>
项目路径：<路径>
期望输出：<文件或结论>
核对该阶段所需的上游证据；不要强制初始化完整项目，不生成未经历的门禁或审批，也不要把局部完成写成 SUBMISSION_READY。
```

### 2. `competition-fast`

适用于比赛过程中快速建立可运行基线、核心结果、验证与论文闭环。

```text
请使用 $cumcm-modeling，以 competition-fast 模式推进：<项目路径>。
本轮目标与截止时间：<目标和时间>
使用 project 或 run 语义，优先保证题意、baseline、核心验证、数字追溯和最小完整论文。可以按 G0+G1、G2+G3、G4+G5、G6+G7 集中提示人工复核，但落盘仍分别记录门禁。缺少 approval set 的 WARN 不等于通过或可提交。
```

### 3. `release-strict`

适用于冻结最终 PDF、源码、代码、结果、证明和发布快照。

```text
请使用 $cumcm-modeling，以 release-strict 模式审计并完善：<项目路径>。
使用 manifest_type: release，保留完整 G0–G7、三人角色覆盖、approval set、哈希、构建收据、最终 PDF 和 release snapshot。只有全部 release 门禁有效时才可报告 SUBMISSION_READY；不要代签人工 review。
```

### 4. `audit`

适用于查错、验收、复核证据链或判断能否提交。

```text
请使用 $cumcm-modeling，以 audit 模式只读检查：<项目路径>。
按 G0–G7 给出 PASS、BLOCK、ENV_BLOCK、STALE 或 NOT_APPLICABLE，并列出证据、严重度、根因、失效传播和应回退阶段。除非我另行要求修复，否则不要修改文件。
重点检查：模型—代码—实验—结果—声明—图表—论文追溯、数值与单位一致性、构建收据、发布快照和三人 approval set。
```

续做任何模式下的已有项目，都应先报告 `workflow_state`、`last_valid_gate`、`rollback_target` 与 `next_legal_action`，再从下一合法动作原地继续。

## 门禁分层

- `manifest_type: project` 或 `run` 缺少人工 approval set 时为 `WARN`：表示仍待人工复核，不能解释成门禁通过或可提交。
- `manifest_type: release` 缺少完整 approval set 时为 `BLOCK`：只有 release-strict 可以在 G0–G7 全部有效后达到 `SUBMISSION_READY`。
- 自动检查不提供人类身份认证，也不能证明模型数学正确；Agent 不得替人把 review 写成 PASS。

## 任务信息清单

调用时尽量同时提供：

1. 赛题题面、附件及结果填写模板的路径；
2. 当前项目目录，以及是否允许创建或修改文件；
3. 本轮目标和截止时间；
4. 已有模型、代码、结果、图表和论文；
5. 需要使用的论文引擎：LaTeX、Typst 或仅 Markdown；
6. 希望本轮停在哪个人工复核点；
7. 是否只审计，或允许发现问题后直接修复。

缺少不会改变问题含义的信息时，可以先做安全分析；缺少会改变模型定义、数据角色或目标函数的信息时，应记录为待输入，不得猜测后写入正式结果。

## 按需参考路由

| 当前任务 | 读取文件 |
|---|---|
| 选择四种运行模式及切换 | [`profiles.md`](cumcm-modeling/references/profiles.md) |
| 新建、续做、状态恢复、回退 | [`workflow.md`](cumcm-modeling/references/workflow.md) |
| 结构化产物、ID、散列、Schema、证据关系 | [`contracts.md`](cumcm-modeling/references/contracts.md) |
| 候选模型、baseline、风险探针、fallback | [`model-selection.md`](cumcm-modeling/references/model-selection.md) |
| 数据处理、代码、实验与条件性验证 | [`validation.md`](cumcm-modeling/validation.md) |
| 三人协作、交接与 approval set | [`team-collaboration.md`](cumcm-modeling/references/team-collaboration.md) |
| 论文、图表、编译、PDF 与发布包 | [`paper-delivery.md`](cumcm-modeling/references/paper-delivery.md) |
| 摘要起草、压缩或审计 | [`abstract.md`](cumcm-modeling/references/abstract.md) |
| 历年案例的安全使用 | [`case-use.md`](cumcm-modeling/references/case-use.md) |
| 建立经原文核对的案例卡 | [`cases/_TEMPLATE.md`](cumcm-modeling/references/cases/_TEMPLATE.md) |
| Skill 本身的迭代测试 | [`forward-testing.md`](cumcm-modeling/references/forward-testing.md) |

## 核心能力摘要

当前版本提供：

当前模板生成的结构化契约版本为 `2.3.0`；工具继续读取和审计合法 `2.x.x`（包括旧 `2.0.0`、`2.0.1`、`2.1.0` 与 `2.2.0`），无参数重复初始化不会自动迁移版本或改写旧项目字节，`1.x` 项目须先迁移。旧 `2.0.1` experiment 缺少 `decision_timing` 时，审计器会返回明确的 `DECISION_TIMING_REQUIRED` finding；旧 `2.1.x` optimization 模型缺少新引入的 `objective_reconciliation` 时，由 G2 返回明确的 `OBJECTIVE_RECONCILIATION_REQUIRED` finding；旧 `2.2.x` 项目缺少 `scenario_sets` 或 optimization 的 `holdout_leakage` 检查时仍可读取，由审计器返回明确的 `SCENARIO_SETS_LEGACY_MIGRATION_REQUIRED` / `SCENARIO_HOLDOUT_CHECK_REQUIRED` finding。以上缺项都应依据真实语义显式补值，工具不会静默推断或自动改写。

- `INTAKE → G0–G7 → SUBMISSION_READY` 单入口状态机；
- 主模型、可信 baseline、风险探针和不可变 fallback 晋升；
- 三名成员对同一证据快照的多签 approval set；
- SHA-256、CAS、侧车锁、失效传播和确定性恢复；
- 模型—实验—结果—声明—图表—论文联合证据链；
- 11 个严格 JSON Schema 与本地 `$ref` 解析；
- LaTeX/Typst lint、构建收据、PDF/代码/证明包装闭包；
- TOCTOU、逻辑路径、reparse/symlink、锁文件与重复报告防护；
- held-out 恢复测试和 Linux/Windows 持续集成。

这些结构化检查用于尽早发现证据断链和实现错误，不能替代数学正确性、模型适用性及结论真实性的实质判断。

## 本地维护与验证

修改 Skill 后至少运行：

```powershell
python -X utf8 C:\Users\WUMIAN\.codex\skills\.system\skill-creator\scripts\quick_validate.py cumcm-modeling
python -X utf8 -m unittest discover -s tests
python -X utf8 evals/run_held_out_resume.py
git diff --check
```

关键版本发生变化时，还应执行 11 个 Schema 自校验、论文 lint 正反例和 G0–G7 合成发布审计，并确认最终状态为 `SUBMISSION_READY`。

## 最近已部署基线

- Git 提交：`fac0da07fbf4a89945640077ee5142369347dbea`
- GitHub：<https://github.com/zhu30969-wq/gittt>
- CI：<https://github.com/zhu30969-wq/gittt/actions/runs/33233069415>
- 基线验证结果：Linux Python 3.10、Linux Python 3.12 和 Windows 全部通过。

本文件当前保持本地未跟踪状态，不受上述提交和 CI 覆盖。提交号只代表最近一次已部署基线；本地修改必须以当前工作树和本轮实跑结果为准。
