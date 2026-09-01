# 全局字号 5 档调节（方案 1：scale 真源 + 全量派生 + 即时生效）

## 1. 需求

以当前字号（基准 13px）为标准，提供 小两号 / 小一号 / 标准 / 大一号 / 大两号 共 5 档。
切换后全软件即时生效：各页面字号、表格行高列宽、控件尺寸同步派生，不出现
「文字被裁切 / 溢出边界 / 字号变了框没变」。档位持久化，重启保留。

**档位定义（±1px）**：`STEPS = (11, 12, 13, 14, 15)`，倍率 = 档位字号 / 13
（0.846 / 0.923 / 1.0 / 1.077 / 1.154）。标准档所有派生值与改版前逐值一致（零回归）。

## 2. 方案选型（三选一，选 1）

| # | 方案 | 结论 |
|---|---|---|
| 1 | **scale 唯一真源 + QSS 生成期全量乘倍率 + 切档即时广播刷新** | ✅ 选定：底座可先验证、标准档零回归可测、即时生效 |
| 2 | 同 1 但重启生效 | 备选：工作量最小，但与「各页面动态调整」预期不符 |
| 3 | QApplication.setFont 全局继承（QSS 去字号） | ✗：22 处 QSS 字号是设计层级（11/12/13/16/20），全删破坏视觉层级；qfluentwidgets+自绘混合下继承链不可控 |

## 3. 架构

```
app/ui/scale.py          唯一真源：STEPS/LABELS、ratio()/px()/sp()、
                         load_step/save_step（prefs.json 的 font_step）
style.py  build_qss(p,s) 全部 px 值（22 处字号 + padding/min-height/圆角/滚动条）
                         经 P() 派生；QSS 不再预生成，qss_for() 按当前倍率即时生成；
                         refresh_qss(app) 切档后重建
main_window              apply_font_step(i)：set_step+save → refresh_qss →
                         titleBar.apply_skin() → sidebar.reapply_metrics() →
                         当前页 refresh()（其余页走既有 showEvent 刷新）→ InfoBar 反馈
入口                     侧栏底部挂件「字号」下拉 + Ctrl+=/Ctrl+-/Ctrl+0
```

**关键原则**：
- 任何 px 尺寸一律 `scale.px(基准值)` 派生，禁止裸数字；基准值 = 标准档像素。
- 表格策略**内容优先**：行高列宽按新字号重建，auto_fit_columns 重跑，允许横向滚动，
  绝不压缩到省略号/遮挡。
- 用户手动调过的列宽（column_state 持久化恢复值）不参与缩放。
- `px()` 最小 1 兜底，避免小档位下边框/间距归零。

## 4. 落地清单（4 个独立 commit，可单独 revert）

| 阶段 | commit | 内容 |
|---|---|---|
| 1 | `71558f9` | scale.py 底座 + style.py 全量派生 + AppTitleBar（恢复自已验证的 a10a4ff） |
| 2 | `872e281` | 侧栏 BASE_* 几何派生 + setPointSizeF(10*ratio) + reapply_metrics()（恢复 28c4f5e） |
| 3 | `b3548d6` | 8 处行高 + 9 处列宽派生（table_view/unified_import/preview/import_verify/collection_fix/manual_entry/problem_fix_panel/problem_dialog/staff_view/column_settings）；补录内嵌输入框等高派生；排序角标派生；apply_font_step 广播 |
| 4 | `e392cc4` | 底部挂件字号下拉 + 快捷键 + 持久化 + tests/_smoke_font_scale.py（30 项） |

**即时不刷新的残留**：构造期定宽的常驻页（如员工类型表 120/80/60）在切档后保持原宽，
重新进入页面/重启后按新档位生成——短文本列实测大档位仍放得下，风险可接受。

## 5. 验收

- `tests/_smoke_font_scale.py` 30/30（沙箱 offscreen）
- 回归：标题栏 11、侧栏 69、统一导入 40、xlsx 冒烟全过
- 真机验收（run_app.bat）：① 底部挂件切 5 档，各页面即时变化 ② Ctrl+=/+-/0 ③
  大两号下销项/结算/分成计算宽表格无裁字、可横向滚动 ④ 重启保留档位 ⑤ 标准档与旧版观感一致
