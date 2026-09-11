# T5 视觉验收清单（规格 §9 V1–V16）

> 用法：用户本机跑 T5 后对照 Python 版（qfluentwidgets 22 页）逐条打勾；
> 「已对齐 / 有意差异 / 未实现 / 待真机验证」四态记录。主观评分门槛 ≥ 4/5。

| # | 项 | Python 基线 | 状态 | 备注 |
|---|---|---|---|---|
| V1 | 无边框窗 + 自绘标题栏（36px、页名左对齐、三按钮右） | `main_window.py:105-171` | 待真机验证 | WindowChrome CaptionHeight=36；双击最大化 |
| V2 | 标题栏底色 = 侧栏底色（#f7f6f3）+ 底 1px 分隔线 | `main_window.py:138-148` | 已实现 | Palette.xaml BgSide + Border |
| V3 | 侧栏 240px、暖灰底、右 1px 分隔线 | `style.py:108` | 已实现 | Sidebar RootBorder Width=240 |
| V4 | 侧栏 6+1 大类，字号 13px，组名 text_mute→hover text | `style.py:113-121` | 已实现 | NAV_GROUPS 逐字对齐（7 组） |
| V5 | 分组图标 20px、1.5px 描边、text_mute→hover text | `nav_icons.py:123-175` | 已实现（3 精确 + 4 简化） | U3 裁定 |
| V6 | 子项缩进 36px、行高 31px、选中底 #e3e1db + 字重 600 | `style.py:113-121` | 已实现 | RadioButton 选中态 |
| V7 | 页标题 20px/700；tab 标题化（20px/700 + 选中底边 2px #2eaadc） | `style.py:167,237-243` | 已实现 | PageTitleTabControl/TabItem |
| V8 | 表格：底 #FFFFFF、表头底 #fbfbfa + 底边 #e9e9e7、网格线 #F1F1EF | `style.py:199-215` | 已实现 | NotionDataGridStyle（圆角 10 未做：DataGrid 圆角需重模板，Pilot 略） |
| V9 | 表格行高 34、单元格内边距 6/10、选中底 #e3e1db | `table_view.py:289` | 已实现 | RowHeight=34 / Padding 10,0 |
| V10 | 冻结首列横向滚动不动 + 灰底 #EAEAEA | `table_features.py:36,63-67` | 已实现 | FrozenColumnCount=1 + FrozenCellStyle |
| V11 | 两级表头（Tab3）：第 1 行大类跨 2 列居中、第 2 行细分、总高 44 | `table_features.py:244,304-316` | 待真机验证（U5） | F12 最小样本先验证 |
| V12 | 按钮：次按钮透明底 + #DADAD7 描边 + 圆角 8 + 内边距 7/16；主按钮 #2eaadc 白字 | `style.py:172-184` | 已实现 | NotionButton/PrimaryButton |
| V13 | 下拉框：白底 + #DADAD7 描边 + 圆角 7 + 展开项选中底 #e3e1db | `style.py:187-196` | 已实现 | NotionCombo（选中底用 #E9E9E7 ≈ Python COMBO_QSS） |
| V14 | 滚动条 10px 宽、圆角把手 text_faint、无箭头 | `style.py:218-224` | 已实现 | NotionScrollBar |
| V15 | 数字格式 1,234.56、右对齐、负数红 #eb5757 | `table_view.py:18,335-336` | 已实现 | StringFormat=N2 + zh-CN 强制 + IsNegativeConverter |
| V16 | 布局基线：左缩进 24 / 上边距 16 / 间距 12 | 简报（用户定案） | 部分 | Tab 容器对齐 Python(20,16,20,16)/Tab 内(8,10,8,10)；侧栏间距已对齐 |

## 有意差异（不判失败）

1. **表格圆角 10**（V8）：WPF DataGrid 圆角需整体重写模板，Pilot 暂用直角 + 1px 描边。
2. **Tab2/3/4 的年份下拉**与 Tab1 共享（Python 侧 r_year/si_year/ii_year 独立但同列表）——Pilot 收敛为单一数据源，避免 4 份年份状态漂移。
3. **Tab3/4 导出按钮**：staff_income / invoice_income 导出器尚未移植到 C#（导出器移植工作包），按钮弹出明确提示；预览可用。
4. **列布局持久化**仅 Tab1 启用（`settlement/personal`；Tab3/4 的两级表头子列显示与 key 解耦，持久化待列设置对话框工作包统一处理）。
5. **月度导出的 report_persons 记忆**存 `%APPDATA%\LawFirm\ui-state.json`（U6 同精神），不写 `data/prefs.json`。

## 自评

- 静态层面对齐：V2-V10、V12-V15（12/16）。
- 待真机验证：V1（拖拽/缩放/最大化）、V11（两级表头共存，最高风险 U5）、V16（逐像素间距）。
- 用户主观评分：待打分（门槛 ≥ 4/5）。
