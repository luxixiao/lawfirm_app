# 设计文档：员工类型「两条线」模型 + 类型/角色统一

日期：2026-09-29 ｜ 分支：dev2 ｜ 状态：设计已定，待分批实现

## 1. 目标与原则

- **两条独立业务线**，互不牵连：
  - **业务线**（是否开票 `is_invoice`）：只管结算收入（净额口径对其生效）；**不碰费用承担**。
  - **费用线**（是否报销 `can_expense`）：**唯一**的费用承担闸门。
- **进报表 = `is_invoice OR can_expense`**（派生，不新增独立开关）。
- **类型与角色统一**：所有结算/报表开关只活在「类型」(`staff_type_def`) 一级；`role_def` 不再持有任何能改变结算结果的开关（去角色闸门）。
- **代码不写死任何类型/角色名**：报表取数全部由勾选框派生（D2）。
- 净额口径枚举保留 **「开票净额 / 收款净额」**（D3，不改名）。
- 「公共专属费用」→「专属费用」，按**费用分类**指定专属人员类型（方案乙）；未指定 = 无限制（D5）。

## 2. 最终字段模型

### 2.1 `staff_type_def`（类型一级，唯一真理源）

| 字段 | 类型 | 含义 | 备注 |
|---|---|---|---|
| `name` | TEXT PK | 类型名 | 不变 |
| `is_invoice` | INTEGER 0/1 | 是否开票（业务线） | **新增**，替换旧 `is_settle` 的"业务"语义 |
| `can_expense` | INTEGER 0/1 | 是否报销（费用线） | **新增**，旧 `is_settle` 的"费用"语义拆出 |
| `net_basis` | TEXT | 净额口径 | 保留；**仅 `is_invoice=1` 时有意义**，否则空 |
| `role_code` | TEXT | 角色（仅标签/分组 + `default_net_basis` 便利默认） | 保留，但**不再作任何闸门** |
| `is_settle` | — | 旧字段 | **退役**（迁移后删列） |
| 其余 | — | note/sort_order/is_builtin/created_at | 不变 |

派生：`in_report = (is_invoice = 1) OR (can_expense = 1)`。

### 2.2 `role_def`（角色降级为标签）

| 字段 | 去留 | 说明 |
|---|---|---|
| `label` | 保留 | 仅显示/分组 |
| `default_net_basis` | 保留 | 勾选 `is_invoice` 时的便利默认值（非强制） |
| `report_class` | 保留（当前无功能消费） | 对外报送分类，独立第 4 轴，本期不动 |
| `include_in_income_report` | **退役** | 不再作闸门（被 `in_report` 派生取代） |
| `forbid_public_exclusive` | **退役** | 被「专属费用」正向白名单取代 |

### 2.3 费用分类 / 类型（`expense_category` / `expense_cat`）

- 分类改名：`公共专属费用` → **`专属费用`**（两表 `UPDATE`）。
- `expense_category` 新增列 **`exclusive_types`**（TEXT，逗号分隔的人员类型名，或 JSON）。
  - 方案乙（分类级）：整个「专属费用」分类设一次归属。
  - 为空 → D5：无限制，所有类型人员均可承担。

## 3. 决策记录

| 决策 | 结论 |
|---|---|
| D1 一人多类型聚合 | 取 OR（任一类型满足即进对应线） |
| D2 进报表是否写死名 | 完全由 `is_invoice`/`can_expense` 派生，代码不写死类型名 |
| D3 净额枚举命名 | 保留「收款净额」，不改名 |
| D4 专属费用指定粒度 | **方案乙：费用分类级**设专属人员类型 |
| D5 未指定类型的专属费用 | 无限制，所有类型人员均可承担（开放式） |
| D6 聘用结算表范围（待确认） | 见 §6.3 |

## 4. 数据模型变更（SQL）

### 4.1 `staff_type_def` 迁移（`db.py` init_db 迁移块）
```sql
-- 新增两列
ALTER TABLE staff_type_def ADD COLUMN is_invoice INTEGER NOT NULL DEFAULT 0;
ALTER TABLE staff_type_def ADD COLUMN can_expense INTEGER NOT NULL DEFAULT 0;

-- 回填（旧 is_settle 拆分；业务角色才进业务线）
UPDATE staff_type_def
  SET is_invoice = CASE WHEN is_settle = 1 AND role_code IN ('partner','employee','parttime')
                       THEN 1 ELSE 0 END,
      can_expense = is_settle;   -- 原参与结算的都能扛费用

-- 删旧列（SQLite 需表重建，标准模式；回退靠 git）
```
> 回退：`is_settle` 删列前先全量备份；本批次 commit 走 API，可 `revert`。

### 4.2 `expense_category` 迁移
```sql
ALTER TABLE expense_category ADD COLUMN exclusive_types TEXT NOT NULL DEFAULT '';
UPDATE expense_category SET name = '专属费用' WHERE name = '公共专属费用';
UPDATE expense_cat SET category = '专属费用' WHERE category = '公共专属费用';
```

### 4.3 常量（`expense_cat.py`）
- `CATEGORIES` 第 6 项 `'公共专属费用'` → `'专属费用'`。
- `PUBLIC_EXCLUSIVE_CATEGORY = "公共专属费用"` → `EXCLUSIVE_CATEGORY = "专属费用"`。

## 5. 业务逻辑变更

### 5.1 业务线闸门（结算收入）
- `person_settlement.py:469-479`：按类型分支 `settle_flags_of` 改为读 `is_invoice`；
  `if is_invoice: income = 开票净额? inv_total : rec_net`；否则 `income = 0`。
- 净额口径统一取自**该人主类型的 `net_basis`**（消除旧按角色 `default_net_basis` 与按类型 `net_basis` 的不一致，即前期 gap）。

### 5.2 费用线闸门（费用承担）
- `expense_validation.py:85` 规则③：`st.is_settle_participant(handler)` → 改为 `st.can_bear_expense(handler)`（读该人主类型 `can_expense`）。
- `staff_type.py` 新增 `can_bear_expense(name)`：解析主类型 → `can_expense`。

### 5.3 进报表派生（替换 `include_in_income_report`）
- `staff_type.py:116-128 identity_roles()`：SQL 由 `WHERE include_in_income_report=1` 改为
  `WHERE role_code IN (SELECT role_code FROM staff_type_def WHERE is_invoice=1 OR can_expense=1)`；
  或新增 `report_roles()` 语义等价。所有报表/预览共用此源。
- `person_settlement.py:472` 按角色分支的 `is_settle = bool(ra["include_in_income_report"])` 删除（改由 5.1 的类型级判定）。

### 5.4 净额口径 UI（点2）
- `staff_view.py:256-266`：「业务金额方式」下拉挂到新「开票」勾选框下，仅 `is_invoice=1` 可编辑，否则灰显留空。
- `staff_view.py:237-254,287-311`：原「参与结算」复选框 → 拆为「开票」「报销」两个复选框（均写 `staff_type_def`）。

### 5.5 专属费用校验（点3，方案乙）
- `expense_cat.py:162-187 validate_public_exclusive` → `validate_exclusive(conn, items)`：
  - 费用类型/分类命中 `专属费用`；
  - 取该分类 `exclusive_types` 集合；
  - 解析承担人主类型（`primary_type_of`）；
  - 集合非空且承担人类型 ∉ 集合 → 违例（返回 `姓名(类型)` 去重升序）；
  - 集合为空（D5）→ 放行（无限制）。
- `expense_validation.py` 导入流程调用点改为 `validate_exclusive`。

## 6. 报表取数影响

### 6.1 开票收入表（`invoice_income_exporter.py:33-45`）
已走 `identity_roles()`（动态），✓ 满足 D2。迁移后 `identity_roles` 改源即自动正确。

### 6.2 月度结算表 / 预览（`settlement_report_exporter.py:276,295`、`settlement_view.py:111,242,630`、`person_settlement_exporter.py:144`）
均经 `identity_roles()`，✓ 自动随 §5.3 生效。

### 6.3 年度聘用结算表（`staff_income_exporter.py:57-63` `_staff_income_roles`）
**当前硬编码 `codes = ("employee","parttime")`** —— 与 D2「不写死名称」冲突。
- **D6 待确认**：去写死后范围怎么定？
  - 选项 A：范围 = `in_report` 全量（合伙也会进聘用结算表，范围变宽）；
  - 选项 B：范围 = `in_report` 且 `role_code <> 'partner'`（保留"仅雇员"语义，但仍由勾选框派生，无字面名）。
  - 推荐 **B**（既去写死、又保留聘用报表只含受雇人员的业务含义）。

## 7. UI 变更

- **类型设置页**（`staff_view.py:169-283` `_build_type_settings_tab` / `_refresh_types`）：
  列改为 `类型 / 开票 / 报销 / 业务金额方式 / 说明 / 人数 / 角色`；
  「开票」「报销」两个复选框；「业务金额方式」随「开票」联动（同现有 `_on_settle_changed` 逻辑迁移）。
  「角色」列仅作标签/分组选择，说明文案去掉"决定计入收入报表/费用"等闸门暗示。
- **费用类型维护页**（`expense_cat_view.py`）：「专属费用」分类增加「专属人员类型」多选
  （写 `expense_category.exclusive_types`）；非专属费用分类不显示该控件。

## 8. 迁移与兼容

- 老库 `is_settle` 按 §4.1 规则拆分；`role_def` 两列保留列定义但停用（避免删列风险可暂留，下个清理批次删）。
- 「公共专属费用」→「专属费用」数据 `UPDATE`（§4.2）。
- 所有改动经真实 venv `run_tests.py` 门禁（QT_QPA_PLATFORM=offscreen）验证后 commit + API 推送。

## 9. 分批实现计划（风险低 → 高）

| 批次 | 内容 | 主要文件 | 门禁 |
|---|---|---|---|
| **批次1（点2）** | 净额口径挂到 `is_invoice`；UI「业务金额方式」随「开票」联动；`net_basis` 枚举/命名不动 | `staff_view.py`、`staff_type.py`(set_net_basis 不变)、`person_settlement.py`(仅口径开关名) | 现有测试 + 新增「未开票则口径空」断言 |
| **批次2（点1）** | 拆 `is_settle` → `is_invoice`+`can_expense`；业务线/费用线闸门；`identity_roles` 改源；`role_def` 两闸门退役；UI 双勾选框；迁移 | `db.py`、`staff_type.py`、`person_settlement.py`、`expense_validation.py`、`staff_view.py`、测试 | 全量门禁 + 新增「汇总=拆分」不变量 + 老库迁移断言 |
| **批次3（点3）** | 「专属费用」改名 + 分类级专属人员类型 + 新校验 + UI 多选 + 迁移 | `expense_cat.py`、`expense_validation.py`、`expense_cat_view.py`、`db.py`、测试 | 重写 `test_expense_public_exclusive.py` 为 `test_expense_exclusive.py` |

> 每批单独 commit + 经 `github-api-push` 推送；不打了 tag（不触发 CI Release）。

## 10. 风险与待确认

- **D6**（§6.3）：聘用结算表去写死后的范围，需用户裁定（推荐 B）。
- 一人多类型时「主类型」取 `primary_type_of`，费用校验/净额均按主类型（与现有取数路径一致）。
- `role_def` 两退役列本期保留列定义、停用读取，下个清理批次物理删列（降风险）。
- 净额口径统一取「主类型 net_basis」后，原按角色 `default_net_basis` 的口径差异被消除（前期 gap 一并修复）。
