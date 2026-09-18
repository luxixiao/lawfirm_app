"""review_compare（「台账 ⇄ 库」比对共用核心）单元测试 — 内存库。

运行：python tests/test_review_compare.py
批 4 重写（2026-09-18）：`build_review_rows` 及其行装配管线随旧「导入后」页
（review_post_view.py）删除，本文件改为**直接测存活的共用核心**：
`lib_diff` / `build_lib_context` / `lib_recv_totals` / `_recv_sides`。
原 13 节的比对口径场景（金额 / 经办人缺失多出 / 分摊金额不符 / 纯人名豁免 /
快照优先 / 账期窗口回退 / manual 纳入口径）全部移植；
「仅源有 / 仅库有 / 已确认异常」属旧页行装配逻辑，已由批 2 的导入前四态
与 `test_import_confirm.py`（回退聚合）覆盖，不再重复。

「已收认定」场景里 `exp_total` 直接手填（`compute_expected_receipts` 对 remark
的还原口径另有 `test_deferred_sheet3` / 冒烟覆盖），只在 `lib_recv_totals`
的回退分支里用真实 `compute_expected_receipts` 走一遍 pure_date 还原。
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import review_compare as rc  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


def src(no="A1", total=100.0, handlers=None, handler_text="张三100",
        remark=None, buyer="甲公司", is_red=False):
    """手工构造 lib_diff 的源侧（与旧 _derive_raw 产物同构）；
    handlers 缺省 = 张三全额（沿用旧 13 节场景的最小形态）。"""
    return {"invoice_no": no, "buyer": buyer, "total_amount": total,
            "is_red": is_red, "handler_text": handler_text,
            "handlers": {"张三": total} if handlers is None else dict(handlers),
            "remark": remark or {}, "synced": True, "source": "sheet1 · 第2行"}


def lib_inv(no="A1", total=100.0, buyer="甲公司"):
    return {"invoice_no": no, "buyer": buyer, "total_amount": total}


def main() -> int:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    class _ConnProxy:
        """引擎 finally 会 close()（应用里每次 get_conn 新建连接）；
        内存库单例连接由本测试持有，close 置空操作防误关。"""
        def __init__(self, c):
            self._c = c
        def execute(self, *a, **k):
            return self._c.execute(*a, **k)
        def commit(self):
            self._c.commit()
        def close(self):
            pass
        def __getattr__(self, n):
            return getattr(self._c, n)

    rc.get_conn = lambda: _ConnProxy(conn)  # 注入内存库

    P = "2025-01"

    def add_inv(no, amount, buyer="甲公司", source="import"):
        conn.execute(
            "INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source, import_batch_id) "
            "VALUES (?, '2025-01-05', ?, ?, ?, 1)", (no, buyer, amount, source))

    def add_cd(no, pairs, source="import"):
        for name, amt in pairs.items():
            conn.execute(
                "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source) "
                "VALUES (?, ?, ?, ?)", (no, name, amt, source))

    def add_snap(no, exp_total, act_total, batch_id=1):
        conn.execute(
            "INSERT INTO received_snapshot (import_batch_id, invoice_no, expected_json, actual_json) "
            "VALUES (?, ?, ?, ?)",
            (batch_id, no, json.dumps({"total": exp_total, "items": []}),
             json.dumps({"total": act_total, "items": []})))

    def add_coll(no, date, amount, person="张三", source="import"):
        conn.execute(
            "INSERT INTO collection (invoice_no, receipt_date, amount, person_name, source) "
            "VALUES (?, ?, ?, ?, ?)", (no, date, amount, person, source))

    # ================================================= lib_diff：逐维度差异
    # 1) 全一致 → None
    d = rc.lib_diff(src(), lib_inv(), {"张三": 100.0}, 100.0, 100.0)
    check("一致 → None", d is None, str(d))

    # 2) 金额不一致（同名不同额会连带「分摊金额不符」，故只断言包含）
    d = rc.lib_diff(src(), lib_inv(total=200.0), {"张三": 200.0}, 100.0, 100.0)
    check("金额不一致 → flag/field", d is not None and "金额不一致" in d["flags"]
          and "金额" in d["fields"], str(d))
    check("金额 detail 带两侧对照值",
          d and "台账100.00" in d["detail"][0] and "库200.00" in d["detail"][0],
          str(d and d["detail"]))

    # 3) 经办人缺失 / 多出
    d = rc.lib_diff(src(handlers={"张三": 60.0, "李四": 40.0},
                        handler_text="张三60、李四40"),
                    lib_inv(), {"张三": 100.0}, 100.0, 100.0)
    check("库内缺经办人 → 经办人缺失:李四",
          d and any("经办人缺失:李四" in f for f in d["flags"]), str(d and d["flags"]))
    d = rc.lib_diff(src(), lib_inv(), {"张三": 60.0, "李四": 40.0}, 100.0, 100.0)
    check("库内多经办人 → 经办人多出:李四",
          d and any("经办人多出:李四" in f for f in d["flags"]), str(d and d["flags"]))

    # 4) 分摊金额不符
    d = rc.lib_diff(src(handlers={"张三": 60.0, "李四": 40.0},
                        handler_text="张三60、李四40"),
                    lib_inv(), {"张三": 70.0, "李四": 30.0}, 100.0, 100.0)
    check("分摊金额不符 → flag",
          d and any("分摊金额不符" in f for f in d["flags"]), str(d and d["flags"]))

    # 5) 纯人名均分豁免（handler_text 无数字 → 不比分摊金额；合计相等 → 全免）
    d = rc.lib_diff(src(handlers={"张三": 50.0, "李四": 50.0},
                        handler_text="张三、李四"),
                    lib_inv(), {"张三": 60.0, "李四": 40.0}, 100.0, 100.0)
    check("纯人名豁免 → 一致（None）", d is None, str(d))
    # 5b) 纯人名但合计也不平 → 仍报「分摊合计不符」
    d = rc.lib_diff(src(handlers={"张三": 50.0, "李四": 50.0},
                        handler_text="张三、李四"),
                    lib_inv(), {"张三": 60.0, "李四": 30.0}, 100.0, 100.0)
    check("纯人名合计不平 → 分摊合计不符",
          d and any("分摊合计不符" in f for f in d["flags"]), str(d and d["flags"]))

    # 6) 已收认定不符（差额进 flag 文本）
    d = rc.lib_diff(src(), lib_inv(), {"张三": 100.0}, 100.0, 50.0)
    check("已收认定不符(差50.00)",
          d and any("已收认定不符(差50.00)" in f for f in d["flags"])
          and "已收认定" in d["fields"], str(d and d["flags"]))

    # 7) 源勾稽不平（exp + remaining ≠ 总额）→ 即便库侧已收对平也报
    d = rc.lib_diff(src(remark={"remaining": 40.0, "receipts": [("2025-01", 70.0)],
                                "pure_date": False}),
                    lib_inv(), {"张三": 100.0}, 70.0, 70.0)
    check("源勾稽不平 → FIELD_RECV", d and d["fields"] == ["已收认定"]
          and any("源勾稽不平" in f for f in d["flags"]), str(d and d["flags"]))

    # 8) 红字：exp=0 口径下不误报（exp/act 都传 0；红字行源侧无经办人）
    d = rc.lib_diff(src(total=-50.0, is_red=True, handler_text="", handlers={}),
                    lib_inv(total=-50.0), {}, 0.0, 0.0)
    check("红字无收款 → 一致（None）", d is None, str(d))

    # 9) `dims` 维度选择器（批 2 修订 · 方案甲）：导入前只比金额
    d = rc.lib_diff(src(handlers={"张三": 60.0, "李四": 40.0},
                        handler_text="张三60 李四40", total=100.0),
                    lib_inv(total=300.0), {"张三": 100.0, "陈娟": 200.0}, 100.0, 50.0,
                    dims=(rc.FIELD_AMOUNT,))
    check("dims=(金额) → 只报金额，吞吐分摊/已收两维差异",
          d is not None and d["fields"] == ["金额"]
          and all(FIELD not in f for FIELD in ("经办人", "已收") for f in d["flags"]),
          str(d))
    d = rc.lib_diff(src(), lib_inv(), {"张三": 100.0}, 100.0, 50.0,
                    dims=(rc.FIELD_AMOUNT,))
    check("dims=(金额) 且金额一致 → None（另两维不合也不报）", d is None, str(d))
    d = rc.lib_diff(src(), lib_inv(total=200.0), dims=(rc.FIELD_AMOUNT,))
    check("dims=(金额) 时另两维入参可省（None）",
          d is not None and d["fields"] == ["金额"], str(d))
    d = rc.lib_diff(src(handlers={"张三": 60.0, "李四": 40.0},
                        handler_text="张三60 李四40"),
                    lib_inv(), {"张三": 100.0}, dims=(rc.FIELD_HANDLERS,))
    check("dims 可单独选分摊（核心能力保留，供将来/导入后复用）",
          d is not None and d["fields"] == ["经办人分摊"], str(d))
    d = rc.lib_diff(src(), lib_inv(), dims=())
    check("dims=() → 不比任何维度（None）", d is None, str(d))

    # ================================================ build_lib_context：库侧三方
    add_inv("B1", 100.0)                       # import
    add_inv("B2", 300.0, buyer="丙公司", source="manual")   # 补录 manual 纳入口径
    add_cd("B1", {"张三": 100.0})
    add_cd("B2", {"李四": 300.0}, source="manual")
    add_snap("B1", 100.0, 80.0)                # 快照挂 batch 1
    add_coll("B1", "2025-01-10", 80.0)
    add_coll("B2", "2025-02-10", 300.0, person="李四")       # 账期窗口之外（P=2025-01）
    add_coll("B3", "2025-01-15", 999.0, source="other")      # 非 import/manual 不读
    ctx = rc.build_lib_context(P, conn=_ConnProxy(conn))
    check("ctx.inv 读 import+manual", set(ctx["inv"]) == {"B1", "B2"}
          and ctx["inv"]["B2"]["buyer"] == "丙公司", str(sorted(ctx["inv"])))
    check("ctx.cd 读 import+manual", ctx["cd"]["B1"] == {"张三": 100.0}
          and ctx["cd"]["B2"] == {"李四": 300.0}, str(ctx["cd"]))
    check("ctx.act 按账期窗口过滤（2025-02 排除）",
          set(ctx["act"]) == {"B1"}, str(sorted(ctx["act"])))
    check("ctx.act 排除其它 source", ctx["act"]["B1"] == [("2025-01", 80.0, "张三")],
          str(ctx["act"]))
    check("无 batch_id → ctx.snap 空", ctx["snap"] == {}, str(ctx["snap"]))

    ctx2 = rc.build_lib_context(P, batch_id=1, conn=_ConnProxy(conn))
    check("给 batch_id → 快照优先读出",
          ctx2["snap"]["B1"] == ({"total": 100.0, "items": []},
                                 {"total": 80.0, "items": []}), str(ctx2["snap"]))
    # 坏快照按缺处理（回退 live）
    conn.execute("INSERT INTO received_snapshot (import_batch_id, invoice_no, "
                 "expected_json, actual_json) VALUES (1,'B9','{bad json','x')")
    ctx3 = rc.build_lib_context(P, batch_id=1, conn=_ConnProxy(conn))
    check("坏快照按缺处理", "B9" not in ctx3["snap"], str(sorted(ctx3["snap"])))

    # ================================================= lib_recv_totals：已收双方
    exp_t, act_t = rc.lib_recv_totals(ctx2, "B1", src())
    check("lib_recv_totals 快照优先", (exp_t, act_t) == (100.0, 80.0),
          f"{exp_t}/{act_t}")
    # 无快照 → 源侧 compute_expected_receipts（pure_date=全额于该日）、库侧 live
    s_pure = src(remark={"pure_date": "2025-01-10"})
    exp_t, act_t = rc.lib_recv_totals(ctx, "B1", s_pure)
    check("lib_recv_totals 回退：源 pure_date 全额 / 库 live 合计",
          (exp_t, act_t) == (100.0, 80.0), f"{exp_t}/{act_t}")

    # ================================================= _recv_sides：仅库有（src=None）
    e_t, e_items, a_t, a_items = rc._recv_sides("B1", None, {}, ctx["act"])
    check("src=None → 源侧 (0.0, [])", (e_t, e_items) == (0.0, []), f"{e_t}/{e_items}")
    check("src=None 库侧仍读 live", a_t == 80.0 and a_items == [
        {"ym": "2025-01", "amount": 80.0}], f"{a_t}/{a_items}")
    # 快照优先于 live（含 items 直读）
    snap = {"B1": ({"total": 100.0, "items": [{"ym": "2025-01", "amount": 100.0}]},
                   {"total": 80.0, "items": [{"ym": "2025-01", "amount": 80.0}]})}
    e_t, _e, a_t, _a = rc._recv_sides("B1", src(), snap, ctx["act"])
    check("_recv_sides 快照优先（exp/act 双侧）", (e_t, a_t) == (100.0, 80.0),
          f"{e_t}/{a_t}")

    conn.close()
    print(f"\n{OK} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
