# 独立前向测试

本文件用于迭代 Skill 本身，不用于解答具体赛题。测试评价可观察行为和产物，不匹配固定标题或措辞。

## 测试协议

1. 使用未参与本轮设计的独立执行者；
2. 只提供真实请求、Skill 路径和最少原始材料，不泄露预期答案或已知陷阱；
3. 每个场景使用新的临时目录，避免污染仓库；
4. 保留生成物、审计报告和失败日志；
5. 先判断失败归因，再做最窄修改；
6. 至少保留一组从未用于调试的 held-out 场景；
7. 测试不得自动删除成批文件，临时目录留存并报告位置。

仓库提供八个可执行的离线场景。E01 从初始化占位推进到完整合成优化 release，真实重跑主入口与独立最优响应脚本，再验证目标对账的负向变异和字节级恢复：

```bash
python -X utf8 evals/run_complete_chain.py <new-target-path>
```

目标路径必须尚不存在。该 harness 保留初始化、完整、负向和恢复四份审计报告，以及负向 `results.yaml` 快照。正向链必须达到 `PASS / SUBMISSION_READY`，每个问题到达 final claim，定量 claim 绑定 eligible result，两份输出在预登记容差内重现，图表进入 paper 证据链。负向链保持 `solver_optimality` 通过，却因固定主决策后的独立最优响应超容差而触发 `OBJECTIVE_REPAIR_GAIN_EXCEEDED` 并令结果失去 eligibility；恢复原始结果字节后必须重新通过。

E03 验证启发式优化的约束回代与最优性措辞：

```bash
python -X utf8 evals/run_heuristic_optimum.py <new-target-path>
```

该 harness 从完整合成 optimization release 构造固定预算候选搜索，移除全局上下界证书，并保留正向、约束超限和无效证明三份项目。正向项目的每条约束由预登记数值阈值回代，结论只写“当前找到的最好可行候选”；约束超限必须触发 `DIAGNOSTIC_THRESHOLD_FAILED` 并令 result 失去资格；把结论升级为“全局最优”并附上不可核验的证明材料时，必须触发 `PROOF_ARTIFACT_INVALID`。

E11 验证基线证据的覆盖、可比性和失效传播：

```bash
python -X utf8 evals/run_baseline_evidence.py <new-target-path>
```

该 harness 构造两个问题、两个独立代码入口和同一定义指标下的主方法与简单基线。正向项目要求两个 result 都 eligible，主 result 精确依赖实际比较的 baseline result；负向项目分别缩减基线的问题覆盖、改变基线指标聚合口径，以及在登记后改变 baseline result 字节。前两类必须触发覆盖或可比性 finding，最后一类必须使比较主结果与下游 claim 一起变为 `STALE`，不能只刷新表面比较值。

E12 验证结构化诊断的一一对应关系与阈值重算：

```bash
python -X utf8 evals/run_structured_diagnostic.py <new-target-path>
```

该 harness 构造一个 required 检查和一个已激活的 conditional 检查，正向项目要求两项各有且仅有一条绑定哈希输出与标量提取器的 diagnostic，并由审计器重新计算两次阈值通过。三个负向项目分别删除 required diagnostic、复制 conditional diagnostic，以及让手填 `PASS` 与预登记数值阈值矛盾；前两类必须触发 `VALIDATION_CHECK_EVIDENCE_AMBIGUOUS`，后一类必须触发 `DIAGNOSTIC_STATUS_MISMATCH`，且三者均令对应 result 失去 eligibility。

E17 验证离线中断恢复：

```bash
python -X utf8 evals/run_held_out_resume.py <new-target-path>
```

目标路径必须尚不存在。该 harness 初始化最小项目、加入中断恢复标记、再次调用初始化器并核对所有文件字节未被覆盖，随后用两个独立报告验证 `workflow_state`、`last_valid_gate`、`rollback_target` 和 `next_legal_action` 的确定性。项目和报告全部保留，脚本不删除文件。

E18 验证混合模型的验证覆盖并集：

```bash
python -X utf8 evals/run_hybrid_validation_facet_union.py <new-target-path>
```

该 harness 分别保留缺失 facets、缺失并集、完整并集和 optimization 逃逸尝试四个合成项目。`hybrid` 缺少 `validation_facets` 时必须阻断；声明 `optimization` 与 `simulation` 后，缺失 finding 必须精确列出两族检查并集，完整登记必须通过；`optimization` 模型额外声明 `simulation` 也不能隐藏自身族级检查。

E19 验证随机优化的 selection/holdout 隔离：

```bash
python -X utf8 evals/run_scenario_set_holdout_isolation.py <new-target-path>
```

该 harness 保留一个正向项目和缺角色、哈希重叠、final claim 使用 selection 指标三个负向项目。正向项目必须同时登记两个角色且情景字节不重叠；三类负向状态分别触发稳定 finding code。

E20 验证比较实验的决策时序一致性：

```bash
python -X utf8 evals/run_decision_timing_comparability.py <new-target-path>
```

该 harness 使用同一合成双候选比较，先让两个 experiment 分别采用 `here_and_now` 与 `wait_and_see` 并确认阻断，再把两者统一为 `here_and_now` 并确认恢复通过。

`evals/scenarios.yaml` 使用明确的执行状态：

- `executable`：仓库中存在真实 fixture 和可运行 harness，可报告本次实际运行结果；
- `specification_only`：只定义待观察行为，不能声称已经完成独立 Agent 端到端评测。

当前 E01、E03、E11、E12、E17、E18、E19 与 E20 具备独立可执行 harness。E02、E04–E10 与 E13–E16 是 specification-only 行为规范；部分不变量虽被普通审计器单元测试覆盖，也不能改写成“独立 Agent 已端到端解题”。

## P0 行为场景

| 场景 | 必须观察到的行为 |
|---|---|
| 小型完整题 | 形成 question→model→experiment→result→claim→paper 的完整链并可复现 |
| 时间序列预测 | 拒绝随机切分；使用时间顺序验证和简单基线 |
| 启发式优化 | 无证书时拒绝“全局最优”措辞；逐条报告约束可行性 |
| 排序评价 | 检查指标方向、标准化、权重扰动和排序翻转 |
| 机理仿真 | 检查量纲、边初值条件、守恒量和步长/网格收敛 |
| 脏数据与歧义 | 不编造缺失数据，不静默选择解释，保留影响分析 |
| 上游文件变化 | 旧结果、图表、结论和审批被标为 `STALE` |
| 证据断链 | 无结果或推导支持的 final claim 被 `BLOCK` |
| 排版污染 | Typst 中的 LaTeX 语法、缺图和断裂引用被检出 |
| 环境缺失 | 编译器或依赖缺失返回 `ENV_BLOCK`，不得伪装为 `PASS` |

## 评价维度

除 15 维量表外，单独记录：

- 是否从任务结构而非关键词选择模型；
- 是否先建立基线再增加复杂度；
- 是否主动寻找反证和失败模式；
- 是否保存失败实验而非只留最好结果；
- 是否准确区分机器检查与人工数学判断；
- 是否在信息不足时暴露不确定性；
- 是否尊重已有项目状态，不覆盖或重新初始化；
- 是否把关键数值绑定到可复现结果。

## 迭代停止条件

同一确定性脚本错误最多自动尝试修复两轮。若重复失败、需要改变科学假设、需要降低阈值或需要扩大容差，停止自动修复并报告根因。Skill 的改动必须由实际失败支持，不能为单个例子累积成普遍硬规则。

## 待建设的可执行场景

后续至少需要为以下行为补齐独立材料、fixture、harness 和结果判定，完成前保持 `specification_only`：

- 时序预测泄漏与时间顺序验证；
- 综合评价中的权重扰动和排名翻转；
- 数据、结果模板、说明与参考资料混合目录的输入角色识别。

不得为了填满场景而生成虚构的优秀论文或伪造独立 Agent 结果。
