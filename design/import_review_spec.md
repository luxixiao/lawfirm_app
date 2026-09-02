# 导入复核页 Spec（合并「导入确认」+「导入校验」，可回写）

> 状态：**待用户确认后实施**
> 方案：方案 5（导入后可回写）+ 融合（三维度融进状态列，取消维度切换）
> 设计基准 commit：`8e1cd4a`

---

## 0. 背景与决策记录

用户诉求：「需要查看确认的内容两边一样，希望合并成同一个页面。导入前在这个页面查看修改确认，导入后仍旧在该页面进行管理。」并判断「导入确认页面的信息已经把导入校验页面包含进去了，只是不能选账期」。

用户对 7 个问题的答复（已锁定）：

| # | 问题 | 答复 |
|---|---|---|
| 1 | 导入后能否回写 | **能**。并因此取消发票台账页面的修改功能，台账数据统一在本页修改 |
| 2 | 合并后命名/位置 | 合并即可，改名「导入复核」 |
| 3 | 三维度保留还是融合 | **融合** |
| 4 | 无早期存档文件 | 无法对比核对 |
| 5 | 同账期多次导入 | 以最新导入的文件为准 |
| 6 | 导入前能否切账期 | 导入后可切，导入前不能切 |
| 7 | anomaly_note 兼容 | 由设计方决定（本 spec §6） |

---

## 1. 代码摸底结论（决定实现方式的关键事实）

### 1.1 两个页面共用同一解析器
`import_verify_view.load_verify()` 调 `resolve_archive()` → `parse_ledger_file(path, period)`，产出与导入确认页**完全相同**的 `parsed` 结构。差异仅三点：

| | 导入确认（现 tab2） | 导入校验（现独立页） |
|---|---|---|
| 数据来源 | 刚选的源文件，内存 | 存档文件重解析 + DB |
| 状态判定 | `import_confidence.evaluate` | `_compare` 三维度 |
| 可写性 | 可改（改完才写库） | 只读 + 标记已确认异常 |

→ **合并的本质：同一张表，换数据源 + 换状态判定器，再补账期选择。**

### 1.2 发票台账页改的是 `raw_ledger` 镜表，不是业务表
`app/ui/invoice_ledger_doc_view.py:293 _edit_row` → `raw_ledger.update_row()` → `_sync_invoice_from_raw()` 向下游同步：

- ✅ `invoice`：buyer / invoice_date / total_amount / case_no / remark
- ✅ `charge_detail`：按 handler_text 重算分摊（保留 received_override 与手写行）
- ✅ 发票号改名级联 6 张表（charge_detail / collection / refund×2 / prepayment_offset）
- ❌ **不同步 `collection`（收款）**
- ❌ **`recv_date_raw`（收到日期）在 `EDIT_FIELDS` 里，但完全没进同步链** → 改了不影响收款认定（疑似潜在 bug，见 §9-A）

`rl.update_row` 全项目仅 `invoice_ledger_doc_view.py:335` 一处调用；`invoice_ledger_view`（销项发票页）无编辑功能。
→ **移除发票台账页编辑后，raw_ledger 无其它编辑入口残留，干净。**

### 1.3 审计留痕已有成熟模式（复用）
```
log_change(conn, table, record_id, field, old, new, note,
           friendly_table=..., invoice_no=..., buyer=..., amount=..., handlers=...)
```
`raw_ledger.update_row` 逐字段写 change_log，并带整行上下文供「修改记录」页展示。

### 1.4 ★ 比对基准：`raw_ledger` 镜表（非存档文件）

> **本条已于 2026-09-02 修订。** 初版定为「存档 Excel 重解析 ↔ DB」，经查证原始镜表设计后改为「镜表 ↔ 业务表」。

**决策：比对基准 = `raw_ledger` 镜表，存档文件降级为「仅供溯源」。**

依据（`invoice_ledger_D_spec.md §2.2` 原始镜表设计 + 实测）：
- D spec 定义 `raw_*` = Excel 原文的 1:1 镜像，**且设计上就支持在其上增/删/改**（改完 `synced=0`，再同步到归一化表）
- 即：**镜像本就是"可改的 Excel 副本"**，是设计预留的改动保存层；存档文件（`data/archive/`）只是溯源快照
- 只读查真库实测：10 个 ledger 账期，镜表行 10/10（每期 85~143 行），**零缺口**

这一改同时消除了初版的最大缺陷：

| | 初版（存档为基准） | **现版（镜表为基准）** |
|---|---|---|
| 回写后 | 源文件不变 → **差异重现**，须自动标记异常打补丁 | 改镜表 + 同步业务表 → **差异干净消失** |
| 存档文件用途 | 比对基准 | **仅供溯源**（双击看原件） |
| 无存档 | 无法核对 | **仍能核对**（镜表在即可） |
| 性能 | 每次重解析 Excel | 直接查表 |

**连带收益**：闲置的 `synced` 字段被激活为「已手工修订」徽标（详见 §12.4）。
**注意**：这与用户第 4 点原答复「无早期文件则无法对比核对」有出入 —— 现语义为「无存档仍能核对，只是不能看原件」，已于 2026-09-02 确认。

---

## 2. 导航与页面归属

- 「数据导入」组：导入台账 / 导入记录 / **导入复核** / 修改记录 / 发票补录
- 删除导航项「导入校验」（`verify`），删除 `app/ui/import_verify_view.py`
- `ImportView` 移除 tab2，`QTabWidget` 改回普通 `QWidget`（`_build_import_tab` 原本就是往一个 QWidget 里塞，改动可控）
- 新增 `app/ui/import_review_view.py`：`ImportReviewView`
  - 独立导航入口 = 导入后模式（账期可选）
  - 导入流程解析完成后跳到本页 = 导入前模式（账期锁定）

---

## 3. 页面结构

### 3.1 顶部工具栏

```
[模式徽章: 导入前·待确认 2025-12 | 已导入 2025-12]   [账期 ▾]   [筛选胶囊]   [只看差异 ☐]
```

- **模式徽章**：只读状态展示，非切换开关。导入前显示「导入前 · 待确认 {period}」，导入后显示「已导入 {period}」
- **账期下拉**：导入前 `setEnabled(False)` 且只含当前账期；导入后列出 `import_batch WHERE batch_type='ledger' AND status='active'` 的全部账期（降序）
- **筛选胶囊**：`全部 / 待确认 / 差异 / 已确认异常 / 已跳过 / 高置信`（导入前隐藏「差异」「已确认异常」；导入后隐藏「待确认」「已跳过」）
- **只看差异**：仅导入后可见

### 3.2 表格：一套列，融合三维度

关键约束：**列数不翻倍**。源/库对照做进同一格内的双值展示，不符时才高亮差异值。

| 列 | 导入前 | 导入后 |
|---|---|---|
| 发票号 | ✓ | ✓ |
| 来源 | sheet/行号 | sheet/行号 |
| 购方 | 值 | `源值 / 库值`（不符高亮） |
| 金额 | 值 | `源值 / 库值`（不符高亮） |
| 经办人 | 值 | `源值 / 库值`（不符高亮） |
| 各经办人已收 | ✓ | ✓ |
| 收款认定 | 源声称 | `源声称 / 库实际`（不符高亮） |
| **状态** | 待确认 / 高置信 / 已跳过 | 见 §4 |
| 说明 | 原因/疑问 | 差异明细 / 已确认异常备注 |

### 3.3 右侧面板
复用 `ProblemFixPanel`：
- 导入前：可编辑（现状不变）
- 导入后：默认只读（复用现成的 `set_readonly(True)`），点「编辑」解锁 → 保存即回写

---

## 4. 融合后的状态列取值

**导入前**（沿用 `evaluate` 口径）：
- `待确认` — 解析失败或低置信
- `高置信` — 含已修正 / 已确认 / 编辑过的行
- `已跳过`
- 附标记 `已确认`（is_confirmed，绿色）

**导入后**（融合三维度，多值可并存）：
- `一致`
- `不符` — 明细后缀可组合：`金额不符` / `经办人不符` / `已收不符`
- `仅源有`（库里缺） / `仅库有`（源里没有）
- `已确认异常`（蓝底蓝字，不计入差异数）
- `—`（无存档，无法核对）

---

## 5. 回写机制（核心新增）

**回写 = 改镜表 + 同步业务表**（镜表为权威可改层，见 §1.4）。

### 5.1 新增引擎模块 `app/engine/review_writeback.py`

```python
def apply_edit(period: str, invoice_no: str, patch: Dict,
               note: str = "", operator: str = "") -> WritebackResult:
    """导入复核页统一回写入口（单事务）。
    1) raw_ledger.update_row        → 改镜表 + 复用现有审计 + invoice/charge_detail 同步
    2) _sync_collection(...)        → 新增：收款同步（现有缺口，见 §9-A）
    3) raw_ledger.synced = 0        → 标记「已手工修订，与 Excel 原件不一致」
    4) _refresh_snapshot_actual()   → 刷新 received_snapshot
    5) 写 change_log（分层，详见 §12；friendly_table/field/note 落点见 §12.6.1）
    """
```

- 走**单事务**，任一步失败整体回滚
- 收款同步为本 spec 新增（见 §9-A）：只有传了 `receipts` / `recv_date` 才触发，避免无谓重算
- **不再自动写 anomaly_note**：镜表基准下正常回写后差异自然消失，补丁已不需要（anomaly_note 角色收缩见 §6）

### 5.2 回写交互
- 导入后模式点「编辑」解锁时，弹出确认：`该账期数据已入库，修改将写入数据库并留痕。`
- **「修改原因」必填**（合规要求），写入 change_log 的 `note` 字段
- 保存后 InfoBar：`已回写 N 项修改。该行已标记为「已手工修订」。`

### 5.3 还原为原件（可选增强）
对 `synced=0` 的行提供「还原为原件」：从存档文件重读该行覆盖镜表 → 重新同步 → `synced=1`。
存档文件缺失时该入口置灰并提示。

---

## 6. anomaly_note 兼容与角色收缩（第 7 点，设计方决定）

### 6.1 兼容（不变）
**表结构不动，历史数据全保留。**

- 新标记统一写 `dim='merged'`（融合后已无维度概念）
- 读取 `_load_confirmed`：优先取 `dim='merged'`；没有则回退聚合旧 `dim IN ('handler','received')` 的备注并拼接
- 旧备注中「维度 1 发票信息不支持标记」的硬限制在融合后**解除** —— 统一行模型下所有维度都可标记

### 6.2 角色收缩（因 §1.4 改为镜表基准而调整）
初版里 anomaly_note 被用作"回写后差异重现"的补丁（自动标记）。**镜表基准下该补丁不再需要** —— 正常回写后差异自然消失。

anomaly_note 现在只用于**无法通过改镜表消除、但人工确认合理**的差异，例如：
- 库里存在源文档没有的发票（补录 / 其他来源导入）
- 源里有、但因解析规则被跳过的行
- 收款走线下、源备注未记载等已知合理的口径差异

即：**`synced=0` 表示"我改过"，`anomaly_note` 表示"这差异我知道、不用管"** —— 两者职责分离，不再混用。

---

## 7. 边界情况

| 情况 | 处理 |
|---|---|
| **无存档文件 / 存档缺失**（第 4 点，语义已按 §1.4 更新） | **仍能正常核对**（基准是镜表，不依赖存档）；仅顶部提示「无存档文件，无法查看 Excel 原件」，双击行的溯源入口置灰，「还原为原件」不可用 |
| **同账期多批次**（第 5 点） | 取最新 `active` 批次的存档文件；raw_ledger 按 invoice_no 取最新行 |
| **导入前切账期**（第 6 点） | 账期下拉 `setEnabled(False)`，账期由文件名决定 |
| 重解析失败 | 顶部红色条 + 保留上一状态，不崩溃（沿用现有 try/except） |
| 解析出的问题行 | 导入后模式下也展示，状态 `仅源有` |

---

## 8. 取消发票台账页的修改功能（第 1 点）

- 删除 `invoice_ledger_doc_view._edit_row` 及右键菜单项「编辑此行」
- **保留**右键「查看修改记录」（审计入口）
- 页面顶部提示改为：`如需修改台账数据，请前往「数据导入 → 导入复核」页。`
- 已确认：`rl.update_row` 仅此一处调用，移除后无孤儿入口；`EDIT_FIELDS` 常量保留（回写引擎复用）

---

## 9. 实施前需用户拍板的点

### A. `recv_date_raw` 不同步收款 —— 疑似潜在 bug【✅ 已确认：修】
`EDIT_FIELDS` 含 `recv_date_raw`（收到日期），但 `_sync_invoice_from_raw` 没有处理它，改了不影响 `collection`。
→ 用户 2026-09-02 确认**修**：在 `review_writeback._sync_collection` 中一并覆盖，顺带解决"改收到日期不生效"。

### B. 回写后自动标记「已确认异常」？【已作废】
~~建议自动标记~~ —— 镜表基准下回写后差异自然消失，该补丁不再需要，已按 §6.2 移除。

### C. 无存档时的行为？【已按镜表基准更新】
原答复「无法对比核对」。改为镜表基准后：**仍能完整核对与回写**，仅不能查看 Excel 原件（见 §7）。

### D. 「修改原因」是否强制必填？【✅ 已确认：必填】
用户 2026-09-02 确认**必填**（合规与可追溯需要），写入 change_log 的 `note`。见 §12.2。

### E. 导入前的修改要不要记修改记录？【✅ 已确认：记】
现状不记（见 §12.6）。用户确认要记，按「修正问题行 / 编辑已解析行」两种情形区分处理，且均置 `synced=0`。

### F. 文档债是否一并修订？【✅ 已确认：修】
`invoice_ledger_D_spec.md §9` 需同步更新（见 §12.7）。

---

## 10. 分阶段实施计划

| 阶段 | 内容 | 产出/验证 |
|---|---|---|
| **0** | **数据修正 + 文档债**：历史「无原件却 synced=1」的行置 0（`amount_raw='' AND amount_num!=0`）；修订 `invoice_ledger_D_spec.md §9` | 只读核查 SQL → 确认后一次性 UPDATE（先备份） |
| **1** | 新页骨架 + 导航 + 账期下拉 + **导入前模式**（迁移现有 tab2 逻辑） | `ImportReviewView` 可跑通导入全流程；`_smoke_unified_import` 迁移并全绿 |
| **2** | **导入后模式**（迁移三维度比对 → 融合为单状态列 + 源/库双值列） | 新冒烟 `_smoke_import_review`：比对结果与现导入校验页逐行一致 |
| **3** | **回写机制**（`review_writeback` + collection 同步 + synced 置 0 + 四层审计） | 单测：改金额/经办人/收款 → 三表落库 + change_log(L1/L2) + synced=0 + 快照刷新 |
| **4** | **导入前留痕**（§12.6：确认入库时统一落 change_log + synced=0） | 单测：修正问题行 → `(导入修正)` 记录；编辑已解析行 → 逐字段 old→new |
| **5** | 移除发票台账页编辑 + anomaly_note 兼容 + 全量回归 | 全量冒烟绿；`_smoke_import_view` 改造（无 tab2） |

每阶段独立 commit + push，保留可回退路径。
**阶段 0 与阶段 3 涉及写真实 DB，执行前必须先备份。**

---

## 11. 风险登记

| 风险 | 等级 | 缓解 |
|---|---|---|
| 回写改坏已入库数据 | **高** | 单事务 + 全字段 change_log + 回写前确认弹窗；建议在阶段 3 上线前对真实 DB 做一次备份 |
| 融合列信息密度过高看不清 | 中 | 源/库双值只在**不符时**才展开显示，一致时只显示单值 |
| 移除发票台账页编辑后用户找不到入口 | 中 | 页面顶部明确提示跳转；右键「编辑此行」改为弹提示引导 |
| `ImportView` 从 QTabWidget 改回 QWidget 影响导入日志回读 | 低 | `showEvent` 逻辑不变，仅换基类 |
| 镜表与存档原件长期漂移 | 低 | `synced=0` 徽标 + 「还原为原件」入口（§5.3）可随时对齐 |

---

## 12. 修改记录（change_log）方案

> 本章回答：合并后回写，**修改该怎么记、记在哪、记几层**。

### 12.1 现状摸底（只读查真库）

`change_log` 列：`id / table_name / record_id / field / old_value / new_value / note / created_at / friendly_table / invoice_no / buyer / amount / handlers`

现有内容分布：

| table_name | 条目数 |
|---|---|
| expense_cat | 18 |
| **raw_ledger** | **2** |

→ `raw_ledger` 仅 2 条 = 历史上**只发生过 1 次**台账编辑（1 条 `field='edit'` 事件行 + 1 条字段变更行）。说明发票台账页的编辑功能几乎未被使用，**移除它的实际影响极小**。

现有记录范式：
```
table_name='raw_ledger'  record_id='76'  field='handler_text'
old='宣浙军6200、陈娟10000'  new='宣浙军16200'
note='手工编辑发票台账行'
friendly_table='发票台账2025-01已开票已入账'
invoice_no/buyer/amount/handlers = 改「前」的上下文快照
```

### 12.2 分层记录设计（本 spec 采用）

| 层 | 记在哪 | 内容 | 回答什么问题 |
|---|---|---|---|
| **L1** 字段变更 | `change_log` | 每个变更的镜表字段一行 | **人改了什么** |
| **L2** 同步落地 | `change_log` | 1 条 `(同步)` 汇总 | **系统跟着改了什么** |
| **L3** 修订状态 | `raw_ledger.synced` | 0 = 已手工修订 | **这行还跟原件一致吗** |
| **L4** 异常确认 | `anomaly_note` | 人工确认说明 | **这差异我知道、不用管** |

**L1 — 镜表字段变更（逐字段）**
```
table_name='raw_ledger'   record_id=<镜表行id>
field=<列名>              # buyer / amount_raw / handler_text / remark / recv_date_raw ...
old_value / new_value     # 原始文本（保留镜表"不归一化"语义）
note=<修改原因，必填>
friendly_table='发票台账2025-01已开票已入账'
invoice_no / buyer / amount / handlers = 改「前」快照
```

**L2 — 同步落地事件（1 条汇总）**
```
table_name='raw_ledger'   ← 关键：仍挂 raw_ledger，不新增 table_name
record_id=<镜表行id>
field='(同步)'
old_value=''
new_value='invoice.total_amount 16200→16200; charge_detail 宣浙军 6200→16200; collection 无变化'
note=<修改原因>
```

### 12.3 为什么这样分层

- **不把业务表变更逐条写进 change_log**：改一个金额可能触发 `invoice` + N 条 `charge_detail` + `collection`，行数爆炸；且 D spec §9 明确 change_log 只归属发票台账，逐条记会污染「修改记录」页
- **但完全不记业务表又断了链路** → 用 1 条 `(同步)` 汇总补足，既完整又不膨胀
- **`table_name` 保持 `raw_ledger` 单一归属**：修改记录页的「表」列始终显示「发票台账」，筛选与展示逻辑不受影响

### 12.4 `synced` 字段激活（连带修复）

现状：`db.py:197` 定义 `synced INTEGER NOT NULL DEFAULT 1 -- 导入即与文档一致`，但 `raw_ledger.update_row` 编辑后**没把它置 0** → 字段实际闲置、语义不符。

改为：
- 回写成功 → `synced = 0`
- 「还原为原件」（§5.3）→ `synced = 1`
- 表格新增「修订」列：`synced=1` 显示 `—`；`synced=0` 显示橙色徽标 **已手工修订**

### 12.5 「修改记录」页需要改造（2026-09-02 修正）

初版判断"基本不用改"**有误** —— 已核实该页 9 列不含「字段」列，`field` 完全不渲染（详见 §12.6.1）。

**改造项：**
1. **补「字段」列**：`_HEADERS` / `_KEYS` 插入 `"field"`，位置在「修改表名」之后
   → 顺带修复"只看旧值/新值、看不出改的是哪个字段"的现有缺陷
2. **底纹分层**：`field` 以 `(` 开头的行（`(导入修正)` / `(同步)` / `(新增)` / `(删除)`）加淡色底纹，与真字段编辑区分
3. `_fill()` 的 `key in ("old_value","new_value","buyer","amount","handlers","invoice_no")` 左对齐集合按需补 `"field"`

### 12.6 ★ 导入「前」的修改，记不记？（2026-09-02 补充）

> 用户提问：导入前修改会记修改记录吗？导入后再修改呢？

**现状：不对称。**

| 场景 | 现在记吗 | 原因 |
|---|---|---|
| **导入前**（确认页修正/编辑） | ❌ **不记** | `app/importer/*.py` 全无 `log_change` 调用（已 grep 确认） |
| **导入后**（回写） | ✅ 记 | 走 `raw_ledger.update_row` → change_log |

导入前不记的逻辑依据：数据尚未入库，无"旧值"可比对。但实测发现一个**更严重的问题**：

```
sqlite> SELECT id,invoice_no,amount_raw,amount_num,synced FROM raw_ledger
        WHERE amount_raw='' AND amount_num!=0;
id=287  no=02095255  amount_raw=''  amount_num=100000.0  synced=1
id=387  no=02095255  amount_raw=''  amount_num=100000.0  synced=1
```

这两行是**导入时被人工修正**的（修正行没有 `raw_row`，故 `_raw_cell()` 取不到原文，原文列留空）。然而 `synced=1` —— 系统把它标记为"与 Excel 原件一致"，但它**根本没有原件，是凭空填的**。

→ **后果：导入时的人工修正既无留痕，又被伪装成与原件一致。这类"凭空填"的行风险最高，反倒最不可追溯。**

**本 spec 的决定：导入前的修改也要记，且按两种情形区分。**

| 情形 | 有无旧值 | 记录方式 |
|---|---|---|
| **A. 修正问题行**（解析失败，人工凭空填） | 无 | `old_value=''`，`new_value='金额 100000; 经办人 王竹青60000、徐思嘉40000'` |
| **B. 编辑已解析行**（高置信/低置信行被主动改） | 有 | 按 §12.2 L1 正常逐字段记 `old→new` |

两者共同点：
- `table_name='raw_ledger'`，`note` = 用户填写的修改原因（**必填**）
- **该行 `synced = 0`**（无论 A 还是 B —— 改过/凭空填的都不再等于原件）
- 在**点「确认入库」时统一落盘**（不是每次右栏保存都写），避免事务碎片化

### 12.6.1 ★「导入修正」标记落在哪一列（2026-09-02 修正）

> 用户确认："导入时修正问题行也记录下来，来源写成导入修正"。
> 但 **`change_log` 没有「来源」列**，且修改记录页不显示「字段」列 → 需明确落点。

**已核实（`app/ui/audit_view.py`）：**
```
_HEADERS = ["修改时间","修改表名","发票号码","对方","金额","经办人","旧值","新值","备注"]
_KEYS    = ["created_at","friendly_table","invoice_no","buyer","amount","handlers",
            "old_value","new_value","note"]
```
- 「修改表名」列显示的是 **`friendly_table`**（非 `table_name`）
- `_KEYS` **不含 `field`** → 「字段」列根本不渲染
- 因此把 `(导入修正)` 写进 `field` **页面上看不见**，仅能被 `fetch_log(keyword=...)` 搜到（keyword 匹配范围含 field）

**修正后的落点（两处都写，双向可见）：**

| 列 | 导入时修正/编辑 | 导入后回写 |
|---|---|---|
| `friendly_table`（**修改表名列，可见**） | `发票台账 · 导入修正` | `发票台账2025-01已开票已入账`（`build_friendly_table` 原样） |
| `field`（程序化过滤用） | `(导入修正)` | 具体列名 / `(同步)` |
| `note`（备注列，可见） | 用户填写的修改原因（必填） | 用户填写的修改原因（必填） |

→ 用户在修改记录页的「修改表名」列直接看到 **`发票台账 · 导入修正`**，即用户所说的"来源"。

**同时修复一个现有缺陷**：修改记录页补一列「字段」（`field`），既让 `(导入修正)`/`(同步)` 标记可见，也解决了"只看旧值/新值、看不出改的是哪个字段"的老问题（样例：`宣浙军6200、陈娟10000 → 宣浙军16200` 无法判断改的是经办人）。
- `_HEADERS` 增加「字段」，置于「修改表名」之后
- `_KEYS` 相应插入 `"field"`
- `field` 以 `(` 开头的行（`(导入修正)` / `(同步)` / `(新增)` / `(删除)`）加淡色底纹，与真字段编辑视觉分层

**代价评估**：只有**被人工改过的行**才写，不是每行都写。实测 10 个账期每期 85~143 行，修正行通常个位数，change_log 增量可接受。

### 12.7 与 D spec §9 的文档冲突（需同步更新）

D spec 写的是「`change_log` 此后**只**由『发票台账』页写入」。本方案移除了该页的编辑功能，写入方变为「导入复核」页（导入前 + 导入后两条路径）。
→ 实施时需同步修订 `invoice_ledger_D_spec.md §9` 的表述，避免文档与实现脱节。（用户 2026-09-02 已确认「文档债一同修订」）

---

## 13. 阶段 3 实施设计（回写机制，2026-09-02 定稿）

> 本节的代码摸底与设计于 2026-09-02 完成，**尚未编码**，待用户确认后实施。

### 13.1 现成可复用件（代码摸底结论）

| 函数 | 位置 | 用途 |
|---|---|---|
| `_write_collection_for_invoice(conn, inv, batch_id)` | importer.py:169 | **删+重写 collection**，支持 split_receipts（逐人收款）与 remark（pure_date/receipts），末尾自动 `_upsert_received_snapshot`（方案E 快照） |
| `_refresh_snapshot_actual(conn, invoice_no, batch_id, expected_json=None)` | importer.py:249 | 只重算 actual（保留 expected） |
| `raw_ledger.update_row` / `get_row` | raw_ledger.py | 镜表编辑 + 逐字段 change_log + `_sync_invoice_from_raw`（invoice/charge_detail 同步）；`_sync_invoice_from_raw`/`_sync_charge_detail` **本身已 conn 感知** |
| `log_change(conn, table_name, record_id, field, old, new, note, **ctx)` | change_log.py | 审计，ctx 带 friendly_table/invoice_no/buyer/amount/handlers |

### 13.2 必须的小重构：`update_row` / `get_row` 支持外部 conn

现状：二者各自 `get_conn()` + 自 commit/close → 无法满足回写"单事务、任一步失败整体回滚"。

重构（向后兼容，发票台账页旧路径不受影响）：
```python
def get_row(rid, conn=None):        # conn 缺省自开自关；传入则复用不关
def update_row(rid, data, note="", conn=None) -> list:
    # 返回本次变更字段列表 [field, ...]（供 L2 汇总）；conn 缺省自 commit/close
```

### 13.3 `app/engine/review_writeback.py`（新增）

```python
def apply_edit(period: str, raw_id: int, patch: Dict, note: str) -> Dict:
    """导入后回写（单事务）。
    patch 键（镜表原始文本语义，与 EDIT_FIELDS 对齐；缺省=不改）：
      invoice_date_raw / invoice_no / buyer / amount_raw / case_no / remark / handler_text
      receipts: Optional[List[(person_name, amount, ym)]]  # 显式收款明细（split_receipts 语义）
    流程：
      1) get_row(raw_id, conn) 取旧行（diff 基准）
      2) data = 旧行合并 patch → update_row(raw_id, data, note, conn=conn)
         → L1 逐字段 change_log + invoice/charge_detail 同步（复用）
      3) 若涉及金额/经办人/备注/收款 → 重建 inv dict（remark=parse_remark(新备注)）→
         _write_collection_for_invoice(conn, inv, batch_id)（删+重写 collection+刷快照）
      4) UPDATE raw_ledger SET synced=0 WHERE id=raw_id
      5) L2 汇总：log_change(field='(同步)', new='invoice.total_amount A→B; charge_detail ×N; collection 重写 M 条')
      6) 成功 commit，任一异常 rollback + 抛错
    返回 {"changed": [字段], "summary": str}（供 UI InfoBar）
    """
```

- `_write_collection_for_invoice` 的 remark 路径自动把"改备注里的收款日期"落到 collection → **recv_date_raw 修复以"发票行收款由 remark/receipts 驱动"的方式消化**
- invoice_no 改名走 update_row 既有级联（6 张表）
- 红字发票（amount_raw 负数）：_write_collection_for_invoice 的 is_red 分支不写 collection

### 13.4 预收款行（sheet4）——范围决策点【待用户拍板】

代码摸底发现比 recv_date_raw 更大的缺口：

- `recv_date_raw` 是**预收款行专属**字段（发票行导入时恒空）
- 旧 `_sync_invoice_from_raw` 对 `kind='prepayment'` 行**整体跳过** → 发票台账页编辑预收款行 = 只改镜表，业务 `prepayment` 表不动
- 预收款页（prepayment_view）**只能核销（offset）**，不能改金额/日期/购方 → 预收款数据全系统无处可改

**方案 A（推荐，阶段 3 聚焦发票行）**：回写引擎仅支持 `kind='invoice'` 行；导入后页对预收款行「编辑」置灰 + 提示"预收款管理请到「业务数据 → 预收款」页（当前仅支持核销）"。预收款行改数据留待后续单独排期。
**方案 B（顺带预收款行）**：阶段 3 一并实现预收款行回写（写 `prepayment` 表 received_date/buyer/amount/person_text/case_no/remark + 镜表 + 审计；涉及与 offset 核销的一致性），范围明显增大。

### 13.5 UI 接入（ReviewPostView）

- 「标记已确认异常」旁新增**「编辑」**按钮；选中行 `raw_id` 非空且 `kind='invoice'` 时启用（阶段 3 先行版本：预收款行按 §13.4 决策置灰或可编辑）
- 打开 `WritebackDialog(period, row)`：
  - 表单字段（镜表原始文本语义）：开票日期 / 发票号码 / 购方 / 金额 / 案号 / 备注 / 经办人
  - **收款明细**子表（收款日期 / 金额 / 经办人，从 collection 预填，可增删改）→ receipts
  - **修改原因必填**（§9-D）
  - 校验：经办人列可解析且合计=总额（复用 parse_handler_column 校验）；金额可解析
- 确定 → `apply_edit` → InfoBar 成功/失败 → 刷新比对行
- 回写后该行差异消失（镜表=业务表），但 `synced=0` → 表格「修订」徽标（阶段 3 同步加"已手工修订"列或徽标，见 §12.4）

### 13.6 测试计划

- `tests/test_review_writeback.py`（内存库单测）：改金额/购方/备注/经办人/收款明细 → 断言 invoice/charge_detail/collection/synced/change_log(L1+L2)/快照刷新；发票号改名级联；红字不改收款；失败回滚（注入异常 → 全表无变化）
- `_smoke_import_review` 增补：编辑按钮启停、WritebackDialog 构造、保存后刷新
- 全量回归 13 套

---

## 14. 阶段 4 实施设计（导入前留痕，2026-09-02 定稿）

> 代码摸底与设计于 2026-09-02 完成，**尚未编码**，待用户确认后实施。

### 14.1 摸底结论

- 统一确认页的状态追踪（`unified_import_dialog`）：
  - `self._fix: Dict[p_index, data]` = 修正的问题行；`self._inv_edits: Dict[inv_idx, data]` = 已存在发票右栏就地编辑
  - 两者存的是同一结构 `self.fix_panel.read_fix()` 结果（invoice_date/total_amount/handlers/split_receipts/buyer/case_no 等）
  - 点「确认」(_confirmed) 不改数据 → **无需留痕**
  - `_rebuild_work` 把修正合并进工作副本；真实 `self._data` 全程不改（留痕的 old 值可安全取自 _data）
- `commit_ledger_import` 内部 `batch_id = _new_batch(...)` 但**返回值不含 batch_id** → 需补（向后兼容）
- 镜表行由 `_insert_raw_ledger` 写入（synced=1 默认）；留痕后需按 (batch_id, invoice_no) 定位 rid 置 synced=0
- 修改记录页 audit_view `_KEYS` 不含 field（spec §12.5 已定改造）

### 14.2 留痕内容（§12.6 定案）

| 情形 | 记录 |
|---|---|
| A. 修正问题行（解析失败凭空填） | 1 条：`field='(导入修正)'`, `old=''`, `new=新值摘要`, `friendly_table='发票台账 · 导入修正'` |
| B. 编辑已解析行 | 逐字段 `field=列名` old→new（invoice_date/total_amount/buyer/case_no/handler_text）；备注/收款明细有变另补 1 条摘要行 |

两者：`note='导入时人工修正'/'导入时人工编辑'`（导入前修正无用户原因输入框，note 记性质即可）；
改后该镜表行 `synced=0`（与 Excel 原件不一致/无原件）。

### 14.3 落点与调用顺序

1. `commit_ledger_import` 返回值补 `"batch_id": batch_id`
2. `UnifiedImportDialog.collect_import_fixes()` → 规范化 items（old=取自 _data 的原解析值；new=ed；fix 的 invoice_no 取 read_fix 结果——实现时核对 `_validate_invoice` 是否带 invoice_no，缺则回退从原 problem/合并结果定位）
3. 新增 `app/engine/import_fix_log.py::log_import_fixes(batch_id, items)`（**独立事务**——留痕失败不应回滚已成功入库的导入；单连接内逐项 UPDATE+log_change，收尾 commit）
   - 按 (import_batch_id, invoice_no) 查 raw_ledger rid；查不到 → skip 计数
   - ctx（friendly_table/invoice_no/buyer/amount/handlers）取**改前**快照
4. `ImportReviewView._on_confirmed`：commit 成功后 → `log_import_fixes(r["batch_id"], page_pre.collect_import_fixes())`；留痕异常仅提示不阻断（数据已入库）

### 14.4 audit_view 改造（§12.5 落地）

- `_HEADERS` 在「修改表名」后插入「字段」；`_KEYS` 同步插 `"field"`
- `field` 以 `(` 开头的行（(导入修正)/(同步)/(新增)/(删除)）加淡色底纹
- `_fill()` 左对齐集合补 `"field"`

### 14.5 测试计划

- `tests/test_import_fix_log.py`（内存库，复用 db.init_db 迁移技巧）：fix/edit 两类 items → 断言 change_log（field/old/new/friendly_table）+ synced=0；查不到镜表行 → skip
- `_smoke_import_review` 增补：collect_import_fixes 空态返回 []；编辑后产生条目
- 全量回归 13 套
