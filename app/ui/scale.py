"""全局字体缩放（5 档）——唯一真源

以当前 13px 为基准，提供 小两号 / 小一号 / 标准 / 大一号 / 大两号 共 5 档
（11 / 12 / 13 / 14 / 15px），倍率 = 档位字号 / 13。

**派生范围分两层，并非「全量」**：
  ① 字号 + QSS —— 全量派生。`style.build_qss` 里所有 px 值（字号、
     padding / min-height / 圆角 / 滚动条）都经 `P()` 乘倍率，随档位同步。
  ② 几何 —— 按**显式清单**派生。表格行高/列宽、标题栏与侧栏几何、关键控件
     高度等调用点在源码里显式写 `scale.px(基准值)`；
     未列入清单的边距/间距在**所有档位恒为标准档像素**（边距本无需跟字号缩放）。
⇒ 新增自绘控件或尺寸时，若需随档位变化请自行包 `scale.px(基准值)`，禁止裸数字；
  否则该尺寸不会跟字号档位变（大档位下可能出现「字大了、框没大」）。

用法：
    from app.ui import scale
    scale.px(34)          # 34px @标准档 → 29 / 31 / 34 / 37 / 39
    scale.ratio()         # 0.846 / 0.923 / 1.0 / 1.077 / 1.154

档位持久化到 data/prefs.json 的 font_step；切换由 main_window 广播，
实时生效、无需重启。
"""
from __future__ import annotations

import json
from pathlib import Path

PREFS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "prefs.json"

BASE = 13                      # 基准字号（= 改版前的界面字号）
STEPS = (11, 12, 13, 14, 15)   # 小两号 → 大两号
LABELS = ("小两号", "小一号", "标准", "大一号", "大两号")
DEFAULT_STEP = 2               # 索引 2 = 13px = 当前基准


# ---------------------------------------------------------------------------
# 当前档位
# ---------------------------------------------------------------------------
def _load_prefs() -> dict:
    try:
        if PREFS_PATH.exists():
            data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_prefs(data: dict) -> None:
    try:
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREFS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except Exception:
        pass


def load_step() -> int:
    """读取持久化的档位索引，越界/缺失回落到基准档。"""
    i = _load_prefs().get("font_step")
    if isinstance(i, bool) or not isinstance(i, int):
        return DEFAULT_STEP
    return i if 0 <= i < len(STEPS) else DEFAULT_STEP


def save_step(i: int) -> None:
    i = max(0, min(len(STEPS) - 1, int(i)))
    data = _load_prefs()
    data["font_step"] = i
    _save_prefs(data)


_current_step = load_step()


def step() -> int:
    """当前档位索引（0=小两号 … 4=大两号）。"""
    return _current_step


def set_step(i: int) -> None:
    """切换档位（仅改内存中的当前值，持久化由 save_step 负责）。"""
    global _current_step
    _current_step = max(0, min(len(STEPS) - 1, int(i)))


# ---------------------------------------------------------------------------
# 派生量
# ---------------------------------------------------------------------------
def base_size() -> int:
    """当前档位的基准字号（px）。"""
    return STEPS[_current_step]


def ratio() -> float:
    """相对基准档的缩放倍率。"""
    return STEPS[_current_step] / BASE


def px(n: float) -> int:
    """按倍率缩放一个像素尺寸，最小 1px（避免 0 导致边框/间距消失）。"""
    return max(1, int(round(n * ratio())))


def sp(n: float) -> int:
    """按倍率缩放字号。与 px 同倍率，仅作语义区分，便于阅读调用点。"""
    return px(n)


def steps() -> tuple:
    """供 UI 枚举：(index, 字号, 标签)。"""
    return tuple((i, STEPS[i], LABELS[i]) for i in range(len(STEPS)))
