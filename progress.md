# 进度日志

## 会话：2026-10-19（P1 L0 检测器补说法）

- **状态：** 分支 `p1/l0-intent`，接到已合的 #10 上
- **做了：** 规则检测器补上对比、四校、非全、推免、目录、单科线、两个年份、隐私和「有哪些导师」这些说法。只看 dev 和 adversarial
- **结果：** 除 test 外 183 道，全部字段都对 72.68% → 88.52%，操作和隐私都到 100%。已知失败 50 → 21。L1 不变
- **没修：** 后一句省略学校或专业（要会话继承）、口语专业名没写代码、sup-31 的两个裸分数
- **记录：** `docs/kaoyan_p1_notes.md`「L0 检测器按说法补规则」

## 会话：2026-10-16（P1 L2 检索评测）

- **状态：** 分支 `p1/l2-retrieval`，从 main 开
- **做了：** `kaoyan.eval.retrieval` 实现 L2：文档级和片段级的 any/all@3/5/10，分过滤和不过滤，按题型和重合度分组，只报命中数。`eval_kaoyan.py` 加 `--build-index`（只用 git 跟踪的原文建 BM25 索引，和 CI 一致）、`--index`、`--backends bm25,hybrid`。CI 改跑 L0–L2，基线加 L2
- **结果（dev，CI 索引，BM25，过滤）：** 文档 any@5 21/24，片段 all@5 8/20。本机全量索引上 hybrid 和 BM25 每项最多差 1 道
- **记录：** `docs/kaoyan_p1_notes.md`「L2 检索第一次结果」；报告 `docs/eval/2026-10-16-l2.md`、`2026-10-16-l2-local.md`
- **顺带：** 删了本机考研索引里重构时误写进去的 3 个测试块
- **之后：** 修 L0 的 3 条检测器漏判；检索问题（同文档挤占前几名、不带校名的题只查到一所学校的文件）要另找调参题再修

## 会话：2026-10-11（只留考研路径，包改名 kaoyan）

- **状态：** 分支 `refactor/kaoyan-only`，叠在 PR #8 上，PR 目标 main，#8 先合
- **做了：** 删通用文档问答代码（年报对比、通用工具、企业查询扩展、`RAG_PROFILE`、`/v1/ingest`、知识库页、`GENERIC_QUESTIONS`）；非考研问题固定拒答、不调 LLM；包 `doc_agent` 改名 `kaoyan`，`doc_agent.kaoyan` 展平到顶层
- **配置：** 去掉 `CHROMA_DIR`、`RAW_DIR`、`COLLECTION_NAME`、`RAG_PROFILE`；本机 `.venv` 要重新 `pip install -e .`
- **核对：** ruff 通过，pytest 284 通过，L0/L1 门禁不变
- **记录：** `docs/kaoyan_p1_notes.md`「只留考研路径，包改名 kaoyan」
- **之后：** 回到 P1，跑 L2 检索评测

## 会话：2026-10-11（仓库改名 kaoyan，删企业文档演示遗留）

- **状态：** 分支 `chore/drop-enterprise-demo`；GitHub 仓库已改名 `ddmashawty/kaoyan`（旧地址自动跳转），爬虫 User-Agent 里的仓库地址同步改了
- **删了（25 个，只删文件不动代码）：** 企业文档 PRD 和《复测.md》；`data/raw` 两份演示 txt、`data/README.md`、`data/SOURCES.md`、`data/gold/sample_qa.json`；docs 下阶段 4/5 笔记、前后端验收清单和结果、`perf_baseline.json`；脚本 `demo_repro.sh`、`download_demo_data.py`、`ingest_demo.py`、`perf_baseline.py`、`smoke_chat.py`、`smoke_phase4.py`。`data/raw/` 改为整个不入库
- **留着：** `findings.md`、`task_plan.md`、`progress.md` 和考研笔记里提到这些文件的历史记录不改
- **之后：** 第二档——拆掉只服务通用文档问答的代码（通用聊天流程、记忆、年报对比、导出、前端上传/任务面板和 `GENERIC_QUESTIONS`），先出依赖分析，单独开 PR

## 会话：2026-10-11（P1 文档题审完并入评测集）

- **状态：** 同分支 `p1/adversarial`（PR #6 追加提交）
- **做了：** 开发助手初核、用户认可的 30 道文档题（`data/gold/docq_m1.json`）并入评测集；`--freeze-append doc-` 抽 9 道进 test，原 26 道不动，共 218 道（dev 103、test 35、adversarial 80）。doc-09/14/21 的易误伤串改成 `judge_flags`（L3 用）；#4 推免数 value_text 改回原文“≤19”（本机库 SQL 改 8 行）；`.gitignore` 加名单兜底规则和测试
- **核对：** 抽查 21 条全部一致（开发助手初核，用户确认）；并入时修了文档题的年份标注（没提年份标 null）和 doc-19 的 metrics；新增 3 条 L0 已知失败（检测器漏判），基线已重写
- **记录：** `docs/kaoyan_p1_notes.md`「文档题审完并入评测集」，`data/gold/README.md`「追加冻结」
- **之后：** 跑 L2（BM25 进 CI、hybrid 本机）；box 上 `build_part2.py` 要同步名单兜底规则

## 会话：2026-10-11（P1 文档题草稿和核对清单第一轮修订）

- **状态：** 同分支 `p1/adversarial`（PR #6 追加提交）；草稿仍未审、不进评测集，`reviewed_by` / 核对结果留空
- **做了：** 草稿 evidence 改成原文片段列表，评审说超出证据的几处都在原文找到并补上；doc-19 改结构化题；新增 8 道（不带校名 3、跨校 3、原文无答案 2）；must_include 支持备选和归一化（`metrics.normalize_text`），`drafts.check_item` 校验片段和 must_include 覆盖；核对清单加核对提示、按文件分组、两组交叉核对、第二轮抽样计划；L2 评测设计写进笔记
- **隐私：** 27 份含个人信息文件 `git check-ignore` 全部忽略、无一跟踪（本机缺 1 份）；加测试防新名单漏忽略；核对截图规则加进 `.gitignore`
- **待用户：** 5 道 `needs_user_check`（doc-08、09、19、27、29）和 21 条核对
- **记录：** `docs/kaoyan_p1_notes.md`「文档题草稿第一轮修订」「L2 检索评测设计」

## 会话：2026-10-11（P1 导师试点 + 核对清单 + 研招网）

- **状态：** 同分支 `p1/adversarial`（PR #6 追加提交）
- **做了：** 暨大目录“导师”列存进 `directions.advisors`，查询带口径说明；sup-10 加导师事实，L1 通过；`docs/audit/fact_audit_m1.md` 21 条人工核对清单（用户本人核）
- **研招网：** 版权声明禁止未经同意的其他用途和镜像，目录多页要登录；不批量抽，等用户决定人工查询还是找华工学院页面
- **记录：** `docs/kaoyan_p1_notes.md`「导师试点、人工核对清单、研招网」
- **之后：** 用户定华工科目先不补；22 道文档题草稿 `data/gold/docq_m1_draft.json` 待用户逐题审（学费 5、同等学力 4、复试算分和形式 6、推免 2、其他 5；中大 8、暨大 5、华师 5、华工 4）

## 会话：2026-10-11（P1 修 L1 四个失败）

- **状态：** 同分支 `p1/adversarial`（PR #6 追加提交）
- **做了：** 精简视图保留每条计划数和口径；多代码查询按代码分别调用；筛选返回 `excluded_programs`（原因 + 科目或计划依据）；新增 `exclude_subject` 反向筛选（工具、API、意图识别）
- **结果：** L1 整题命中 98.4%（原 95.2%），L0 不变；已知失败清单只删不增。报告 `docs/eval/2026-10-11-l1-fixes.md`
- **代价：** 全库精简视图输出约 5.2 万 → 8.6 万字符
- **记录：** `docs/kaoyan_p1_notes.md`「L1 四个失败」

## 会话：2026-10-11（P1 冻结 test + 已知失败清单）

- **状态：** 同分支 `p1/adversarial`（PR #6 追加提交）
- **做了：** 核第 12 题年份：scnu-017 是 2025 届报名数，`sources.json` 改年份，sup-12 改为不能算报录比；第 10 题按用户四条重写裁判要求；`--freeze-test` 按“学校 × 类别”分层抽 26 道 test 并冻结（`data/gold/split_m1.json`，记内容哈希）；评测默认不跑 test，跑也只报总分；baseline 加逐题 `known_failures`，CI 拦新失败，也拦“修好了还挂在清单上”
- **核对：** 加题前后逐题比，原 148 道无一从对变错；比例门槛确实被加题拉低，逐题清单补上了这个口子
- **结果：** 默认切分 162 道，L0 71.0%、L1 95.2%。报告 `docs/eval/2026-10-11-split.md`
- **记录：** `docs/kaoyan_p1_notes.md`「冻结 test 和已知失败清单」、`data/gold/README.md`「test 切分」「回退门槛」

## 会话：2026-10-11（P1 补充集 40 道）

- **状态：** 同分支 `p1/adversarial`（PR #6 追加提交）
- **做了：** 用户出 40 道补对抗集偏差，写成 `data/gold/supplement_m1.json`（`sup-*`）；`--adversarial` 改名 `--specs`，支持多个手写文件、`field=` 单科线、`sources` 纯文档出处；L1 认单科线；改 adv-pool-08 年份、adv-bd-05 补事实
- **发现：** 7 道的期望和库里不符（学费、学制、导师、报名人数其实有原文；华工科目没取到），按原文改并写 `label_change`
- **结果：** 188 道；L0 71.3%、L1 95.9%；新增 4 道 L1 真实失败留给 P3。报告 `docs/eval/2026-10-11.md`，基线已重写
- **记录：** `docs/kaoyan_p1_notes.md`「补充集 40 道」、`data/gold/README.md`「补充集」

## 会话：2026-10-10（P1 对抗集）

- **状态：** 分支 `p1/adversarial`（叠在 `p1/v1-facts` 上，PR #5 待合并）
- **做了：** `data/gold/adversarial_m1.json` 80 道（8 类陷阱 × 10），`build_gold_candidates.py --adversarial` 按键从库取值和来源（只认 verified，`checked` 需写原文依据）；报告、基线、CI 门槛按切分和陷阱分组
- **审查：** 自查改 5 处意图；和用户定 4 条规则（不带姓名的逐人分数不算隐私、未发布年份可补数但标年份、超范围比较记 lookup、多年份记问的那年）
- **结果：** 148 道。dev / unsplit 不变；对抗题 L0 62.5%、L1 96.4%，缺口清单留给 P3。报告 `docs/eval/2026-10-10-adversarial.md`
- **记录：** `docs/kaoyan_p1_notes.md`「对抗集」、`data/gold/README.md`「对抗集」

## 会话：2026-10-10（P1 回填旧 18 题事实）

- **状态：** 分支 `p1/v1-facts`（从 `main` `8bf7470`，PR #3、#4 已合并）
- **做了：** `data/gold/v1_review.json`（51 条事实，交互审查定 v1-11 / v1-18 / 核对人），`--migrate-v1` 应用；L1 多认 `exam_subjects`、`admission_stats`，并读 `search_programs` 的 `score_lines`
- **结果：** L1 66 道 100%，L0 97.1%；新报告 `docs/eval/2026-10-10.md`，基线已重写
- **记录：** `docs/kaoyan_p1_notes.md`「v1 回填」

## 会话：2026-10-09（P1 评测 L0 + L1，不调 LLM）

- **状态：** 分支 `p1/eval-l0-l1`；M0 已开 [PR #3](https://github.com/ddmashawty/enterprise-document-intelligence-agent/pull/3)
- **记录：** `docs/kaoyan_p1_notes.md`；第一份报告 `docs/eval/2026-10-09.md`
- **做了：** `src/doc_agent/eval/`（metrics、runner）、`scripts/eval_kaoyan.py`、CI 建库后跑 L0/L1 并和 `docs/eval/baseline.json` 比（掉超过 2 个百分点失败）；修正 v1-15 金标准代码（0812Z3）
- **结果：** L0 全字段 97.1%（“四校”识别不出学校，2 道）；L1 50 道全命中（同库生成，只防回归）
- **验证：** `ruff check` 通过；`pytest -q` 261 passed（255 + 6）；CI 方式新建库上 L0/L1 结果和本机库相同
- **下一步：** 回填 v1 的 `expected_facts`；扩充评测集

## 会话：2026-10-08（M0 工程骨架，不调 LLM）

- **状态：** 骨架已提交到 `m0/skeleton`（`4e39661`）；候选题核对未提交
- **计划：** `docs/kaoyan_improvement_plan.md`。本阶段记录：`docs/kaoyan_m0_notes.md`
- **做了：** `pyproject.toml`、MIT `LICENSE`、README 版权说明、CI（ruff + pytest）、评测集说明、18 题迁入 `data/gold/kaoyan_eval_v2.jsonl`（split=dev）、60 道模板候选 `data/gold/candidates_m0.jsonl`、`run_agent` 汇总 token
- **候选题核对：** agent 逐道对照本机官方原文，决定写在 `data/gold/review_m0.json`，数字无错值。和用户交互审查两轮后：收 20 / 改 29 / 丢 11，手写补 1 道（暨大 0812 2027 统筹 24）；新增字段 `judge_rubric`。`build_gold_candidates.py --apply-review` 把 50 道以 `rv-*`、`split=unsplit` 并入评测集，共 68 道
- **没做：** 不跑 `smoke_kaoyan.py`（18 题要花 DeepSeek）。`docs/baseline_2026-10.json` 不存在。2024–2025 复试线没有去官网逐份核对
- **验证：** `ruff check` 通过；`pytest -q` 255 passed（原 250 + 本阶段 5）

## 会话：2026-10-07（考研改造 K7：OCR / 视觉 + 前端 + 文档）

### K7
- **状态：** complete（待用户看 diff 摘要后提交）
- 新增 `ingest/ocr_table.py`（按线框还原表格、合并格展开）、`kaoyan/roster.py` + `scripts/roster_stats.py`（扫描名单只出每个专业代码的行数）、`kaoyan/extract/scut_baseline_img.py`、`scut_subjects_img.py`、`requirements-ocr.txt`、`frontend/kaoyan_view.py`、`frontend/components/programs_panel.py`、`tests/test_kaoyan_ocr.py`、`tests/test_frontend_kaoyan_view.py`、`docs/kaoyan_phase7_notes.md`
- 修改 `ingest/ocr.py`（rapidocr / vision / 缓存）、`loaders.py`（图片 OCR、扫描 PDF 页渲染 + OCR）、`pipeline.py`（名单文件强制不 OCR）、`config.py` + `.env.example`（`OCR_CACHE_DIR`、`VISION_*`）、`extract/run.py`（`extraction_method=ocr`）、`extract/evaluate.py`（未复现原因）、`sysu_retest_html.py`（也匹配图片）、两个脚本加 `--ocr`、`requirements*.txt` 显式写出 Pillow / pypdfium2、前端 `api_client.py` / `app.py` / `ingest_panel.py`、README、`data/README.md`
- `PYTHONPATH=src pytest -q`：250 passed（223 + 27；vision 用 MockTransport，rapidocr 真实识别在缺依赖 / 缺图片时 skip）
- 6 张图片 OCR（rapidocr，首次 6.5 s、缓存 0.2 s）：61 条事实，17 条与种子一致（`verified=1`），15 条种子没有（`verified=0`），29 条在 37 个专业以外，0 冲突；评估报告种子复现 363 → 380
- 本机 `data/kaoyan.db` 已用 `--ocr rapidocr` 重新抽取（写入 32 条 OCR 事实）；运行前备份 `/tmp/kaoyan_before_k7.db`
- 前端：本机起 API + Streamlit 检查“专业筛选”页（37 个专业、口径 / 来源链接、详情）和 18 条演示问句
- 未能真实验证：vision（`.env` 无 `VISION_*`）；scut-044 扫描名单本机没有（本地专用文件）

## 五问重启检查（K7）
| 问题 | 答案 |
|------|------|
| 我在哪里？ | K7 完成，等用户看 diff 摘要后提交；K0–K7 全部完成 |
| 我要去哪里？ | 可选：包改名（单独 PR，等用户决定）；配置多模态模型后对比 vision 与 rapidocr；下载 scut-044 后人工核对名单计数 |
| 目标是什么？ | 考研信息 Agent：每个数字带来源 / 年份 / 口径，不知道就说不知道 |
| 我学到了什么？ | 政府 / 高校通知里的表格图片线框规整，用“长墨迹 = 线”就能还原合并格，比让 OCR 猜表结构可靠；彩色表头是实心带，要把它的两条边当分隔线；OCR 与种子键不同（门类线 vs 按专业展开）时宁可不命中，也不替人展开口径 |
| 我做了什么？ | 两个 OCR 后端 + 缓存、表格还原、扫描页 OCR、名单计数、2 个图片抽取器、专业筛选页、4 个前端客户端方法、27 条新测试、README / data README / 阶段说明 |

## 会话：2026-10-07（考研改造 K6：采集层）

### K6
- **状态：** complete（已提交 `13dab2a`；补跑与正文指纹修复已提交 `6e11eac`）
- 新增 `collect/base.py`、`dedupe.py`、`http.py`（`PoliteClient`：按 host 串行 ≥3 s、robots、重试 / 退避、条件 GET、每校请求上限、登录跳转 → blocked）、`generic_list.py`、`adapters/{sysu,scut,jnu,scnu}.py`、`sites.py` + `sites.json`、`crawler.py`（运行编排、登记 / 版本 / 下架、附件、手动导入）、`scripts/crawl_kaoyan.py`
- 修改 `config.py`（`crawl_*`）、`.env.example`、`api/routes_kaoyan.py` + `schemas_kaoyan.py`（`POST /v1/crawl` → 202 + `run_id`，`GET /v1/crawl/{run_id}`）
- `PYTHONPATH=src pytest -q`：223 passed（196 + 27，全部 MockTransport，不联网）
- 文档：`docs/kaoyan_phase6_notes.md`

### 真实 probe（2026-10-07，本机网络，UA `kaoyan-info-agent/0.4`）
命令：`python scripts/crawl_kaoyan.py --mode probe [--no-dry-run]`，四校、每校列表第 1 页、同 host 间隔 ≥3 s、每校上限 20 次请求。

| 运行 | run_id | 请求 | 耗时 | 文章（新 / 已知） | 登记 | 未变化 | 提醒 | 错误 / blocked |
|------|--------|------|------|------------------|------|--------|------|----------------|
| dry-run（修复前） | `crawl_c58eb0ae290e` | 8 | 41.9 s | 中大被 robots 跳过、华师 404、暨南标题混摘要 | — | — | — | — |
| dry-run | `crawl_0a8a4af50229` | 11 | 42.4 s | 93（79 / 14） | 0 | 0 | 7 | 0 / 0 |
| 非 dry-run | `crawl_41795309a6f4` | 80 | 321.1 s | 93（79 / 14） | 67 | 2 | 7 | 0 / 0 |

非 dry-run 分校：

| 学校 | 请求 | 耗时 | 列表条目 | 新 / 已知 | 登记 | 未变化 | 探测 |
|------|------|------|----------|-----------|------|--------|------|
| 中大 | 20 | 61.2 s | 20 | 17 / 3 | 17 | 0 | 文章 ID 543 起无新文章 |
| 华工 | 20 | 58.7 s | 13 + 6（两个栏目） | 15 / 3 | 15 | 2（`scut-043`、`scut-051`，sha256 相同） | 目录系统可访问，年度下拉 2027 |
| 暨南 | 20 | 84.6 s | 23 | 20 / 3 | 18 | 0 | 2028 目录栏目 410（未发布） |
| 华师 | 20 | 116.5 s | 22 + 20（两个栏目） | 27 / 5 | 17 | 0 | 目录年份下拉出现 2027 |

- `documents` 97 → 164（全部 `active`）；新登记 4 篇按标题标为含个人信息（拟录取 / 递补复试名单、咨询人员名单），只存在被忽略的 `data/kaoyan/cache/`（本机 5.9 MB）
- 每校预算都用完：新文章先登记，暨南 2 篇、华师 10 篇未登记，中大 / 暨南 / 华师的已知文章未复查，下次运行补上
- 7 条提醒：中大 / 华工 2027 招生章程、华工 2 个联培项目 2027 简章（误报，可接受）、华工目录系统可访问、华师 2027 目录（文章 + 下拉）
- 运行前已备份本机库到 `/tmp/kaoyan_before_k6.db`；库文件、缓存不入 git

### 补跑非 dry-run（2026-10-07 10:50，`--max-pages 40`）
| run_id | 请求 | 耗时 | 新 / 已知 | 登记 | 未变化 | 变化 | 下架 | 错误 / blocked |
|--------|------|------|-----------|------|--------|------|------|----------------|
| `crawl_99061ce8e6ac` | 104 | 437.4 s | 12 / 81 | 12 | 76 | 5 → 0（误判，已清理） | 0 | 0 / 0 |

| 学校 | 请求 | 耗时 | 新 / 已知 | 登记 | 未变化 | 变化 |
|------|------|------|-----------|------|--------|------|
| 中大 | 23 | 70.4 s | 0 / 20 | 0 | 20 | 0 |
| 华工 | 21 | 61.7 s | 0 / 18 | 0 | 18 | 0 |
| 暨南 | 25 | 96.3 s | 2 / 21 | 2 | 21 | 0 |
| 华师 | 35 | 208.9 s | 10 / 22 | 10 | 17 | 5（误判） |

- 上次没登记的暨南 2 篇、华师 10 篇已登记；第一次登记的 67 篇在复查中全部判为未变化（sha256 / 304 / 正文指纹）
- 华师 5 篇种子文档（`scnu-021 / 028 / 029 / 030 / 033`）被判为“变化”：差异全在边栏（最新消息 / 本周图文 / 热门消息出现 9-28 新发的 2027 目录）和“上一篇 / 下一篇”，正文一字未改。修复：`sites.json` 加每校 `content_selector`，正文指纹只取正文文字 + 正文内链接 / 附件地址；离线用同样的字节复核 5 篇均判为未变化，删除误建的 5 条 `-v2` 记录和缓存文件，父文档 `last_seen` 更新为本次时间
- `documents` 164 → 176，全部 `active`、无版本记录；已知文章全部复查完毕。运行前备份 `/tmp/kaoyan_before_k6_run2.db`

## 五问重启检查（K6）
| 问题 | 答案 |
|------|------|
| 我在哪里？ | K6 完成，等用户看 diff 摘要后提交 |
| 我要去哪里？ | K7：OCR / 视觉（rapidocr + 多模态）、专业筛选前端页、README / docs 更新 |
| 目标是什么？ | 考研信息 Agent：每个数字带来源 / 年份 / 口径，不知道就说不知道 |
| 我学到了什么？ | 真实站点和种子记录会漂移：robots.txt 被 WAF 拦、栏目改版 404、原来被统一认证拦的目录系统现在能访问；所以必须先 dry-run 看结果再落库，并让 `sites.json` 能覆盖种子 |
| 我做了什么？ | 礼貌 HTTP 客户端、4 个站点适配器、变化检测与版本、crawl API / CLI、27 条新测试、三次真实 probe（含补跑） |

## 会话：2026-09-29（考研改造 K5：Agent 工具、提示词、API）

### K5
- **状态：** complete（已提交 `a1c5ee7`）
- 新增 `kaoyan/query.py`（查询服务：筛选、统招取值、复试线口径、科目、对比表行、文档元数据）、`tools/kaoyan.py`（6 个结构化工具 + citation / 导出行 / LLM 视图）、`agent/kaoyan_flow.py`（按意图确定性调工具、reflect、finalize 数字校验）、`api/routes_kaoyan.py` + `schemas_kaoyan.py`（`/v1/programs`、`/v1/programs/{id}`、`/v1/score-lines`、`/v1/documents`、`/v1/documents/{id}`）、`scripts/smoke_kaoyan.py`
- 修改 `guardrails.py`（考研意图识别、数字校验）、`prompts.py`（考研 15 条规则）、`nodes.py`（按意图分流，企业路径不变）、`state.py` / `graph.py`（`intent`、`facts`）、`registry.py`（注册工具，`rag_search` 加 `profile`）、`/health` 加可选字段、`__version__` 0.4.0 并用于 `create_app()`
- `PYTHONPATH=src pytest -q`：196 passed（139 + 57）；`test_api_errors.py` 未改
- 真实 DeepSeek 冒烟 `smoke_kaoyan.py`：18/18（数值 14/14、未知 2/2、隐私 1/1、导出 1/1、引用 18/18）；企业 `smoke_chat.py` 3/3
- 文档：`docs/kaoyan_phase5_notes.md`

## 五问重启检查（K5）
| 问题 | 答案 |
|------|------|
| 我在哪里？ | K5 完成，等用户看 diff 摘要后提交 |
| 我要去哪里？ | K6：采集层（`collect/`、按 host 限速 ≥3s、robots、`POST /v1/crawl` 默认 dry-run），全部用 MockTransport 测试 |
| 目标是什么？ | 考研信息 Agent：每个数字带来源 / 年份 / 口径，不知道就说不知道 |
| 我学到了什么？ | 工具调用交给规则后，模型失误集中在措辞：会主动讲没问的缺口、用有歧义的“考 408”；数字校验要豁免学科代码这类“看起来像数字”的标识 |
| 我做了什么？ | 查询服务、6 个工具、意图识别、数字校验、考研提示词、Agent 分流、5 个接口、57 条新测试、真实冒烟 18/18 |

## 会话：2026-09-28（考研改造 K4：结构化抽取）

### K4
- **状态：** complete（已提交 `d933603`）
- 新增 `kaoyan/extract/`：10 个规则抽取器（中大细则 / 校线 / 目录 PDF、暨南目录 / 复试方案 xlsx / 推免 PDF、华师目录 / 推免 xls / 复试方案、华工计划）、`apply.py`（专业解析 + 与种子逐键比对 + 幂等写库）、`run.py`、`llm_fallback.py`（证据片段必须在原文中）、`evaluate.py`
- 脚本 `scripts/extract_kaoyan.py`、`scripts/eval_extraction.py` → `docs/kaoyan_extraction_report.md`
- 四类验收逐值一致、冲突 0；450 条种子复现 363 条，其余为名单统计 / 图片 / 种子判断；504 条规则事实全部带来源和证据；本机 `data/kaoyan.db` 已抽取
- `PYTHONPATH=src pytest -q`：139 passed（113 + 26）
- 文档：`docs/kaoyan_phase4_notes.md`

## 五问重启检查（K4）
| 问题 | 答案 |
|------|------|
| 我在哪里？ | K4 完成并已提交（`d933603`） |
| 我要去哪里？ | K5：考研结构化工具（search_programs / get_score_lines / …）、考研提示词与数字校验、`/v1/programs` 等接口 |
| 目标是什么？ | 考研信息 Agent：每个数字带来源 / 年份 / 口径，不知道就说不知道 |
| 我学到了什么？ | 真实表格的特殊行靠备注 / 括号说明区分，且备注常在双层表头的另一行；跳过考生表要看表头，不能看数据行 |
| 我做了什么？ | 抽取协议、10 个规则抽取器、比对写库、LLM 证据校验、评估报告、26 条新测试 |

## 会话：2026-09-28（考研改造 K3：检索元数据 + 考研索引）

### K3
- **状态：** complete（已提交 `0aa958f`）
- chunk 带 doc_id / school / college / year / doc_type / title / url / redacted；表格按行切块，重复标题 + 表头 + 学院 / 专业行
- 独立考研索引 `data/chroma_kaoyan/`（`scripts/ingest_kaoyan.py`）：90 文档、1926 chunk、hybrid；检索可按学校 / 年份 / 类型过滤，问题里只有一所学校时自动过滤
- 考研排序先验（名单降权、文档类型 / 学院匹配）；golden hit@5：BM25 16/18、hybrid 15/18
- 企业语料：chunks 逐字节相同、检索结果与工具输出一致、`smoke_chat.py` 3/3
- `PYTHONPATH=src pytest -q`：113 passed（100 + 13）
- 文档：`docs/kaoyan_phase3_notes.md`

## 五问重启检查（K3）
| 问题 | 答案 |
|------|------|
| 我在哪里？ | K3 完成，等用户看 diff 摘要后提交 |
| 我要去哪里？ | K4：规则抽取器（暨南目录、华师目录 / 推免 xls、中大细则 / 校线）+ LLM 兜底，与种子逐条比对 |
| 目标是什么？ | 考研信息 Agent：每个数字带来源 / 年份 / 口径，不知道就说不知道 |
| 我学到了什么？ | 名单类文档在词频检索里天然占优，必须用元数据先验压下去；先验只在候选池里生效，池子要够大 |
| 我做了什么？ | chunk 元数据、按行切表、考研索引与过滤、查询扩展与先验、单元格级脱敏、13 条新测试 |

## 会话：2026-09-28（考研改造 K2：多格式解析层）

### K2
- **状态：** complete（已提交 `f6ea30f`）
- 新增 `ingest/{tables,ocr,redact}.py`、`kaoyan/privacy.py`、`collect/attachments.py`；`loaders.py` 支持 html / xlsx / xls / 图片与 PDF 抽表，`ParsedDocument` 带表格 / 图片 / `needs_ocr`
- 个人信息：名单文件在 `load_document` 里脱敏（姓+某、删编号列）；泄漏扫描发现并修复公示正文“拟录取X等N人”首名
- 临时库 ingest `data/kaoyan/raw`：96 文件，90 indexed / 0 failed / 7 needs_ocr / 26 redacted；4187 个姓名 0 泄漏
- `PYTHONPATH=src pytest -q`：100 passed（70 + 30）
- 文档：`docs/kaoyan_phase2_notes.md`

## 五问重启检查（K2）
| 问题 | 答案 |
|------|------|
| 我在哪里？ | K2 完成，等用户看 diff 摘要后提交 |
| 我要去哪里？ | K3：chunk 元数据（school / year / doc_type）、独立考研索引、表格按行切块 |
| 目标是什么？ | 考研信息 Agent：每个数字带来源 / 年份 / 口径，不知道就说不知道 |
| 我学到了什么？ | 名单以外的公示正文也会带姓名；泄漏扫描要扫全库而不是只扫名单文件 |
| 我做了什么？ | 多格式解析 + 表格模型、附件发现、OCR 接口、脱敏与统一入口、30 条新测试 |

## 会话：2026-09-27（考研改造 K1：种子库 + golden）

### K1
- **状态：** complete（已提交 `125b5a0`）
- 新增 `src/doc_agent/kaoyan/`（schema / db / models / normalize / seed）、`scripts/seed_kaoyan.py`、`scripts/verify_kaoyan_bundle.py`、`data/gold/kaoyan_qa.json`（18 条）、两组测试
- 联网下载中大 2026 目录 PDF（5.2MB，sha256 与 manifest 一致，本地专用不入库）；verify：96 ok / 1 missing(warn) / 0 bad
- 种子：37 专业、97 文档、`seed_only` 0；K1 七组断言全部通过；幂等
- `PYTHONPATH=src pytest -q`：70 passed（21 + 49）
- 文档：`docs/kaoyan_phase1_notes.md`

## 五问重启检查（K1）
| 问题 | 答案 |
|------|------|
| 我在哪里？ | K1 完成，等用户看 diff 摘要后提交 |
| 我要去哪里？ | K2：多格式解析层（html / xlsx / xls / 图片 / 扫描 PDF 识别、附件发现） |
| 目标是什么？ | 考研信息 Agent：每个数字带来源 / 年份 / 口径，不知道就说不知道 |
| 我学到了什么？ | 页面级 URL 对应多个文档，来源要按事实类型打分；一级学科统筹值要带 `pool_scope`，统计表也一样 |
| 我做了什么？ | 建结构化库与种子、备注窄正则（含真实字符串单测）、golden 18 条、包校验脚本 |

## 会话：2026-09-27（考研改造 K0：阅读 + 基线 + 计划）

### K0
- **状态：** in_progress（计划已写，等待用户确认）
- 种子包 `kaoyan_bundle_lite.zip` 解压到 `data/kaoyan/`；`git status` 80 个未跟踪文件，28 条忽略规则生效
- 基线 `PYTHONPATH=src pytest -q`：21 passed
- manifest：95/97 sha256 一致，缺 2 个 >3MB 本地专用文件（lite 包不含）
- 阶段 6 前端按代码核对：F1–F3 已完成，F4 未做；已更正 task_plan
- 计划：`task_plan.md` 阶段 K0–K7

## 五问重启检查（K0）
| 问题 | 答案 |
|------|------|
| 我在哪里？ | K0 完成计划，等用户确认 |
| 我要去哪里？ | 确认后执行 K1（种子库 + golden） |
| 目标是什么？ | 考研信息 Agent：每个数字带来源 / 年份 / 口径，不知道就说不知道 |
| 我学到了什么？ | 备注写法比需求里列的多（暨南 2026 统招计划、拟录取人数等），K1 正则要覆盖 |
| 我做了什么？ | 通读仓库与种子包、跑基线、写 K0–K7 计划 |

## 会话：2026-09-06（终测 + 前端计划）

### 后端终测
- **状态：** complete / 可发布
- pytest 13；phase4 smoke OK；async done；人工 P0–P4 通过
- 修复 smoke_chat 空格判分；结论见 `docs/final_backend_test.md`

### 阶段 6：前端
- **状态：** pending（计划已写）
- 文档：`docs/frontend_implementation_plan.md`

### 阶段 5
- **状态：** complete

## 五问重启检查
| 问题 | 答案 |
|------|------|
| 我在哪里？ | 后端已终测可推送；前端计划已就绪 |
| 我要去哪里？ | 推送 GitHub 后实施 F1 |
| 目标是什么？ | Streamlit 可视化全流程 |
| 我学到了什么？ | 冒烟判分需归一化中文空格 |
| 我做了什么？ | 终测 + 前端计划 |

---
*每个阶段完成后或遇到错误时更新此文件*
