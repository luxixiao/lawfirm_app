# T5 实现派单（主理人 → 工程师 寇豆码）

> 本文件是 T5 的**实现任务书**。规格书是权威：`docs/t5-ui-skeleton-spec.md`（1,031 行，全文读完再动手）。

## 你的任务

在 `C:\Users\Bingo\Desktop\buddy2\lawfirm_app\`（分支 `pilot/dotnet`，**不要切分支、不要 push、不要 rebase/amend**）上，按规格实现 T5 全部 5 个子任务（T5.1 → T5.5，严格串行）。

## 用户已拍板的 4 项决策（覆盖规格中的对应 U 项）

| U 项 | 用户决策 |
|---|---|
| U9 范围 | **完整做 4 个 tab**（T5-MUST 全量，不砍 Tab2/3/4） |
| U10 视觉对标 | **已解决**：我已在沙箱用 offscreen 模式截好 3 张 Python 真机参考图（见下） |
| U2 侧栏自动展开/收回 | **T5 不做**（固定展开 + 手动折叠，与规格建议一致） |
| U6 列布局持久化位置 | **存 `%APPDATA%`，不同步**（与规格建议一致） |

## 视觉参考图（新增，规格里没有的）

我用 Python venv 的 offscreen 模式从真代码渲染的截图，放在：

- `docs/_ref_settlement_page.png` —— Tab1 个人结算总表（整体布局：tab 栏/工具栏/表格/合计/日志）
- `docs/_ref_settlement_tab2.png` —— Tab2 月度结算表（工具栏含更多下拉）
- `docs/_ref_settlement_tab3.png` —— Tab3 年度聘用结算表（**含两级表头真实渲染**：第一行大类跨列居中 + 第二行子列 + 冻结的序号/姓名列 + 数据行 + 底部按钮）

注意：offscreen 渲染用的是系统缺省字体，字形细节与真机有差异；**以 `style.py` 的 token 数值（规格 §1.3）为准确，图片看布局与结构**。

## 沙箱环境约束（与 T3 相同）

- **无 NuGet 网络，无法 build** → 你只做**静态实现 + 自审**，构建与真机验证由用户本机执行
- 因此：所有代码必须能通过**静态审查**——API 名称、签名、XAML 命名空间必须准确；不确定的 API 在代码注释中标注 `// TODO(verify): ...` 并在交付报告中列出
- 不改 Python 侧任何文件
- 数据零风险：全程只读（`query_only + ReadOnly`），UI 层无任何写库调用

## 关键实现要点（从规格提炼，务必遵守）

1. **工程结构**：新建 `csharp/LawFirm.UI/` 类库（net10.0-windows），`LawFirm.App` 保留唯一 WinExe 入口；`Directory.Packages.props` 追加 `CommunityToolkit.Mvvm 8.4.0`（**不引入 HandyControl**，U8 已裁定）
2. **T5.2 首日最小验证（规格 R1/U5，最高风险）**：两级表头 + `FrozenColumnCount=1` + `ScrollUnit=Pixel` 三者共存的最小可复现示例代码，放在 `NotionDataGrid` 的 XML doc 注释 + 一个独立的最小 XAML 示例文件里，供用户本机第一时间验证。**这一步的代码要在 T5.2 的第一批文件里就写出来**，不要等全部写完
3. **列持久化**：存 `%APPDATA%\LawFirm\ui-state.json`（`Environment.SpecialFolder.ApplicationData`），列集合不一致时丢弃（不串档）
4. **ComputeFill**：纯函数移植 `column_layout.py:98-151`，配 4 个单测（可用普通 assert 风格的独立类，沙箱没有 xUnit 运行环境）
5. **异步导出**：后台线程**自建只读连接**（禁止跨线程传 SqliteConnection）；`IProgress<T>` + `CancellationToken`；`CanExecute=!IsBusy` 防重入；进度文本「正在导出 {姓名}（{n}/42）…」
6. **两级表头**：用规格 §4.3 的"单表头单元格内嵌 Grid + ColumnSpan"方案，跨列文字宽度同步用 converter（`+2` px 补偿）
7. **culture**：`App` 启动时强制 `zh-CN`（防 `StringFormat N2` 出千分位错乱）
8. **中文文件编码**：所有新文件 UTF-8 with BOM（含中文注释的 `.cs`/`.xaml`）

## 交付流程

1. 按 T5.1 → T5.5 顺序实现，每个子任务完成后做**全局一致性自审**（命名空间/引用/API 拼写）
2. 全部完成后输出交付报告（SendMessage 给 team-lead），包含：
   - 新建/修改文件清单（路径 + 行数）
   - 全局一致性审查结论（IS_PASS: YES/NO）
   - 需要用户本机验证的步骤清单（按顺序：build → T5.2 最小验证 → 冒烟 → 视觉对比）
   - TODO(verify) 项列表
3. **不要 commit**——主理人审核后统一处理（沙箱 git ref 有已知 bug，由我操作）

## 验收标准

每个子任务的验收标准以规格 §8 为准（T5.1 ①–⑦ / T5.2 ①–⑦ / T5.3 ①–⑧ / T5.4 ①–⑦ / T5.5 ①–④）。静态审查阶段能做到的：代码结构完整、API 准确、逻辑与规格一致；真机项（视觉/拖动/滚动手感）标注为"待本机验证"。
