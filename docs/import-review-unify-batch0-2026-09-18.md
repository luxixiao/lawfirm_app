# 导入复核页统一 · 批 0 只读实测报告

- 日期：2026-09-18
- 目标：量出「把『台账 ⇄ 销项/库』比对放进**导入时**判定、不一致的行直接落『待确认』」
  会新增多少待确认行，以及它们分别属于哪一类。
- 性质：**只读实测**。全部操作发生在 `lawfirm.db` 的临时副本上；
  `importer._auto_snapshot` / `_archive_file` 已打桩 → 仓库与源库**零写入**。
- 脚本：`p0_6_compare_measure.py`（主）/ `p0_6b_buyer_patterns.py`（购方模式拆分）/
  `p0_6c_r5_probe.py`（差异行溯源）/ `p0_6d_final.py`（收口核算）。

## 1. 重建的库状态

| 项 | 值 |
| --- | --- |
| 源库 | `测试/lawfirm_app/data/lawfirm.db`（319,488 B，mtime 2026-09-18 09:35） |
| 销项导入 | 12 期全部 OK（2025-01 已存在跳过；累计 1,130 张） |
| 台账导入 | 12 期全部 OK，**0 失败**；每期 `batch_id` 235–246 |
| 镜表发票行（批内同号去重） | 1,337 |
| 参与比对票号（全局去重，仅 sheet1/2） | 1,130 |
| sheet3 行（deferred_count 合计） | 207 |

台账导入结果（`deferred_count` = 该期 sheet3 行数）：

| 账期 | 发票数 | 预收 | sheet3 | 账期 | 发票数 | 预收 | sheet3 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2025-01 | 86 | 3 | 4 | 2025-07 | 102 | 1 | 17 |
| 2025-02 | 70 | 5 | 15 | 2025-08 | 94 | 1 | 30 |
| 2025-03 | 79 | 2 | 18 | 2025-09 | 93 | 1 | 9 |
| 2025-04 | 96 | 0 | 10 | 2025-10 | 86 | 3 | 9 |
| 2025-05 | 90 | 1 | 15 | 2025-11 | 120 | 2 | 21 |
| 2025-06 | 79 | 1 | 5 | 2025-12 | 135 | 1 | 54 |

## 2. 核心结果：裸文本比对 vs 归一化比对

| 口径 | 会落「待确认」 | 占比 | 其中真差异 |
| --- | --- | --- | --- |
| **裸文本比对**（`norm()` 去空白） | **574 / 1,337** | 42.9% | 1 |
| **归一化比对**（`canon_name()` 去括注 + 全半角标点统一） | **1 / 1,337** | 0.07% | 1（实为口径伪差异） |
| **归一化 + 排除 sheet3 行** | **0 / 1,130** | 0.00% | 0 |

分类明细（裸口径）：

| 类别 | 行数 |
| --- | --- |
| R1 金额不一致 | 0 |
| **R2 购方不一致** | **573** |
| R3 开票日期不一致 | 0 |
| R4 经办人集合不一致 | 0 |
| R5 分摊金额不一致 | 1 |
| N1 仅源有·sheet3（=待补录） | 67 |
| N2 仅源有·非 sheet3 | 0 |
| N3 镜表无票号 | 0 |

## 3. 573 行「购方不一致」的真相

按模式拆分 1,130 张在库票的购方：

| 模式 | 张数 | 占比 |
| --- | --- | --- |
| 完全一致 | 583 | 51.6% |
| **仅括注后缀差异**（销项多 `（个人）`） | **546** | **48.3%** |
| 去括注后仍不同 | 1 | 0.1% |

样本：

```
sheet1 row4  25332000000000836111  台账='程国娥'        销项='程国娥（个人）'
sheet1 row8  25332000000001072898  台账='钟刚峰'        销项='钟刚峰（个人）'
sheet1 row10 25332000000001266188  台账='钱伟国、王建强'  销项='钱伟国、王建强（个人）'
```

唯一的「去括注后仍不同」1 行也是文字问题（全/半角逗号不统一）：

```
25332000000539677738  台账='周云兴，周峰,叶文琴'  销项='周云兴,周峰,叶文琴（个人）'
```

**结论：销项导出的「对方」列在个人客户名后固定追加主体类型括注 `（个人）`，台账的「对方」列没有。若不归一化，48% 的票会被误判为差异。**

## 4. 唯一 1 行「分摊金额不一致」的溯源

票号 `25332000000333499160`（中共绍兴市委绍兴市人民政府信访局，80,000.00）：

| 来源 | 位置 | 经办人列原文 |
| --- | --- | --- |
| 2025-07 台账 sheet2（收款） | row24 | `徐琦20000，宣浙军30000,傅锦琪30000` |
| 2025-08 台账 sheet3（应收账款） | row9  | `徐琦40000，宣浙军10000,傅锦琪30000` |

库侧 `charge_detail` 由 2025-07 批次（`import_batch_id=241`）写入，即 **sheet2 的已收视角分摊**。
测量脚本按「全局取末行」取到了 2025-08 的 sheet3 行 → 报差异。
实测口径下把 sheet3 行排除后，该差异消失。

**结论：sheet3 行的「经办人」列是"应收账款视角"的分摊，与 sheet2 的"已收视角"可能不同；
sheet3 行按现行 D1-甲口径本就不建票 / 不写 `charge_detail`，因此不应参与「台账 ⇄ 库」比对。**

## 5. 对方案的直接影响

1. **比对必须做购方归一化**，否则 42.9% 的行会被刷成待确认，待确认分类直接失效。
   归一化规则：去括注（`（个人）`/`（单位）` 等主体类型后缀）→ 全角标点转半角 → 去空白与分隔符。
2. **sheet3 行排除在比对之外**（另有「待补录」分类专门承载）。
3. **比对的实际捕获量很低**：12 期真实数据、归一化后新增待确认 = **0 行**。
   即「导入时比对」在现网数据上几乎不会触发 —— 它是一道**兜底防线**，不是主要的发现问题手段。
4. 金额（R1）、开票日期（R3）在 12 期数据上**零差异**，说明台账与销项这两个来源本身高度一致。
5. 唯一被比对抓出的真问题类型是 **R5 分摊金额不一致**（且本例还是 sheet3 口径问题）——
   说明**经办人分摊**是台账独有的信息（销项不写 `charge_detail`），
   比对它等于"拿台账跟台账比"，价值有限。

## 6. 已收认定：不能在导入时比对（源码 + 实测）

**现状口径**（`review_compare.py:205-267`）：
- 源侧 exp = 台账「备注」推导的**本期声称收款**（有快照取快照，无快照 `compute_expected_receipts`）；
- 库侧 act = 有快照取**本批次**写入的（`import_batch_id=?`）；**无快照**取 `receipt_date <= 账期` 的**全部** collection（= 历史累计）；
- 判定 `abs(exp - act) > _RECV_EPS`（**双向**）。

**导入后**（本次实测，库副本 12 期）：
| 指标 | 数值 |
| --- | --- |
| `received_snapshot` 行数 | 1,130（12 期**每票都有**） |
| 快照里 exp ≠ act 的行 | **0** |
| 把 act 强行换成「历史累计」后报差异的行 | **0**（12 期数据无一张票跨期收款） |

⇒ 导入后比 exp/act **恒为一致**（exp 与 act 是同一批一起写的）。

**导入时**（批 2 的语境）—— 结论相反且致命：
| 指标 | 数值 |
| --- | --- |
| 台账「本期声称收款」> 0 的票 | **884 / 1,130（78.2%）** |
| 导入那一刻库侧 act | **0**（本批 `collection` 要等 commit 才写；`collection` 全部行都带本期 `import_batch_id`） |

⇒ 在导入时把「已收认定」并入待确认，会为 **884 张票（78%）假报**「已收认定不符」。
若改成「比库中已有 + 本批将写入」，则本批将写入 ≡ `exp` ⇒ **恒等，等于没比**。

**结论：已收认定不应进入导入时比对。** 收款核对已有归宿 ——
复核页 sheet3 的「已入库请确认收款」（蓝态）+ A10 超额校验（`over_collection_message`）。

## 7. ⚠️ 附带发现：`over_collected` 是一条**静默失败路径**（UI 零消费）

`commit_ledger_import` 的返回值里有 `over_collected`（`importer.py:837/897/940/972`），
写入时机有两处：

1. **A10 确认收款**（`importer.py:936-940`）：台账登记的收款使「全来源累计 > 开票总额」时，
   `_append_receipts_to_existing` 返回报错文案 → **该笔收款不写入** → 记入 `over_collected`。
   代码注释写的处置是「该行作为问题行**由复核页提示用户修正台账**」（`importer.py:905`）。
2. **补录写失败**（`importer.py:895-898`）：`_write_backfills` 的 `bf_problems` 同样只进 `over_collected`。

**但 UI 层没有任何地方读它。** 核对结果：
- `grep over_collected app/ui` → **0 命中**（`app/` 下除 `importer.py` 外也只有 `collection.py` 的定义）；
- `import_review_view._on_confirmed`（`:507-513`）的入库完成文案只报
  `invoice_count / prepayment_count / deferred_count / backfill_count` 四项；
- `UnifiedImportDialog` 里 `over_collected` 也零命中。

⇒ **注释里承诺的「复核页提示用户」没有兑现**：超额收款与补录写失败都是
「钱/数据没进去，界面也不说」，用户以为导入成功了。

**12 期实测未触发**（12 期的 `over_collected` 全部为 `[]`）—— 是数据干净，不是代码安全。

### 7.1 但「收款超额」的检查**只挂在一条路径上**

用户问：「能否分辨出『发票金额 1000、合计收款 1200』这种情况？」

两条写入路径，只有一条有校验：

| 路径 | 函数 | 有超额校验？ |
| --- | --- | --- |
| **A10**：sheet3「已在库」行、复核页已确认收款 | `_append_receipts_to_existing`（`importer.py:566-591`） | **有** —— 调 `over_collection_message`，超额则不写入 |
| **普通**：sheet1/2 本期发票 | `_write_collection_for_invoice`（`importer.py:187-221`） | **无** —— 只做「删本票全部 import 收款 → 按台账 INSERT」 |

线索旁证：`validate_ledger_before_write`（`importer.py:686-732`）只有**三道**校验 ——
① sheet1+sheet2 合计 = 销项合计 ② 经办人在花名册 ③ 补录票 `_validate_backfills`。
**没有任何一条检查收款是否超过票面金额。**

⇒ 若台账在同一期内给一张 1,000 的票登记 1,200 收款，走普通路径会**原样落库**，
不报错、不提示、不拦。用户举的正是这一种。

`over_collection_message` 的报错文案（`collection.py:70-72`，已写好、只是没人看）：
> 该发票金额 1,000.00 元，已于 2025-07-15 收款 800.00 元，本次台账再登记 400.00 元后将累计 1,200.00 元，剩余应收 0 元，已收款完成。

### 7.2 若把该口径套到**所有行**，实测噪声 = 0

| 指标 | 数值 |
| --- | --- |
| 台账本期声称收款 > 0 的票 | 884 |
| 其中「本期声称收款 > 票面金额」 | **0** |
| 落库后「collection 累计 > 票面金额」的票 | **0** |

⇒ 把「库中已有（排除本批）+ 本批声称 > 票面金额」这道检查套到每一行，**不会产生任何噪声**，
却能同时盖住两条路径（普通路径的「库中已有」在导入时基本为 0，式子即退化为「本批声称 > 票面」）。

### 7.3 四条收款写入路径的完整现状（源码核对）

| # | 路径 | 函数 | 超额校验 | 超额时 |
| --- | --- | --- | --- | --- |
| 1 | **sheet1/2 本期发票** | `_write_collection_for_invoice`（`:187-221`） | **无** | **原样落库**（静默） |
| 2 | sheet3「已在库」行 A10 | `_append_receipts_to_existing`（`:566-591`） | 有 | 不写入 + 进 `over_collected`（**静默**） |
| 3 | 补录 建票 `create=True` | `build_backfill`（`backfill_module.py:274-277`） | 有（`已收合计 > 价税合计` → `ValueError`） | **中止整批入库** |
| 4 | 补录 只追加收款 `create=False` | `_append_receipts_to_existing` | 有 | 不写入 + 进 `over_collected`（**静默**） |

⇒ **路径 1 是唯一连检查都没有的**（= 用户举的「票面 1000 / 收款 1200」）。

### 7.4 已锁定口径（2026-09-18 用户拍板）

| 维度 | 口径 |
| --- | --- |
| 购方 | **归一化判定**（去括注 + 全角标点转半角 + 去空白与分隔符）+ 括注差异挂**悬浮提示**，不落待确认 |
| sheet3 行 | **照常参与比对**（用户明确纠正：已在库行分摊不一致**要报**，不排除） |
| 逐行比对字段 | 金额 / 开票日期 / 购方 / 经办人集合 / 分摊金额 —— 全部严格比对 |
| 收款超额 | **乙+**：对**所有行**算「库中已有（排除本批）+ 本批声称 > 票面金额」→ 超了落待确认；
  四条路径共用 `over_collection_message` 同一算式；实测噪声 0（884 张有收款，超额 0 张） |
| 不并入 | 「已收认定」的 exp/act 双向比对（导入时 act 恒为 0 → 884 行假报） |

## 8. 测量口径的已知局限

- **R4（经办人集合）在本重建中恒为 0**：源库 `charge_detail` 为空，
  由本次台账导入创建 → 与台账必然同源，比对是重言式。
  真实场景（先导台账、后补销项，或二次导入）下才可能出现 R4 差异。
- 本实测的 12 期数据来自 `测试/销项导出` + `测试/发票台账`，
  与 `data/archive/ledger/*/` 内的历史 archive 可能不一致（以源目录为准）。

## 9. 批 1a 落地（2026-09-18）：取消「跳过」+ `accept()` 升级为硬拦

### 9.1 用户口径

> 「按照导入时，确认入库那一刻的状态来。同时取消跳过按钮，不允许跳过，所有问题行都必须确认过后，才能入库」
> 「待补录，待确认都必须处理完毕」

⇒ **最硬一档**：两个状态**都**要清零才允许入库；「已跳过」这个类别因此**整体消失**（筛选栏 7→6）。

### 9.2 代码改动（`app/ui/unified_import_dialog.py`）

| # | 改动 | 说明 |
| --- | --- | --- |
| 1 | `FILTERS` 去掉「已跳过」 | `["待补录","待确认","已补录","已确认","高置信","全部"]`（6 项）；`FILTER_ALL` 仍由 `.index("全部")` 推出，不写死 |
| 2 | `_PRIO` 去掉「已跳过」 | `{"待补录":0,"待确认":1,"高置信":2}` |
| 3 | 删除 `btn_skiprow` / `self._skip` / `_skip_row()` / `_set_actions(skip=)` | 问题行**只有**「保存修改」一个出口 |
| 4 | 问题行状态不再有「已跳过」 | 一律 `待确认`；`_render` 里的灰色分支一并删除 |
| 5 | `accept()` 由「问是否继续」→ **硬拦** | 只要 `n_backfill or n_pending` > 0 就 `QMessageBox.warning` + `return`，**一行不写**；文案分别列出 待补录 / 待确认 计数，并细分「解析失败问题行 / 红字与蓝字不一致 / sheet3 已入库待确认」 |

### 9.3 测试同步（4 个文件 → 2 个测试文件）

一致性代价：`accept()` 的契约变了，**所有走 `accept()` 端到端路径的用例都必须先把两态清零**。

| 文件 | 改动 |
| --- | --- |
| `tests/_smoke_unified_import.py` | 新增 `mk_clean_data()`（无待补录/待确认的最小数据，供 accept 端到端用）；第 4 节改为**硬拦**断言；新增 4b/5 两节在干净数据上验「校验不过 → 留在对话框」「校验通过 → 原地合并」；原第 5 节降级为 5b（直接验 `_merged_data()` 合并口径，注明 accept 已被硬拦）；第 6 节重写为「不允许跳过 + 预收款 + 硬拦不写库」；第 10f 由「预警」改为「硬拦」 |
| `tests/_smoke_red_backfill.py` | L 节筛选栏顺序去掉「已跳过」；N/O 两节的「入库预警」断言改为「入库硬拦」断言 |

### 9.4 验证

| 项 | 结果 |
| --- | --- |
| 单文件 | `_smoke_unified_import.py` **165/165**；`_smoke_red_backfill.py` **84/84** |
| 全量回归 | **33/33 全绿，0 Traceback，40.4s**（基线不变，未新增测试文件） |
| 反向校验 | `rev_check.py` 本批 2 条探针：**P1 关掉硬拦 → 变红 10 条**；**P2 把「已跳过」塞回 FILTERS → 变红 1 条**；跑完 `git diff --stat` 核对源码已还原（正好本批 4 个文件），复跑全量仍 33/33 |

### 9.5 测试实现上的两个坑（已固化进 skill 的踩坑表）

1. **干净数据 + 默认筛选 = 空表**：`mk_clean_data()` 没有「待补录」行，而默认筛选就是「待补录」
   → 表格 `rowCount() == 0`，`setCurrentCell` 选不中行 → `_load_right()` 拿到 `None` →
   右栏被 `set_problem(None)` 清空 → `htable.item(0,2)` 是 `None`。
   **修法**：选行前先 `_show_all(dlg)`。
2. **两个测试文件的 `QMessageBox` 桩语义不同**：
   `_smoke_unified_import.py` 的 `warning` 只记 `text`（`("warning", text)`）；
   `_smoke_red_backfill.py` 的 `warning = information` 是**别名**，记的是 `(kind, title, text)`
   → 按 `c[0] == "warning"` 过滤**恒为空**。断言必须按「记的是 text 还是 title」分别写。

### 9.6 后续批次

| 批 | 内容 | 状态 |
| --- | --- | --- |
| 1b | `app/engine/review_rebuild.py`：从 `raw_ledger` 重建某账期的导入期 `data` 结构（sheet1/2→invoices、sheet3→deferred、sheet4→prepayments）；`UnifiedImportDialog` 加 `mode="pre"/"post"`，post 读库重建 | ✅ 见 §10 |
| 1c | 把「确认入库那一刻的状态」持久化到库，供导入后读回 | 未开工 |
| 2 | 把「台账 vs 销项/库」比对接进导入时判定（口径见 §7.4） | 未开工 |
| 3 | 第二落点 `review_writeback.apply_edit` | 未开工 |
| 4 | 删除旧页（`review_post_view.py` / `review_compare` 融合比对）+ 死代码 + 文档收口 | 未开工 |

---

## 10. 批 1b 落地（2026-09-18）：导入后读库重建 + `mode="post"` 只读

### 10.1 做了什么

| # | 改动 | 文件 |
| --- | --- | --- |
| 1 | **抽出共享规则** `apply_sheet_receipt_rule(sheet_key, remark, total, period)`（sheet2 纯日期降级 / sheet1 空备注全额兜底 / sheet3 保留），`_parse_invoice_sheet` 改为调用它 | `app/importer/ledger_import.py` |
| 2 | **新增** `app/engine/review_rebuild.py`：`active_ledger_batch()` / `rebuild_period_data()` / `read_ledger_row()` | 新增 |
| 3 | `UnifiedImportDialog` 加 `mode="pre"/"post"` + `load_period(period)` + 只读契约 | `app/ui/unified_import_dialog.py` |
| 4 | 新增 7 节 56 条断言的冒烟（含**往返等价性**） | `tests/_smoke_review_rebuild.py`（新增） |

### 10.2 数据源与口径（`review_rebuild`）

`raw_ledger` 是**归一化**镜像（18 列业务字段，非逐列镜像）—— 它存的是原始文本
（`invoice_date_raw` / `amount_raw` / `handler_text` / `buyer` / `case_no` / `remark` / `recv_date_raw`）
**加上** `amount_num`（解析后金额）。故重建只需再跑一遍**同一批**纯函数：

| 维度 | 复用函数 |
| --- | --- |
| 日期 | `date_utils.normalize_date` |
| 经办人 | `parse_handler.parse_handler_column`（**纯函数**，不查库） |
| 备注 | `parse_remark.parse_remark` |
| sheet 归属收款修正 | `ledger_import.apply_sheet_receipt_rule`（**批 1b 新抽出**） |
| sheet3 切分 | `ledger_import.split_deferred` |

**与导入期的三处已知不等价**（都可接受、已写进模块 docstring）：

1. **`problems` 恒为空**：解析失败的问题行**不写镜表**（`commit_ledger_import` 只镜像
   `invoices` + `deferred`），只有被复核页修正后的行才随之落库 → 导入后本就不存在该桶。
2. **`header` / `raw_row` 恒为空**：镜表不存表头与原始整行。
   → 「查看原始台账行」改走批次存档文件：`read_ledger_row(sheet_name, row_no, archive_path)`
   （`archive_path` 取自 `import_batch`），行号口径与 `ledger_import` 一致，**逐列原文照样能看**。
3. **显式逐人收款（`split_receipts`）不存镜表** → 从本批 `collection` 读回，且
   **只在「备注推不出收款」时注入**：普通行的 `collection.person_name` 是空串
   （备注分支不带姓名），盲目注入会把「各经办人已收」整列打成 0。

### 10.3 `mode="post"` 只读契约

| 维度 | pre（默认） | post |
| --- | --- | --- |
| 底部按钮 | 「取消」+「确认入库」 | 只有「关闭」（`accept()` 直接 return，**一行不写**） |
| 右栏表单 | 按行状态可编辑 / 只读 | **恒只读**（建面板即锁 + 每次选行再锁一次） |
| 行内动作 | 保存修改 / 重新修正 / 确认 / 编辑 | 全部隐藏（`_set_actions` 内统一短路） |
| 「补录原票」入口 | 按行四态显示 | 隐藏（补录写库，去「发票补录」页） |
| 左表 | 筛选 / tooltip / 双击溯源 | 同 pre |
| sheet3「已在库」行状态 | **待确认**（去确认收款信息） | **高置信** —— 收款已在入库那一刻按 A10 处理完毕，导入后无待办 |
| sheet3「需补录」行状态 | 待补录 | **仍是待补录**（真待办，去补录页） |

### 10.4 验证

| 项 | 结果 |
| --- | --- |
| 新测试 | `_smoke_review_rebuild.py` **56/56**；`_smoke_unified_import.py` **165/165**；`_smoke_red_backfill.py` **84/84** |
| 全量回归 | **34/34 全绿，0 Traceback，67.5s**（基线 33 → **34**，已同步 `run_tests.py` 的 `EXPECTED_FILES`） |
| 反向校验 | `rev_check.py` 本批 **6 条探针全部如实变红**：P1 关 post 状态判定→红 1；P2 关 `_set_actions` 短路→红 1；P3 关补录按钮隐藏→红 1；P4 关「建面板即锁」→红 1；P5 去掉 collection 注入闸门→红 1；P6 关 sheet2 纯日期降级→**红 2** |
| 往返等价性 | 同一份台账行「解析 → 落镜表 → 重建」后，invoices / deferred 的 13 个业务字段**逐字段一致**，prepayments 9 个字段一致 |

### 10.5 本批踩到的三个坑

1. **`parse_remark` 只认 2 位年的纯日期**（`25.1.20` ✅ / `2025.1.20` ❌）。
   第一版 fixture 写了 `2025.1.20` → sheet2 的「纯日期降级」断言**空转**（本来就为空，
   规则关掉也不变红，反向校验才发现）。→ 改 fixture 为 `25.1.20`，并加了一条
   **前置断言**（「fixture 备注确实能被 `parse_remark` 认成纯日期」）防止再次空转。
2. **`_load_right()` 的「无行选中」早退分支里 `set_problem(None)` 会把面板重置为可编辑**
   （它服务于问题行修正路径，内部 `self._readonly = False`）。
   默认筛选「待补录」而本期没有待补录行时，表格空、无行被选中 → 面板停在可编辑态。
   → 在 `_apply_mode_chrome` / `_rebuild_fix_panel` / `_load_right` 三处补
   `_lock_panel_for_mode()`（与选中无关的保证），并加一条「空表时也锁死」的断言。
3. **`collection.invoice_no` 有外键约束**：fixture 里给不存在的票插收款行会
   `IntegrityError: FOREIGN KEY constraint failed`。→ 先补最小 `invoice` 行。

### 10.6 未接线（属批 4）

`ImportReviewView` 的「导入后」仍指向 `ReviewPostView`（`page_post`），**本批未切换** ——
1b 只交付「能力 + 测试」。切页 + 删旧页（`review_post_view.py` / `review_compare`）
按原计划归批 4。

### 10.7 提交

`aee6b2c`（5 文件 +924/−31）→ 分支 ref 照旧未推进 → Python 直写 loose ref + `pack-refs --all`
→ `ls-remote` 问远端仍是 `91f3fe3` → 走 skill `github-api-push` 的 `push_commit.py`
（13 对象 201）→ `VERIFY_REMOTE MATCH`、`AHEAD_BEHIND 0 0`、父数 1。
（`git commit` 又打印「已自动推送 ✅」——依旧是**假消息**，别信。）

## 11. 批 3-1 落地（2026-09-18）：导入时「确认」留痕 → 「导入后」不再重报已处理疑问

### 11.1 要解决的问题（批 1b 暴露的两条毛病之一）

批 1b 的 `mode="post"` 是**只读**的（隐藏「确认」按钮），而四态里有两类疑问**只能靠人点
「确认」消掉**：

1. `import_confidence.evaluate()` 的**兜底低置信**（无经办人 / 经办人重复 / 分摊合计≠价税合计 /
   经办人已收>开票金额 / 勾稽不平 …）；
2. `import_confidence.red_orig_diff()` 的**红字 ⇄ 蓝字原票不一致**。

这两类的状态只活在 `UnifiedImportDialog._confirmed`（**内存下标集合**）里，**库里零痕迹**。
批 1a 把 `accept()` 升级为硬拦（「待补录 / 待确认」必须清零才能入库）之后，**入库那一刻这些
疑问必然都已逐行消掉**；于是 post 模式再从 `raw_ledger` 重推四态时，会把它们**原样重新报成
「待确认」** —— 而 post 隐藏了「确认」按钮 ⇒ **用户点不掉的假待办**。
（批 2 把「台账 ⇄ 销项/库」比对判进待确认后，这条会成为放大器。）

> 注：本条（毛病 2）与毛病 1（手工修正过的行，`raw_ledger` 刻意存**原文** → post 重解析拿不到
> 修好的值）是两件事；毛病 1 归 **批 3-2**（**已落地，见 §12**：精确落点是「经办人分摊」列）。

### 11.2 落点：复用 `anomaly_note` 的独立 dim（**零 schema**）

`app/db.py:353` 早有 `anomaly_note(invoice_no, dim, period, note, confirmed_at)`，主键
`(invoice_no, dim, period)`，`dim` 为 TEXT ⇒ **新增一个 dim 取值不需要建表**。它原先只被
**旧「导入后」页**（`review_post_view`）的「标记已确认异常」写（`dim='merged'`），由
`review_compare.load_confirmed_notes(period)` 读回。

新增引擎 `app/engine/import_confirm.py`，用**独立 dim `import_confirm`**，与旧页人工备注
`merged` **互不覆盖**：

| 函数 | 作用 |
| --- | --- |
| `save_confirmations(conn, period, items)` | 逐票 `INSERT OR REPLACE`（票号为空跳过），返回条数 |
| `clear_confirmations(conn, period)` | **只删本 dim**；旧页 `merged`/`handler`/`received` 绝不碰 |
| `load_confirmations(period, conn=None)` | 本 dim 优先，缺失时回退 `merged`→`handler`/`received`（与 `review_compare` 同口径） |

### 11.3 三处接线

1. **写**（`importer.commit_ledger_import`）：**同一事务**内 `clear_confirmations(period)`
   → `save_confirmations(period, data["confirmations"])`；返回值新增 `confirmation_count`。
   **先清后写**是刻意的：覆盖式重导同一账期时，上一次的确认对新一批已不适用，必须失效
   （否则会拿旧确认去压制新一批的疑问）。
2. **收集**（`UnifiedImportDialog._merged_data`）：把本次点过「确认」的**发票行**收成
   `merged["confirmations"] = [{invoice_no, note}]`（`note` = 该行疑问原文；红字不一致的行
   前置一条「红字与蓝字原票不一致（…）」）。**只收发票行** —— 账面行（deferred）的确认走
   `receipt_confirmed`（A10 写收款），两者不是一件事。
3. **清**（`importer.rollback_batch`）：撤销本批时按**批次账期**清本 dim（只对
   `batch_type='ledger'`；先查 `import_batch` 拿账期再删，且在做 `UPDATE status` 之前）。

### 11.4 复核页 post 模式：逐票压制，且**不隐藏原文**

新增 `_import_confirmation(r)`（**只在 `mode == "post"` 生效**）+ `_rebuild` 里一行判定：

- 有留痕 ⇒ 该行 `is_import_confirmed=True`、`import_confirm_note=<备注>`；
  **仅当状态是「待确认」时**降为「高置信」（「待补录」是真待办 —— 票不在库得去补，留痕
  **绝不**覆盖它）。
- 无留痕 ⇒ 状态一字不动（**逐票判定，不一刀切**）。
- 原因列：疑问原文**照旧全展示**，只在末尾追加 `〔导入时已确认〕`（`REASON_IMPORT_CONFIRMED`）。
  刻意如此：post 是**只读查看**，不该把历史疑问从屏幕上抹掉，只该说明它已被处理过。

**`pre` 模式刻意不读留痕**：pre 的确认是**实时**的（`_confirmed` 下标集合）。若 pre 也读留痕，
「入库不过 → 返回修改 → 覆盖式重导同一账期」时，上一批的旧确认会**提前吞掉本批的疑问**。

### 11.5 边界

- 落痕只认**发票号**；问题行（`problems`）在 post 恒为空桶（镜表不含问题行），无票号可挂。
- `data["confirmations"]` 在 pre 模式不存在（`parse_ledger_file` 不产该键）→ 读侧一律
  `.get(...) or {}`，不报错。
- 旧页 `merged` 备注**只读不写**（`load_confirmations` 兜底兼容），批 4 删旧页时收口。

### 11.6 验证

| 项 | 结果 |
| --- | --- |
| 新测试 | `tests/test_import_confirm.py` **21/21**（引擎往返 9 + commit 同事务 7 + 读侧带出 4 + 空骨架 1） |
| 扩充测试 | `tests/_smoke_review_rebuild.py` **56 → 71/71**（新增 D 节 15 条：pre 不生效 / post 逐票压制 / 标注与原因文案 / `_merged_data` 收集） |
| 全量回归 | **35/35 全绿，0 Traceback，61.8s**（基线 34 → **35**，已同步 `run_tests.py` 的 `EXPECTED_FILES` + skill 的 4 处基线） |
| 反向校验 | `rev_check.py` 本批 **6 条探针全部如实变红**：P1 关 post 读留痕→红 4；P2 关 `_merged_data` 收集闸门→红 2；P3 关 commit 同事务写→**红 6**；P4 关 `rollback_batch` 清理→红 1；P5 `clear_confirmations` 误删 `merged`→红 3；P6 优先级反转→红 1 |
| 还原校验 | 跑完 `git diff --stat` = 本批 4 个改动文件，无探针残留 |

**本批踩到的坑**：**低置信原因必须选「能过写前校验」的那一类**。
`validate_ledger_before_write` 只查「经办人是否在花名册」，所以「**不在花名册**」的行在真实
导入里**到不了「确认入库」**（会被拦死）；第一版 fixture 用它 → 等于对着**不存在的情形**做验证。
改用「**无经办人**」（`handlers` 为空 → 校验直接通过）才是真实的「导入时确认」场景，并在用例里
加了 `D0` 前置断言锁定 fixture 语义（照抄「fixture 必须能被解析成预期形态」这条既有教训）。

### 11.7 提交

`43500e7`（7 文件 +645/−5）→ 分支 ref 照旧未推进（`git commit` 打印「已自动推送 ✅」仍是**假消息**：
`ls-remote` 问远端还是 `870e63c`）→ Python 直写 loose ref + `pack-refs --all`
→ 走 skill `github-api-push` 的 `push_commit.py`（15 对象：1 commit / 7 tree / 7 blob，全部 201）
→ `VERIFY_REMOTE MATCH`、`AHEAD_BEHIND 0 0`、`HEAD_PARENTS` 父数 1。


## 12. 批 3-2 落地（2026-09-18）：「经办人分摊」取库侧真值，原文留作〔源填写〕

### 12.1 要解决的问题（批 1b 暴露的两条毛病之二）

毛病 1：**手工修正过的行**，`raw_ledger` 刻意存**原文** → post 重解析拿不到修好的值。

### 12.2 先把「到底哪一列错」钉死（回源码逐字核对 + 真实库实测）

不先做这一步就会把批 3-2 做成一个大而无当的改造。逐列核对后，毛病 1 的落点**只有一处**：

| 复核页列 | post（批 1b）读哪 | 修正值可达？ |
| --- | --- | --- |
| 发票号 | `raw_ledger.invoice_no` | ✅ 镜表**存的就是纠正后的号**（`_insert_raw_ledger` 的明确例外，为了与库对齐） |
| 金额 | `raw_ledger.amount_num` | ✅ 它就是 `item["total_amount"]`，即**修正后**的金额 |
| 购方 | `raw_ledger.buyer` | ⚪ 购方**本来就不可修**：`_apply_resolved` 取 `p["buyer"]`，修正面板不产该字段 |
| 源文件备注 | `raw_ledger.remark` | ⚪ 同上（取 `p["remark_raw"]`），不可修 |
| 开票日期 | `invoice_date_raw` 重解析 | ⚪ 修正日期**不落镜表**，但界面里**根本没有这一列**（`HEADERS` 共 11 列） |
| **经办人分摊** | `raw_ledger.handler_text` **重新解析** | ❌ **毛病 1 的唯一实际落点** |

⇒ 精确表述：**post 的「经办人分摊」列显示台账原文的解析结果，而不是库里真正生效的分摊。**
原文解析失败的问题行（人工补的经办人）显示为 `—`，并重新冒出一串疑问。

### 12.3 落点：`charge_detail`（**只认本台账批次**），零 schema

真值在 `charge_detail`：`commit_ledger_import` 对每张建的票写 `(票号, 姓名, 分摊, source='import',
import_batch_id=本批)`。新增 `review_rebuild._batch_handlers(conn, batch_id)` 一次读出本批次全部分摊，
`rebuild_period_data` 逐行覆盖原文解析结果，并给 item 加 `handlers_from_lib` 标记。

- `handler_text` **原样保留** ⇒ 界面照旧 `库值　〔源填写〕原文`（与 pre 模式形状一致，UI 零改动）。
- 不新增表、不加列、不改导入写入路径 ⇒ **动到的只有一个纯读函数**。

### 12.4 为什么是「只认本批次」而不是「票号在库就取」

| 方案 | 后果 |
| --- | --- |
| 票号在库就取（任意批次） | sheet3 行也会被注入**别的账期的发票视角**分摊（批 0 §9.6：两者语义不同）。实测 12 期 sheet3 有 121 行的票在库，风险面很大 |
| **只认本批次**（采纳） | 本批次 ⟺ 本次台账导入把该行当**发票视角**建过账 ⟺ 分摊与行金额必然勾稽（导入前校验过）。sheet3 行永不写 `charge_detail`（`is_sheet3_row` 拦在票路径之外）⇒ 天然回退原文 |

另加一道**勾稽门闸**：`abs(sum(库值) − 行金额) <= 0.01` 才采用。宁可照旧显示原文，
也绝不因此冒出「经办人分摊合计≠价税合计」——post 没有「确认」按钮，这种假疑问就是
**用户点不掉的假待办**（批 1b 的老毛病，不能自己再引入一个）。反向探针 P3 证明这道闸真的在干活：
关掉它，E4 的原因列立刻打印出 `经办人分摊合计≠价税合计`。

### 12.5 边界 —— 以及**一条刻意没修**的缺口

- ⚠️ **sheet3（应收账款）行在导入时手工修正的分摊，库里没有任何落点**：`commit_ledger_import`
  对 sheet3 只写镜表（`is_sheet3_row` → `continue`），不建票、不写 `charge_detail`。
  ⇒ post 只能显示台账原文，**批 3-2 原理上修不到它**。要回显必须**先新增落点**
  （`raw_ledger` 加列 / 复用 `anomaly_note` 的新 dim），属独立后续批次（暂记 **3-2b**）。
- 同名多行按姓名聚合（与导入侧 `_agg_h` 同口径）——零成本防御，避免万一重复名被判「经办人重复」。
- 同一票在以往批次已有 `charge_detail` 的名，若本次金额变化，导入侧是走「只更新
  `received_override`」分支（`importer.py:891`）⇒ 该行仍挂**旧批次号** ⇒ 本批过滤命中不到
  ⇒ **回退原文解析**（安全侧失效）。

### 12.6 实测影响面（改前先量，避免「改了个看起来很像的东西」）

| 场景 | 结果 |
| --- | --- |
| 未修正的真实数据（2025-01，90 行发票类镜表行 / 86 行有本批 `charge_detail`） | **不一致 0 行** ⇒ 批 3-2 对正常数据**零扰动** |
| 受控复现（把 1 张 sheet1 行 + 1 张 sheet3 行的经办人改成同一人后导入） | sheet1 行：`charge_detail`=`傅强`（本批）/ 镜表原文=`胡坚` ⇒ 批 3-2 **正确回显**；sheet3 行：三表查无 ⇒ **无落点**（§12.5） |
| 12 期真实库 sheet3 | 198 行 / 121 行票在库 / **库值合计≠行金额 = 0 行**（勾稽门闸在真实数据上不误伤） |

### 12.7 验证

| 项 | 结果 |
| --- | --- |
| 扩充测试 | `tests/_smoke_review_rebuild.py` **71 → 82/82**（新增 E 节 11 条：主场景 5 + 端到端 1 + 反向探针 4 + 回归保护 1） |
| 全量回归 | **35/35 全绿，0 Traceback，61.0s**（测试文件数不变 → `EXPECTED_FILES` 不动） |
| 反向校验 | `rev_check.py` 本批 **4 条探针全部如实变红**：P1 关整段注入→红 3；P2 丢掉批次过滤→红 2（含 sheet3 那条）；P3 关勾稽门闸→红 2（**并如实打印出假疑问「经办人分摊合计≠价税合计」**）；P4 来源标记写死 `True`→红 4 |
| 还原校验 | 跑完 `git diff --stat` = 本批 **2 个改动文件**（`+161/−1`），无探针残留 |

> ⚠️ 测试**顺序依赖**：E 节往 2025-01 的票上补 `charge_detail`，必须排在 B2「往返等价」**之后**
> ——B2 断言的正是「没有库值时重建 ≡ 解析」，两者不能对调。

### 12.8 提交

`06a72dc`（3 文件 +242/−2：`app/engine/review_rebuild.py` / `tests/_smoke_review_rebuild.py` / 本文档）
→ 分支 ref 照旧未推进（`git commit` 打印的「已自动推送 ✅」仍是**假消息**）→ 修引用 → 走 skill
`github-api-push` 的 `push_commit.py`（9 对象：1 commit / 5 tree / 3 blob，全部 201）
→ `VERIFY_REMOTE MATCH`、`AHEAD_BEHIND 0 0`、`HEAD_PARENTS` 父数 1。

> 🆕 **本批新踩的坑（已补进 `github-api-push` skill 的踩坑清单 #25）**：
> 修引用时把 loose ref 写成了**短 SHA**（`06a72dc`，7 位）。git 只认**完整 40 位 OID** ——
> 于是 `git rev-parse HEAD` 返回**字面量 `HEAD`**、`git log` 空、`git status --short` 把整棵树
> 显示成 `A `（"像要删库"的 unborn 假象）。**对象层与 reflog 全程无损**，重写完整 OID 即愈。
> 判据：`git cat-file -p <sha>` + `rev-list --parents -n 1 <sha>` 能正常读出提交 ⇒ 只是引用写坏了。
> **禁止**在此状态下 `reset --hard`。

## 13. 批 2 落地（2026-09-18）：「台账 ⇄ 库」三维比对判进导入前四态

### 13.1 要解决的问题

导入前复核页（pre 模式）此前只做**台账内部**的规则判定（备注解析 / 分摊勾稽），
不与库里已有数据对照 —— 而旧「导入后」页恰恰有一套成熟的 `review_compare` 比对
（金额 / 经办人分摊 / 已收认定）。批 2 把这套比对**前置**：导入确认时就能看到
「台账写的 ⇄ 库里存的」对不上，而不是等入库后再发现。

### 13.2 落点（三个文件，零 schema）

| 文件 | 改动 |
| --- | --- |
| `app/engine/review_compare.py` | 抽出**共用比对核心**：`_recv_sides` / `lib_diff`（逐维差异 + 对照值）/ `lib_recv_totals` / `build_lib_context`（库侧三表一次读出）；`build_review_rows` 改为调用同一套（行为不变） |
| `app/engine/import_confidence.py` | `evaluate` 新增 `lib` 参数 + 比对块：命中 → 原因 `REASON_LIB_DIFF = "台账⇄库不一致（{}），请确认"` +〔逐维对照值〕；`None` → 完全跳过（行为同批 1） |
| `app/ui/unified_import_dialog.py` | pre 模式 `load_data` 时 `build_lib_context(period)` **一次读库缓存**（失败 → None，绝不卡导入）；`_rebuild` 只在 pre 模式传入 |

### 13.3 口径要点（与旧页逐字同一套）

- **三维**：金额 / 经办人分摊 / 已收认定。**不含购方**（573 行假差异的教训，§3）。
- **sheet3 排除**：同一票号可与 sheet2 并存且收款声明矛盾（应收视角 vs 发票视角），
  是已知噪声源 —— 排除后 12 期实测 **0 差异**（§13.4）。
- **纯人名豁免**：源经办人列无金额（沿用首月拆分的省略写法）→ 不比分摊金额。
- **消除机制只吃「确认」**（3-1 留痕），**不吃 `has_split`** —— 用户填了逐人收款
  ≠ 认可台账金额与库一致（与兜底类的关键差异）。
- **post 模式刻意不比对**：导入后镜表原文与库的差异正是导入时人工修正的痕迹
  （批 3-1/3-2 已分别落点），再报一遍 = 把毛病复活成点不掉的假待办。
- **库中缺失也报**：sheet1（已开票已入账）语义上必须在库，缺失即异常 →
  「库中缺失〔金额 台账X ⇄ 库 —〕」。
  ⚠️ **已于 §18 删除**（当期票本就不在库，实测把当期数据全刷成待确认）；同节还把
  比对维度收敛到「金额」一维 —— 本节的「三维」口径请以 §18 为准。

### 13.4 噪声实测（改前先量）

12 期真实库副本：基线 1235 行 → 1042 一致 / 127 不符，127 条**全部**是「已收认定不符、
库侧空」且根因是同号 sheet2+sheet3 并存矛盾；**排除 sheet3 后 = 1037 行、0 差异**。
即批 2 接上后对真实数据**零扰动**（不多报一行待确认）。

### 13.5 验证

| 项 | 结果 |
| --- | --- |
| 引擎不回归 | `test_review_compare.py` **27/27**、`_smoke_import_review.py` **77/77**（重构后旧行为逐字不变） |
| 扩充测试 | `tests/_smoke_unified_import.py` **165 → 179/179**（第 12 节 14 条：引擎层 10 + 对话框层临时库 4） |
| 全量回归 | **35/35 全绿，65.2s** |
| 反向校验 | `rev_check.py` 本批 **5 条探针全部如实变红**：P1 关整段比对块→红 7；P2 去 sheet3 排除→红 1；P3 库中缺失不报→红 1；P4 pre 不建上下文→红 2；P5 confirmed 不压制→红 1 |

> ⚠️ **本批测试基建新坑（两个）**：
> ① pre 模式对话框现在会按账期读**真实 DB** 建比对上下文 —— 所有构造 `UnifiedImportDialog`
> 的冒烟（`_smoke_unified_import` / `_smoke_red_backfill`）都必须把 `build_lib_context`
> 打桩成返回 `None`（evaluate 视为跳过），否则假票号在真实库里「库中缺失」全变待确认、
> 既有断言全灭。批 2 自身行为集中在第 12 节用**临时库 + 真函数**验证。
> ② 「不吃 has_split」是代码里的**缺席**（没有分支可关），反向探针探不了它 ——
> 由 12-8 正向断言覆盖；探针改为探「confirmed 压制」这个真实分支（P5）。

### 13.6 提交

`5149ca6`（6 文件 +498/−53：`review_compare.py` / `import_confidence.py` /
`unified_import_dialog.py` / `_smoke_unified_import.py` / `_smoke_red_backfill.py` / 本文档）
→ 分支 ref 照旧未推进（commit stderr 的「已自动推送 ✅」仍是假消息）→ 修引用（完整 40 位
OID + `pack-refs --all`）→ 走 skill `github-api-push` 的 `push_commit.py`
（13 对象：1 commit / 6 tree / 6 blob，全部 201）
→ `VERIFY_REMOTE MATCH True`、`AHEAD_BEHIND 0 0`、父数 1。
→ `d282718`（docs 补记提交号与验证口径，1 文件 +10）→ 同通道推送（4 对象全 201）→
`MATCH True`、`0 0`、父数 1。
⚠️ 本节初稿误写「docs 与代码同一次提交」，实际仍是两段式（代码+文档主体 → docs 补记）——
**修 ref 时还踩了新坑**：commit 后立刻 `rev-parse HEAD` 读到的仍是**旧值**（ref 未推进），
把旧 OID 写进了 ref；正确做法是**用 commit stdout 里的新短 SHA 解析完整 OID** 再写（已补进
skill 踩坑 #26）。

## 14. 批 3-3 落地（2026-09-18）：post 行级「编辑回写 + 还原为原件」接进新 post 页

### 14.1 要解决的问题

批 1b 落地的新「导入后」页（`UnifiedImportDialog(mode="post")`）只有**只读**能力；
旧 `ReviewPostView` 的编辑回写（`apply_edit`）与还原原件（`restore_from_archive`）
还挂在旧页上。批 3-3 把这两条能力接进新页 —— 「入库后仍可看**可改**」成立，
批 4 换页/删旧页的前提（三能力搬完）就差本批 + 批 3-1/3-2 已完成的这两项。

### 14.2 落点（四文件 + 一个新模块，零 schema）

| 文件 | 改动 |
| --- | --- |
| `app/engine/review_rebuild.py` | 重建 invoice item 补 `raw_id`（镜表行锚点）/ `synced`（修订标记）；sheet3 行经 `split_deferred` 原样搬 → 同样带锚点。pre 模式解析侧没有这两个键 → 编辑入口按「行有没有 raw_id」判定，天然只在 post 出现 |
| `app/ui/writeback_dialog.py`（**新**） | `WritebackDialog` 从 `review_post_view.py` **原样搬出**（字段/校验/收款明细逐字一致，仅新增 `result_data` 回执）；旧页改 import → 批 4 删旧页无牵连 |
| `app/ui/review_post_view.py` | 删除原地 `WritebackDialog` 类（−137 行），改 import 中立模块；行为不变 |
| `app/ui/unified_import_dialog.py` | 右栏加「编辑回写 / 还原为原件」两按钮（**仅 post 可见**）；可用性随行切换（编辑=行有 raw_id；还原=raw_id + synced=0 + 账期有存档）；`_writeback_row` / `_restore_row` → 引擎 → `load_period` 重载 + InfoBar 提示 |

### 14.3 口径要点

- **pre 模式不受影响**：批 1b 只读契约不破（右栏表单仍锁死、两按钮 pre 隐藏）；
  导入前的修改走既有的右栏就地面板。
- **按钮初始禁用**：post 空表/无选中行时 `itemSelectionChanged` 不触发，
  可用性停在 `_apply_mode_chrome` 的禁用态，防误点；选中行后由
  `_set_writeback_buttons` 按行解锁。
- **还原门闸三条件**（与旧页 `_on_sel` 同口径）：行有镜表锚点 + 已手工修订
  （synced=0）+ 账期有存档文件（还原要对原件重解析）。
- **重载统一走 `load_period`**：回写/还原成功后按账期从库重建（行值、四态、
  汇总、按钮全刷新），不做局部补丁。
- deferred（sheet3）行同样带锚点 → 编辑入口对其开放（旧页同口径）；
  `restore` 的「无原件坐标」拦截（问题行）由引擎侧 `ValueError` 兜底并弹「无法还原」。

### 14.4 验证

| 项 | 结果 |
| --- | --- |
| 扩充测试 | `tests/_smoke_unified_import.py` **179 → 191/191**（第 13 节 12 条：锚点/按钮可见性与可用性/回写三表落库/按钮路径自动重载/还原全链） |
| 全量回归 | **35/35 全绿（40.0s，复跑 47.6s）** |
| 反向校验 | `rev_check.py` 本批 **5 条探针全部如实变红**：P1 锚点置空→红 6；P2 还原门闸去 synced→红 2；P3 编辑按钮恒可用→红 1；P4 回写后不重载→红 1；P5 还原不置回 synced→红 2 |

> ⚠️ **本批测试实现两坑**：
> ① 反向探针 P4 首跑 **0 变红** —— 断言只测了手动 `_reload_post()`，没覆盖「按钮路径
> → 回写 → 自动重载」。补按钮路径断言：**exec 桩**（offscreen 不能真模态）——
> 子类覆写 `exec()` = 填必填项 + `_on_ok()` + 返回 Accepted，再 `U.WritebackDialog`
> 替换后 `btn_writeback.click()`，落库路径与真实点击逐字相同。
> ② exec 桩首版**忘了填 note** → `_on_ok` 走「修改原因必填」校验提前 return，
> 断言红得莫名其妙 —— 桩必须复刻真实输入（必填项也要填）。

### 14.5 提交

`b066b91`（6 文件 +497/−144：`review_rebuild.py` / `writeback_dialog.py`（新）/
`review_post_view.py` / `unified_import_dialog.py` / `_smoke_unified_import.py` / 本文档）
→ 分支 ref 照旧未推进 → 按 commit stdout 新短 SHA 解析完整 OID 修 ref（本批未再踩 #26）
→ skill `github-api-push` 的 `push_commit.py`（13 对象：1 commit / 6 tree / 6 blob，全 201）
→ `VERIFY_REMOTE MATCH True`、`AHEAD_BEHIND 0 0`、父数 1。

## 15. 批 4 落地（2026-09-18）：`ImportReviewView` 换页 + 删旧页 —— 统一收口

至此**入库前后是同一套实现**：`page_post = UnifiedImportDialog(parent, mode="post")`，
`page_pre = UnifiedImportDialog(parent)`。1b / 3-1 / 3-2 / 批 2 / 3-3 五批交付的能力
自此在界面上全部可见。

### 15.1 换页接线（`import_review_view.py`）

- `self.page_post = UnifiedImportDialog(parent=self, mode="post")`；`_reload_periods` /
  `_on_period_changed` 的 `set_period(cur)` 全部换成 `load_period(cur)`。
- **post 页「关闭」接线**：post 的「关闭」按钮走 `reject()` → `cancelled` →
  宿主 `_on_cancelled`。队列未跑时该方法的语义正是「清空队列态 + 回导入后 + navigate_back」
  ⇒ 关闭 = 返回导入页（旧 ReviewPostView 没有关闭按钮，这是换页后的新行为，配了 7b 断言）。
- `refresh()` / `showEvent` 契约不变：每次显示 `_set_mode("post")` → `load_period` 重读库。

### 15.2 删除清单

- **`app/ui/review_post_view.py` 整删**（423 行）：融合比对单表 + 筛选胶囊 +
  `AnomalyConfirmDialog`。⚠️ **「标记已确认异常」写入能力（`anomaly_note` dim='merged'）
  随之退役** —— 这是刻意收口：批 2 把差异判进导入前四态、批 1a 硬拦要求入库前清零、
  批 3-1 的 `dim='import_confirm'` 留痕取代了"入库后补标记"；旧 merged 数据的**读取回退链**
  （`load_confirmations`：本 dim 优先 → merged → handler/received）原样保留，历史标记仍生效。
- **`review_compare.py` 只留批 2 共用核心**（453 → 227 行）：删
  `build_review_rows` / `latest_batch` / `_derive_raw` / `_raw_side` / `load_confirmed_notes`
  及 `sqlite3` / `raw_ledger` / `parse_handler` / `parse_remark` 四个 import；
  docstring 重写为「台账 ⇄ 库比对共用核心」。
- `import_confidence.py` 一处注释同步改指向 `lib_diff`。

### 15.3 测试迁移（文件数不变，`EXPECTED_FILES` 未动）

| 文件 | 改法 |
|---|---|
| `test_review_compare.py` | **整文件重写**：13 节 `build_review_rows` 场景移植为**直测共用核心**（`lib_diff` 8 场景 + `build_lib_context` 8 + `lib_recv_totals` 2 + `_recv_sides` 3，共 23 断言）；「仅源有/仅库有/已确认异常」不再重复覆盖（批 2 四态 + `test_import_confirm` 已盖） |
| `test_deferred_sheet3.py` | H/H-2 弃用 `build_review_rows`，改「镜表行存在 + `build_lib_context` 库侧缺失/补录后出缺 + `deferred_sheet3_nos`」 |
| `_smoke_import_review.py` | 第 1/6/7 节换新页断言（6 胶囊、默认待补录、`load_period("")` 空安全、`btn_writeback/btn_restore` 初始禁用、`WritebackDialog` 构造）；**新增 7b**：post「关闭」→ navigate_back；深交互断言（按钮按行解锁/还原门闸）已在 `_smoke_review_rebuild.py` C 段，不重复 |
| `_smoke_import_queue.py` | 删 RP 引用；`_fake_load` 类级桩加 `mode=="post"` 早退（否则 post 页 `load_period` 也计数，「只载入队首一次」红）；补 `_uid/_RR get_conn` 桩 |
| `_smoke_data_clear.py` | 桩从 `RC.build_review_rows` 换成 `RR.rebuild_period_data`（喂假发票行，形状对齐 `review_rebuild` 产物）+ `_staff_names_from_db` 方法桩；断言前 `_show_all`（默认筛选=待补录）；空账期统计断言改 `"高置信 0"` |

**⚠️ 新坑（换页类改动的通用问题）**：`page_post` 也是 `UnifiedImportDialog` ⇒ 凡类级
打桩（如 `_fake_load`）或按属性断言（`chips`/`lbl_stat` 文案）的旧测试都会**同时命中两页**。
另一个必须记住的点：`load_period` 在 `load_data` **之前**还有两次查库
（`review_rebuild.get_conn` 取批次 + `_uid.get_conn` 取花名册，均为模块级
`from app.db import get_conn`）⇒ 构造 `ImportReviewView` 的冒烟**这两个也要桩**，
否则打到真实 `data/lawfirm.db`。

### 15.4 反向校验（`rev_check.py` 5 探针，全部如实变红）

1. 换页本体退回 `mode="pre"` → 「导入后页 mode='post'」红 1；
2. `_reload_periods` 不驱动 `load_period` → data_clear A1/A3 红 2；
3. 切账期不重载 → data_clear A2 红 1；
4. post「关闭」不接 `cancelled` → 7b 红 1；
5. `lib_diff` 金额比对关掉 → 重写后的单测「金额不一致」红 2（证明不是空转）。

### 15.5 提交

`35dcf9b`（9 文件 +313/−964；本批 **`git add -A` 差点把工作树里 C# 重写工作流的未跟踪
文件扫进提交 —— 已 `git reset` 后按 9 个路径重新暂存提交**；悬空的错误提交对象 `a30758a`
未被推送、未被引用，无害）→ 分支 ref 照旧未推进 → 完整 OID 直写 loose ref + `pack-refs`
→ skill `github-api-push` 的 `push_commit.py` → `VERIFY_REMOTE MATCH True`、
`AHEAD_BEHIND 0 0`、父数 1。

## 16. 批 3-2b 落地（2026-09-18）：导入时就地修改的字段留痕（方案 B，dim=`import_edit`）

### 16.1 要解决的问题（§12.5 刻意留下的缺口）

sheet3「**已在库**」行在导入时被人工修改的**文本字段**（购方 / 经办人分摊 / 案号 /
开票日期 / 总金额 / 经办人原文）库内**零落点**（收款走 A10 落 `collection`，文本不写任何
表）⇒「导入后」模式从 `raw_ledger`（原文镜表）重建时会**无声显示旧值**，用户以为改动丢了。
修改记录页（change_log）有字段级留痕，但不在复核现场。用户在 A(静默) / B(提示留痕) /
C(镜表回写) / D(加列) / E(查 change_log) 五案中拍板 **B**：只记「改了哪些字段」，不改
镜表原文（铁律不破坏），post 页原因列就地标注；回写仍走批 3-3 的 `review_writeback`。

### 16.2 落点（与批 3-1 同构，零 schema）

| 文件 | 改动 |
|---|---|
| `app/engine/import_confirm.py` | 新增 `EDIT_DIM="import_edit"` + `clear_edit_hints` / `save_edit_hints` / `load_edit_hints`（无 legacy 维度，不需要回退） |
| `app/importer/importer.py` | `commit_ledger_import` 同一事务内先清本账期本 dim → `save_edit_hints(data["edit_hints"])`；返回 dict 加 `edit_hint_count` |
| `app/engine/review_rebuild.py` | `rebuild_period_data` 带出 `edit_hints`（空骨架含该键） |
| `app/ui/unified_import_dialog.py` | ① `REASON_IMPORT_EDIT="导入时已修改"`；② `_edit_changed_fields(orig, ed)`：右侧表单编辑 vs 行原值的 7 字段归一化比较（金额 2 位小数、handlers/splits 排序后比）；③ `_merged_data` 收集 `_inv_edits` + `_deferred_edits` → `merged["edit_hints"]`（同票号合并去重；**待补录行跳过** —— 其修改随补录条目落库，不重复提示；发票行编辑也留痕 —— post 只显示镜表原文，批 3-2 只回填分摊）；④ `_rebuild` 设 `r["import_edit_hint"]`（`_import_edit_hint` 仅 post 生效）；⑤ `_row_field` invoice/deferred 两处 reason 追加〔导入时已修改：字段…〕（无疑问的高置信行同样标注，deferred 分支由早退 return 改为 text 累积） |

### 16.3 口径要点

- **pre 模式不读留痕**（与确认留痕同款约束）：pre 的修改实时显示在右栏；读留痕会让
  「覆盖式重导同账期」时上一批的旧提示提前混进本批。
- 提示**只标注、不改状态**：疑问原文照旧展示，绝不隐藏。
- 「已确认」「待补录」「待确认」等状态判定完全不受影响。

### 16.4 验证

- `test_import_confirm.py` 新增 D1–D10（dim 往返 / 空 items / 幂等 / 两 dim 并存互不覆盖 /
  clear 只清自己 / commit 同事务写 + `edit_hint_count` / rebuild 带出 / 覆盖式重导先清后写），
  全文件 **32/32**。
- `_smoke_review_rebuild.py` 新增 D6–D11（rebuild 带出 / pre 不读 / post 行 dict +
  原因列标注 / sheet3 在库行主场景 / `_merged_data` 发票行与 sheet3 在库行收集 +
  待补录行跳过；每段带**前置断言锁 fixture 语义**防空转），全文件 **93/93**。
- 全量 **35/35**（基线不变，`EXPECTED_FILES` 未动）。
- 反向校验 `rev_check.py` 5 探针全红：P1 importer 不写→3 红；P2 rebuild 不带出→5 红；
  P3 `_rebuild` 不收集→3 红；P4 `_merged_data` 不产出→1 红；P5 buyer 比对关掉→3 红
  （diff 逻辑非空转）。复跑全量确认还原（`git diff --stat` 恰好 6 文件）。

### 16.5 提交

`a0c329b`（6 文件 +345/−12，按路径 add）→ 分支 ref 照旧未推进 → 完整 OID 直写 loose
ref + `pack-refs` → `push_commit.py` API 推送 → `VERIFY_REMOTE MATCH True`、
`AHEAD_BEHIND 0 0`、父数 1。

## 17. bugfix（2026-09-18 晚）：sheet3 蓝字应收行补录误报「红字与蓝字原票不一致」（`c12dd66`）

### 17.1 现象与根因

用户报告：sheet3 引起需补录（如 `24332000000041172942`，**根本没有红字发票引用它**）时，
补录弹窗保存会弹「补录的蓝字原票与引用它的红字发票不一致」—— 填什么都报。

根因在 `_red_prefill_diff`（批 3-3/阶段 6-2 情形 b 的软校验取数）：它拿「**本行 ev**」当
红字侧去比，而 **deferred（sheet3）行的 `r["ev"]` 恒为 None**（sheet3 行不进 evaluate）
→ `red_orig_diff(None, None, 弹窗金额, 弹窗经办人)` 把红字侧当成 **0 元 / 无经办人** →
「总金额」「经办人」两项必中。该软校验在 `_open_backfill` 里对所有补录弹窗**无条件挂载**，
于是**所有 sheet3 行的补录都吃到假提示**。

（补录页 `ManualEntryView._red_soft_check` 走**查库反查** `load_red_reference`，查不到
红字返回 None —— 那条路径本来就对，不受影响。四态判定 `_red_consistency` 也有
`kind != "invoice"` 早退，同样不受影响。）

### 17.2 修复（用户口径：该提示只属于 sheet1/2 的补录）

`_red_prefill_diff` 加 `is_red` 守卫：只有 `ev["is_red"]`（sheet1/2 销项里的红字行落
「待补录」的场景）才做红⇄蓝一致性软比对；sheet3 行补录的 soft_check **结构保留但恒放行**。

### 17.3 验证

- `_smoke_red_backfill.py` 新增 **P2** 三断言：结构不变恒放行 / 随便填什么都不弹
  「不一致」/ 对照组红字行 soft_check 仍生效（防一刀切关死）。全文件 **89/89**。
- 全量 **35/35**；rev_check 1 探针（关掉 `is_red` 守卫）如实变红 2 条后复跑还原
  （diff 恰好 2 文件）。
- 提交 `c12dd66`（2 文件 +39）→ 直写 loose ref + `push_commit.py` → `MATCH True`、
  `AHEAD_BEHIND 0 0`、父数 1。

## 18. 批 2 修订（2026-09-18 晚）：门槛下沉到「行 + 维度」—— 导入前**只比金额**

### 18.1 现象与口径

用户报告：导入复核的「待确认」里**大部分是当期数据** —— 当期票还没入库，库侧当然没有，
自然被判成不一致。口径：这种核对应该只在**入库后**看导入复核页时才做。

先按用户选定的**方案 B**（比对只跑「票已在库」的行，删「库中缺失」分支）落地，
随后实测发现 **B 不够**，用户改拍**方案甲：导入前只比「金额」一个维度**。

### 18.2 🔴 实测：为什么方案 B 不够

真实库副本 + 真 `测试/发票台账/2025.1台账.xls`（源库/仓库**零写入**）。
真实 `data/lawfirm.db` 正处在「**2025-01 销项已导（86 票）、台账未导**」状态 ——
这正是**台账导入那一刻的常态**（`测试/销项导出/` 与 `测试/发票台账/` 都是**按月各 12 个文件**）。

| 项 | 值 |
| --- | --- |
| 库侧上下文 `build_lib_context("2025-01")` | `inv=86 / cd=0 / act=0 / snap=0` |
| 台账解析 sheet1/2 行数 | 86 |
| **票号已在库** | **86 / 86** ← 方案 B 的门槛**过了** |
| 维度隔离：**金额**（销项 ⇄ 台账） | **0**（两来源金额完全一致） |
| 维度隔离：**经办人分摊** | **86 / 86（100%）** |
| 维度隔离：**已收认定** | **67 / 86（77.9%）** |
| 旧行为 / 方案 B 的待确认 | **86 / 86（100%，B 无任何改善）** |

**根因**：`invoice` 由**销项导入**写（所以「票在库」成立），而 `charge_detail` /
`collection` / `raw_ledger` **全由本次台账导入**写 —— 导入那一刻必然为空。
⇒ **「票在库」≠「库侧有可比数据」**。

⚠️ 批 0 早已量到同一个数字并给出结论：**§6「已收认定不应进入导入时比对」**（假报
**884 / 1130 = 78.2%**，本次实测 **77.9%** 复现）、**§5.5「经办人分摊是台账独有信息，
比对它等于拿台账跟台账比」**。⇒ 批 2 把「三维全比」判进导入前四态，本身就是**违反
批 0 结论**的；方案 B 只把错误从「行级」缩到「维度级」，没真正修掉。

### 18.3 落点（方案甲）

| 文件 | 改动 |
| --- | --- |
| `app/engine/review_compare.py` | `lib_diff` 新增 **`dims` 维度选择器**（缺省 `None` = 三维全比，**旧行为不变**；`d_handlers` / `exp_total` / `act_total` 改为可省）；模块与函数 docstring 记录口径与理由。纯函数核心与 `build_lib_context` 的三维能力**保留**（供测试与将来导入后场景复用） |
| `app/engine/import_confidence.py` | 比对块加两道门槛：① `if no2 and d_inv is not None:` —— 票在库才比（删「库中缺失」分支）；② `lib_diff(src, d_inv, dims=(FIELD_AMOUNT,))` —— **只比金额**，不再调 `lib_recv_totals` |
| `tests/_smoke_unified_import.py` | 12-3 翻转为「不比不报、保持高置信」+ 新增 **12-3b**（库非空但本票不在库）；12-4 / 12-5 翻转为「分摊 / 已收维度退出导入前比对」（库侧有数据且不符也不报）；12-9 原「纯人名豁免 / 对照」改写为「金额一致 → 零扰动 / 金额不同 → 照报」；12-10 对话框层改判**金额**差异 |
| `tests/test_review_compare.py` | 新增第 9 组 **5 条**：`dims=(金额)` 吞吐另两维差异 / 金额一致返 `None` / 另两维入参可省 / 单维可选（分摊）/ `dims=()` 全不比（**23 → 28 条**） |

### 18.4 验证

| 项 | 结果 |
| --- | --- |
| 全量回归 | **35/35 全绿**（52.4s / 52.3s 两次，含 rev_check 前与还原后） |
| 单文件 | `_smoke_unified_import.py` **192 / 0 FAIL**；`_smoke_red_backfill.py` **89 / 0**；`test_review_compare.py` **28 / 0** |
| 反向校验（两条） | **A** 行级门槛关掉（退回「库中缺失也报」）→ 红 **2** 条（12-3 / 12-3b）；**B** 维度限定改回三维全比 → 红 **4** 条（12-4 / 12-5 / 12-9 / 12-10）。复跑回归确认还原，`git diff --stat` 恰为本批 **5 文件** |

### 18.5 影响与边界

- 导入前对**当期票**不再报差异；导入后页**照旧不比对**（批 2 的 post 契约不变：
  镜表原文与库的差异正是导入时人工修正的痕迹，已有批 3-1 / 3-2 / 3-2b 分别落点）。
- ⇒ 该比对的**实际生效窗口**＝「同一票的 `invoice` 已由**销项导入**写入，且金额与台账
  不一致」（= `销项 ⇄ 台账` 两个独立来源的交叉校验；现网实测 0 差异），以及重导已入库
  账期时。
- **分摊 / 已收认定两维退出导入前四态** —— 收款核对的归宿照旧：sheet3「已入库请确认
  收款」+ A10 超额校验（`over_collection_message`）。核心函数能力保留，将来若要在
  **导入后**页做这两维可直接复用 `dims=(FIELD_HANDLERS, FIELD_RECV)`。
- 残留（已知、不处理）：`build_lib_context` 仍读 `cd` / `act` / `snap`，对当前唯一
  消费者无用；保留是为测试与将来复用，代价是 `load_data` 多两次查询（可忽略）。
- ⚠️ **测试契约收紧**：12-9b / 12-10 的断言写成 `"台账⇄库不一致（金额）"`（**全等**），
  因此它们同时锁住「只比金额」—— 反向探针 B 命中它们是**预期**行为，不是脆弱断言。
  

---

## 19. 批 3-2c：导入后（post）页丢「已确认 / 已补录」标签（2026-09-19，用户报障）

提交：`daa285b`（代码 + 测试）；本节为配套文档提交。

### 19.1 现象（用户原话）

> 入库后，再次查看导入复核页面，之前的已确认、已补录中，没有记录了。只有在高置信中有。
> 也就是说没有了之前的状态。

即：**两个叠加视图筛选点开是空表**，处理过的行全沉进「高置信」。

### 19.2 🔴 根因：**状态是现算的，标签是内存的**

post 页与 pre **共用**同一张表、同一套四态判定，但数据来源不同：

| 项 | pre（导入前） | post（导入后） |
| --- | --- | --- |
| 数据 | 源文件解析 | `review_rebuild.rebuild_period_data` 从 `raw_ledger` 镜像**反向重建** |
| **状态**（待补录/待确认/高置信） | `evaluate` 现算 | **同样现算** ⇒ 一致 ✓ |
| **标签**（`is_confirmed` / `is_backfilled`） | 导入会话的**内存集合**（`_confirmed` / `_deferred_confirmed` / `_inv_edits` / `_deferred_edits` / `_backfills`） | 这些集合**全空**（`load_data` 每次清空重建）⇒ ✗ |

⇒ **同一个用户动作（点确认 / 改字段 / 填补录），在 pre 有标签、到 post 就没了。**
批 3-1 / 3-2b 已经给**两小类**落了留痕，其余三个断点没落：

| # | 断点 | 位置 |
| --- | --- | --- |
| 1 | sheet3「确认收款」不留痕 —— `_merged_data` 收集 `import_confirm` 时 `if r["kind"] != "invoice" … continue` | `unified_import_dialog.py:1915`（旧） |
| 2 | 靠「保存修改」解决的行不留痕迹 —— `_inv_edits` / `_deferred_edits` 仅内存；post 只有 `import_edit` 留痕（3-2b，**只标注原因列**），`is_confirmed` 仍 False | `_rebuild` 标签循环 |
| 3 | **「已补录」post 恒空表** —— `rebuild_period_data` 刻意 `backfills=[]`（内容已写进 `invoice(source='manual')`），而 `_row_backfilled` 只看 `self._backfills` | `review_rebuild.py:189-190,206` / `unified_import_dialog.py:1398-1404`（旧） |

### 19.3 落点（三条修法，三个独立 dim，均**只在 post 读**）

| 代号 | 修法 | 落点 |
| --- | --- | --- |
| **甲** | sheet3「确认收款」与发票行「确认」**同性质** → 也落 `import_confirm`；post 原因列同款追加〔导入时已确认〕 | `import_confirm`（既有 dim）+ `_merged_data` 收 deferred 行 + `_row_field` 的 deferred 分支 |
| **乙** | post 把「导入时已修改」计入「已确认」——与 pre 同口径（pre 的 `_inv_edits`/`_deferred_edits` **本就计进** `is_confirmed`）；**只补 `is_confirmed`，不动状态**（沿用 3-2b「只标注不改状态」的定论） | `_rebuild` 标签循环 2 行 |
| **丙** | 本批补录票号落**新 dim `import_backfill`** → `_row_backfilled` 在 post 读留痕 | `import_confirm.py`（+3 函数）/ `_merged_data["backfilled"]` / `commit_ledger_import` / `review_rebuild` / `_row_backfilled` |

| 文件 | 改动 |
| --- | --- |
| `app/engine/import_confirm.py` | 新增第三只 dim `BACKFILL_DIM = "import_backfill"` + `clear_/save_/load_backfill_hints`（与 `import_edit` 同构；载荷**只有票号**，故 `load` 返回 `set`）。模块 docstring 补「三只 dim 同源动机」 |
| `app/engine/review_rebuild.py` | `out["backfilled"] = load_backfill_hints(period, conn)`（骨架 + 有批次两处）；`backfills` 仍恒空（**内容 vs 判据**分离），docstring 说明 |
| `app/importer/importer.py` | commit 同事务 `clear_backfill_hints` + `save_backfill_hints`，返回 `backfill_hint_count`；**`rollback_batch` 改为一并清三类留痕**（原先只清 `import_confirm` —— 批 3-2b 上线时漏了 `import_edit`，本批收口） |
| `app/ui/unified_import_dialog.py` | 甲（`_merged_data` 收 deferred + 原因列标注）、乙（`is_confirmed`）、丙（`merged["backfilled"]` + `_import_backfilled_nos()` + `_row_backfilled` 双模式互补）；模块 docstring 新增「导入时留痕三类」 |
| `tests/test_import_confirm.py` | 新增 E 节 13 条（载荷/幂等/三 dim 并存/clear 各清自己/commit 计数/rebuild 带出/覆盖式重导失效/空骨架）+ B4b/B5 改判**三类留痕一起清**（**32 → 48 条**） |
| `tests/_smoke_review_rebuild.py` | 新增 F 节：post 端到端（含**真按筛选按钮 + 真读表**）+ pre 写侧收集（**+16 条 → 110 条**） |

### 19.4 验证

| 项 | 结果 |
| --- | --- |
| 全量回归 | **35/35 全绿**（58.2s / 58.9s 两次：改后与 rev_check 还原后） |
| 单文件 | `test_import_confirm.py` **48 / 0 FAIL**；`_smoke_review_rebuild.py` **110 / 0 FAIL** |
| 反向校验（7 条探针） | **全部如实变红、无「0 条变红」**：A 甲·写侧（F5）红 1；B 甲·读侧标注（F2）红 1；C 乙（F4）红 1；D 丙·读侧（F3 判定 + F3 筛选）红 2；E 丙·写侧（F6）红 1；F 丙·落库（E6/E7/E8）红 3；G 撤销清三类（B5）红 1 |
| 还原核验 | `git diff --stat` 恰为本批 **6 文件 / 403 插入 / 29 删除** |

### 19.5 影响与边界

- **写入端只增不改语义**：`confirmations` 由「只收发票行」扩为「发票行 + sheet3 确认收款行」；
  新增 `backfilled` 键（**与 `backfills` 内容分离**：内容随本事务入 `invoice`/`charge_detail`/
  `collection`，判据靠留痕）。
- **pre 行为一字不变**：`_import_confirmation` / `_import_edit_hint` / `_import_backfilled_nos`
  三个读口都 `if self._mode != "post": return 空` ⇒ 覆盖式重导同一账期时，上一批的旧留痕
  **不会**提前吞掉本批的疑问/补录（与批 3-1 的既定约束同款）。
- **三类留痕同生共死**：`rollback_batch` 一并清；`commit_ledger_import` 各自先清后写 ⇒
  覆盖式重导与撤销都不会留下失效痕迹。
- 仍**不恢复**的部分（已知、不属本批）：
  · 「已修正的问题行」(`_fix`) —— post 的 `problems` 恒空（问题行不入镜表，见 §7 / `review_rebuild`
    模块 docstring），修正后的行已随 `invoices` 落库 ⇒ 该类别在 post 本就不存在，无需留痕；
  · `_ov`（已收覆盖值）走的是历史弹窗路径，现已停用。
- ⚠️ **一处口径取舍（乙）**：post 里「导入时已修改」的行会同时是 `is_confirmed=True` **和**
  「待确认」（若还有改不掉的硬疑问）—— 这与 **pre 完全一致**（pre 的 `_inv_edits` 也是只置
  `is_confirmed`、不改状态），不是新引入的矛盾。
