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

论文源码和可选 PDF 的只读 lint：

```bash
python scripts/lint_paper.py ./work --engine latex --source paper/main.tex
python scripts/lint_paper.py ./work --engine typst --source paper/main.typ \
  --pdf paper/main.pdf --max-pages <project-specific-limit>
```

lint 检查占位符、跨引擎语法污染、危险 LaTeX shell escape、缺失 include/图片、LaTeX 引用与文献键、final claim marker、登记图表引用和 PDF 可读性。页数只有在项目显式传入 `--max-pages` 时检查，不硬编码永久规则；PDF 视觉叙事和公式语义仍需逐页人工复核。

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
| G0 输入 | 题面、规则、数据路径和哈希 | 文件是否正式且年份/题号正确 | 输入收集 |
| G1 问题 | 子问、交付物、假设、歧义、单位 | 题意和任务分解 | 问题规格 |
| G2 模型 | 符号闭包、引用、验证和失败计划 | 模型适切性、推导、可识别性 | 模型规格 |
| G3 实验 | 代码/环境哈希、种子、切分、指标、输出 | 实验设计、基线公平性、泄漏风险 | 实验规格 |
| G4 结果 | 执行状态、指纹、输出、指标 | 实现与模型对应、诊断合理性 | 模型或实验，按根因 |
| G5 结论/图 | 证据引用、特殊 claim 规则、图来源 | 推断力度、限制、图表是否误导 | 结果或结论 |
| G6 论文 | 独立论文 lint、可选 PDF/profile 检查 | 表达、视觉可读性和项目采用的格式要求 | 论文构建 |
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

## 推荐自测

至少保留以下正反向案例：

1. 完整小项目通过 schema、引用、哈希和人工门禁。
2. 修改上游文件后出现 `STALE`。
3. final claim 无结果证据时 `BLOCK`。
4. 启发式结果声称 formally proved 时 `BLOCK`。
5. 路径含 `..`、绝对路径或 symlink 逃逸时 `BLOCK`。
6. 格式 profile 未启用时 G6 为 `NOT_APPLICABLE`，而非 `PASS`。
7. 缺少可选外部格式工具时为 `ENV_BLOCK`，而非伪通过。

独立前向测试应让未参与设计的 agent 只看到 skill、真实请求和原始材料；评价实际 artifact 和门禁行为，不匹配固定措辞，也不提前泄露陷阱。
