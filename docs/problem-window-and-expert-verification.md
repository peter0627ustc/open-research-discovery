# 问题窗口与专家验收设计

状态：提案（尚未全部实现）

本文定义 `open-research-discovery` 下一版的问题构造原则。目标不是把开放科学问题改造成可以由 AI 或 CI 自动判分的 benchmark，而是产出：

- 仍然具有实质研究难度；
- 来源忠实；
- 任务边界具体；
- 不把一整个领域的研究计划包装成一道题；
- 由相应领域的专业研究者能够判断“是否已经解决”的问题。

本文中的 **问题窗口（problem window）**，指一个问题声明所覆盖的对象、量词、物理区间、交付结果和完成条件的总范围。

## 1. 当前问题

当前 pipeline 优先保持文献问题的自然广度，同时要求每个问题拥有完整的 `verification_contract`。当来源列出多个相关开放方向时，Selection Agent 容易把它们合并成一个宏大的问题，例如同时要求：

- 新算法；
- 更大系统尺寸或新的参数区间；
- 多类可观测量；
- 完整相图或分类；
- 实验实现；
- 对整个模型族或架构族的普遍结论。

这样的记录适合作为研究路线图或领域级元问题，但通常不适合作为一个独立求解任务。下游 `verification_contract` 随后会出现两种错误：

1. 只验收其中一部分，却把它当作整个问题已经解决；
2. 为了覆盖全部要求，使用“充分大”“令人信服”“完整”“普遍接受”等无法定位审查边界的标准。

下一版必须把“忠实保存宏观开放问题”和“发布可求解任务”分成两个层级。

## 2. 设计目标

### 2.1 必须满足

每一个可派发的 leaf problem 必须满足：

1. **单一解决命题**：能够用一句话说明，什么科学命题被证明、反驳、构造、测量或确定后，该问题即告解决。
2. **边界闭合**：模型或系统、参数/极限、主要可观测量或输出对象、量词和排除项在题面内闭合。
3. **完整答案可识别**：专业领域审稿人能够根据提交物和领域标准判断答案是否解决该命题。
4. **部分进展不冒充解决**：只能回答部分义务的答案可以记录为 progress，但不能关闭该问题。
5. **来源关系透明**：若 leaf problem 是著名问题或宽泛文献问题的派生子问题，必须保留父问题和来源关系，不能冒充原始问题的完整版本。

### 2.2 明确不要求

本设计不要求：

- 问题容易；
- 问题能在短期内解决；
- 存在标准答案；
- AI 能独立判断正确性；
- CI 能检查核心科学结论；
- 所有通过条件都转换为数字阈值；
- 数学证明、理论推导或实验因果判断被简化成有限测试。

只要专业领域的人能够依据明确的题面、提交物和审查任务判断是否解决，验证就可以是专家型、推导型或整体型的。

## 3. 三类问题记录

pipeline 应区分三类记录。

### 3.1 `meta_problem`

领域级、著名或文献原始问题。它可以很宽，用于保存来源忠实性、研究谱系和子问题关系。

- 可以没有直接的求解仓库；
- 可以没有单一 `verification_contract`；
- 不因过宽而被删除；
- 不直接作为一次提交即可关闭的任务；
- 通过 `subproblem_ids` 指向可派发问题。

### 3.2 `leaf_problem`

可独立求解和验收的任务，是 solution repository 的主要编译对象。

- 必须只有一个解决命题；
- 可以包含多个彼此依赖的技术要求；
- 每个 load-bearing requirement 都必须被完整答案覆盖；
- 允许验证难度很高；
- 允许 `ci_contract: null`。

### 3.3 `progress_target`

明确有价值但不足以关闭父问题的研究里程碑，例如排除一个架构子类、把数值尺寸推进到一个新范围、解决六个分类分支中的一个。

- 必须明确标记为部分进展；
- 不能以“反例”“构造”或“数值结果”的名字伪装成完整答案类型；
- 可以有独立 repository，但状态和 README 必须表明其只解决父问题的哪一部分。

## 4. Leaf problem 的问题窗口规则

### 4.1 单一量词主干

一个 leaf problem 应有一个主要逻辑形式，例如：

- `exists`：是否存在满足条件的对象；
- `forall/no-go`：某类对象是否全部不可能；
- `classification`：对一个闭合输入类给出完备分类；
- `asymptotic`：确定一个明确极限中的行为；
- `mechanism`：在指定系统与证据范围内区分有限组机制；
- `construction`：构造满足明确性质的对象；
- `measurement`：在指定平台上测得并排除明确替代解释。

若题面同时出现两个无法由同一个结论共同决定的量词主干，应拆题。

例如，“是否存在某种 NQS 架构”与“某个前馈架构子类是否存在指数下界”应当是父子或相邻问题，不能把后者作为前者的完整反例合同。

### 4.2 独立可发表测试

Selection Agent 必须对每项要求提问：

> 如果只完成这一项，它是否可能形成一篇独立论文，而其他项仍完全开放？

若答案为“是”，该项原则上应成为独立子问题，除非有来源证据表明它只是同一个定理、构造或实验判据不可分割的一步。

### 4.3 合取要求测试

允许同一 leaf problem 含有多个 `and` 条件，但这些条件必须共同认证同一个解决命题。例如，一个非阿贝尔任意子实验可以同时要求融合、编织和排除平凡解释，因为这些可能是同一实验结论的必要证据。

以下情况默认拆分：

- 不同模型族分别给出完整结果；
- 基态、有限温度、实时动力学和完整相图同时成为独立交付物；
- 理论字典、谱密度、所有阶修正和高维推广同时出现；
- 构造一个实例与完成整个类别的分类同时出现；
- 理论、数值和实验被写成必须全部完成，而它们分别可以回答不同科学命题。

### 4.4 开放尾部禁止进入 leaf problem

Leaf problem 不得使用未闭合的尾部扩张，例如：

- “and related systems”；
- “generic models”但没有定义输入类；
- “all relevant observables”；
- “across materials”但没有材料选择规则；
- “and higher-dimensional/generalized variants”；
- “complete phenomenology”但没有列出必须覆盖的现象。

这些表述可以出现在 `meta_problem` 的背景或研究路线中，但不能成为 leaf problem 的 load-bearing scope。

### 4.5 不用字数或容易程度裁剪

问题窗口 gate 不使用题面字数、预计求解时间、算力或成功概率作为阈值。一个问题可以非常困难，也可以需要新的数学或实验技术。判断标准是“是否只有一个闭合的解决命题”，而不是“是否容易”。

## 5. 专家验收，而非 AI 验收

### 5.1 新的验收定义

`verification_contract` 的目标应改为：

> 让具备指定领域能力的独立研究者知道需要审查哪些提交物、重建哪些推导或证据链，以及什么结论算作解决题面。

验收合同必须说明：

1. **提交物**：定理与证明、模型与构造、代码和数据、实验记录、理论推导、分类表等；
2. **核心结论**：该答案声称解决的命题；
3. **专家检查任务**：审稿人要核对哪些假设、推导、极限、误差、对照或排除项；
4. **失败条件**：哪些缺失或逻辑不匹配意味着没有解决问题；
5. **覆盖范围**：是完整解决还是部分进展。

### 5.2 允许专家判断的标准

以下标准可以合法存在，不因无法机械执行而自动扣分：

- 数学证明是否正确且覆盖声明的定理；
- 近似或展开是否在声明区间内受控；
- 有限尺寸/有限时间证据是否足以支持提交者声明的渐近结论；
- 实验系统误差和对照实验是否排除了题面列出的替代解释；
- 新分类的不变量是否在声明输入类上完备；
- 新理论是否真正解释而非仅拟合指定数据；
- 领域标准意义下的鲁棒性、稳定性或普适性是否得到论证。

但合同必须限定审查对象和声明范围。可以要求专家判断“这个有限尺寸论证是否支持所声明的热力学极限”，不能只写“结果应令人信服”。

### 5.3 CI 的新定位

`ci_contract` 是可选的辅助复现信息，不是质量要求。

- `ci_contract: null` 不扣分；
- CI 只检查代码运行、数据重现、有限实例、形式证明文件或算术一致性；
- CI 不承担判断新定理、新物理机制或热力学极限是否成立；
- 不因为核心结论需要专家审查而降低问题质量；
- 不为获得更高 benchmark 分数而发明代理 benchmark。

## 6. 答案类型与覆盖关系

每个答案类型应新增覆盖语义：

```yaml
verification_contract:
  proof:
    resolution_scope: full
    covers: [O1, O2]
    contract: ...
    ci_contract: null
  bounded_depth_no_go:
    resolution_scope: partial_progress
    covers: [O2]
    contract: ...
    ci_contract: null
```

规则：

1. 只有 `resolution_scope: full` 的答案类型可以关闭当前 leaf/progress 记录；关闭 `progress_target` 不会关闭其父问题；
2. `partial_progress` 可以被审查、发布和引用，但不得触发 solved；
3. 每一个 full branch 必须覆盖该 leaf problem 的所有 load-bearing obligations；
4. Counterexample 必须与题面量词匹配；
5. 排除子类只能回答关于该子类的问题，除非证明该子类穷尽题面定义域。

## 7. Problem Schema v1.1 的结构化字段

Schema v1.1 增加：

```yaml
problem_kind: meta_problem | leaf_problem | progress_target
source_relation: original | derived | surviving_core
resolution_claim: "一句话写明何时该问题被解决"
scope:
  object: "研究对象或模型类"
  regime: "参数、维数、极限或实验范围"
  quantifier: exists | forall | classification | asymptotic | mechanism | construction | measurement
  exclusions: []
obligations:
  - id: O1
    requirement: "完整答案必须满足的要求"
    load_bearing: true
    independently_publishable: false
expertise_required:
  - "能够审查该问题的专业领域"
```

`resolution_claim` 和 `obligations` 不是把科学问题变成简单 checklist，而是帮助 pipeline 检查题面与合同是否谈论同一个问题。

## 8. Pipeline 修改

### 8.1 Discovery

继续忠实保存 LKM/source 中的原始开放方向，但要区分：

- 作者明确提出的单一问题；
- 一组 future directions；
- 领域级研究计划；
- 从论文限制中推导出的候选子问题。

Discovery 不负责强行缩小问题，但应输出可分离方向的边界提示。

### 8.2 Selection

Selection 输出的可编译候选只允许 `leaf_problem` 或 `progress_target`，并且必须包含：

- `resolution_claim`；
- 量词主干；
- obligations；
- `keep_as_one` 或 `split` 的决定及理由；
- 宽问题的来源关系与拆分理由；宽问题本身留在 provenance，需要时可另建 `meta_problem` 索引记录。

“保持文献自然广度”应解释为：保留宽问题作为 parent，而不是把所有方向压进一个 leaf problem。

### 8.3 Research

Research 在核对原始文献后可以：

- 修正问题边界；
- 将候选改为 meta problem；
- 生成来源忠实的子问题；
- 拒绝无法形成闭合解决命题的 leaf candidate。

Research 不应在同一次自由文本生成中同时扩写题面并自行寻找一个便宜的验收分支。它应先冻结 `resolution_claim/scope/obligations`，再生成合同。

### 8.4 Problem Window Review

在 Problem Review 前增加一个结构化的 Problem Window Review，执行：

1. 单一解决命题检查；
2. 合取要求拆分检查；
3. 量词匹配检查；
4. full/partial coverage matrix 检查；
5. 开放尾部检查；
6. 专家是否能够识别完整答案。

该阶段不判断问题是否容易，也不要求 AI 能证明答案正确。

### 8.5 Problem Review 与修订

Reviewer 不再只有 `accept/reject` 的单向出口。至少允许一次结构化返修：

- `accept`；
- `revise_scope`；
- `split_into_children`；
- `reclassify_as_meta`；
- `reject`。

### 8.6 Compile

- `leaf_problem` 编译为 solution repository；
- `progress_target` 编译时明确显示父题和部分覆盖范围；
- `meta_problem` 编译为 roadmap/catalog 页面，默认不创建“单一提交即可解决”的 solution repository。

## 9. 放宽后的 Quality Benchmark

Quality benchmark 的目标从“尽量机械可执行”改为“问题边界和专家验收是否清楚”。建议维度为：

1. `citation_accuracy`：引用是否真实准确；
2. `openness_argument`：开放状态是否有证据；
3. `problem_window`：是否是单一、闭合、不过度宽泛的解决命题；
4. `expert_resolution_clarity`：专业领域的人是否知道如何判断完整答案；
5. `evidence_relevance`：证据是否真正支持该问题。

原 `verification_executability` 不再要求 AI/CI 可执行。若保留字段名，其定义必须改为 human-expert executability。

### 9.1 不应扣分

以下情况不再扣分：

- `ci_contract` 为 null；
- 需要审查长证明或新推导；
- 需要领域专家判断数值外推或实验因果性；
- 没有公认的固定数值阈值；
- 验证难度为 7–10；
- 答案没有预先存在的标准答案；
- 结果只能通过同行评议和独立复现确认。

### 9.2 仍然属于严重问题

放宽 benchmark 不意味着取消语义质量要求。以下仍是 major/critical：

- 一个 leaf problem 包含多个可以独立发表、独立解决的核心问题；
- partial-progress branch 被标记为 full resolution；
- 存在性问题的反例只排除一个非穷尽子类；
- 全称或渐近问题被一个有限实例直接判定；
- 题面中的对象、参数范围、量词或完成边界没有闭合；
- 验收合同检查的是与题面不同的代理问题；
- “正确、完整、预期、普遍接受”是唯一标准，且没有说明专家应审查什么证据；
- 为了容易验收而把派生子问题冒充著名原问题。

### 9.3 评分解释

高分含义应是：

> 题目是一个来源忠实、边界合适的困难科学任务；领域专家能够判断提交物是否完整解决它。

高分不再意味着：

> AI 或 CI 可以廉价、确定地输出 pass/fail。

机械复现能力可单独记录为非评分元数据，例如：

```yaml
automation_support: none | partial | substantial
human_review_mode: local_checks | connected_derivation | expert_holistic
```

## 10. 现有问题迁移

对现有问题不应直接删除或把合同强行写宽。迁移分三步：

1. **分类**：标记为 leaf、meta 或 progress target；
2. **拆分**：对 meta problem 生成来源可追踪的子问题；
3. **重写合同**：为每个 leaf problem 建立 obligation coverage，区分 full 与 partial。

例如：

- ORP-0018 应保留为 tensor-network matrix-model 研究路线父题，并拆出 exact-singlet ansatz、large-N convergence、finite-temperature observables、phase-transition characterization 等子问题；
- ORP-0020 应将“是否存在通用 sign-faithful NQS”作为父题，将具体架构 hardness 作为 progress target 或架构子类问题；
- ORP-0021 应拆分严格键维数界、解析 $f(k,d)$、非等距 PEPS、对称 RMPS 和复杂性问题；
- ORP-0082 应拆分 HOTI/fracton 对应问题与一般 bulk-to-boundary 分类问题。

## 11. 需要修改的仓库位置

| 文件/模块 | 所需修改 |
|---|---|
| `AGENTS.md` | 将“保持自然广度”改为“宽问题保留为 parent”；允许专家验收、拆分和返修 |
| `README.md` | 更新 first principles、workflow、selection、verification 和 benchmark 定位 |
| `docs/discovery-pipeline.md` | 增加 Problem Window Review、返修、parent/child 编译路径 |
| `docs/problem-schema-v1.0.md` | 定义 problem kind、resolution claim、scope、obligations 和 full/partial coverage |
| `schemas/problem.schema.json` | 实现上述字段并允许 campaign 生成 parent/subproblem 关系 |
| `schemas/stages/selection.schema.json` | 增加候选类型、拆分理由、量词和 obligations |
| `src/open_research_discovery/campaign.py` | 修改各阶段 prompt，并在 Research/Review 后执行 window 与 obligation coverage 校验 |
| `src/open_research_discovery/ranking.py` | 明确“cheapest sound branch”必须是 full-resolution branch |
| `docs/problem-quality-benchmark.md` | 用 problem window 与 expert resolution clarity 替代机械可执行性偏好 |
| `src/open_research_discovery/quality.py` | 更新 blind reviewer rubric 和 issue taxonomy |
| `schemas/quality/*.json` | 更新评分维度与字段描述 |
| `docs/verification-difficulty-casebook.md` | 明确高专家判断负担不等于低问题质量 |
| `tests/` | 增加问题拆分、量词匹配、full/partial coverage 和 human-only verification 回归测试 |

## 12. 实现验收标准

实现完成后至少应通过以下回归场景：

1. 六个独立 future directions 不再自动合并成一个 leaf problem；
2. Schema 可以保留著名宽问题为 meta problem；campaign 编译的候选保持为带来源关系的 leaf/progress 记录；
3. 一个 `exists` 问题不能由排除非穷尽子类的 counterexample 关闭；
4. 只有覆盖全部 load-bearing obligations 的 `full` 合同分支能通过完整解决校验；
5. `ci_contract: null` 且需要领域专家审查的严格理论问题可以获得高质量分；
6. 需要有限尺寸外推的问题不会因为缺少机械阈值自动低分，但必须写清声明、数据范围和专家审查任务；
7. quality benchmark 在发布前可以发现问题窗口过大，但不以“AI 无法验证”为理由拒绝问题；
8. 每个发布的 leaf problem 都有且只有一个明确的 `resolution_claim`。

## 13. 核心原则

最终规则可以压缩为一句话：

> 保留开放问题的科学难度和来源广度，但把广度保存在父问题与研究路线中；每个实际派发的问题必须是一个边界闭合、由领域专家能够判断是否解决的具体任务。
