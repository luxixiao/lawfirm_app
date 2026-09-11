# T6 三件验证之 ②：3 台 PC 3 天 0 锁冲突 —— 静态核查报告

- 仓库：`C:\Users\Bingo\Desktop\buddy2\lawfirm_app`（分支 `pilot/dotnet`）
- 场景：律所 3 台 PC 经 **Seafile** 同步整个工作目录，`data\lawfirm.db` 本体在同步目录内，不经数据库服务器
- 方法：**静态源码核查**（grep + 逐文件读），未运行程序、未改动任何代码
- 核查人：严过关（QA）
- 结论一句话：**代码层的「只读纪律」是成立的（全仓零写库调用、连接全部短生命周期），真正的风险不在 C# 代码，而在 SQLite 的 WAL 伴生文件 + `bin/obj` 构建产物 + 导出产物这三样东西落在 Seafile 同步目录内。**

---

## 一、核查结论表

### ① `OpenReadOnly` / `FindDatabase` 实现

| # | 检查项 | 证据（文件:行号） | 判定 | 说明 |
|---|---|---|---|---|
| A1 | 是否真用 `Mode=ReadOnly` 连接串 | `csharp/LawFirm.Data/DbConnection.cs:34-38`（`Mode = SqliteOpenMode.ReadOnly`） | **PASS** | 文件系统级只读，非注释、非口头约定，代码实证 |
| A2 | 是否真开 `PRAGMA query_only=ON` | `csharp/LawFirm.Data/DbConnection.cs:44` | **PASS** | 连接级第二道防线，只接受 SELECT |
| A3 | 连接是否短生命周期（不长期持锁） | `SettlementQueryService.cs:32` + 各方法 `using var conn`；`ExportService.cs:38/63/88` 均 `using (...)`；`Cli/Program.cs:62` `using SqliteConnection` | **PASS** | **这是多机场景下最关键的一条**。全部 4 个调用点都是 `using` 作用域，用完即释放，不存在"软件开着就一直占着 db"的情况。锁窗口 = 单次查询耗时 |
| A4 | `FindDatabase` 是否硬编码路径 / 依赖 CWD | `DbConnection.cs:62-69`（从 `AppContext.BaseDirectory` 逐级向上找 `data/lawfirm.db`） | **PASS** | 不依赖当前工作目录，相对 exe 自身位置自定位 |
| A5 | **3 台 PC 路径不同会不会找错库** | 同上 | **PASS** | 3 台机器的 Seafile 本地路径可以各不相同（C 盘 / D 盘 / 不同用户名），因为是**从 exe 所在目录往上回溯**，不是硬编码。前提是每台机器的 exe 都在「同一 Seafile 资料库内的同一相对位置」 |
| A6 | 环境变量 `LAWFIRM_DB` 优先级最高 | `DbConnection.cs:58-60` | **RISK** | 若某台机器残留旧的 `LAWFIRM_DB` 指向本地副本，它会**静默**使用与另 2 台不同的库，且 UI 无任何提示 → 表现为"三台机器数据不一致"，极易被误判为同步问题。见 R-M2 |
| A7 | 兜底路径是否正确 | `DbConnection.cs:72`（`BaseDirectory/../../data/lawfirm.db`） | **RISK** | 从 `bin\Debug\net10.0-windows` 上两级 = `bin\data\lawfirm.db`，**并不存在**。且该分支**未做 `File.Exists` 校验**（前两个分支都做了，不一致）。仅在 `data/` 彻底缺失时触发，但会抛 `FileNotFoundException` 且路径误导，3 台机器排查时浪费时间。见 R-M4 |
| A8 | UI 是否展示当前连接的 db 路径 | UI 侧无（对比 `Cli/Program.cs:144` 有 `[INFO] DB = ...`） | **RISK** | CLI 会打印，UI 不打印。3 天测试里无法一眼确认"这台机器读的是哪个库"。见 R-M2 |

### ② 全仓写库调用扫描

扫描范围：`csharp/` 全部 `.cs`（含 `Data` / `UI` / `Exporter` / `Cli` / `App`）。
扫描模式：`INSERT|UPDATE|DELETE|CREATE TABLE|ALTER TABLE|DROP TABLE|VACUUM|REPLACE INTO|PRAGMA`、`ExecuteNonQuery|ExecuteScalar|.Execute(|ExecuteAsync|SqliteCommand|SqliteOpenMode`。

| # | 检查项 | 证据（文件:行号） | 判定 | 说明 |
|---|---|---|---|---|
| B1 | 是否存在 `INSERT / UPDATE / DELETE` | 全仓 0 处真实命中（仅 `DbConnection.cs:15` 注释、`README_T2.md:22` 文档中提及） | **PASS** | 零 DML |
| B2 | 是否存在 `CREATE TABLE / ALTER / DROP / VACUUM` | 全仓 0 处真实命中（仅 `CollectionRow.cs:7`、`InvoiceRow.cs:7` 注释里描述 Python 侧建表） | **PASS** | 零 DDL、无迁移、无 EF、无建表 |
| B3 | 是否存在 `ExecuteNonQuery`（Dapper 写路径） | 全仓 **0 处** | **PASS** | 最强证据：Dapper 的写入口根本没被使用 |
| B4 | `.Execute(` 调用是否安全 | `DbConnection.cs:44-45` 两处，均为 `PRAGMA query_only` / `PRAGMA foreign_keys` | **PASS** | PRAGMA 是连接参数设置，不是数据写入 |
| B5 | `ExecuteScalar` 调用是否安全 | `CollectionRepo.cs:21`、`InvoiceRepo.cs:22`，均为 `SELECT COUNT(*)` | **PASS** | 读 |
| B6 | `OpenConn` / 全部连接入口 | `SettlementQueryService.cs:32` 唯一入口 → `DbConnection.OpenReadOnly(FindDatabase())` | **PASS** | 单一入口，无法绕过只读封装 |
| B7 | 是否存在非 `ReadOnly` 的打开模式 | 全仓 `SqliteOpenMode` 仅 `DbConnection.cs:37` 一处，值为 `ReadOnly` | **PASS** | 无 `ReadWrite` / `Memory` |
| B8 | 结算引擎是否只读 | `csharp/LawFirm.Exporter/SettlementEngine.cs` 无任何写关键字命中 | **PASS** | 纯内存计算；`ExportService.cs:40` 调用 `SettlementEngine.Build(conn, year)` 后只写 xlsx |
| B9 | **会不会在 UI 使用中被触发** | 综合 B1-B8 | **PASS** | **结论：不会。** UI 层在代码层面不存在任何写库路径，R11 只读纪律成立 |

> **B 组小结**：这是本次核查中最干净的一块。`csharp/` 下不存在任何一条会写 `lawfirm.db` 的代码路径。所谓"锁冲突"，**不可能由 C# 版主动写库引发**。

### ③ 非数据库文件的写入落点

| # | 检查项 | 证据（文件:行号） | 判定 | 说明 |
|---|---|---|---|---|
| C1 | 列布局 `ColumnStateStore` 落点 | `ColumnStateStore.cs:50-53` → `%APPDATA%\LawFirm\ui-state.json`；`:28-29` 注释明确"不进 Seafile、不进 git" | **PASS** | **已在同步目录之外**，3 台机器各自独立，不会冲突。设计正确 |
| C2 | 列布局写入是否原子 | `ColumnStateStore.cs:76`、`:205` 用 `File.WriteAllText` 直接覆盖 | **RISK（低）** | 非原子写；崩溃/断电可能写出半截 JSON。但 `ReadRoot` `:66-69` 捕获异常并回退默认，**最坏只丢列布局，不损数据**。见 R-L1 |
| C3 | UI 导出产物落点 | `PersonalSettlementViewModel.cs:166/193/221`、`MonthlyReportViewModel.cs:112` 均经 `PickFolder()` 由**用户选择目录** | **取决于用户** | 若用户选了 Seafile 同步目录内的文件夹 → 会被同步 → 3 台机器导出同名文件必冲突。见 R-M1 |
| C4 | CLI 导出默认落点 | `Cli/Program.cs:33`（`csharp/out/settlement_report_csharp.xlsx`）、`:36`（`csharp/out/person_settlement`） | **RISK** | **默认就在仓库目录内 = Seafile 同步目录内**。`.gitignore:20` 忽略了 `csharp/out/`，所以不进 git，**但 Seafile 不看 .gitignore，照样同步**。见 R-M1 |
| C5 | 构建产物 `bin/` `obj/` | `.gitignore:15-16`；实际存在（grep 命中大量 `csharp/*/obj/Debug/net10.0-windows/*.g.cs`） | **RISK（高）** | **本次核查发现的最大 Seafile 冲突源，且不是数据库问题。** 每次 `dotnet build` 会重写 `obj/` 下成百上千个文件。3 台机器只要有人 build，Seafile 就要同步几 MB～几十 MB 的 churn；两台同时 build → 冲突副本大概率出现。见 R-H2 |
| C6 | 日志文件写入 | 全仓 `.cs` 无 `StreamWriter` / `File.AppendAllText` 等非 UI 状态的文件写（仅 C1 的 `File.WriteAllText`） | **PASS** | 无日志落盘 → 无日志 churn、无日志锁 |
| C7 | 临时文件 | 全仓无 `Path.GetTempPath` / `GetTempFileName` | **PASS** | 无临时文件落盘 |
| C8 | 是否会在同步目录内创建 SQLite 伴生文件 | 见 ④ 组 D2 | **RISK（高）** | WAL 模式下**只读打开也会触发 `-shm` 文件创建**（若目录可写），即"零写入"在文件系统层面并不严格成立。见 R-H1 |

### ④ SQLite 只读打开 + Seafile 的已知风险

前置事实：**Python 版把库设为 WAL 模式** —— `app/db.py:1`「数据库连接、建表、WAL 管理」、`app/db.py:417` docstring「获取连接（WAL 模式 + 外键约束 + 行工厂）」、`app/db.py:422` `PRAGMA journal_mode = WAL`。
即：`data/lawfirm.db` 旁边会有 `lawfirm.db-wal` 和 `lawfirm.db-shm` 两个伴生文件。

| # | 检查项 | 证据（文件:行号） | 判定 | 说明 |
|---|---|---|---|---|
| D1 | 库是否为 WAL 模式 | `app/db.py:422` | **确认为 WAL** | 决定了后续所有风险 |
| D2 | 只读打开 WAL 库需要伴生文件 | SQLite 语义（WAL 模式读需 `-wal` + `-shm`） | **RISK（高）** | 只读连接必须能读到 `-wal`；`-shm` 不存在时 SQLite 会尝试**创建**它。Seafile 目录可写 → `-shm` 被创建 → **在同步目录内产生了写文件**，同步 churn + 潜在冲突。见 R-H1 |
| D3 | Seafile 同步了 db 但没同步 wal/shm 会怎样 | 同上 | **RISK（高）** | 三种后果：① 读不到 `-wal` → 抛 `unable to open database file`（SQLITE_CANTOPEN）；② 读到**过期/不完整**的 wal → `database disk image is malformed`（SQLITE_CORRUPT）；③ 读到**旧数据**（wal 里的新事务还没同步过来）→ 三台机器数据不一致且**无报错**，最危险。见 R-H1 |
| D4 | Seafile 就地替换 db 文件的瞬时错误 | 短连接已缓解（A3） | **RISK（中）** | 同步发生时 SQLite 已打开的文件句柄可能撞上就地写入 → 瞬时 `disk I/O error`。因为连接是短生命周期，窗口很小，但**没有消除**。见 R-M3 |
| D5 | 是否有 WAL 合并（checkpoint）手段 | `app/db.py:560-563` 已有「WAL 合并回主库（**同步软件前调用，保证 db 为单一文件**）」，执行 `PRAGMA wal_checkpoint(TRUNCATE)` | **已有工具，未纳入流程** | 代码里早就写好了这个 helper，docstring 明说是给同步软件前用的。**3 天测试前必须先用它**，或直接把库转回 `DELETE` 日志模式。见 R-H1 缓解 |
| D6 | 历史事故：Seafile 干扰导致目录被清空 | 用户口述历史教训 | **RISK（高）** | 非代码问题，但 3 天测试期间**必须**先在 Seafile 之外留一份备份，且**禁止在 Seafile 同步进行时执行 `git checkout` / 分支切换**。见 R-H3 |

---

## 二、风险清单（按等级排序）

### 🔴 高风险（3 条）

**R-H1｜WAL 伴生文件（`-wal` / `-shm`）+ Seafile 是本次头号风险**
- 现象：某台机器打开软件报 `unable to open database file` 或 `database disk image is malformed`；更坏的情况是**不报错但数据比别人旧**。
- 成因：`app/db.py:422` 把库设为 WAL，只读打开仍依赖 `-wal`/`-shm`；Seafile 对这类伴生文件的同步时序不保证。同时只读打开会在同步目录内创建 `-shm`（`DbConnection.cs:37` 的 `Mode=ReadOnly` 挡不住 SQLite 自己建 shm）。
- **缓解（一句话）**：测试开始前，在 Python 版里执行一次 `PRAGMA journal_mode=DELETE`（或调用现成的 `app/db.py:560` `wal_checkpoint(TRUNCATE)` helper），让 `lawfirm.db` 变成**单一自包含文件**；并在 Seafile 里把 `*.db-wal` / `*.db-shm` / `*.db-journal` 加入忽略列表。

**R-H2｜`bin/` `obj/` 构建产物在同步目录内，是实际最大的冲突源**
- 现象：Seafile 长时间"正在同步"、出现大量冲突副本、同步流量异常；严重时 build 报文件被占用。
- 成因：`csharp/*/obj/Debug/...` 与 `bin/` 真实存在于磁盘上（已 grep 证实），`.gitignore:15-16` 只挡 git，**挡不住 Seafile**。
- **缓解（一句话）**：在 Seafile 客户端把 `csharp/*/bin`、`csharp/*/obj`、`.vs` 设为忽略/选择性同步，或在 `.gitignore` 之外额外维护一份 `.seafileignore`；3 天测试期间**只在 1 台机器上做 build**，另外 2 台只跑编译好的 exe。

**R-H3｜历史 Seafile 事故（目录被清空）**
- 现象：整个工作目录或部分子目录突然变空。
- 成因：Seafile 同步与 git 文件操作（尤其是 `git checkout` / 分支切换）互相干扰。
- **缓解（一句话）**：测试前把 `data/lawfirm.db` 复制到 **Seafile 目录之外**（如 `D:\backup\lawfirm_YYYYMMDD.db`）；测试期间**绝不在同步进行中执行 git 分支切换**，切分支前先暂停 Seafile。

### 🟡 中风险（4 条）

**R-M1｜导出产物落在同步目录内，同名文件必冲突**
- 证据：`Cli/Program.cs:33/36` 默认 `csharp/out/...`（在仓库内）；UI 侧由用户选目录（`:166/193/221`、`MonthlyReportViewModel.cs:112`）。文件名为 `个人结算总表_张三.xlsx`（`ExportService.cs:47/70`），**不含机器名或时间戳**。
- **缓解（一句话）**：3 台机器导出时各自选** Seafile 之外**的本地目录，或在目录名/文件名上加机器名+日期后缀；CLI 默认输出目录改到 `%LOCALAPPDATA%\LawFirm\out`。

**R-M2｜`LAWFIRM_DB` 残留 / 启动了旧副本 → 静默读错库，且 UI 无提示**
- 证据：`DbConnection.cs:58-60` 环境变量优先级最高且静默生效；UI 不像 `Cli/Program.cs:144` 那样打印 db 路径。
- **缓解（一句话）**：3 台机器在测试前统一执行 `setx LAWFIRM_DB ""` 清除该变量；并在 UI 标题栏或"关于"里显示 `FindDatabase()` 的实际路径（代码改动一行，本次不改）。

**R-M3｜Seafile 就地替换 db 时的瞬时 I/O 错误**
- 证据：短连接（`SettlementQueryService.cs:32` 等全 `using`）已大幅缩小窗口，但未消除。
- **缓解（一句话）**：遇到偶发 `disk I/O error` 时先确认 Seafile 是否正在同步，**等同步图标变绿再操作**；不要连续快速点刷新。

**R-M4｜`FindDatabase` 兜底路径算错且未做存在性校验**
- 证据：`DbConnection.cs:72` 从 `bin\Debug\net10.0-windows` 上两级得到 `bin\data\lawfirm.db`（不存在），且未像 `:59`/`:66` 那样 `File.Exists` 校验。
- **缓解（一句话）**：兜底改成向上找 `data/lawfirm.db` 直到盘符根，并在返回前做存在性校验 + 在异常消息里打印**已尝试的完整路径列表**。

### 🟢 低风险（2 条）

**R-L1｜列布局 JSON 非原子写**
- 证据：`ColumnStateStore.cs:76/205` 直接 `File.WriteAllText` 覆盖。
- 影响有限：文件在 `%APPDATA%`（`:52-53`）**不在 Seafile 内**，故**不会引起同步冲突**；损坏时 `ReadRoot:66-69` 优雅回退默认。
- **缓解（一句话）**：改为"写临时文件 + `File.Move(overwrite:true)`"的原子替换。

**R-L2｜导出文件名无机器/时间戳区分**
- 证据：`ExportService.cs:47/70` 固定 `个人结算总表_{姓名}.xlsx`。
- **缓解（一句话）**：文件名追加 `_{机器名}_{yyyyMMdd}` 后缀，从源头杜绝跨机覆盖。

---

## 三、给用户实操的 3 天测试卡

> 目标：验证 C# 版在 3 台 PC 上同时/交替使用 3 天，**不出现 SQLite 锁冲突或文件占用冲突**。
> 原则：**测试期间不改代码、不做 build（只在 1 台机器 build 一次）、不切 git 分支。**

### 0. 准备动作（3 台机器每台都要做，Day 0 完成）

| 步骤 | 操作 | 完成打勾 |
|---|---|---|
| 0-1 | **备份**：把 `data\lawfirm.db` 复制一份到 **Seafile 目录之外**（例：`D:\backup\lawfirm_20260501.db`）。3 台机器各备各的。 | ☐ |
| 0-2 | **清环境变量**：在命令行执行 `setx LAWFIRM_DB ""`，**重启电脑**使其生效。（防 R-M2 读错库） | ☐ |
| 0-3 | **确认同步已绿**：打开 Seafile 客户端，确认资料库状态为「已同步 / 绿色对勾」，**没有任何"正在同步"**。 | ☐ |
| 0-4 | **确认 db 路径一致**：3 台机器分别确认 `lawfirm.db` 位于**同一个 Seafile 资料库内的同一相对路径**（即 `...\lawfirm_app\data\lawfirm.db`）。本地绝对路径可以不同，相对路径必须相同。 | ☐ |
| 0-5 | **确认 db 为单一文件**：检查 `data\` 目录下**有没有** `lawfirm.db-wal` / `lawfirm.db-shm`。<br>· **如果有** → 先打开** Python 版**软件，随便做一次查询，再执行一次 WAL 合并（或请技术同事执行 `PRAGMA wal_checkpoint(TRUNCATE)`），直到这两个文件消失或大小为 0。<br>· 记录下当时的状态。 | ☐ |
| 0-6 | **确认 exe 位置**：3 台机器都从同一个 Seafile 资料库里的 `csharp\LawFirm.App\bin\...\LawFirm.App.exe` 启动。**不要**启动任何 Seafile 目录之外的旧副本。 | ☐ |
| 0-7 | **约定导出目录**：3 台机器各自在 **Seafile 之外**建一个导出目录，例如 `C:\Users\本机用户名\Desktop\导出_机器A\`（B 机用 `导出_机器B`，C 机用 `导出_机器C`）。**千万不要 3 台共用同一个同步目录。** | ☐ |
| 0-8 | **暂停自动 build**：3 天测试期间，**只在 A 机上做过一次 build**，B/C 机不再编译。 | ☐ |

### 1. 每天操作步骤（Day 1 / Day 2 / Day 3，每台机器各做一遍）

> **每天建议顺序**：A 机先开 → 保持开着 → B 机开 → 保持开着 → C 机开 → 三台同时操作 → 再逐个关闭。
> **关键**：每天至少要有 **一次"三台同时开着并同时点查询"** 的时刻，这是压力最大的场景。

| 顺序 | 操作 | 操作要点 |
|---|---|---|
| 1 | 看一眼 Seafile 图标，确认是绿色「已同步」 | 若是「正在同步」，**等它变绿再开始** |
| 2 | 打开 C# 版软件 | 记下：能否正常打开？首屏数据是否正常？ |
| 3 | 切到「个人结算」页，选年份/人员，点查询 | 记下：数据条数、金额是否和另两台一致 |
| 4 | 切到「月度报表」「员工收入」「发票收入」各点一次 | 记下：每次切换有无报错、有无卡死 |
| 5 | **导出一次**：导出到 0-7 约定好的本机目录 | 记下：能否成功生成 xlsx？能否用 Excel 打开？ |
| 6 | **保持软件开着**，去另一台机器重复步骤 2-5 | 重点：第二台、第三台打开时**会不会报错** |
| 7 | **三台同时开着**时，每台各点一次「刷新/查询」 | **这是最关键的一步**（同时读） |
| 8 | 三台都做完后，逐个关闭软件 | 记下：关的时候有无报错、有无卡住 |
| 9 | 关闭后看 Seafile 图标 | 记下：有没有突然开始大量同步？有没有冲突提示？ |
| 10 | 检查 `data\` 目录和导出目录 | 记下：有没有多出奇怪的文件？（见判读标准） |

**Day 3 额外加做一次**：三台机器**同时点导出**（各自导到各自的本机目录），验证"同时写文件"场景。

### 2. 判读标准：出现什么算「冲突」

#### 2.1 软件报错关键词（看到任何一个 → 记录为「疑似冲突」）

| 报错文字（中/英） | 含义 | 严重度 |
|---|---|---|
| `database is locked` | SQLite 锁冲突（SQLITE_BUSY）—— **T6 的直接失败信号** | 🔴 |
| `unable to open database file` | 打不开库，**大概率是 `-wal`/`-shm` 缺失**（R-H1） | 🔴 |
| `database disk image is malformed` | 库损坏，可能是 wal 同步不完整（R-H1） | 🔴 |
| `disk I/O error` | 文件被同步进程就地改动时读出问题（R-M3） | 🟡 |
| `attempt to write a readonly database` | 出现了本不该有的写操作（R-H1 的 shm 创建） | 🔴 |
| `file is not a database` | 同步截断导致文件不完整 | 🔴 |
| `The process cannot access the file ... because it is being used by another process` | 文件被占用（常见于 xlsx 正被 Seafile 同步时打开） | 🟡 |
| `SqliteException` / `IOException` / `Unhandled exception` | 任何未处理异常弹窗 | 🟡 |
| 软件**直接闪退 / 无响应** | 记下闪退前正在做什么 | 🔴 |

> **注意**：C# 版可能显示的是英文异常，Python 版显示中文。看到**任何弹窗报错**，先**截图**再关掉。

#### 2.2 Seafile 冲突副本的文件名特征

同步冲突时，Seafile 不会覆盖原文件，而是**另外生成一个"冲突副本"**。请在 `data\`、导出目录、`csharp\` 下留意：

| 特征 | 典型样子 |
|---|---|
| 含 `SFConflict` 字样 | `lawfirm (SFConflict zhangsan 2026-05-01 14-32-10).db` |
| 含 `conflicted copy` 字样 | `个人结算总表_张三 (conflicted copy 2026-05-01 14-32-10).xlsx` |
| 文件名里带**时间戳 + 用户名/邮箱** | 形如 `xxx (... 2026-05-01 14-32-10).xlsx` |
| 莫名其妙多出 `(1)` `(2)` 后缀 | `lawfirm (1).db` |
| 忽然出现 `.seafile-ignore` 之外的大量临时文件 | 如 `~$xxx.xlsx`、`xxx.tmp` |

> **建议**：Day 0 时先在 Seafile 客户端确认一下你这版的冲突副本命名规则（不同版本略有差异），把实际样式记在下面备注里。
> 备注：______________________________________________

#### 2.3 判定结论

| 现象 | 结论 |
|---|---|
| 3 天内**零报错**，且**零冲突副本**文件 | ✅ **T6 通过** |
| 出现过 `database is locked` 或闪退 | ❌ **T6 不通过**（锁冲突） |
| 只出现**冲突副本文件**，但软件**没报错** | ⚠️ **部分通过** —— C# 版本身没冲突，但**同步策略需要调整**（通常是 `bin/obj` 或导出产物引起，见 R-H2 / R-M1） |
| 三台机器**数据不一致**（条数/金额不同） | ❌ **不通过** —— 优先查 R-M2（读错库）和 R-H1（wal 没同步） |

### 3. 记录表（请每天填，一行一次操作）

**机器编号**：A = ____________　B = ____________　C = ____________（填机器名或使用者姓名）

| 日期 | 机器 | 当时几台同时开着 | 操作内容 | 现象（正常/报错原文/截图编号） | 有无冲突副本文件 | 结论（通过/疑似/失败） |
|---|---|---|---|---|---|---|
| Day1 | A | 1 台 | 打开软件 |  |  |  |
| Day1 | A | 1 台 | 查询-个人结算 |  |  |  |
| Day1 | A | 1 台 | 切换页面 |  |  |  |
| Day1 | A | 1 台 | 导出 |  |  |  |
| Day1 | B | 2 台 | 打开软件 |  |  |  |
| Day1 | B | 2 台 | 查询-个人结算 |  |  |  |
| Day1 | C | 3 台 | 打开软件 |  |  |  |
| Day1 | A/B/C | 3 台 | **同时点查询** |  |  |  |
| Day1 | A/B/C | 3 台 | 逐个关闭 |  |  |  |
| Day2 | | | |  |  |  |
| Day2 | | | |  |  |  |
| Day2 | | | |  |  |  |
| Day2 | | | |  |  |  |
| Day2 | | | |  |  |  |
| Day3 | | | |  |  |  |
| Day3 | | | |  |  |  |
| Day3 | | | |  |  |  |
| Day3 | A/B/C | 3 台 | **同时点导出** |  |  |  |
| Day3 | A/B/C | 3 台 | 逐个关闭 |  |  |  |

**每日收尾检查（每天下班前填一次）**

| 日期 | `data\` 下有无 `-wal`/`-shm` 文件 | Seafile 有无冲突提示 | 有无新增冲突副本文件 | 三台数据是否一致 | 当日小结 |
|---|---|---|---|---|---|
| Day1 | ☐有 ☐无 | ☐有 ☐无 | ☐有 ☐无 | ☐一致 ☐不一致 | |
| Day2 | ☐有 ☐无 | ☐有 ☐无 | ☐有 ☐无 | ☐一致 ☐不一致 | |
| Day3 | ☐有 ☐无 | ☐有 ☐无 | ☐有 ☐无 | ☐一致 ☐不一致 | |

---

## 四、核查结论

| 维度 | 结论 |
|---|---|
| **代码层只读纪律** | ✅ **成立且证据充分**。全仓 0 处 DML/DDL、0 处 `ExecuteNonQuery`、唯一打开模式为 `ReadOnly`、全部连接短生命周期。**C# 版不可能主动写 `lawfirm.db`** |
| **多机路径自定位** | ✅ **成立**。`FindDatabase` 相对 exe 自定位，3 台机器本地路径不同也能各自找到正确的库 |
| **UI 状态持久化** | ✅ **已隔离**。`%APPDATA%\LawFirm\ui-state.json`，不在 Seafile 内 |
| **主要风险来源** | ⚠️ **不在 C# 代码内**，而在：① WAL 伴生文件与 Seafile 的时序（R-H1）② `bin/obj` 构建产物被同步（R-H2）③ 导出产物落点（R-M1）④ 历史 Seafile 事故（R-H3） |
| **能否进入 3 天实测** | ✅ **可以进入，但必须先完成准备动作 0-1（备份）、0-2（清 LAWFIRM_DB）、0-5（WAL 合并为单一文件）三条**，否则大概率会在 Day 1 就撞上 R-H1 的假故障，白白浪费一天 |

**风险计数：高 3 条 / 中 4 条 / 低 2 条。**
