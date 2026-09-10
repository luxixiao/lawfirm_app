# 使用阶段（终端用户视角）三方案对比 — 2026-09-09

> 背景：用户要求比较「使用起来」的优缺点，而非开发阶段。对比对象：
> - Python（方案1 重组 + 方案8 启动/体量优化，现状技术栈 PySide6 6.11.1 + qfluentwidgets + SQLite WAL + xlrd/openpyxl）
> - C#/WPF（方案4，.NET 10 + Dapper + NPOI，WPF 多目标保 Win7 后门）
> - Go + Wails（用户假设「没有 xls」前提下的现代单文件方案）

## 结论

从「用起来」的角度，三者在修掉 Python plan3（QThread + 进度条）之后，日常体验差距很小。
Python 已经具备用户要的 Notion 风格 UI（qfluentwidgets），且原生支持 .xls/.xlsx；
重写（C#/WPF、Go+Wails）的主要代价在开发侧，使用侧收益有限。
这进一步支撑「方案1+8 主线、方案4 唯一备选」的最终决策。

## 五维度对比表

| 维度 | Python（方案1+8） | C#/WPF（方案4） | Go+Wails（无 xls 假设） |
|---|---|---|---|
| 启动 / 内存 | PyInstaller 目录 100–300MB；冷启动 1–3s | self-contained 几十 MB；<1s（预编译原生） | 单 exe 10–30MB；<0.5s（最小最快） |
| UI 卡顿 | 现状报表打开冻结 1–2s（plan3 修复后消除）；导出同理 | Task.Run 后台线程，原生不冻；DataGrid 成熟 | Go goroutine 后台算，WebView 前端不冻 |
| 分发（Seafile 3台PC） | 绿色目录复制即用、免安装；但几百 dll/pyc 同步量大易冲突 | 需 installer 或 self-contained 目录；比 Python 干净 | 单 exe 最轻，复制即用，同步量最小 |
| 崩溃 / 数据安全 | SQLite WAL + git revert；增量改动可回退 | 同 WAL；但整盘重写期 bug 多、风险高 | 同 WAL；整盘重写期 bug 多、风险高 |
| 视觉 / .xls | qfluentwidgets 已 Notion 风（用户认可）；xlrd+openpyxl 原生读 | 需重写接近 Notion（WPF+Fluent 主题可接近，但要工时）；NPOI 读 .xls 完整 | CSS 最易做毛玻璃/Notion；.xls 读取是硬伤（假设无 xls 才成立） |

## 逐项展开

### 1. 启动与内存
- Python：PyInstaller 打包为绿色目录，体积 100–300MB，冷启动 1–3s（取决于机器，已编译 pyc 但仍走解释器）。
- C#/WPF：self-contained 单目录含 .NET runtime 几十 MB，或 AOT 单 exe 更小；冷启动 <1s（预编译原生代码）。
- Go+Wails：单一 exe 10–30MB，冷启动 <0.5s，最轻最快。
- 结论：1–3s 对桌面记账工具完全可接受，不是使用侧痛点。

### 2. UI 卡顿（用户的真实痛点）
- Python 现状：报表打开时 `person_settlement._compute()` 主线程跑 24 次，导出 1+4N 次，导致 UI 冻结 1–2s。这是**调用结构问题，不是语言运行时问题**。plan3（QThread + 进度条，2–5人日）直接消除。
- C#/WPF：重算放 `Task.Run` 后台线程，UI 线程天然不冻；DataGrid 成熟。
- Go+Wails：重算在 Go 后端 goroutine，前端 WebView 不冻。
- 结论：修复 plan3 后三者都不冻，差距归零。

### 3. 分发（3台PC + Seafile 同步）
- Python：绿色目录复制即用、免安装；但目录含几百个 dll/pyc，Seafile 同步量大、易冲突（非语言问题，是目录 vs 单文件差异）。
- C#/WPF：需 installer 或 self-contained 目录；比 Python 绿色目录干净，但仍多文件。
- Go+Wails：单 exe，Seafile 同步最轻（1 个文件），复制即用。
- 结论：Go 单文件最优雅；Python 免安装也够用。

### 4. 崩溃 / 数据安全
- 三者都靠 SQLite WAL，崩溃不损库；git revert 回退 Python 已有成熟流程。
- 差异：Python / WPF 改动是**增量、可回退**；Go+Wails / WPF 是**整盘重写**，重写期 bug 多、反而增加使用侧风险。
- 结论：数据安全等效；重写路径短期风险更高。

### 5. 视觉与 .xls
- Python：qfluentwidgets 已给 Notion 风格（用户已认可）；xlrd + openpyxl 原生读 .xls/.xlsx。
- C#/WPF：需重写 UI 接近 Notion（WPF + Fluent 主题可接近，但要工时）；NPOI 读 .xls 完整。
- Go+Wails：CSS 最易实现毛玻璃/Notion；但 .xls 读取 Go 生态弱（需 CGO 或外部库），用户已假设「没有 xls」才成立。
- 结论：Python 已满足视觉与 .xls；重写要重新搭视觉，且 Go 在 .xls 上是硬伤。

## 对决策的含义

使用侧唯一的真痛点是「报表打开/导出卡顿」，用 plan3 的 2–5人日即可解决，无需动语言。
其余（Notion UI、.xls 支持、Seafile 绿色分发、WAL 安全）Python 均已满足。
因此「使用起来更好」不应成为触发重写的理由；重写理由只应来自开发侧（可读性/可维护性/长期人力）。

## 关联文档
- docs/final-decision-summary-2026-09-09.md — 最终决策汇总（方案1+8 主线 / 方案4 备选）
- docs/refactor-audit-2026-09-09.md — 代码审计与 N+1 热点
- docs/tech-stack-rewrite-options-2026-09-09.md — 8 个改写方案量化对比
- docs/plan4-dotnet-deep-dive-2026-09-09.md — .NET 专项深评
