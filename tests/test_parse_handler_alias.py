"""parse_handler 经办人别名归一单元测试。

覆盖「管理人」别名规则（2025.9 台账实测触发）：
台账经办人列常写成「管理人报酬（道兴家私）」，需归一到花名册里的正式姓名「管理人」。

运行：python tests/test_parse_handler_alias.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.importer.excel_reader import ImportError_  # noqa: E402
from app.importer.parse_handler import (  # noqa: E402
    _normalize_handler_text,
    parse_handler_column,
)

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"  [OK  ] {label}")
    else:
        FAILS.append(label)
        print(f"  [FAIL] {label}  {detail}")


# ---------------------------------------------------------------------------
print("== 1. 归一化：以「管理人」开头的段 -> 管理人 ==")
for text, want in [
    ("管理人", "管理人"),
    ("管理人报酬", "管理人"),
    ("管理人报酬（道兴家私）", "管理人"),
    ("管理人报酬(道兴家私)", "管理人"),
    ("管理人（某某项目）", "管理人"),
    ("管理人补助", "管理人"),          # 台账经办人列不会出现，按前缀规则归一，行为确定
]:
    got = _normalize_handler_text(text)
    check(f"{text!r} -> {want!r}", got == want, f"got={got!r}")

print()
print("== 2. 非别名文本零副作用 ==")
for text in ["胡坚", "徐志庆4500、柳立中4500", "朱云，黄宝根", "周士杰2000.朱琰芳1000"]:
    got = _normalize_handler_text(text)
    check(f"{text!r} 原样", got == text, f"got={got!r}")

print()
print("== 3. 段级归一（混合场景，不误伤其它经办人）==")
got = _normalize_handler_text("徐志庆5000、管理人报酬（道兴家私）")
check("混合段只折叠管理人段", got == "徐志庆5000、管理人", f"got={got!r}")

print()
print("== 4. 保留段内显式金额 ==")
got = _normalize_handler_text("管理人报酬（xx）3000")
check("保留 3000", got == "管理人3000", f"got={got!r}")

print()
print("== 5. 解析结果（金额分配）==")
# 5.1 单经办人全额（2025.9 台账实际案例）
r = parse_handler_column("管理人报酬（道兴家私）", 72457.48, "25332000000406956789")
check("单经办人全额 72457.48", r == [("管理人", 72457.48)], f"got={r}")

# 5.2 显式金额
r = parse_handler_column("管理人报酬（xx）3000", 3000, "X")
check("显式金额 3000", r == [("管理人", 3000.0)], f"got={r}")

# 5.3 混合：指定 + 未指定（未指定按平摊剩余）
r = parse_handler_column("徐志庆5000、管理人报酬（道兴家私）", 57257.48, "Y")
check("混合平摊剩余", r == [("徐志庆", 5000.0), ("管理人", 52257.48)], f"got={r}")

# 5.4 红字发票取负
r = parse_handler_column("管理人报酬（xx）", -1000, "Z")
check("红字取负", r == [("管理人", -1000.0)], f"got={r}")

print()
print("== 6. 不影响既有解析行为（回归）==")
r = parse_handler_column("徐志庆4500、柳立中4500", 9000, "A")
check("双人带金额", r == [("徐志庆", 4500.0), ("柳立中", 4500.0)], f"got={r}")

r = parse_handler_column("胡坚", 1000, "B")
check("单人简写", r == [("胡坚", 1000.0)], f"got={r}")

r = parse_handler_column("徐志庆8万", 80000, "C")
check("万单位", r == [("徐志庆", 80000.0)], f"got={r}")

print()
print("== 7. 其它段仍正常报错（未被归一掩盖）==")
try:
    parse_handler_column("张三&&&", 100, "D")
    check("非法段应报错", False, "未抛异常")
except ImportError_:
    check("非法段应报错", True)

print()
print("=" * 60)
print(f"PASS {OK}  FAIL {len(FAILS)}")
if FAILS:
    for f in FAILS:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASS")
