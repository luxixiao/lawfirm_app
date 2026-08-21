"""数据行身份（person_type）回填与标注

身份默认 = 人员当前类型（staff.staff_type）；少数双身份者可在台账数据页手动改。
"""
from __future__ import annotations

from app.db import get_conn


def norm_type(staff_type) -> str:
    t = (staff_type or "").strip()
    if "合伙" in t:
        return "合伙"
    if "兼职" in t:
        return "兼职"
    if "聘用" in t:
        return "聘用"
    return "其他"


def staff_type_of(conn, name: str) -> str:
    r = conn.execute("SELECT staff_type FROM staff WHERE name=? AND is_active=1", (name,)).fetchone()
    return norm_type(r["staff_type"]) if r else ""


def backfill_person_types() -> None:
    """历史数据回填：person_type 为空的行按人员当前类型标注"""
    conn = get_conn()
    try:
        for name in [r["person_name"] for r in conn.execute(
                "SELECT DISTINCT person_name FROM charge_detail WHERE person_type=''")]:
            t = staff_type_of(conn, name)
            if t:
                conn.execute("UPDATE charge_detail SET person_type=? WHERE person_name=? AND person_type=''",
                             (t, name))
        for name in [r["actual_handler"] for r in conn.execute(
                "SELECT DISTINCT actual_handler FROM expense_ledger WHERE person_type=''")]:
            if not name:
                continue
            t = staff_type_of(conn, name)
            if t:
                conn.execute("UPDATE expense_ledger SET person_type=? WHERE actual_handler=? AND person_type=''",
                             (t, name))
        conn.commit()
    finally:
        conn.close()
