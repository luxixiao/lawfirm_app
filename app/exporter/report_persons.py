"""列表式报表的**报表口径名单**与人员勾选过滤（年度结算表 / 开票收入表共用）。

为什么放exporter 层而不放 engine：
    这份名单服务的是**报表口径**（哪些人进这两张列表式报表），不是**结算口径**
    （个人结算总表按数据出现与否取人）。两者刻意分开，避免动到结算页既有行为。

名单来源：任一类型勾了「开票」或「报销」的人员（勾线即进，不区分身份）；
不过滤 is_active（停用功能已取消），但入职月份晚于当前月排除（按姓名 hire_month）。
本模块 SQL 与 hire_month 过滤逻辑 = 原 staff_income_exporter._staff_employees
（与 invoice_income_exporter._main_persons 逐行相同，故两处统一收敛到这里）。
"""
from __future__ import annotations

__all__ = ["list_report_persons", "apply_person_filter"]


def list_report_persons(conn, year: int, month: int) -> list:
    """报表名单：任一类型勾了「开票」或「报销」的人员（勾线即进，不区分身份）。

    离职不影响：不按 is_active 过滤；入职月份晚于当前月则排除（按姓名 hire_month）。
    conn 必传（本模块不开连接，由调用方负责生命周期；亦保 tests/test_conn_hygiene 绿）。
    """
    rows = conn.execute(
        "SELECT DISTINCT r.name, r.hire_month FROM staff_roster r "
        "JOIN staff_type_map m ON m.name = r.name "
        "JOIN staff_type_def t ON t.name = m.type_name "
        "WHERE t.is_invoice = 1 OR t.can_expense = 1 ORDER BY r.name"
    ).fetchall()
    cur = f"{year}-{month:02d}"
    out: list[str] = []
    for r in rows:
        hm = (r["hire_month"] or "").strip()
        if hm and hm > cur:
            continue  # 尚未入职
        out.append(r["name"])
    return out


def apply_person_filter(persons: list, selected) -> list:
    """纯函数：把名单按勾选取交集，**保持 persons 原序**。

    ⚠️ `None` 与 `[]` 语义**必须区分**（这是一处数据泄露的根因，勿再合并）：

    - ``selected is None``  → **未筛选**，原样返回全部（向后兼容：6 个导出函数的
      ``persons=None`` 默认值即走这条，等价于旧行为）。
    - ``selected == []``    → **用户勾选了 0 人**，如实返回空表。控件契约是
      ``selected_persons()`` 返回 list，空 list 就代表 0 人勾选；若这里再按「不过滤」
      处理，预览/导出会列出**全部人员**——summary 写着「未选择人员」而表格里全是他人的
      收入数据。当前只靠「导出按钮置灰」兜住，但置灰会被任何新入口绕过，必须从语义堵死。

    交集也可能为空（张三 3 月入职，1/2 月名单里没有他）——此时如实返回空表，
    绝不为了「看起来有数据」而把勾选扩成全量（导出 sheet 名/数量只由年月决定）。
    """
    if selected is None:
        return list(persons)
    keep = set(selected)
    return [p for p in persons if p in keep]
