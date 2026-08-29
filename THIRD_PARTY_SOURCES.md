# 第三方来源与原创边界

本文件记录本项目在设计 CUMCM 数学建模 Skill 时查阅的第三方公开仓库、固定版本、许可证状态与 clean-room（洁净室）原创边界。固定版本与远端 HEAD 已于 2026-08-28 再次核对。

## 来源清单

### 1. jihe520/MathModelAgent

- 固定分支与 HEAD：`main` @ `83d8783187a2d29dda1b046cb667009cc50c8203`
- 固定版本：[jihe520/MathModelAgent@83d8783](https://github.com/jihe520/MathModelAgent/tree/83d8783187a2d29dda1b046cb667009cc50c8203)
- 参考角色：用于研究从赛题分析、模型建立、计算绘图到中文论文和最终验收的端到端执行层。
- 许可证/版权状态：固定版本根目录未发现 `LICENSE`、`LICENCE`、`COPYING` 或 `NOTICE`，GitHub License API 也未识别许可证。公开可见不等于获得复制、修改或再分发许可。
- 使用边界：仅借鉴“完整产出链”这一抽象工作流；未复制其 Skill 文本、提示词、代码、模板、前后端实现、示例或资产。

### 2. datawhalechina/intro-mathmodel

- 固定分支与 HEAD：`main` @ `39ca6c9a6e7e53bfbb87315208d893a68459efd2`
- 固定版本：[datawhalechina/intro-mathmodel@39ca6c9](https://github.com/datawhalechina/intro-mathmodel/tree/39ca6c9a6e7e53bfbb87315208d893a68459efd2)
- 参考角色：用于研究数学建模知识层的组织方式，包括方法原理、适用条件、数学表达和可运行实现之间的关系。
- 许可证/版权状态：固定版本根目录仅含 `README.md` 与 `docs/`，未发现许可证文件，GitHub License API 也未识别许可证。
- 使用边界：未复制、翻译或改写其教程、公式编排、代码和图片；模型选择与验证说明由本项目依据公知数学知识独立撰写。

### 3. zhanwen/MathModel

- 固定分支与 HEAD：`master` @ `cd5be91735ebf11d5ee52eb170e86a6d07131977`
- 固定版本：[zhanwen/MathModel@cd5be91](https://github.com/zhanwen/MathModel/tree/cd5be91735ebf11d5ee52eb170e86a6d07131977)
- 参考角色：用于研究历年赛题、论文、模板、算法资料在案例检索、反例检查、复现分级和写作研究中的作用。
- 许可证/版权状态：固定版本根目录未发现许可证文件，GitHub License API 也未识别许可证；仓库聚合的论文、赛题、课件和模板还可能分别具有不同权利人。
- 使用边界：未引入其中任何论文、赛题、代码、模板、图片、课件或二进制文件；本项目只独立设计案例使用规范和元数据字段。

### 4. zhnnky329/MathModeling-skills

- 固定分支与 HEAD：`main` @ `046a6e74814c2e5fef72b5ee56305509a8635e1d`
- 固定版本：[zhnnky329/MathModeling-skills@046a6e7](https://github.com/zhnnky329/MathModeling-skills/tree/046a6e74814c2e5fef72b5ee56305509a8635e1d)
- 参考角色：用于研究人类决策驱动的阶段门禁、模型中立的问题解析、主方法与可用基线的筛选、方法特异风险探针以及结果冻结思想。
- 许可证/版权状态：仓库根目录采用 **MIT License**，版权声明为 `Copyright (c) 2026 Zhijun Zhang`。
- 固定许可证据：[根目录 LICENSE](https://github.com/zhnnky329/MathModeling-skills/blob/046a6e74814c2e5fef72b5ee56305509a8635e1d/LICENSE)
- 使用边界：本项目没有复制其多 Skill 编排、`SKILL.md` 表述、提示词、代码、示例或资产；阶段语义、G0–G7 门禁和全部契约均为独立设计。

### 5. yushui2022/MathModel-Skill

- 固定分支与 HEAD：`master` @ `51054497f052197c3afe434e502e38edb85b2870`
- 固定版本：[yushui2022/MathModel-Skill@5105449](https://github.com/yushui2022/MathModel-Skill/tree/51054497f052197c3afe434e502e38edb85b2870)
- 参考角色：用于研究可恢复的竞赛生产流水线、输入预检、状态恢复、SHA-256 新鲜度、运行账本、证据门禁、正式论文交付与持续集成。
- 许可证/版权状态：固定版本的根目录及完整 Git tree 未发现 `LICENSE`、`LICENCE`、`COPYING` 或 `NOTICE`，GitHub API 也未识别许可证。公开可见不等于获得复制、修改或再分发许可。
- 固定版权证据：[固定版本根目录](https://github.com/yushui2022/MathModel-Skill/tree/51054497f052197c3afe434e502e38edb85b2870)
- 使用边界：仅借鉴抽象的可恢复工作流和可复现性思想；未复制其 `SKILL.md`、脚本、提示词、Word/OMML 模板、示例论文、图表、数据、发布包或其他资产。

### 6. capwitf/My-MathModeling-skills

- 固定分支与 HEAD：`main` @ `da53b41cab7e25be906f5899488229387e7921c0`
- 固定版本：[capwitf/My-MathModeling-skills@da53b41](https://github.com/capwitf/My-MathModeling-skills/tree/da53b41cab7e25be906f5899488229387e7921c0)
- 参考角色：用于研究独立数学核验、条件性数值诊断、结果—结论—图表证据关系、跨媒体一致性检查和评审视角。
- 许可证/版权状态：仓库根目录采用 **MIT License**，版权声明为 `Copyright (c) 2026 capwitf`。
- 固定许可证据：[根目录 LICENSE](https://github.com/capwitf/My-MathModeling-skills/blob/da53b41cab7e25be906f5899488229387e7921c0/LICENSE)
- 使用边界：本项目没有复制其 Skill 文本、CSV 登记表、脚本、论文模板、图表模板或资产；证据 DAG、JSON Schema、审计器、论文 lint 与测试均为独立实现。

## Clean-room 原创规则

本项目遵循以下边界：

1. 可以参考不受版权保护的思想、事实、公知数学方法、公式、模型类别名称和抽象工作流程。
2. 所有 Skill 指令、提示词、检查表、示例、脚本和目录编排均独立设计和撰写，不沿用来源仓库的独特表达、选择或编排。
3. 算法实现依据数学定义、原始论文或官方文档重新实现；需要核对竞赛格式时以当年官方材料为准。
4. 示例数据采用原创、公开且许可证兼容的数据，或明确标注为合成数据；不得从来源仓库抽取附件作为样例。
5. 不引入第三方文本、代码、模板、图片、字体、论文、赛题或二进制资产，除非已经完成逐项许可证审查、归因和兼容性确认。
6. 若未来确需引入第三方受保护内容，必须在合并前记录来源、固定版本、作者、许可证、修改说明与分发条件；许可证不兼容或权利链不清时不得合并。

## 未复制声明

本项目对上述六个仓库的使用仅限于概念性调研和事实核对。项目没有复制、翻译、改写或打包上述来源中的受保护文本、提示词、源代码、模板、图表、图片、数据集、论文、赛题、压缩包或其他二进制资产；相关链接仅用于透明记录灵感来源和版权边界。

无明确许可证并不等于可以自由复用。GitHub 对无许可证仓库的说明见：[Licensing a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)。本文件是保守的工程合规记录，不构成法律意见。
