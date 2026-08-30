# 侧栏图标与动效改造完成

## 做了什么
- 重绘 7 个大类图标（托盘/表格/钱包/人形+币/计算器/柱状图/齿轮），彻底消除原有图标重复、缺失、风格不统一的问题。
- 新增 `app/ui/nav_icons.py`：24 网格单色线性图标，QPainter 自绘，按状态换色，零外部依赖。
- 重写 `app/ui/sidebar.py`：大类行自绘 `NavHeaderButton`（hover/press 插值、chevron 旋转）、子项容器 `GroupPanel`（高度 + 透明度 + 错峰淡入）。
- 增加侧栏宽度动画（240↔60）、大类折叠动画、防抖（移入 80ms / 移出 120ms）、收起态图标 tooltip。
- 新增 `style.motion_enabled()` 动效开关；底部皮肤挂件加「动效」复选框，随时关闭 reduced motion。
- `style.py` 提供 `palette()` 动态取色，侧栏自绘跟随深浅皮肤切换。

## 关键文件
- `app/ui/nav_icons.py`（新建）
- `app/ui/sidebar.py`
- `app/ui/style.py`
- `app/ui/main_window.py`
- `tests/_smoke_sidebar.py`（新建）

## 验证结果
- `_smoke_sidebar`：64/64 通过（图标 7 枚非空两两不同、hover/press 插值、chevron 旋转、组折叠/展开、宽度动画、防抖、动效开关、深浅皮肤）
- 回归单测：calc 206 项 + staff_type 30 + expense_cat 51 全部通过
- 回归冒烟：staff_view 16 + expense_cat_view 36 + calc 56 全部通过
- `py_compile` 通过

## 提交
`git commit` 已推送至 `main`。

## 使用
本机 `run_app.bat` 启动，看左侧栏七枚图标与动效；如不喜欢动画，展开侧栏后在底部「皮肤」区域取消「动效」勾选即可。
