# EconLens 析经济 — IB Economics IA Commentary Coach

在学生交出「唯一一轮官方草稿」之前，提供无限轮 **criterion-referenced** 练习反馈。
**AI 不代写**：只按 IB 评分标准逐条诊断、引用学生自己的句子、用苏格拉底式追问逼学生自己改好；每一轮留痕，可导出修改过程档案。

## 它解决什么问题

IB 经济 IA = 3 篇基于真实新闻的 commentary（各 ≤800 词），占 HL 最终成绩 20% / SL 30%。
IB 规定教师对正式草稿**只能给一轮书面反馈**——学生最大的损失是用低质量草稿浪费这一轮。
EconLens 让学生在见老师之前，先把机械性问题和 criterion 短板自己解决掉。

## 功能

| 模块 | 依赖 | 说明 |
|---|---|---|
| **Preflight 机械预检** | 无（完全离线） | 词数（800 上限）、图表引用、术语密度（内置约 90 词经济术语词库）、九大 key concept 检测、与文章贴合度（4-gram 重叠 + 数字复用）、评价角度线索（利益相关者/长短期/幅度/假设/反方）、段落结构 |
| **Claim / Evidence Map**（v0.2） | 无 | 逐句检测因果与评价性 claim，标记「附近无证据信号」的观点（同句或相邻句无数字/引文/文章引用即警告）——"这个观点可能需要证据" |
| **IA 工作区检查**（v0.2） | 无 | Focus question 质检（是否问句/长度/含经济概念/含糊措辞）；文章元数据（来源、发表日期是否在一年窗口内） |
| **Portfolio 状态**（v0.2） | 无 | 跨三篇 commentary 的 rubric 要求：不同 unit / 不同 key concept / 不同来源、篇数进度 |
| **AI criterion 反馈** | OpenAI 兼容 API | A–E 五个 criterion 逐条：分带估计＋优点＋问题＋苏格拉底追问。**反代写合同**（违反即拒绝展示，自动重试一次）：引文必须逐字存在于草稿、所有字段 ≤45 词、禁止改写类措辞、追问必须以问号结尾 |
| **Workflow Coach**（v0.3） | 可选 API，无 key 同格式回退 | 四个工作流原生教练（RQ / Claim / Evaluation / Draft），只回答一个问题：**「你现在的推理哪一步还没有被证明？」**——从不回答「应该怎么写」。详见下节 |
| **过程档案** | 无 | 每轮草稿 SHA-256、词数、与上一轮相似度、分带轨迹 → 一键导出 HTML 诚信报告 |

## 快速开始

```bash
pip install -r requirements.txt
uvicorn econlens.app:app --port 8760
# 打开 http://127.0.0.1:8760
```

不配 API key 也能用（预检 + 过程档案）。开启 AI 反馈（OpenAI 兼容端点均可：DeepSeek / Kimi / Qwen / OpenAI）：

```
set ECONLENS_API_KEY=sk-...
set ECONLENS_BASE_URL=https://api.deepseek.com   （默认值）
set ECONLENS_MODEL=deepseek-chat                 （默认值）
```

`samples/` 内附一篇合成练习文章与一份 round-1 草稿，可直接粘贴体验。

## Workflow Coach（v0.3）

不是 ChatGPT for Economics——AI 被嵌进已存在的确定性工作流，每个阶段只做机械规则做不到的判断：

| 教练 | 输入 | 只做的事 |
|---|---|---|
| **RQ Coach** | 确定性 RQ 检查结果 | 判断变量/因果方向/时间范围/经济概念是否足够聚焦，禁止代拟 RQ |
| **Claim Coach** | Claim/Evidence Map 结构化结果 | 判断 claim 强度是否超过证据强度，区分 evidence missing / adjacent-but-insufficient / claim overreach / evaluation claim unsupported——只指出推理缺口 |
| **Evaluation Coach** | 确定性评价链检测 | 沿 criterion → mechanism → consequence → judgement 报「缺哪一步」，不生成评价段落 |
| **Draft Coach** | preflight + claim map | 按段落反馈，每条必须引用已有检测结果 |

**Coach Contract**（schema 校验，违约拒绝并重试一次）：每条 finding = `stage / finding / evidence / question / severity`；question 必须以问号结尾；**evidence 必须锚定**——`check:<检测id>` 或 `quote:<草稿逐字原文>`，二者皆无即拒绝；禁代写措辞；单次最多 8 条。**AI 输出永不改变机械评分结果**（preflight 先算、原样返回）。

**同格式回退**：无 key / API 挂掉时，确定性检测直接映射成同一 contract 形状（内置苏格拉底问题库），UI 只认一种结构化格式再渲染成自然语言——换 DeepSeek/OpenAI 兼容任何 provider 都不动产品逻辑。

**实验记录**：每次 coach 调用存 `coach_log`（input SHA-256 快照、确定性检测快照、coach 输出、来源 ai/deterministic、学生 action：accepted/dismissed/revised＋备注）；before/after 由轮次 diff 自然衔接——为 pre/post 与 A/B 实验预留了数据结构。

产品规则一句话：Coach 不回答"应该怎么写"，只回答"你的推理哪里还没被证明"。示例输出：
> Evidence supports the change in quantity demanded, but your claim also asserts a welfare improvement. What evidence would allow you to justify that second step?

## 测试

```bash
python -m pytest tests/ -q     # 88 项，全部离线（LLM 走 mock），无需网络与 key
```

覆盖：rubric 一致性校验、八项预检的正反用例、claim/evidence 映射（因果/评价/复合型、相邻句证据、无证据警告）、focus question 与来源日期检查、portfolio 重复 unit/concept/source 检测、反代写合同的全部拒绝路径（幻觉引文/越界分带/改写措辞/超长字段/非问句）、重试恢复与重试失败、会话存储与相似度、路径穿越防护、API 端到端。

## 诚实边界

- 评分标准按 IB Economics Guide（first assessment 2022）转写：A 图表(3) / B 术语(2) / C 应用与 key concept(2) / D 分析(3) / E 评价(4)，单篇 14 分、portfolio 45 分。**高风险使用前请对照当年最新 Guide 核对**（`rubric/ib_econ_ia.json` 单文件可改，改错会被启动时一致性校验拦住）。
- AI 分带估计是**练习参考**，不是预测分数；正式评分以教师与 IB moderation 为准。
- 过程档案证明「修改过程逐轮发生在本工具内」，**不能证明所有文字都由学生撰写**——报告内已明文声明此边界。
- 未做真实学生对照实验；教学有效性（盲评分数增益）待验证。
