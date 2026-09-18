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
（本批 docs 与代码**同一次提交** —— §12.8 的两段式补记不再需要，提交号已验后写入本节。）




