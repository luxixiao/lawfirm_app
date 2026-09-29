"""去写死（Plan A）回归测试 — 角色口径 + F1 改名安全 + 公共专属按角色 + person_type 过滤。

覆盖：
- role_def 种子（4 角色）与 role_code_of / role_attr / identity_roles /
  person_type_combo_items / role_label_map / list_roles；
- F1 根因修复：把"合伙"改名为任意名，结算/收入（汇总与分身份视图）不受影响；
- 专属费用按分类级白名单校验（exclusive_types，方案乙）；
- charge_detail.person_type 存的是角色码，结算按角色码过滤。

运行：python tests/test_role_def.py
（纯逻辑、无 Qt 依赖，可在无头环境跑；UI 门禁仍须在本机 venv 跑。）
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import DB_PATH as _DB_PATH, init_db  # noqa: E402
import app.db as db  # noqa: E402
from app.engine import staff_type as st  # noqa: E402
from app.engine import backfill as bf  # noqa: E402
from app.engine import expense_cat as ec  # noqa: E402
from app.engine import person_settlement as ps  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def main() -> int:
    tmp = Path(tempfile.gettempdir()) / "test_role_def.db"
    if tmp.exists():
        tmp.unlink()
    db.DB_PATH = tmp
    init_db()   # 建 schema + 跑迁移（role_def 种子 + staff_type_def.role_code + person_type 翻码）

    # 两条线模型下 identity_roles 反映「实际已配置且进线的角色」—— 建库后类型表空，
    # 故此处按员工类型页自建动作铺出内置三类（合伙/聘用/兼职 业务线开）用于 §2 断言。
    c0 = db.get_conn()
    try:
        for name, role_code, basis in (("合伙", "partner", "开票净额"),
                                        ("聘用", "employee", "收款净额"),
                                        ("兼职", "parttime", "收款净额")):
            if st.get_type(name, c0) is None:
                st.add_type(name, conn=c0)
            st.set_role_code(name, role_code, conn=c0)
            st.set_invoice(name, True, conn=c0)
            st.set_net_basis(name, basis, conn=c0)
        c0.commit()
    finally:
        c0.close()

    # ===== 1. role_def 种子 =====
    roles = {code: label for code, label in st.list_roles()}
    check("role_def 4 种子", set(roles) == {"partner", "employee", "parttime", "other"}, f"got={roles}")
    check("partner=合伙", roles["partner"] == "合伙")
    check("other=其他", roles["other"] == "其他")

    # ===== 2. role 助手 =====
    check("role_code_of 缺失→other", st.role_code_of("", None) == "other"
          and st.role_code_of("不存在", None) == "other")
    attr = st.role_attr("partner", None)
    check("partner forbid_public_exclusive=1", attr["forbid_public_exclusive"] == 1)
    check("partner include_in_income_report=1", attr["include_in_income_report"] == 1)
    check("partner default_net_basis=开票净额", attr["default_net_basis"] == "开票净额")
    idr = st.identity_roles()
    check("identity_roles 含三身份", ("partner", "合伙") in idr and ("employee", "聘用") in idr
          and ("parttime", "兼职") in idr, f"got={idr}")
    items = st.person_type_combo_items()
    check("person_type_combo_items 首项未标", items[0] == ("未标", ""), f"got={items[:1]}")
    check("person_type_combo_items 含 partner", ("合伙", "partner") in items)
    lm = st.role_label_map()
    check("role_label_map 翻码", lm["partner"] == "合伙" and lm["other"] == "其他")

    # ===== 3. F1 改名安全：把"合伙"改名后，结算不变 =====
    conn = db.get_conn()
    try:
        # 新建一个角色=partner 的自定义类型，并改名（模拟"合伙"→"啊啊啊"）
        st.add_type("啊啊啊", conn=conn)
        conn.execute("UPDATE staff_type_def SET role_code='partner' WHERE name='啊啊啊'")
        st.set_invoice("啊啊啊", True, conn=conn)        # 进业务线（partner 角色）
        st.set_net_basis("啊啊啊", "开票净额", conn=conn)  # 合伙按开票净额计
        conn.execute("INSERT OR IGNORE INTO staff_roster(name) VALUES('张三')")
        conn.execute("INSERT OR IGNORE INTO staff_type_map(name, type_name, is_primary) VALUES('张三','啊啊啊',1)")
        conn.commit()
        check("改名前 role_code_of=partner", st.role_code_of("啊啊啊", conn) == "partner")

        # 一张开票 100 的蓝票（无收款 → 开票净额=100，收款净额=0）；
        # person_type 已是迁移后的角色码 'partner'
        conn.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount) "
                     "VALUES('INV1','2025-01-15',100)")
        conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount, person_type) "
                     "VALUES('INV1','张三',100,'partner')")
        conn.commit()
    finally:
        conn.close()

    # 汇总口径（person_type=None）：合伙人按开票净额 → income=100
    m_summary = ps.build_settlement(2025, person_type=None)
    inc_s = m_summary["张三"]["months"][1]["income"]
    check("汇总视图 income=100(开票净额)", abs(inc_s - 100.0) < 0.01, f"got={inc_s}")
    # 分身份视图（override=角色码 partner）：应解析到 partner → income=100（修复前=0）
    m_partner = ps.build_settlement(2025, person_type="partner")
    inc_p = m_partner["张三"]["months"][1]["income"]
    check("分身份(partner码) income=100", abs(inc_p - 100.0) < 0.01, f"got={inc_p}")
    # 再改名（把"啊啊啊"改成"新名"），收入仍按角色解析不变
    c2 = db.get_conn()
    try:
        st.rename_type("啊啊啊", "新合伙名", conn=c2)
    finally:
        c2.close()
    m_after = ps.build_settlement(2025, person_type=None)
    inc_after = m_after["张三"]["months"][1]["income"]
    check("改名后汇总 income 仍=100(F1)", abs(inc_after - 100.0) < 0.01, f"got={inc_after}")

    # ===== 4. 专属费用按分类级白名单校验（方案乙）=====
    c3 = db.get_conn()
    try:
        conn3 = c3
        # 费用类型「物业费」归入「专属费用」分类
        conn3.execute("INSERT INTO expense_cat(expense_type, category, sort_order) "
                      "VALUES('物业费','专属费用',99)")
        # 白名单 = 内置三类（合伙/聘用/兼职）；空 = 无限制（D5）
        ec.set_exclusive_types('专属费用', ['合伙', '聘用', '兼职'], conn3)
        # 王五：主类型=合伙（在白名单）→ 放行
        conn3.execute("INSERT OR IGNORE INTO staff_roster(name) VALUES('王五')")
        conn3.execute("INSERT OR IGNORE INTO staff_type_map(name, type_name, is_primary) "
                      "VALUES('王五','合伙',1)")
        conn3.commit()
        viol = ec.validate_exclusive(conn3, [
            {"expense_type": "物业费", "actual_handler": "张三"},   # 主类型 啊啊啊 不在白名单 → 拦
            {"expense_type": "物业费", "actual_handler": "王五"},   # 合伙 在白名单 → 放行
            {"expense_type": "物业费", "actual_handler": "李四"},   # 非花名册经办人 → 豁免放行
        ])
        check("专属费用白名单拦 张三", "张三(新合伙名)" in viol, f"got={viol}")
        check("白名单内 王五 放行", "王五" not in " ".join(viol), f"got={viol}")
        check("非花名册 李四 豁免放行", "李四" not in " ".join(viol), f"got={viol}")
        # D5：清空白名单 → 无限制，张三也放行
        ec.set_exclusive_types('专属费用', [], conn3)
        conn3.commit()
        viol2 = ec.validate_exclusive(conn3, [{"expense_type": "物业费", "actual_handler": "张三"}])
        check("D5 未指定=无限制", viol2 == [], f"got={viol2}")
    finally:
        c3.close()

    # ===== 5. person_type 过滤按角色码 =====
    c4 = db.get_conn()
    try:
        # 再插一个 employee 角色的经办人 + 发票（person_type 存角色码 'employee'）
        conn4 = c4
        conn4.execute("INSERT OR IGNORE INTO staff_roster(name) VALUES('李四')")
        conn4.execute("INSERT OR IGNORE INTO staff_type_map(name, type_name, is_primary) VALUES('李四','员工类型',1)")
        conn4.execute("UPDATE staff_type_def SET role_code='employee' WHERE name='员工类型'")
        st.set_invoice("员工类型", True, conn=conn4)
        conn4.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount) "
                      "VALUES('INV2','2025-02-10',50)")
        conn4.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount, person_type) "
                      "VALUES('INV2','李四',50,'employee')")
        conn4.commit()
    finally:
        c4.close()
    # 分身份(partner)视图：含张三(partner)、不含李四(employee) —— 证明按角色码过滤
    m_partner2 = ps.build_settlement(2025, person_type="partner")
    check("分身份(partner)含张三", "张三" in m_partner2, f"got={list(m_partner2)}")
    check("分身份(partner)不含李四(employee)", "李四" not in m_partner2, f"got={list(m_partner2)}")
    # 分身份(employee)视图：含李四、不含张三
    m_emp = ps.build_settlement(2025, person_type="employee")
    check("分身份(employee)含李四", "李四" in m_emp, f"got={list(m_emp)}")
    check("分身份(employee)不含张三(partner)", "张三" not in m_emp, f"got={list(m_emp)}")

    # 清理临时库
    if tmp.exists():
        tmp.unlink()

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
