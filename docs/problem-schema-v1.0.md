# Problem Schema v1.0

这份 schema 用于描述可交给 Agent 或人类研究者探索的开放科学问题。出题重点是题面和文献状态；验证难度只是内部的可选估计。

## JSON 格式

```json
{
  "schema_version": "1.0",
  "problem_id": "problem-id",

  "parent_problem_id": null,
  "subproblem_ids": [],

  "title": "题目名称",

  "abstract": "问题的简短概述。",

  "background": "理解问题所需的背景、定义和已知结果。",

  "references": [
    "参考文献或来源"
  ],

  "previous_progress": [
    "该问题此前已经取得的进展"
  ],

  "problem_statement": "完整、明确、自包含的题面。",

  "scientific_significance": {
    "affected_field": {
      "level": "high",
      "description": "具体说明这个领域会受到什么影响。"
    }
  },

  "solution_difficulty": [
    "一个可能的求解难点",
    "另一个可能的求解难点"
  ],

  "verification_difficulty": {
    "score": 5,
    "rationale": "根据题面设想已完成的解答，排除明确可机械核查的部分后，说明剩余的独立推理和科学判断负担。"
  }
}
```

## 字段说明

- `schema_version`：schema 的版本。
- `problem_id`：问题的稳定标识。
- `parent_problem_id`：直接父问题；没有父问题时为 `null`。
- `subproblem_ids`：直接子问题的 ID 列表。
- `title`：简短、明确的题目名称。
- `abstract`：问题的简短概述。
- `background`：理解问题所需的背景、定义和已知结果。
- `references`：相关文献或其他来源。
- `previous_progress`：此前已经取得的研究进展。
- `problem_statement`：完整、明确、自包含的题面。
- `scientific_significance`：受影响领域、影响等级以及具体影响。
- `solution_difficulty`：可能遇到的求解难点列表，不打分。
- `verification_difficulty`：内部选填字段，包含整个问题的验证难度分数和理由；无法可靠估计时可省略或设为 `null`。
- 历史 `verification_contract` 字段仅保留读取兼容，也是选填；新 Research/Reviewer 不生成验收合同、答案类型列表或 CI。

业务 schema 允许省略验证字段。Codex 的严格结构化输出模式只在调用边界将可为 `null` 的选填字段编码为必填键，用 `null` 表示未评估；本地记录校验仍使用原始选填 schema。Claude 等提示词输出模式可直接省略字段。

`problem_statement` 的问题窗口按整体科学目标判断，而不是按方法、成果或潜在论文的数量判断。一个问题可以同时要求方法开发、计算、分类和物理解释，只要这些结果在科学上相互关联，并共同支撑一个领域专家能够识别其完成边界的目标。“删除一项后其余内容仍可发表”以及“某部分可独立发表”只能作为进一步审查的提示，不能自动判定题目过宽。相对表述在引用文献、上下文或领域惯例足以让专业审稿人稳定理解时也是允许的；不得为了机械化验收而发明任意数值阈值。

## Scientific significance 判定标准

`scientific_significance` 不设置单一总分，而是对每个受影响领域分别判断：

- `high`：解决问题会直接改变该领域的核心认识、方法或能力。
- `medium`：会带来明确进展，或实质性推动若干后续研究。
- `low`：影响较局部、间接或增量性。

每一项必须同时写明：

1. 哪个领域受到影响；
2. 具体会改变什么；
3. 影响是直接的还是间接的。

## Verification difficulty 评分标准

`verification_difficulty` 是整个问题的一个 `0–10` 内部总分，附简短 `rationale`。省略或为 `null` 表示未评估，不等于 0 分或 10 分，也不阻止编译或发布。

- `0`：排除机械检查后，没有剩余的 Agent 或人类判断。
- `1–3`：剩余少量独立、局部、标准的推理检查。
- `4–6`：剩余相互依赖的推导，或需要重建题面与答案之间的对应关系。
- `7–9`：剩余较长、脆弱或新颖的推理链，或需要大量代码审查。
- `10`：关键结论无法分解，只能依赖整体专家判断。

### 打分思路

1. 依据题面和已经查阅的文献，设想一份真正完成科学目标的解答；不实际求解，不另外设计验收要求。
2. 判断最省力但可靠的核查路径，包括机械代入、已知终止程序、重放或自然可提供的有限见证。
3. 排除明确可机械核查的部分；是否已经自动化不影响结构性评分。
4. 评估剩余的局部推理、相互依赖的推导、题面对应关系和整体科学判断。
5. 在 `rationale` 中简要说明机械部分、剩余负担和总分依据；不输出详细验收流程或 CI。

有限反例评估的是检查见证的成本，不是找到见证的成本。仅当题面本身允许一项见证完成目标时才使用此路径；一个有限实例不能代替需要无限族或极限论证的反驳。通过独立数值复现有限尺寸模型核查精确解时，模型一致性、容差和例外情形留下的轻量残余按现有规则校准为 2 分。完整评分 rubric 由 `src/open_research_discovery/ranking.py` 统一提供给 Research 和 Reviewer。

`verification_difficulty` 衡量的是验收难度，不是解题难度，也不作为出题或发布阈值。

## 父问题与子问题

如果一个父问题已经把求解和验收委托给子问题，可以使用：

```json
{
  "solution_difficulty": [],
  "verification_difficulty": null
}
```

子问题同样以题面为中心；`verification_difficulty` 仍为选填，不要求生成验收合同。
