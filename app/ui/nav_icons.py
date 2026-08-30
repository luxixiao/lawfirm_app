"""侧栏大类图标：24 网格单色线性图标，QPainter 自绘。

设计真源见 `design/sidebar_redesign_spec.md` §1.2；本模块的 `NAV_ICON_PATHS`
保留 SVG path 原样（供文档核对 / 日后 Web 端复用）。

注意：**Qt 无法从 SVG `d` 字符串构造 QPainterPath**，故绘制用 QPainter 基础图元
（直线 / 圆角矩形 / 圆 / 圆弧 / 实心点）实现——好处是颜色可由调用方按状态传入
（默认态 text_mute、hover 与激活态 text），且高分屏缩放清晰、无外部资源依赖。

七枚图标分属不同形状族，20px 下可区分：
    数据导入=U 形托盘  台账查看=分栏矩形  业务数据=开口钱包
    工资个税=人形+圆币 分成计算=分块计算器 各类报表=柱轴  数据维护=放射齿轮
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

GRID = 24.0

# SVG path 真源（stroke=currentColor，24 网格）
NAV_ICON_PATHS = {
    "数据导入": [
        "M3.5 14.5v3.5a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-3.5",
        "M12 3v11",
        "M7.5 9.5L12 14l4.5-4.5",
    ],
    "台账查看": [
        "M5.5 3.5h13a1.5 1.5 0 0 1 1.5 1.5v14a1.5 1.5 0 0 1-1.5 1.5h-13"
        "A1.5 1.5 0 0 1 4 19V5a1.5 1.5 0 0 1 1.5-1.5z",
        "M4 8.5h16", "M9.5 8.5v11.5", "M15 8.5v11.5",
    ],
    "业务数据": [
        "M3.5 8.5A2.5 2.5 0 0 1 6 6h11.5a2.5 2.5 0 0 1 2.5 2.5v7"
        "a2.5 2.5 0 0 1-2.5 2.5H6a2.5 2.5 0 0 1-2.5-2.5z",
        "M3.5 9.5h17", "M16 14.25h2",
    ],
    "工资个税": [
        "M6.6 8.6a3.4 3.4 0 1 0 6.8 0 3.4 3.4 0 0 0-6.8 0",
        "M4.2 19.6a5.8 5.8 0 0 1 11.6 0",
        "M13.8 16.4a3.8 3.8 0 1 0 7.6 0 3.8 3.8 0 0 0-7.6 0",
        "M17.6 14.2v4.6", "M15.9 15.1h3.4", "M15.9 17.9h3.4",
    ],
    "分成计算": [
        "M6 3.5h12a1.5 1.5 0 0 1 1.5 1.5v14a1.5 1.5 0 0 1-1.5 1.5H6"
        "A1.5 1.5 0 0 1 4.5 19V5A1.5 1.5 0 0 1 6 3.5z",
        "M7.5 6.5h9v3.5h-9z",
    ],
    "各类报表": ["M4 4v16h16"],
    "数据维护": [
        "M8.4 12a3.6 3.6 0 1 0 7.2 0 3.6 3.6 0 0 0-7.2 0",
        "M12 2.6v3", "M12 18.4v3",
        "M4.6 4.6l2.1 2.1", "M17.3 17.3l2.1 2.1",
        "M2.6 12h3", "M18.4 12h3",
        "M4.6 19.4l2.1-2.1", "M17.3 6.7l2.1-2.1",
    ],
}

# 分成计算的按键点（实心，fill=currentColor）
_CALC_DOTS = [(8.5, 13.5), (12.0, 13.5), (15.5, 13.5),
              (8.5, 17.5), (12.0, 17.5), (15.5, 17.5)]
# 各类报表的三根柱（x, y, w, h）
_REPORT_BARS = [(7.5, 13.5, 3.0, 6.5), (12.0, 9.5, 3.0, 10.5), (16.5, 15.0, 3.0, 5.0)]

ICON_FALLBACK = "数据维护"


class _Grid:
    """把 24 网格坐标映射到目标矩形内，并提供基础图元绘制。"""

    def __init__(self, painter: QPainter, rect: QRectF, color, width: float) -> None:
        self.p = painter
        self.s = min(rect.width(), rect.height()) / GRID
        self.ox = rect.x() + (rect.width() - GRID * self.s) / 2
        self.oy = rect.y() + (rect.height() - GRID * self.s) / 2
        pen = QPen(QColor(color), max(0.8, width * self.s), Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    def xy(self, x: float, y: float) -> QPointF:
        return QPointF(self.ox + x * self.s, self.oy + y * self.s)

    def line(self, x1, y1, x2, y2) -> None:
        self.p.drawLine(self.xy(x1, y1), self.xy(x2, y2))

    def polyline(self, pts) -> None:
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            self.line(x1, y1, x2, y2)

    def rrect(self, x, y, w, h, r=0.0) -> None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.ox + x * self.s, self.oy + y * self.s,
                                   w * self.s, h * self.s), r * self.s, r * self.s)
        self.p.drawPath(path)

    def circle(self, cx, cy, r) -> None:
        self.p.drawEllipse(QRectF(self.ox + (cx - r) * self.s, self.oy + (cy - r) * self.s,
                                  2 * r * self.s, 2 * r * self.s))

    def arc(self, cx, cy, r, start_deg: float, span_deg: float) -> None:
        """0° = 3 点钟方向，正值逆时针（上半圆 = start 0 / span 180）。"""
        self.p.drawArc(QRectF(self.ox + (cx - r) * self.s, self.oy + (cy - r) * self.s,
                              2 * r * self.s, 2 * r * self.s),
                       int(start_deg * 16), int(span_deg * 16))

    def dot(self, cx, cy, r) -> None:
        self.p.setBrush(QColor(self.p.pen().color()))
        self.p.drawEllipse(QRectF(self.ox + (cx - r) * self.s, self.oy + (cy - r) * self.s,
                                  2 * r * self.s, 2 * r * self.s))
        self.p.setBrush(Qt.BrushStyle.NoBrush)

    def quad(self, segments) -> None:
        """segments = [(起点, 控制点, 终点), ...]，依次首尾相连。"""
        path = QPainterPath()
        path.moveTo(self.xy(*segments[0][0]))
        for _s, ctrl, end in segments:
            path.quadTo(self.xy(*ctrl), self.xy(*end))
        self.p.drawPath(path)


def draw_nav_icon(painter: QPainter, name: str, rect: QRectF, color, width: float = 1.5) -> None:
    """在 rect 内绘制名为 name 的大类图标（未知名称回退到齿轮）。"""
    key = name if name in NAV_ICON_PATHS else ICON_FALLBACK
    g = _Grid(painter, rect, color, width)

    if key == "数据导入":
        g.quad([((3.5, 14.5), (3.5, 20.0), (6.0, 20.0)),
                ((6.0, 20.0), (18.0, 20.0), (18.0, 20.0)),
                ((18.0, 20.0), (20.5, 20.0), (20.5, 17.5))])
        g.line(20.5, 17.5, 20.5, 14.5)
        g.line(12, 3, 12, 14)
        g.polyline([(7.5, 9.5), (12, 14), (16.5, 9.5)])

    elif key == "台账查看":
        g.rrect(4, 3.5, 16, 17, 1.5)
        g.line(4, 8.5, 20, 8.5)
        g.line(9.5, 8.5, 9.5, 20.3)
        g.line(15, 8.5, 15, 20.3)

    elif key == "业务数据":
        g.rrect(3.5, 6, 17, 13, 2.5)
        g.line(3.5, 9.5, 20.5, 9.5)
        g.line(16, 14.25, 18, 14.25)

    elif key == "工资个税":
        g.circle(10, 8.6, 3.4)
        g.arc(10, 19.6, 5.8, 0, 180)
        g.circle(17.6, 16.4, 3.8)
        g.line(17.6, 14.2, 17.6, 18.8)
        g.line(15.9, 15.1, 19.3, 15.1)
        g.line(15.9, 17.9, 19.3, 17.9)

    elif key == "分成计算":
        g.rrect(4.5, 3.5, 15, 17, 1.5)
        g.rrect(7.5, 6.5, 9, 3.5, 0.8)
        for cx, cy in _CALC_DOTS:
            g.dot(cx, cy, 0.85)

    elif key == "各类报表":
        g.polyline([(4, 4), (4, 20), (20, 20)])
        for x, y, w, h in _REPORT_BARS:
            g.rrect(x, y, w, h, 0.8)

    else:  # 数据维护
        g.circle(12, 12, 3.6)
        g.line(12, 2.6, 12, 5.6)
        g.line(12, 18.4, 12, 21.4)
        g.line(4.6, 4.6, 6.7, 6.7)
        g.line(17.3, 17.3, 19.4, 19.4)
        g.line(2.6, 12, 5.6, 12)
        g.line(18.4, 12, 21.4, 12)
        g.line(4.6, 19.4, 6.7, 17.3)
        g.line(17.3, 6.7, 19.4, 4.6)


def draw_chevron(painter: QPainter, rect: QRectF, color, width: float = 1.5) -> None:
    """折叠指示箭头：默认朝右（›）。旋转由调用方用 QPainter 变换实现。"""
    painter.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    cx, cy = rect.center().x(), rect.center().y()
    d = min(rect.width(), rect.height()) * 0.28
    painter.drawLine(QPointF(cx - d * 0.5, cy - d), QPointF(cx + d * 0.5, cy))
    painter.drawLine(QPointF(cx + d * 0.5, cy), QPointF(cx - d * 0.5, cy + d))
