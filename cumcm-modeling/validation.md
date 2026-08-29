# 验证与阶段门禁

## 工具入口

安装依赖：

```bash
python -m pip install -r scripts/requirements.txt
```

初始化新项目：

```bash
python scripts/init_project.py ./work --project-id project:cumcm-2026-a
```

初始化器只创建缺失文件。目标文件已存在时返回 `NOT_APPLICABLE`，不会覆盖、合并或刷新其内容。模板中的题面、代码、环境和结果是明确的占位内容，填写并重新锁定 manifest 前，审计出现 `BLOCK` 或 `STALE` 是预期行为。

查看 manifest 与当前文件的差异，或在明确确认后刷新其顶层 artifact 哈希：

```bash
python scripts/manifest.py ./work
python scripts/manifest.py ./work --write --expected-manifest-sha256 <sha256>
```

默认调用完全只读。`--write` 只更新 `manifest.artifacts[*].sha256` 并增加 revision；它不会刷新结果指纹、人工审批指纹或嵌套数据/代码哈希，避免把已经过期的科学证据“洗成最新”。

只读审计：

```bash
python scripts/audit_project.py ./work
python scripts/audit_project.py ./work --json-report ./audit-001.json
```

报告路径已存在时脚本拒绝覆盖。审计器不运行实验、不修改哈希、不修复模型，也不编译论文。

JSON 报告除 G0–G7 发现外，还给出 `workflow_state`、`last_valid_gate`、`rollback_target` 和非空 `next_legal_action`。这些值由当前证据重新推导，不是另一份需要手工同步的 memory；续做项目应从该动作继续。G7 的当前 approval set 必须绑定审计器计算的 `snapshot:release`，因此改变 release 范围或包索引会使旧最终会签失效。

论文源码和可选 PDF 的只读 lint：

```bash
python scripts/lint_paper.py ./work --engine latex --source paper/main.tex
python scripts/lint_paper.py ./work --engine typst --source paper/main.typ \
  --pdf paper/main.pdf --max-pages <project-specific-limit>
```

lint 检查占位符、跨引擎语法污染、危险 LaTeX shell escape、缺失 include/图片、LaTeX 引用与文献键、final claim marker、登记图表引用和 PDF 可读性。页数只有在项目显式传入 `--max-pages` 时检查，不硬编码永久规则；PDF 视觉叙事和公式语义仍需逐页人工复核。release 必须把最终 PDF 作为 `paper_pdf` deliverable 登记并绑定 G6/G7 review，只有源码不能发布。

记录人工门禁：

```bash
python scripts/record_gate_review.py ./work \
  --gate G2 --decision PASS --reviewer "reviewer-name" \
  --rationale "已逐项核对符号、模型假设、推导和适用范围" \
  --evidence model:main --fingerprint model:main=<sha256>
```

该脚本执行显式的追加操作，保留原有 review，采用临时文件完成原子替换，并检查可选的期望文件哈希以避免并发覆盖。

## 门禁定义

| 门禁 | 自动证据 | 必须人工复核 | 失败回退 |
|---|---|---|---|
| G0 输入 | 题面、输入角色、分类依据、用途、路径和哈希 | 文件是否正式，结果模板/说明是否已排除 | 输入收集 |
| G1 问题 | 子问、交付物、假设、歧义、单位 | 题意和任务分解 | 问题规格 |
| G2 模型 | 符号闭包、方法选择、基线策略、题型验证覆盖 | 模型适切性、推导、豁免理由、可识别性 | 模型规格 |
| G3 实验 | 代码/环境哈希、批准数据、种子、切分、可比基线、指标、输出 | 实验设计、基线公平性、泄漏风险 | 实验规格 |
| G4 结果 | 执行状态、重复次数、指纹、输出、指标、结构化诊断和阈值回算 | 实现与模型对应、诊断证据是否可信 | 模型或实验，按根因 |
| G5 结论/图 | eligible 证据引用、特殊 claim 规则、图来源 | 推断力度、限制、图表是否误导 | 结果或结论 |
| G6 论文 | 独立论文 lint、必需最终 PDF、可选 profile 检查 | 表达、逐页视觉可读性和项目采用的格式要求 | 论文构建 |
| G7 发布 | manifest、必需交付物、最新人工复核 | 最终科学签核 | 最早失效阶段 |

格式 profile 是可配置项。未启用 profile 时，审计器对格式检查给出 `NOT_APPLICABLE`；它不会把未配置的年度格式规则伪装成已通过。启用后仍需由用户依据官方规则维护 profile。

## 状态聚合

严重度顺序为：

```text
BLOCK > ENV_BLOCK > STALE > WARN > PASS > NOT_APPLICABLE
```

同一门禁包含多项发现时取最高严重度。人工 `PASS` 只能补充人工判断，不能抵消自动 `BLOCK`、`ENV_BLOCK` 或 `STALE`。

脚本退出码：

- `0`：整体 `PASS` 或仅有 `WARN`/`NOT_APPLICABLE`。
- `10`：`BLOCK`。
- `11`：`ENV_BLOCK`。
- `12`：`STALE`。
- `13`：输入或报告路径无效。
- `14`：工具内部错误。

## 上游变更与回退

回退由依赖图决定，不固定退一步。审计报告列出发生变化的 artifact 和受影响下游。环境工具缺失属于 `ENV_BLOCK`，不会错误地要求修改科学模型。实验性能未达到预期应如实记录并限制 claim，不能通过改写历史阈值或删除失败结果解决。

人工 review 绑定 artifact 指纹。任一绑定指纹与当前 manifest 不一致时，review 为 `STALE`，需要重新审阅。

## 机器验证边界

机器可以可靠检查字段、引用、哈希、依赖、命令完成情况和预登记的数值比较。机器不能证明题意理解、模型选择、数学推导、因果识别、全局最优、结果解释或论文洞察正确。最终报告必须保留这一限制，不得使用“已证明模型正确”等措辞。

## 结构化检查如何进入结果

验证采用“计划—运行—结果”闭环：

1. G2 在 `model_spec.validation_plan.checks` 为每项风险分配稳定 `check:*` ID；
2. 数值门槛写入 check 的 `threshold`，不得看到结果后再改写；
3. 每个成功 result 为 required/conditional check 生成唯一 diagnostic；
4. 审计器核对 check 类型、适用性、单位、实际观测、运算符和状态；
5. required check 非 PASS、blocking check 未解决、重复次数不足或诊断缺失时，`result_eligibility=false`；
6. 失败后按 `failure_response` 回到建模、阻断结果、降低结论或仅报告，不删除失败记录。

定性诊断的 PASS 仍依赖证据文件和人工复核；只有带结构化阈值的诊断由审计器重算数值判断。LaTeX/Typst 公式不能安全自动解析时，不得把符号闭包检查写成“数学已验证”。

## 推荐自测

至少保留以下正反向案例：

1. 完整小项目通过 schema、引用、哈希和人工门禁。
2. 修改上游文件后出现 `STALE`。
3. final claim 无结果证据时 `BLOCK`。
4. 启发式结果声称 formally proved 时 `BLOCK`。
5. 路径含 `..`、绝对路径或 symlink 逃逸时 `BLOCK`。
6. 格式 profile 未启用时 G6 为 `NOT_APPLICABLE`，而非 `PASS`。
7. 缺少可选外部格式工具时为 `ENV_BLOCK`，而非伪通过。
8. 结果模板、说明文件或仅靠文件名猜测的资产进入实验时 `BLOCK`。
9. 题型必要检查没有被显式考虑，或 required check 缺少 diagnostic 时 `BLOCK`。
10. diagnostic 的手填 PASS 与预声明阈值计算冲突时 `BLOCK`。
11. 声明基线却没有同题同指标实验、eligible result 或主结果依赖绑定时 `BLOCK`。
12. release 缺少唯一、可读取且已哈希的最终 PDF 时 `BLOCK`。
13. partial trigger 手填 BLOCK 但阈值重算为 PASS、缺少任何另一项 required diagnostic、事件指纹过期、同一 fallback 重复激活、fallback 运行早于晋升或重复替换路线时 `BLOCK`。
14. `paper_build` 未绑定完整源码/资源/依赖日志，或构建命令不真实消费 canonical source 时 `BLOCK`。
15. 理论证明为空、PDF 只有空白页、不可读取、仅手填进无效构建收据，或未进入经验证的论文资源闭包/required appendix 时 `BLOCK`。
16. 支持 release 证据的实验遗漏任一辅助 `code_files` deliverable 时 `BLOCK`。
17. LaTeX/Typst 构建日志含明确 compiler failure 时不得出现 `PAPER_BUILD_RECEIPT_VERIFIED`。
18. 纯证明 release 使用单词、未绑定 claim ID/命题的文本，或 `formally_proved` 回执未绑定证明 SHA-256 时 `BLOCK`。
17. JSON/YAML 出现重复键、NaN、Infinity 或递归别名，旧版 1.x 模板/manifest 试图被刷新，或 release 改用非 bundled Schema 根目录时 `BLOCK` 且不得修改目标文件。

独立前向测试应让未参与设计的 agent 只看到 skill、真实请求和原始材料；评价实际 artifact 和门禁行为，不匹配固定措辞，也不提前泄露陷阱。
