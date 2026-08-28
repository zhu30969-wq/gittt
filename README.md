# CUMCM Modeling Skill

[![CI](https://github.com/zhu30969-wq/gittt/actions/workflows/ci.yml/badge.svg)](https://github.com/zhu30969-wq/gittt/actions/workflows/ci.yml)

面向全国大学生数学建模竞赛（CUMCM／国赛）的 Codex Skill。它把题意理解、模型选择、程序实验、结果验证、三人交接和中文论文组织为一条可审计的工作流。

本项目的重点不是提供“常见算法套题器”，而是保证：

- 先定义现实问题和评价标准，再选择数学模型；
- 复杂模型必须与简单基线公平比较；
- 题意、模型、实验、结果、结论和论文之间可追溯；
- 上游变化会使下游证据和旧审批失效；
- 机器检查与人工数学判断明确分工；
- 训练和正式赛使用同一质量标准，仅调整时间与交付节奏。

## 项目定位

**cumcm-modeling** 是单入口 Skill，不是六个相互调用的阶段 Prompt。完整工作流为：

~~~text
输入
  → 问题规格
  → 模型规格
  → 实验规格
  → 运行结果
  → 结论与图表
  → 论文
  → 发布包
~~~

对应证据链：

~~~text
question → model → experiment → result → claim → figure/table/paper
~~~

结构化 YAML 契约负责稳定 ID、依赖、散列和证据引用；Markdown 与论文源码负责推导和解释。契约通过只能说明声明的证据结构通过检查，不能替代数学推导或现实判断。

## 三个来源如何融合

本次整合比较的是三个数学建模 Skill 仓库的工作流、触发边界、产物和验证策略，不把三个目录机械拼接为一个 Skill：

| 来源范式 | 保留的核心能力 | 本项目中的可执行落点 | 有意不沿用的设计 |
|---|---|---|---|
| [zhnnky329/MathModeling-skills](https://github.com/zhnnky329/MathModeling-skills) 的人类决策门禁 | 模型中立的问题解析、可用基线、方法特异失败探针、冻结与回退 | [模型选择](cumcm-modeling/references/model-selection.md)、[G0–G7 工作流](cumcm-modeling/references/workflow.md)、绑定指纹的人工 review | 多入口 Skill 路由、只存在于提示词中的软门禁 |
| [yushui2022/MathModel-Skill](https://github.com/yushui2022/MathModel-Skill) 的可恢复生产流水线 | 断点恢复、SHA-256 新鲜度、运行账本、论文交付与 CI | project manifest、[审计器](cumcm-modeling/scripts/audit_project.py)、CAS 写入、release fixture、Ubuntu/Windows CI、[可选 Word 派生交付](cumcm-modeling/references/paper-delivery.md) | 题型到算法的固定映射、按篇幅机械扩写、重复维护一份独立 memory 状态 |
| [capwitf/My-MathModeling-skills](https://github.com/capwitf/My-MathModeling-skills) 的证据与核验工具箱 | 数学与数值诊断、条件性验证、结论—结果—图表关系、跨媒体一致性 | [验证规范](cumcm-modeling/validation.md)、9 个 JSON Schema、联合证据 DAG、论文 claim marker 与严格 lint | 任意代码行数门槛、需要手工同步的大量平行登记表 |

在三者基础上，本项目新增单入口状态机、稳定 typed ID、完整 `question → model → experiment → result → claim → paper` 路径、依赖闭包失效传播、派生数字逐项登记、并发侧车锁、三人交叉复核和独立前向测试。`manifest.yaml` 是恢复状态的单一事实源，审计器根据当前字节重建门禁状态，避免另一份 workflow memory 与证据文件发生漂移。

本仓库独立设计指令、Schema、脚本、夹具和文档。来源版本、许可证核查与使用边界统一记录在 [THIRD_PARTY_SOURCES.md](THIRD_PARTY_SOURCES.md)，README 不重复维护容易失真的版本或许可结论。

## 新增的可靠性能力

### G0–G7 阶段门禁

| 门禁 | 检查对象 | 人工判断重点 |
|---|---|---|
| G0 | 输入、路径、题面与附件指纹 | 是否使用了正确的一组材料 |
| G1 | problem_spec | 题意、子问、单位、歧义和交付目标 |
| G2 | model_spec | 模型适切性、推导、可识别性和失败方式 |
| G3 | experiment | 数据切分、基线、指标、种子、预算和接受规则 |
| G4 | results | 实现与模型是否一致，诊断是否可信 |
| G5 | claims 与 figures | 推断力度、限制、数值和图表是否有证据 |
| G6 | 论文 | 表达、可读性、公式、引用和数值一致性 |
| G7 | release manifest | 最终论文、代码、结果和审批是否属于同一版本 |

人工 PASS 不能抵消自动发现的 BLOCK、ENV_BLOCK 或 STALE；机器 PASS 也不能代替人工判断。

### 可复现与失效传播

- 原始输入、代码、环境、输出和交付物使用 SHA-256 指纹；
- result 记录 experiment、model、data 和 code 的当前指纹；
- claim 绑定 result、指标、单位、容差、假设和适用范围；
- figure 绑定结果、数据和生成程序；
- 上游文件改变后，依赖图把旧结果、结论、图表和审批标为 STALE；
- 失败运行保留，不通过删除失败结果或放宽阈值换取 PASS。

### 防止算法套模

模型名称之前必须先确定对象、任务、输入输出、单位、机制、数据生成过程、约束和评价标准。每个复杂候选都需要说明：

- 相对简单基线增加了什么可验证价值；
- 必要假设是否成立；
- 参数能否由数据识别；
- 哪个检查会否定该路线；
- 为什么没有采用其他候选；
- 失败后回退到哪里。

案例用于检查遗漏、比较验证方法和寻找反例，不能替当前题目的数据作决定。

## 仓库结构

~~~text
cumcm-modeling/
├── SKILL.md
├── agents/openai.yaml
├── assets/project-template/
├── references/
│   ├── contracts.md
│   ├── workflow.md
│   ├── model-selection.md
│   ├── case-use.md
│   ├── team-collaboration.md
│   ├── rubric.md
│   ├── paper-delivery.md
│   ├── forward-testing.md
│   └── schemas/
├── scripts/
│   ├── init_project.py
│   ├── audit_project.py
│   ├── manifest.py
│   ├── record_gate_review.py
│   └── lint_paper.py
└── validation.md

tests/
├── build_release_fixture.py
├── test_audit_regressions.py
├── test_lint_regressions.py
├── test_path_safety.py
├── test_write_concurrency.py
└── fixtures/
~~~

## 安装

需要 Python 3.10 或更高版本。建议使用虚拟环境：

~~~bash
python -m venv .venv
~~~

PowerShell 激活虚拟环境：

~~~powershell
.\.venv\Scripts\Activate.ps1
~~~

Bash 激活虚拟环境：

~~~bash
source .venv/bin/activate
~~~

激活后安装依赖：

~~~bash
python -m pip install --upgrade pip
python -m pip install -r cumcm-modeling/scripts/requirements.txt
~~~

### 安装到本地 Codex Skills 目录

Codex 从 Skill 目录读取 **SKILL.md**。根据[官方 OpenAI Skills 文档](https://learn.chatgpt.com/docs/build-skills)，本地发现位置是：

- 用户级：**$HOME/.agents/skills/cumcm-modeling**，适用于当前用户的所有仓库；
- 仓库级：从启动 Codex 的当前目录到仓库根目录沿途的 **.agents/skills/cumcm-modeling**，适用于对应目录树。

仅把仓库克隆到任意目录不会自动安装 Skill；必须让 **cumcm-modeling/SKILL.md** 位于上述某个发现位置。下面先安装为用户级 Skill，且在目标已存在时停止，避免静默合并两个版本。

PowerShell：

~~~powershell
$userSkillRoot = Join-Path $HOME '.agents\skills'
$destination = Join-Path $userSkillRoot 'cumcm-modeling'
if (Test-Path -LiteralPath $destination) {
    throw "目标 Skill 已存在，请先审阅差异：$destination"
}
New-Item -ItemType Directory -Force -Path $userSkillRoot
Copy-Item -Recurse -LiteralPath '.\cumcm-modeling' -Destination $destination
~~~

Bash：

~~~bash
user_skill_root="$HOME/.agents/skills"
destination="$user_skill_root/cumcm-modeling"
if [ -e "$destination" ]; then
  printf '目标 Skill 已存在，请先审阅差异：%s\n' "$destination" >&2
  exit 1
fi
mkdir -p "$user_skill_root"
cp -R ./cumcm-modeling "$destination"
~~~

若要做仓库级安装，把目标根目录改为该仓库的 **.agents/skills**。Codex 支持符号链接；Skill 没有自动出现时重启 Codex。目录结构、发现位置和调用方式见 [官方 OpenAI Skills 文档](https://learn.chatgpt.com/docs/build-skills)。

仓库级安装示例（PowerShell）：

~~~powershell
$repoRoot = git rev-parse --show-toplevel
$repoSkillRoot = Join-Path $repoRoot '.agents\skills'
$destination = Join-Path $repoSkillRoot 'cumcm-modeling'
if (Test-Path -LiteralPath $destination) {
    throw "目标 Skill 已存在，请先审阅差异：$destination"
}
New-Item -ItemType Directory -Force -Path $repoSkillRoot
Copy-Item -Recurse -LiteralPath '.\cumcm-modeling' -Destination $destination
~~~

仓库级安装示例（Bash）：

~~~bash
repo_root="$(git rev-parse --show-toplevel)"
repo_skill_root="$repo_root/.agents/skills"
destination="$repo_skill_root/cumcm-modeling"
if [ -e "$destination" ]; then
  printf '目标 Skill 已存在，请先审阅差异：%s\n' "$destination" >&2
  exit 1
fi
mkdir -p "$repo_skill_root"
cp -R ./cumcm-modeling "$destination"
~~~

安装后可以显式调用：

~~~text
使用 $cumcm-modeling 分析这道 CUMCM 赛题，先建立问题规格和可验证基线。
~~~

## 快速开始

以下命令均从仓库根目录执行。使用 **-X utf8** 可以避免不同系统区域设置影响中文 YAML 和 Markdown。

### 1. 初始化项目

~~~bash
python -X utf8 cumcm-modeling/scripts/init_project.py ./work/cumcm-a \
  --project-id project:cumcm-2026-a
~~~

初始化器只创建缺失文件，不覆盖已有目标。新模板包含明确占位内容；填入真实题面、模型、运行和审批前，审计返回 BLOCK 或 STALE 属于正常结果。

只查看将要创建的内容：

~~~bash
python -X utf8 cumcm-modeling/scripts/init_project.py ./work/cumcm-a \
  --project-id project:cumcm-2026-a --dry-run
~~~

### 2. 只读审计

~~~bash
python -X utf8 cumcm-modeling/scripts/audit_project.py ./work/cumcm-a
~~~

生成新的 JSON 报告：

~~~bash
python -X utf8 cumcm-modeling/scripts/audit_project.py ./work/cumcm-a \
  --json-report ./work/audit-001.json
~~~

报告路径已经存在时工具拒绝覆盖。

### 3. 检查 manifest

只读比较登记散列与当前文件：

~~~bash
python -X utf8 cumcm-modeling/scripts/manifest.py ./work/cumcm-a
~~~

明确更新 manifest 中的 artifact 散列：

~~~bash
python -X utf8 cumcm-modeling/scripts/manifest.py ./work/cumcm-a --write
~~~

**--write** 只更新 manifest 的 artifact 散列，不会替你更新 result 指纹、claim 证据或人工审批。

#### 登记环境文件与交付物

**environment_files** 用于锁定依赖清单、解释器环境或其他复现环境文件；**deliverables** 用于锁定论文源码、PDF、代码包等交付文件。两类条目都必须使用项目相对路径、全局唯一 typed ID 和文件当前内容的 SHA-256：

~~~yaml
environment_files:
  - id: environment:python-lock
    path: environment/requirements-lock.txt
    sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    size: 2048
    media_type: text/plain
deliverables:
  - id: deliverable:paper-source
    path: paper/main.tex
    sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    required: true
~~~

上面的散列只演示字段形状，必须替换成对应文件的真实值。typed ID 必须匹配 **type:name** 形式：类型以小写字母开头，之后可含小写字母、数字、下划线或连字符；冒号后的名称必须以小写字母或数字开头，之后只可含小写字母、数字、点、下划线或连字符。SHA-256 必须是恰好 64 位的小写十六进制。**manifest.py --write** 不会刷新 **environment_files** 或 **deliverables**，这些条目需要在文件最终确定后显式计算并更新，审计会核对其当前字节。

#### 推荐使用 manifest CAS

多人或多进程可能同时更新 manifest 时，先记录自己审阅过的 manifest 散列，再通过 **--expected-manifest-sha256** 提交。以下 PowerShell 示例把“读取的版本”和“准备写入的版本”绑定在一起：

~~~powershell
$manifestPath = '.\work\cumcm-a\manifest.yaml'
$expectedManifestSha = python -X utf8 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())" $manifestPath
python -X utf8 cumcm-modeling/scripts/manifest.py .\work\cumcm-a `
  --write `
  --expected-manifest-sha256 $expectedManifestSha
~~~

若锁内重新读取时散列已经变化，命令返回 **STALE / MANIFEST_CHANGED** 和退出码 12。此时应重新读取、检查差异并重新计算期望散列，不能只把期望值替换为最新值后盲目重试。

### 4. 记录人工门禁复核

**record_gate_review.py** 只追加一条人工或混合复核，不会覆盖旧记录，也不会替自动 BLOCK 或 STALE 作决定。PASS 复核必须同时给出至少一个 typed evidence ID 和对应工件指纹。以下示例先绑定当前 problem spec 与 review log，再提交 G1 复核：

~~~powershell
$project = '.\work\cumcm-a'
$problemSha = python -X utf8 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())" "$project\specs\problem_spec.yaml"
$reviewLogSha = python -X utf8 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())" "$project\reviews\gate-reviews.yaml"
python -X utf8 cumcm-modeling/scripts/record_gate_review.py $project `
  --gate G1 `
  --decision PASS `
  --basis human `
  --reviewer '队员甲' `
  --rationale '题意、单位、边界和交付目标已逐项复核' `
  --evidence problem:main `
  --fingerprint "problem:main=$problemSha" `
  --expected-log-sha256 $reviewLogSha `
  --review-id review:g1-problem-v1
~~~

先增加 **--dry-run** 可只验证候选记录。写入成功后命令返回新的 review log SHA-256；随后需要显式运行 manifest 更新流程，使 manifest 中的 gate-review artifact 散列与新日志一致。

#### 并发写入与侧车锁

**manifest.py --write** 与非 dry-run 的 **record_gate_review.py** 都通过目标旁的持久侧车文件串行化写入，例如 **.manifest.yaml.lock** 和 **reviews/.gate-reviews.yaml.lock**。锁内会重新读取目标、检查期望散列、构造候选并原子替换，因此首次创建 review log、无 expected hash 的并发追加和 CAS 写入都不会在两个协作进程间静默丢失更新。

侧车文件会保留且由 **.gitignore** 忽略；保留文件名是锁协议的一部分。默认最多等待 5 秒，可用 **--lock-timeout-seconds** 调整。超时返回 **BLOCK / LOCK_TIMEOUT** 和退出码 10。直接用编辑器或其他程序改写 YAML 不会自动遵守该锁协议。

### 5. 论文 lint

LaTeX：

~~~bash
python -X utf8 cumcm-modeling/scripts/lint_paper.py ./work/cumcm-a \
  --engine latex \
  --source paper/main.tex \
  --claims claims/claims.yaml \
  --figures figures/figures.yaml \
  --strict
~~~

Typst：

~~~bash
python -X utf8 cumcm-modeling/scripts/lint_paper.py ./work/cumcm-a \
  --engine typst \
  --source paper/main.typ \
  --claims claims/claims.yaml \
  --figures figures/figures.yaml \
  --strict
~~~

如已有编译 PDF，可增加 **--pdf paper/main.pdf**。页面上限不是内置常量；需要时通过 **--max-pages** 显式提供。

### 可选 Word 派生交付

需要可编辑 Word 时，保留冻结的 `.tex` 或 `.typ` 作为 canonical paper entrypoint，再从同一证据版本生成 `.docx`。只有生成成功后才把 DOCX 以稳定 typed ID、实际路径和 SHA-256 登记到 release manifest；转换后必须检查公式、图片、表格、引用、中文字体和分页，并通过 PDF 或逐页图片做视觉复核。

通用审计器会验证 DOCX 的存在性和散列，但不会把“文件能打开”当成 Word 排版通过。完整边界见 [论文与交付](cumcm-modeling/references/paper-delivery.md)。

## 状态含义

| 状态 | 含义 |
|---|---|
| PASS | 声明的结构和证据通过当前检查 |
| WARN | 可以继续，但存在应人工核对的问题 |
| BLOCK | 科学证据、Schema、引用或交付条件不满足 |
| ENV_BLOCK | 缺少依赖、编译器或运行环境 |
| STALE | 上游变化导致已有证据或审批过期 |
| NOT_APPLICABLE | 当前项目没有启用该项检查 |

## 能力边界

本项目：

- 不保证获奖、模型最优或结论必然正确；
- 不把 Schema、散列或 lint PASS 等同于数学证明；
- 不能自动证明题意理解、模型适切性、因果关系或全局最优；
- 不能用静态检查替代代码复现、条件性验证和三人交叉复核；
- 不附带历年论文、赛题、教材、通用算法实现或排版资源库；
- 不建议把内部量表分数解释为官方评分或获奖概率。

它能做的是缩短证据断链、语义漂移、数据泄漏、约束遗漏、数值错配和论文低级错误被发现的时间。

## 开发与测试

Skill 结构验证：

~~~bash
python -X utf8 /path/to/skill-creator/scripts/quick_validate.py cumcm-modeling
~~~

正向论文 lint：

~~~bash
python -X utf8 cumcm-modeling/scripts/lint_paper.py \
  tests/fixtures/paper-pass \
  --engine latex \
  --source paper/main.tex \
  --claims claims/claims.yaml \
  --figures figures/figures.yaml \
  --strict
~~~

反向论文 lint 应返回 BLOCK 和退出码 10：

~~~bash
python -X utf8 cumcm-modeling/scripts/lint_paper.py \
  tests/fixtures/paper-fail \
  --engine typst \
  --source paper/main.typ \
  --strict
~~~

生成合成 release 夹具并审计：

~~~bash
python -X utf8 tests/build_release_fixture.py ./synthetic-release
python -X utf8 cumcm-modeling/scripts/manifest.py ./synthetic-release
python -X utf8 cumcm-modeling/scripts/audit_project.py ./synthetic-release
~~~

夹具完全由本项目生成，不包含第三方赛题或论文内容。CI 还会运行 Skill metadata 快速验证、全部 Python 文件编译、JSON Schema 自校验、完整 **unittest discover** 回归测试和 Windows 并发/路径 smoke；合成 release 审计只有整体状态严格等于 PASS 才通过。
