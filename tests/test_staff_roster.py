"""批1基础：花名册 + 人员类型关联 引擎层单测（不依赖 Qt）。

覆盖：花名册 CRUD、编号/身份证号唯一、人×多类型、主类型、staff 过渡镜像、导入同步。
直接对 app.engine.staff_type 的函数做断言，使用内存库 + SCHEMA 建表。
所有引擎函数均显式传入内存 conn（不触发 get_conn 打开真实库）。
"""
import os
import sqlite3
import sys

# 把项目根加入 sys.path，使 `app` 包可导入
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import db  # noqa: E402
from app.engine import staff_type as st  # noqa: E402


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(db.SCHEMA)
    # 镜像 init_db 的关键迁移（SCHEMA 不含这些 ALTER 列）：引擎过渡镜像依赖 staff.hire_month
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff)")]
    if "hire_month" not in cols:
        conn.execute("ALTER TABLE staff ADD COLUMN hire_month TEXT DEFAULT ''")
    return conn


def check(label, cond):
    status = "✅" if cond else "❌"
    print(f"  {status} {label}")
    return cond


def main():
    fails = 0

    # ---- 花名册 CRUD ----
    c = make_conn()
    st.add_roster_person("P001", "张三", "3301", "138", "2025-01", "", "备注A", c)
    p = st.get_roster_person("张三", c)
    fails += 0 if check("花名册新增后可查到", p is not None and p["code"] == "P001") else 1
    fails += 0 if check("list_roster 返回 1 人", len(st.list_roster(c)) == 1) else 1

    # 编号唯一（非空重复 → IntegrityError）
    try:
        st.add_roster_person("P001", "李四", "", "", "", "", "", c)
        fails += 1
        print("  ❌ 编号重复未报错")
    except sqlite3.IntegrityError:
        print("  ✅ 编号重复被唯一索引拦截")

    # 身份证号唯一
    try:
        st.add_roster_person("P002", "王五", "3301", "", "", "", "", c)
        fails += 1
        print("  ❌ 身份证号重复未报错")
    except sqlite3.IntegrityError:
        print("  ✅ 身份证号重复被唯一索引拦截")

    # 空值不冲突：两个空编号可共存
    st.add_roster_person("", "赵六", "", "", "", "", "", c)
    st.add_roster_person("", "钱七", "", "", "", "", "", c)
    fails += 0 if check("空编号不冲突（两人均可）", len(st.list_roster(c)) == 3) else 1

    # 修改
    st.update_roster_person("P009", "张三", "3302", "139", "2025-02", "2026-03", "改后", c)
    p = st.get_roster_person("张三", c)
    fails += 0 if check("花名册修改生效", p["id_card"] == "3302" and p["leave_month"] == "2026-03") else 1

    # ---- 人 × 多类型 + 主类型 ----
    c = make_conn()
    st.add_roster_person("", "张三", "", "", "", "", "", c)
    st.add_person_type("张三", "合伙", c)
    st.add_person_type("张三", "聘用", c)
    types = st.list_person_types("张三", c)
    fails += 0 if check("一人两类型", types == ["合伙", "聘用"]) else 1
    fails += 0 if check("首类型为主类型", st.primary_type_of("张三", c) == "合伙") else 1

    # 重复添加同类型被忽略
    st.add_person_type("张三", "合伙", c)
    fails += 0 if check("重复关联被忽略", st.list_person_types("张三", c) == ["合伙", "聘用"]) else 1

    # 删除主类型 → 剩余第一个提升为主类型
    st.remove_person_type("张三", "合伙", c)
    fails += 0 if check("删主类型后聘用升主", st.primary_type_of("张三", c) == "聘用") else 1

    # set_person_types 整体替换
    st.set_person_types("张三", ["兼职", "合伙"], c)
    fails += 0 if check("set_person_types 按序设主", st.primary_type_of("张三", c) == "兼职") else 1

    # ---- staff 过渡镜像 ----
    c = make_conn()
    st.add_roster_person("", "张三", "", "", "2025-01", "", "注", c)
    st.set_person_types("张三", ["合伙", "聘用"], c)
    row = c.execute("SELECT staff_type, hire_month, note FROM staff WHERE name=?", ("张三",)).fetchone()
    fails += 0 if check("镜像 staff.staff_type=主类型", row and row["staff_type"] == "合伙") else 1
    # 去掉所有类型 → staff 行应被删
    st.set_person_types("张三", [], c)
    row = c.execute("SELECT 1 FROM staff WHERE name=?", ("张三",)).fetchone()
    fails += 0 if check("无类型时 staff 镜像行被清", row is None) else 1

    # ---- 导入同步 ----
    c = make_conn()
    st.sync_imported_staff(c, "李四", "聘用", "导入备注")
    rp = st.get_roster_person("李四", c)
    fails += 0 if check("导入同步进花名册", rp is not None and rp["note"] == "导入备注") else 1
    fails += 0 if check("导入同步建关联(主)", st.primary_type_of("李四", c) == "聘用") else 1

    # ---- 删除护栏 ----
    c = make_conn()
    st.add_roster_person("", "孙八", "", "", "", "", "", c)
    st.add_person_type("孙八", "合伙", c)
    # 无任何业务引用 → 可删
    st.delete_roster_person("孙八", c)
    fails += 0 if check("无引用人员可删", st.get_roster_person("孙八", c) is None) else 1

    print()
    if fails == 0:
        print("全部通过 ✅")
        return 0
    print(f"失败 {fails} 项 ❌")
    return 1


if __name__ == "__main__":
    sys.exit(main())
