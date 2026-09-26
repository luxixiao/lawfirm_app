"""expense_validation（费用台账编辑校验 + 同账期重导 diff）单元测试 — 内存库。

运行：python tests/test_expense_validation.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import account_subject as asub  # noqa: E402
from app.engine import staff_type as st  # noqa: E402
from app.engine import expense_validation as ev  # noqa: E402

OK, FAILS = 0, []


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # 员工类型参与结算开关迁移（与 db.init_db 迁移块一致，幂等）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]
    if "is_settle" not in cols:
        conn.execute("ALTER TABLE staff_type_def ADD COLUMN is_settle INTEGER NOT NULL DEFAULT 0")
    if "net_basis" not in cols:
        conn.execute("ALTER TABLE staff_type_def ADD COLUMN net_basis TEXT NOT NULL DEFAULT '收款净额'")
    # 去写死（Plan A）：角色列 + role_def 种子（复刻 init_db 迁移，幂等）
    if "role_code" not in cols:
        conn.execute("ALTER TABLE staff_type_def ADD COLUMN role_code TEXT NOT NULL DEFAULT 'other'")
    st.ensure_roles(conn)
    conn.commit()
    return conn


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def seed_types(conn) -> None:
    """铺出规则③用到的员工类型（合伙/聘用/公共/行政 参与、挂靠/其他 不参与）。

    改动 1 后 `ensure_defaults()` 不再预置任何类型（员工类型表初始为空），
    用例必须自己建类型，否则「经办人必须参与结算」规则会全部误判为未参与。
    """
    for name, settle, basis in (("合伙", True, "开票净额"), ("聘用", True, "收款净额"),
                                ("兼职", True, "收款净额"), ("公共", True, "收款净额"),
                                ("行政", True, "收款净额"), ("挂靠", False, "收款净额"),
                                ("其他", False, "收款净额")):
        if st.get_type(name, conn) is None:
            st.add_type(name, conn=conn)
            conn.execute("UPDATE staff_type_def SET is_builtin=? WHERE name=?",
                         (1 if name in st.BUILTIN_TYPES or name in ("公共", "行政") else 0,
                          name))
            conn.commit()
        st.set_settle(name, settle, conn=conn)
        st.set_net_basis(name, basis, conn=conn)


def main() -> int:
    conn = make_conn()
    st.ensure_defaults(conn)
    seed_types(conn)

    # 会计科目主表（供规则②校验）
    p1 = asub.add_level1("资产类", conn=conn)
    asub.add_level2(p1, "办公费", conn=conn)
    asub.add_level2(p1, "差旅费", conn=conn)
    p2 = asub.add_level1("费用类", conn=conn)
    asub.add_level2(p2, "招待费", conn=conn)

    # ===== 规则①：账面金额 ≈ 费用金额 − 税额 =====
    rows_ok = [
        {"id": 1, "expense_amount": 100.0, "tax_amount": 10.0, "book_amount": 90.0,
         "subject1": "资产类", "subject2": "办公费", "actual_handler": "合伙"},
        {"id": 2, "expense_amount": 200.0, "tax_amount": 0.0, "book_amount": 200.0,
         "subject1": "费用类", "subject2": "招待费", "actual_handler": "聘用"},
    ]
    check("规则①全通过返回 []", ev.validate_expense_edit(rows_ok, conn) == [])

    rows_bad_book = [
        {"id": 1, "expense_amount": 100.0, "tax_amount": 10.0, "book_amount": 95.0,
         "subject1": "资产类", "subject2": "办公费", "actual_handler": "合伙"},
    ]
    v = ev.validate_expense_edit(rows_bad_book, conn)
    check("规则① book≠amount−tax 报 Violation",
          len(v) == 1 and v[0].field == "book_amount" and v[0].row_id == 1, f"got={v}")

    # 浮点容差 ±0.01 视为通过
    rows_eps = [{"id": 3, "expense_amount": 100.0, "tax_amount": 33.33, "book_amount": 66.67,
                 "subject1": "资产类", "subject2": "办公费", "actual_handler": "合伙"}]
    check("规则① ±0.01 容差通过",
          ev.validate_expense_edit(rows_eps, conn) == [],
          f"got={ev.validate_expense_edit(rows_eps, conn)}")

    # ===== 规则②：科目必须在会计科目主表 =====
    rows_bad_subj = [
        {"id": 4, "expense_amount": 10.0, "tax_amount": 0.0, "book_amount": 10.0,
         "subject1": "不存在类", "subject2": "子", "actual_handler": "合伙"},
    ]
    v = ev.validate_expense_edit(rows_bad_subj, conn)
    check("规则② 未知一级科目报 Violation(subject1)",
          any(x.field == "subject1" for x in v), f"got={v}")

    rows_bad_subj2 = [
        {"id": 5, "expense_amount": 10.0, "tax_amount": 0.0, "book_amount": 10.0,
         "subject1": "资产类", "subject2": "不存在二级", "actual_handler": "合伙"},
    ]
    v = ev.validate_expense_edit(rows_bad_subj2, conn)
    check("规则② 已知一级但未知二级报 Violation(subject2)",
          any(x.field == "subject2" for x in v), f"got={v}")

    # ===== 规则③：经办人必须参与结算 =====
    rows_bad_handler = [
        {"id": 6, "expense_amount": 10.0, "tax_amount": 0.0, "book_amount": 10.0,
         "subject1": "资产类", "subject2": "办公费", "actual_handler": "挂靠"},
    ]
    v = ev.validate_expense_edit(rows_bad_handler, conn)
    check("规则③ 非参与结算经办人报 Violation(actual_handler)",
          any(x.field == "actual_handler" for x in v), f"got={v}")

    # 公共/行政 经迁移已设为参与 → 通过
    rows_pub = [
        {"id": 7, "expense_amount": 10.0, "tax_amount": 0.0, "book_amount": 10.0,
         "subject1": "资产类", "subject2": "办公费", "actual_handler": "公共"},
        {"id": 8, "expense_amount": 10.0, "tax_amount": 0.0, "book_amount": 10.0,
         "subject1": "资产类", "subject2": "办公费", "actual_handler": "行政"},
    ]
    check("规则③ 公共/行政 参与→通过", ev.validate_expense_edit(rows_pub, conn) == [])

    # 规则③（真实路径回归）：actual_handler 传「经办人姓名」，需经 staff.staff_type 解析类型
    # 修复前 is_settle_participant 误把姓名当类型名查 staff_type_def，导致 聘用 类经办人(如方国兴)
    # 在详情页保存时被误判「未参与结算」而拦截。
    conn.execute(
        "INSERT INTO staff (name, staff_type, is_active, source) VALUES (?,?,1,'manual')",
        ("方国兴", "聘用"))
    conn.execute(
        "INSERT INTO staff (name, staff_type, is_active, source) VALUES (?,?,1,'manual')",
        ("李挂靠", "挂靠"))
    conn.commit()

    rows_person_settle = [
        {"id": 10, "expense_amount": 10.0, "tax_amount": 0.0, "book_amount": 10.0,
         "subject1": "资产类", "subject2": "办公费", "actual_handler": "方国兴"},
    ]
    check("规则③ 经办人姓名(聘用)→参与→通过",
          ev.validate_expense_edit(rows_person_settle, conn) == [],
          f"got={ev.validate_expense_edit(rows_person_settle, conn)}")

    rows_person_nonsettle = [
        {"id": 11, "expense_amount": 10.0, "tax_amount": 0.0, "book_amount": 10.0,
         "subject1": "资产类", "subject2": "办公费", "actual_handler": "李挂靠"},
    ]
    v = ev.validate_expense_edit(rows_person_nonsettle, conn)
    check("规则③ 经办人姓名(挂靠)→未参与→Violation",
          any(x.field == "actual_handler" for x in v), f"got={v}")

    # 多规则同时触发（一条行可多字段）
    rows_multi = [
        {"id": 9, "expense_amount": 100.0, "tax_amount": 10.0, "book_amount": 80.0,
         "subject1": "乱类", "subject2": "乱", "actual_handler": "其他"},
    ]
    v = ev.validate_expense_edit(rows_multi, conn)
    fields = {x.field for x in v}
    check("多规则同时触发(book+subject+handler)",
          v[0].row_id == 9 and {"book_amount", "subject1", "actual_handler"} <= fields,
          f"got={v}")

    # ===== diff_expense_reimport：按 seq 配对 =====
    parsed = [
        {"seq": 1, "expense_amount": 100.0, "book_amount": 90.0, "name": "A", "subject1": "资产类", "subject2": "办公费"},
        {"seq": 2, "expense_amount": 200.0, "book_amount": 200.0, "name": "B", "subject1": "费用类", "subject2": "招待费"},
        {"seq": 3, "expense_amount": 50.0, "book_amount": 50.0, "name": "C", "subject1": "资产类", "subject2": "差旅费"},  # 新增
    ]
    db = [
        {"seq": 1, "expense_amount": 100.0, "book_amount": 90.0, "name": "A", "subject1": "资产类", "subject2": "办公费"},
        {"seq": 2, "expense_amount": 200.0, "book_amount": 200.0, "name": "B改", "subject1": "费用类", "subject2": "招待费"},  # name 变
        {"seq": 9, "expense_amount": 9.0, "book_amount": 9.0, "name": "Z", "subject1": "费用类", "subject2": "招待费"},  # 删除
    ]
    diff = ev.diff_expense_reimport(parsed, db)
    check("diff.period 取自 parsed", diff.period == "2025" or diff.period == "", f"got={diff.period}")
    check("diff.added = [3]", diff.added == [3], f"got={diff.added}")
    check("diff.removed = [9]", diff.removed == [9], f"got={diff.removed}")
    check("diff.matched 含 seq=2 且字段差 name",
          any(seq == 2 and any(fd["field"] == "name" for fd in fds) for seq, fds in diff.matched),
          f"got={diff.matched}")
    check("diff.changed = True", diff.changed is True)

    # 完全相同 → changed=False
    same = [
        {"seq": 1, "expense_amount": 100.0, "book_amount": 90.0, "name": "A", "subject1": "资产类", "subject2": "办公费"},
        {"seq": 2, "expense_amount": 200.0, "book_amount": 200.0, "name": "B", "subject1": "费用类", "subject2": "招待费"},
    ]
    d2 = ev.diff_expense_reimport(same, list(same))
    check("完全相同 changed=False", d2.changed is False and d2.added == [] and d2.removed == [] and d2.matched == [])

    # 数值浮点容差：amount 100 vs 100.0 视为相同
    near = [
        {"seq": 1, "expense_amount": 100.0, "book_amount": 90.0, "name": "A", "subject1": "资产类", "subject2": "办公费"},
    ]
    d3 = ev.diff_expense_reimport(near, [{"seq": 1, "expense_amount": 100, "book_amount": 90, "name": "A",
                                          "subject1": "资产类", "subject2": "办公费"}])
    check("数值浮点容差视为未变", d3.matched == [], f"got={d3.matched}")

    conn.close()
    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
