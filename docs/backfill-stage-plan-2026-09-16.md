# 补录改造 · 阶段执行计划（Stage Plan）

日期：2026-09-16　分支：`feat/expense-staff-tweaks`
状态：**阶段 0 / 1 / 2 / 3 全部完成并推送**（阶段 3 = 3-1 `e25022d` / 3-2 `f85da3f` / 3-3 本次提交）

| 阶段 | 提交 | 落地项 |
|---|---|---|
| 0 前置 | — | 现有改动落盘 + 基线 28/28 + 探针 P0-3/P0-4 + 待补录基线 71 |
| 1 引擎口径 | `02ddfa6` | 判定只看票号 + A4 校验 + 在库票不抹历史收款 |
| 2-1 引擎/落库 | `18622ef` | A5 判定刷新 + A10 确认收款写库 + A11 多期预填展开 |
| 2-2 复核页 | `da4fde2` | A1 应收账款行进队列 + A2 右栏三态 + A3 就地编辑回写 |
| 2-3 守卫/提示 | `dc0c3ba` | A6 离开确认 + A7 侧栏角标/提示条 + A8 台账守卫 + A9 关闭确认 |
| 3-1 引擎层 | `e25022d` | B2a build/apply 拆分 + B2b 补录随台账**同事务**入库 + B2d 校验两次 |
| 3-2 弹窗抽取 | `f85da3f` | B2f `BackfillDialog`（复核页与补录页共用） |
| 3-3 行内按钮 | 本次提交 | B2c 收集进 `data["backfills"]` + B2g/B2h 行内「补录原票」按钮 + B2e 重复票号拦截 |

依据文档：`docs/backfill-decision-plan-2026-09-16.md`（口径与决策；本次复查结论见其第十二节）

---

## 0. 总览

```
阶段 0  前置      现有改动落盘 + 基线回归 + 2 个探针 + 待补录基线快照     ← 不写业务代码   ✅ 已完成
   ↓
阶段 1  引擎口径  工作包 C：判定只看票号 + 日期坏转问题行 + A4 校验 + 在库票跳过   ← 纯引擎，不碰 UI   ✅ 02ddfa6
   ↓
阶段 2  复核期    A1–A11：期外票进队列 + 就地修改 + 判定刷新 + 提示/守卫 + deferred 写库
                 ├─ 2-1 引擎/落库  A5 · A10 · A11                    ✅ 18622ef
                 ├─ 2-2 复核页    A1 · A2 · A3                      ✅ da4fde2
                 └─ 2-3 守卫/提示  A6 · A7 · A8 · A9                 ✅ dc0c3ba
   ↓
阶段 3  补录入库  B2a–B2k：pending 模式（同事务）+ 行内「补录原票」按钮 + BackfillDialog 抽取
                 ├─ 3-1 引擎层    B2a · B2b · B2d                    ✅ e25022d
                 ├─ 3-2 弹窗抽取  B2f                              ✅ f85da3f
                 └─ 3-3 行内按钮  B2c · B2e · B2g · B2h · B2i/B2k   ✅ 本次提交
```

**每阶段独立可交付、独立验收、独立回滚点**；上一阶段验收通过前不进入下一阶段（confirm-per-batch）。
回退一律 `git revert`，**不用** `git reset --hard`。

**关键依赖**：
- ✅ ~~阶段 1 的开工内容取决于 D1 / D2~~ → **D1 / D2 / D3 已于 2026-09-16 18:29 全部拍板**（D1 甲 / D2 维持不并集 / D3 甲），**阶段 1 可直接开工**。
- 阶段 2 的 A7 依赖阶段 0 的**探针 P0-3**（侧栏角标能力）。
- 阶段 3 依赖阶段 2 的 `data["deferred"]` 队列化（A1）与就地编辑（A3）。

---

## 1. 阶段 0：前置（开工前必须完成，不写业务代码）

| # | 事项 | 为什么必须先做 | 完成判据 |
|---|---|---|---|
| **P0-1** | **把现有未提交改动 commit + push** | 当前工作树有 **40 个 `M` + 大量 `??`**（4 项 UI 修复 + ①②④ + ③ C 方案）。若带着它们开工，阶段 1 的改动与旧改动混在一起，**无法单独 revert** | `git status` 干净（或只剩本次不涉及的 `??` 临时文件）；`git log` 可见本次提交 |
| **P0-2** | **基线全量回归** | 需要一个"已知全绿"的起点，才能判断阶段 1 引入的失败 | `tests/*.py` 逐文件跑完 **0 失败 / 0 traceback**；记录文件数与总项数 |
| **P0-3** | **探针：侧栏角标能力** | A7 想给「导入复核」加角标/红点，但 qfluentwidgets 的 `NavigationInterface` 是否原生支持**未验证** | 结论二选一：① 原生支持（记为用法）② 不支持（改为在侧栏项上覆一层自绘小圆点 `QLabel`）→ 决定 A7 的实现方式与工作量 |
| **P0-4** | **待补录基线快照** | 阶段 1 会让「待补录清单」的口径从"日期 + 缺号"变成"只看缺号"，**数量会变**（日期解析失败的 sheet3 行会从**漏掉**变成**纳入**）。必须能对比改前/改后 | 在库副本上跑一次 `list_pending_backfill()` 与 `deferred_sheet3_invoices()`，**把票号清单存成文本**（改后 diff 用） |

> P0-4 的库副本操作沿用既有做法：`app.db.DB_PATH` 直接赋值切到副本 + `imp._auto_snapshot`/`imp._archive_file`/`RP.install_column_layout` 打桩，跑完删脚本，**不碰 `data/lawfirm.db`**。

**测试环境**：`C:/Users/Bingo/.lawfirm_venv/Scripts/python.exe` + 环境变量 `QT_QPA_PLATFORM=offscreen`。

---

## 2. 阶段 1：口径修正（工作包 C）—— 纯引擎层，不碰 UI

**目标**：把补录判定从"看开票日期"改为"看发票号码"，并修掉现状的日期解析隐藏 bug。

### 改动清单

| # | 文件 | 改动点 |
|---|---|---|
| C1 | `app/importer/ledger_import.py` | `is_deferred_invoice()` 语义改造：判据从「sheet3 且 开票月份 < 账期」改为「sheet3 且 **票号不在库**」（**D1 甲 / D2 维持不并集** —— 无需并入本批将建票号） |
| C2 | 同上 | `split_deferred()` 新增入参：**库中已有票号集合**。返回值「移出条数」保持兼容 |
| C3 | 同上 | **sheet3 行一律移出 `invoices`**（不再只移"期外"），并在行上打标记：`need_backfill=True`（票号不在库，需补录）/ `False`（已在库，**跳过建票与分摊、不动 collection**）—— **D1 甲已定** |
| C4 | 同上 | `_parse_invoice_sheet()`：`invoice_date` 解析（现 `:164`，在 `try` **之外**）**挪进 `try/except ImportError_`** → 失败转**问题行**（A1）；**空值放行**不报错 |
| C5 | 同上 | 新增 **A4 硬校验**：sheet3 行开票月份 **≥ 账期** → 抛 `ImportError_` 中止导入（文案见第 6 节）。**定位降级为"数据质量校验"**（安全不再依赖它，见 D2） |
| C6 | `app/importer/importer.py` | **5 个** `is_deferred_invoice` / `split_deferred` 调用点全部带新入参：`ledger_import.parse_ledger_file:271` / `_apply_resolved:543` / **`validate_ledger_before_write:575`**（⚠️ 复查新发现，原计划漏了）/ `import_ledger_file:623` / `commit_ledger_import:638` |
| C7 | 同上 | **在库票分支（D1 甲已定）**：票号在库 → 不建票 / 不写 `charge_detail` / **不写 `collection`**；把台账收款随行带出，供阶段 2 复核页确认。⚠️ 若不做这一步，`_write_collection_for_invoice()`（`importer.py:181` 的 `DELETE FROM collection WHERE invoice_no=? AND source='import'`）会**删掉该票历史收款** |
| C8 | `app/engine/raw_ledger.py` | `deferred_sheet3_invoices()` **去掉 `is_period_before` 过滤**（`:146`），保留 `period`/`batch_id` 限定；`_derive_deferred` 的 `year` 仍取 `_period_year(r["period"])`，不动 |
| C9 | `app/engine/backfill.py` | `is_period_before` 标注为历史用途（**本期不删**，避免连带） |
| C10 | `tests/test_deferred_sheet3.py` | 按新口径重写四组：**票号判定** / **A1 问题行** / **D1 在库票跳过** / **A4 报错**；补 A2 幂等断言 |

### 测试

- `tests/test_deferred_sheet3.py`（重写）
- `tests/test_review_compare.py`（`needs_backfill` 口径不变，应维持全绿）
- `tests/test_raw_ledger_mirror.py`（镜表契约不变）
- 全量回归（阶段 0 基线对照）

### 本机验收点

1. 导入 `2025.1台账.xls` → 建票数与现状一致（**4 张期外票仍不建票**）；
2. 造一行 **sheet3 开票日期乱码** → **不再整份导入失败**，转问题行（可修正后入库）；
3. 造一行 **sheet3 开票日期为空** → 不报错，按票号判定；
4. 造一行 **sheet3 开票月份 = 账期** → **报错中止**，提示可读；
5. 用 P0-4 的基线快照做 diff → **待补录清单变化符合预期**（只应多出"日期解析失败"那类行）。

### 回滚

阶段 1 全部在一个 commit 内 → `git revert <sha>` 即回到阶段 0 状态。

---

## 3. 阶段 2：复核期可见性 + 就地修改 + 队列守卫

**目标**：期限票在导入复核阶段"看得到、改得动"，且判定始终对应当前库状态。

### 改动清单

| # | 文件 | 改动点 |
|---|---|---|
| A1 | `app/ui/unified_import_dialog.py` | 待确认队列行装配**纳入 `data["deferred"]`**；行上带 `need_backfill` 语义。落点：`load_data` 后向 `import_confidence.evaluate(...)`（`:331`）额外注入 deferred 行（或在其后追加行字典） |
| A2 | 同上 | 右栏「原因 / 疑问」承载 **3 种状态**：`需补录原票` / `已入库，请确认收款` / `系统判定无疑问`（`self._info["reason"]`，`:237/:244`） |
| A3 | 同上 | 期外行 / 在库行**复用 `ProblemFixPanel` 就地修改**（开票日期 / 总额 / 逐经办人分摊 / 收款日期，支持同名多行 = 多期收款）；新增 `_apply_deferred_edit()` 把右栏结果回写 `data["deferred"]` |
| **A5** | `app/ui/import_review_view.py` | **（必做）** `_load_current()`（`:202`）在 `page_pre.load_data(...)` **之前**重跑一次 `split_deferred` → 判定刷新到最新库状态，**批量导入 ≡ 单账期导入** |
| **A6** | `app/ui/import_review_view.py` + `app/ui/main_window.py` | **离开复核页弹一次确认框**（队列未跑完 + pre 模式）；**不锁侧栏** |
| **A7** | `app/ui/import_review_view.py` + `app/ui/import_view.py` + `main_window.py` | **待确认提示**：侧栏「导入复核」角标/红点 + 导入页顶部提示条「还有 N 个账期待确认 → 去处理」。实现方式取决于 **P0-3 探针**结果 |
| **A8** | `app/ui/import_view.py` + `main_window.py` + `import_review_view.py` | **台账导入前置守卫**：队列未跑完时 **「发票台账」类导入整批拒绝**（弹文件对话框**之前**拦）；**销项 / 收款 / 费用 / 员工清单照旧可导**。计数由 main_window 注入（如 `ImportView.ledger_guard: callable \| None`） |
| **A9** | `app/ui/main_window.py` | **关闭程序时确认框**（用户 16:58 选**甲**）：队列未跑完 → 提示「还有 N 个账期待确认，关闭后将需要重新导入」；走 `closeEvent` |
| **A10** | `app/importer/importer.py` | 新增 **deferred 行写库**（见 D1）：`commit_ledger_import` 里对**用户在复核页确认过收款**的在库票，更新 `collection`；未确认则不动。**本阶段必做**，否则 A3 的编辑不落库（原计划把写入放阶段 3，与验收点③ 矛盾） |

| **A11** | `app/engine/raw_ledger.py` + `app/ui/problem_fix_panel.py` | **（D3 甲）多期收款预填展开**：`_derive_deferred()`（`:211`）由"仅单期预填"改为**按「期 × 经办人」展开多行** —— 每人第一行填开票分摊额、其余行开票金额填 0（保证"开票额合计 = 开票总额"校验通过，且 `_aggregate_handlers` 合并后分摊正确）；每行收款金额 = 该期金额 × (该人份额 / 总额)、日期 = 该期 `YYYY-MM`；**同月多笔先合并**（粒度只到月，无损） |

### 【2-3 实现说明】（2026-09-17 落地，与上文表格的差异以此为准）

- **A8 守卫的落点**：上表写「弹文件对话框**之前**拦」，实现在**选完文件/文件夹之后、
  任何解析或写库之前**拦。原因：单文件入口在弹框前无法知道类型，若在弹框前一律拦死，
  就会把「销项导出」一并挡住，**违反本行自己写的硬边界**（销项/收款/费用/员工清单照旧可导，
  否则堵死「必须先导销项、再导台账」的顺序依赖）。落点仍满足"零半成品"：单文件在
  `_do_import` 之前拦；**批量则在处理任何文件之前整批拒绝**（避免"销项已入库、台账被拦"）。
- **A7 实现方式**：按 P0-3 结论改为**自绘**——新增 `sidebar.NavItemButton(QPushButton)`，
  `paintEvent` 里在 `#navItem` QSS 之上画角标；对外 `SidebarWidget.set_item_badge(key, text)`。
  **不得**用「子 QLabel + 自身 QGraphicsEffect」（整条侧栏只允许 `GroupPanel` 那一层 effect）。
- **A7 已知局限**：侧栏默认为**收起态**（`pinned=False`），收起时子项整体隐藏 → 角标此时不可见，
  鼠标移入展开后可见。收起态若要提示，需给大类行也加一枚点（本次未做）。
- **A6「不锁侧栏」**：不置灰任何导航入口，只在真正离开本页时询问一次；勾「本次不再提示」
  后本队列内不再打扰（新队列由 `open_pending` 重新武装）。
- **A9**：`main_window.closeEvent` 消费 `page_review.pending_count()`；判定查询出错时**放行**
  （不能因为一个提醒功能让程序关不掉）。

### 测试

- `tests/_smoke_import_queue.py`（队列：deferred 行进队列、N 计数、守卫、离开确认）
- `tests/_smoke_unified_import.py`（右栏 3 状态、就地编辑回写）
- `tests/_smoke_import_review.py`（A5 判定刷新、A10 写库）
- `tests/test_review_compare.py`、`tests/test_deferred_sheet3.py`
- 全量回归

### 本机验收点

1. 队列里能直接看到期外票行，右侧写「需补录原票」；已入库存量票写「已入库，请确认收款」；
2. 期外行可直接改经办人 / 分摊 / 收款日期，**确认入库后落库**（A10）；
3. **A5 对照测试**：老票同时出现在 2025-01 与 2025-03 的 sheet3 → 在 01 补录入库后，03 复核页**不再标需补录**；且批量导入结果与"分两次单账期导入"**逐行一致**；
4. **A8**：队列未跑完时再导台账 → **被拒**且提示可读；**同一时刻销项文档仍能正常导入**；「放弃全部待确认」后恢复；
5. **A6/A7/A9**：切页弹确认框、侧栏/导入页显示「N 个待确认」、未处理后关程序有提醒；
6. **A11**：打开真实的多期收款票据（`2025-03 / 02865232`、`2025-07 / 24332000000333839406`、`2025-10` 与 `2025-12` 的 `25332000000094152650`）→ 补录弹窗**已自动展开多行**、日期与金额与备注一致、开票额合计等于开票总额。

### 回滚

阶段 2 涉及 3 个 UI 文件 + `importer.py` 一段 → **按 A1–A10 拆成 2–3 个 commit**，便于局部 revert。

---

## 4. 阶段 3：pending 补录（随批入库）+ 行内按钮

**目标**：补录不再立即写库，与台账**同一事务**入库；点「取消」一行都不写。

| # | 文件 | 改动点 |
|---|---|---|
| B2a | `app/engine/backfill_module.py` | `save_backfill()` 拆两层：`build_*`（纯计算 + 校验）/ `apply_*(conn, ...)`（用传入 conn、**不 commit**）。参照 `raw_ledger.update_row(conn=None)` 既有模式 |
| B2b | `app/importer/importer.py` | `commit_ledger_import(data, period, path, backfills=None)` —— **同一事务**写补录 `invoice(source='manual')` + `charge_detail` + `collection`；**不写 `received_snapshot`**、`import_batch_id` **留空** |
| B2c | `app/ui/unified_import_dialog.py` | 补录结果收集进 **`data["backfills"]`**；点「取消」→ 一行都不写。**统一结构**：每项含 `invoice_no` + `create: bool` + `invoice_date/buyer/total_amount` + `handlers[]` + `collections[]`；`create=False` 表示"票已在库、只更新收款"（与 A10 共用同一通道） |
| B2d | 同上 + 引擎 | **校验两次**：弹窗确定时即时 + 入库前复核（共用 `missing_handlers`）。**入库不过 → 返回修改**（中止写库、留在对话框、定位到出问题那一行），**不跳过问题行、不整批重来** |
| B2e | 引擎 | **重复票号**：允许重复，**入库时报错中止** + 可诊断提示。**A5 之后此错只剩同账期场景**（同账期两张红字引用同一原票 / 同账期 sheet3 两行同号 / 同账期重复导入） |
| B2f | `app/ui/manual_entry_view.py` | `_open_dialog()`（`:203`）内联弹窗抽成独立可复用类 **`BackfillDialog`**（复核页与补录页共用） |
| B2g | `app/ui/unified_import_dialog.py` | `row_btn`（`:247-252`）在 `btn_source` 之后插 **「补录原票」** 按钮；原票已在库 → 变**「查看原票」**；红字行原票号用 `SELECT orig_invoice_no FROM invoice WHERE invoice_no=?` 反查（查 `invoice` 表，`raw_invoice` **无此列**） |
| B2h | 同上 | 补录完成后**刷新当前行**（按钮切「查看原票」、状态更新、底部计数同步） |
| B2i | `app/ui/manual_entry_view.py` | 「发票补录」页保持**立即写库**不变（独立页面、不属任何批次） |

### 【3-1/3-2/3-3 实现说明】（2026-09-17 落地，与上文表格的差异以此为准）

- **B2a 落地形态**：`save_backfill()` 拆成 `build_backfill(conn, data)`（纯计算 + 校验，
  返回 `{invoice_no, invoice_date, buyer, total_amount, charge, collections}`）与
  `apply_backfill(conn, payload)`（用**传入的** conn 写入、**不 commit / 不 close**）。
  `save_backfill()` = 两者 + 自开连接 commit，**仅「发票补录」页**（独立、立即写库）使用。
- **B2b 落点**：`commit_ledger_import(data, period, path, in_library=None, backfills=None)`
  内部调 `_write_backfills(conn, backfills, batch_id)`；`backfills` 缺省取 `data["backfills"]`，
  故「一致性写入」由 data 通道天然保证（同一事务、同一 batch_id、台账与补录同进同出）。
  新增 `_append_receipts_to_existing(conn, invoice_no, receipts, batch_id, sheet, row)`
  作为 **A10（复录确认收款）与补录 `create=False`（票已在库、只追加收款）共用的唯一通道**。
- **B2f 落地形态**：`app/ui/backfill_dialog.py` 的 `BackfillDialog` + 模块级
  `backfill_validator(data)`（= `build_backfill(get_conn(), data)`）。三种模式：新增 /
  编辑（`locked_no=True`）/ 查看（`readonly=True`，全禁用 + 仅「关闭」）。字段级校验全部
  在弹窗内完成（校验不过**留在弹窗**、不丢输入）；`validator` 再挂一层写前校验，与写库**同源**。
- **B2c 落地形态（复核页只产出 `create=True`）**：`_open_backfill()` 成功后把条目塞进
  `self._backfills`（键 = 票号）并置 `item["create"] = True`；**不立即写库**。已在库的票
  走 ① **只读「查看原票」**（不收集）。`create=False`（只追加收款）通道在引擎侧与 A10 共用、
  已由 `test_backfill_pending.py` G 段覆盖，UI 侧当前不产出（设计如此）。
- **B2g 落点差异**：按钮插在 `btn_source`（「查看原始台账行」）之后；文案随行切换
  `补录原票` / `查看原票`。**红字行补的是它引用的原票**——票号经
  `SELECT orig_invoice_no FROM invoice WHERE invoice_no=?` 反查（**`raw_invoice` 镜表没有
  该列**，它是从 remark 现算的）；反查不到 → 无入口（按钮隐藏，可接受）。
- **B2h 已知交互（非缺陷）**：行内补录成功后该行**归入已确认**（`_deferred_confirmed`）→
  **离开「待确认」筛选**，默认筛选下会从表里消失，按钮的「查看原票」态需切「全部」才看得到。
  这是既有语义（处理完的行不再占待确认视图），底部汇总同时给出
  「已填补录待随台账入库 N 张」计数。
- **B2d 兜底**：`import_review_view._on_confirmed` 单独 `except ImportError_` →
  弹「校验未通过」+ `_reload_current_for_fix(item)`（重载本账期、**不推进队列、不整批重来、
  零写入**）；其余异常仍走原「写库失败 → 跳过该账期并记账」路径。
- **B2e 拦截点**：写前 `_validate_backfills` 报「重复票号（含位置）」+ 弹窗内
  `_validate_backfill` 先拦「**本次已填过同票号**」；A5 之后只剩同账期场景。
- **B2i/B2k**：「发票补录」页保持**立即写库**不变（不属任何批次）。

### 测试

- `tests/test_backfill_pending.py`（**新增 39 项**）：build 校验 / apply 不 commit + 非 manual
  拒绝覆盖 / save_backfill 立即写库 + 不写 `received_snapshot` / commit 同事务写入 /
  **原子性（注入故障 → 台账与补录都回滚）** / 写前校验（重复、已在库、同批冲突、空经办人）/
  `create=False` 追加收款 + 超额拦截。
- `tests/_smoke_unified_import.py`（**新增第 11 节 34 项**）：行内按钮四态（需补录行 / 普通
  发票行 / 已在库行 / 红字行取原票号）、确定即收集、取消零收集、原因第四态、按钮翻转、
  底部计数同步、`merged["backfills"]`、写前校验与同票号拦截。
- `tests/_smoke_import_queue.py`（**新增第 10 节 9 项**）：确认入库携带 `backfills` 且只 commit
  一次；取消 / 放弃全部 → **一行未写**。
- `tests/_smoke_import_review.py`（**新增 5b 节 9 项**）：`ImportError_` → 弹提示 + 留在本账期
  重载 + 不推进队列 + 零写入；改好后再次确认即写库并推进。
- `tests/_smoke_manual_entry.py`（`BackfillDialog` 抽取后的两处调用 + 弹窗自身 11 项）。
- 全量回归：`tests/*.py` **29/29 全绿、0 Traceback**（基线由 28 升至 29 = 新增
  `test_backfill_pending.py`）。


### 本机验收点

1. 复核页补录一张 → 点「取消」→ **库中无任何新行**；
2. 补录 + 确认入库 → **原子写入**（台账与补录同进同出）；
3. 同一票号补两次 → 入库时给出可诊断报错；
4. 补录后再导同账期 → manual 票**不重复计收款**；
5. 补录后该行按钮变「查看原票」（需把筛选切到「全部」——该行已离开「待确认」），
   底部计数同步出现「已填补录待随台账入库 N 张」；
6. 同一票号补两次 → 弹窗内即被拦下（提示「本次已填过…」），不必等到写库。

---

## 5. 待决事项 —— ✅ 已于 2026-09-16 18:29 全部拍板（保留选项备查）

### D1（严重）在库票出现在本账期 sheet3，怎么处理？

**背景（复查发现的真实风险）**：应收账龄表里的老票（如 2024-11 开票、未收完款）会**持续出现在各月 sheet3**。
- **旧口径**：按日期判"期外" → 进 deferred → **不动 `collection`** ✅
- **新口径（只看票号）**：票号在库 → 不进 deferred → **走普通票路径** → `_write_collection_for_invoice` 执行
  `DELETE FROM collection WHERE invoice_no=? AND source='import'`（`importer.py:181`）→ **历史收款被删、按 sheet3 行重建** ❌

| 选项 | 做法 | 评价 |
|---|---|---|
| **甲** | 在库票 → **不建票 / 不写分摊 / 不写 `collection`**，只把台账收款读进复核页供确认（确认后才更新） | ✅ **推荐**：行为与旧口径等价（安全），且把 A2 从"仅 manual"统一到"任何在库票" |
| 乙 | 在库票完全不动、也不读收款 | 最保守，但用户失去"确认收款"的能力 |
| 丙 | 按普通票走（现状新口径的自然结果） | ❌ 会删历史收款，**不可接受** |

> **✅ 已定：甲（用户 18:29）。** 落地到阶段 1 的 C3 / C7 与阶段 2 的 A10。

### D2 是否恢复「并集」（判定时并入本批将建票的票号）？

**背景（复查发现 B3 的论证不成立）**：我原先说"同批 sheet1 与 sheet3 同号会被 A4 拦下"，
但**只在 sheet3 那行日期如实填写时成立**。若日期填错（写成更早月份），A4 放行 → 而该票此刻**还没入库**
（本批尚未提交）→ 被误判为"需补录"。

| 选项 | 做法 | 评价 |
|---|---|---|
| **甲** | **恢复并集**：在库票号 ∪ 本批 sheet1/2 票号 | ✅ **推荐**：成本极低（`split_deferred` 本就在遍历 `invoices`），彻底消除误判；A4 保留为**数据质量校验** |
| 乙 | 维持"不做并集"（原始 B3） | 依赖"台账不会跨表同号 + 日期不会填错"，属可被下游击破的假设 |

> **✅ 已定：乙（维持不做并集）—— 用户 18:29 给出业务事实："在一个发票台账文档里（一个账期里），sheet3 的发票不会出现在 sheet1 和 sheet2 中。"**
> → 那么"sheet3 行的票号不在库"**必然**等于"从未被任何一期建票" → 判"需补录"永远正确，D2 的担心前提不成立。
> **⚠️ 撤回声明**：本节原先的"建议恢复并集"**作废**；阶段 1 的 C1/C2 **不需要并入本批将建票号**。
> A4 保留为**数据质量校验**（正是上面这条业务事实的守卫）。

### D3 多期收款预填，本期补不补？

**背景**：`raw_ledger._derive_deferred()`（`:211`）只对**单期收款**预填已收金额与日期；
`len(items) > 1` 时预填**全空**，用户须手工逐行填。A3 的就地编辑面板支持多行，但**不会自动带出**。

| 选项 | 做法 |
|---|---|
| 甲 | **本期补**：多期时按开票份额比例分摊到各经办人、**每期一行**预填（放阶段 2） |
| 乙 | 本期不做（预填留空，用户手工加行），归「后期优化」 |

> **✅ 已定：甲（用户 18:29）。** 落地到阶段 2 的 **A11**。
>
> **📊 实测数据（本计划编写时直接解析 `data/archive/ledger/*/` 下 12 期真实台账的 sheet3，共 207 行）**：
>
> | 备注形态 | 行数 | 占比 | 能否自动预填 |
> |---|---|---|---|
> | 纯日期（单期全额） | 194 | 93.7% | ✅ 按开票份额分摊预填 |
> | 单期收款（带金额） | 2 | 1.0% | ✅ 同上 |
> | 无收款信息（其他） | 7 | 3.4% | ✅（本就留空） |
> | **多期收款（≥2 笔）** | **4** | **1.9%** | ❌ 预填全空 → 须手工加行 |
>
> 4 条明细：`2025-03 / 02865232 / 25.3.18到15000，3.19到35000`（2 期·**同月**·2 人）、
> `2025-07 / 24332000000333839406`（2 期跨月·1 人）、
> `2025-10` 与 `2025-12` 各一条 **同一张票** `25332000000094152650`（2–3 期跨月·1 人·备注是累计）。
>
> **可行性已核实**：`ProblemFixPanel` **天然支持**同名多行 = 多期收款（`handlers` 按姓名聚合开票额 `_aggregate_handlers:450`，
> 而 `split_receipts` 逐行收集 `(name, amt, ym)` `:505`）→ **展开成多行不需要改面板结构，只改预填**。
> **附带理由**：`25332000000094152650` 在 2025-10 与 2025-12 两期都出现，在 D1 甲 的流程里走"已在库 → 读收款进复核页确认"，
> **该通道同样需要表达跨月多笔收款** → 这个能力不只服务那 4 行。

---

## 6. 报错 / 提示文案清单（新增文案统一走这里，避免散落）

| 场景 | 文案 |
|---|---|
| **A4** sheet3 出现 ≥ 账期的票 | `【{sheet3名}】第 {row} 行发票 {no} 的开票日期 {date}（{ym}）不早于本账期 {period}。应收账款表只应登记本账期之前的发票，请核对台账文件后重新导入。` |
| **A1** 开票日期无法解析（问题行） | `开票日期「{text}」无法识别（支持 2025.9.1 / 25.09.01 等写法）` |
| **A8** 台账导入守卫 | `存在未确认入库的账期（{periods}），请先完成「确认入库」，或点「取消 → 放弃全部待确认」清空后再导入新的发票台账。` |
| **A6** 离开复核页 | `还有 {n} 个账期待确认入库，离开后可随时回到本页继续。确定离开？`（可勾"本次不再提示"） |
| **A9** 关闭程序 | `还有 {n} 个账期待确认入库。关闭后将需要重新导入这些台账文件（不会写入任何数据）。确定关闭？` |
| **A7** 侧栏 / 导入页提示 | `还有 {n} 个账期待确认入库 → 去处理` |
| **B2e** 重复票号 | `本批补录存在重复发票号码 {no}（{哪两行 / 由红字票 X 与 Y 各引用一次}）。同一张原票只能补录一次，请返回修改。` |
| **阶段2 右栏原因** | `需补录原票` / `已入库，请确认收款` / `系统判定无疑问` |
| **阶段3 右栏原因（第 4 态）** | `已补录，待随台账入库`（`unified_import_dialog.REASON_BACKFILL_DONE`；填过补录后显示，**绿色**） |
| **阶段3 底部汇总** | `·　已填补录待随台账入库 {n} 张` |
| **B2e 弹窗内拦截** | `票号 {no} 本次已填过补录，请只保留一份（如需修改，请点该行的「查看原票」）。` |
| **B2d 写前校验未通过** | 弹窗标题「校验未通过」，正文 = `ImportError_` 原文（如 `本批补录存在重复发票号码 …`） |
| **B2i** 补录按钮 | `补录原票` / 原票已在库时 → `查看原票` |

---

## 7. 测试矩阵

| 测试文件 | 阶段 1 | 阶段 2 | 阶段 3 | 关注点 |
|---|---|---|---|---|
| `tests/test_deferred_sheet3.py` | 重写 | 补 | — | 票号判定 / A1 / **已在库票跳过（D1 甲）** / A4 / 幂等 |
| `tests/test_review_compare.py` | ✅ | ✅ | — | `needs_backfill`、`source IN ('import','manual')` |
| `tests/test_raw_ledger_mirror.py` | ✅ | — | — | 镜表原文契约 |
| `tests/test_import_fix_log.py` | ✅ | ✅ | — | 修改留痕 |
| `tests/_smoke_import_queue.py` | — | 补 | ✅ | 队列 / 守卫 / 取消零写入 / **backfills 随同一 commit** |
| `tests/_smoke_unified_import.py` | — | 补 | ✅ | 右栏 3+1 状态 / 就地编辑 / 弹窗 / **行内补录四态** |
| `tests/_smoke_import_review.py` | — | 补 | ✅ | A5 判定刷新 / A10 写库 / **B2d 写前校验返回修改** |
| `tests/test_backfill_pending.py` | — | — | 新增 | **build/apply 拆分 · 同事务 · 原子性 · 校验 · create=False** |
| `tests/_smoke_manual_entry.py` | — | — | ✅ | `BackfillDialog` 三模式（新增 / 编辑 / 查看） |
| `tests/_smoke_manual_entry.py` | — | — | 补 | `BackfillDialog` 抽取 |
| 全量 `tests/*.py` | ✅ | ✅ | ✅ | 基线与阶段对照 |

---

## 8. 风险登记

| 风险 | 触发条件 | 缓解 |
|---|---|---|
| **历史收款被重写** | 在库老票出现在 sheet3（若 D1 选丙） | ✅ 已定甲：在库票不建票、不写分摊、不写 `collection`；阶段 1 的测试专门钉住"在库票不写 collection" |
| ~~误判需补录~~ | ~~sheet1/3 同号且 sheet3 日期填错~~ | ✅ **风险不成立**（用户 18:29：同一账期台账内 sheet3 与 sheet1/2 不会同号）→ 维持"不做并集" |
| **待补录数量突变** | 去掉日期过滤后，日期解析失败的行被纳入 | P0-4 基线快照 diff，验收时人工确认 |
| **判定刷新引入性能问题** | A5 每次载入账期都查一次 `invoice` 全表票号 | 数据量级数千行、账期≤12 → 可忽略；若变慢再加按年份限定 |
| **pending 模式下数据丢失** | 用户点「取消」以为已保存 | 取消文案明确"不会写入任何数据"；阶段 3 验收点 1 专门覆盖 |
| **改动混批无法回滚** | 带着现有未提交改动开工 | P0-1 强制先落盘 |

---

## 附：与主文档的对应关系

| 本计划 | 主文档（`backfill-decision-plan-2026-09-16.md`） |
|---|---|
| 阶段 1 | 第三节（工作包 C）+ 第十一节批次 1 |
| 阶段 2 | 第四节（工作包 A）+ 第五节 B1 + 第十一节批次 2（A5–A11） |
| 阶段 3 | 第五节 B2 + 第十一节批次 3 |
| D1 / D2 / D3 | 主文档 **第十二节**（本次复查新增） |
| 文案清单（第 6 节） | 主文档未单列，以本计划为准 |
