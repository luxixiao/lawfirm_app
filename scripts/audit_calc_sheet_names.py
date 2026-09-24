"""计算表表名 / 跨表引用 存量审计（**只读**，只出报告不改数据）

背景：阶段5 C 方案把「表名 = Excel 工作表标题」对齐了。新数据入库时
`calc_sheet.validate_sheet_name` 已按 Excel 规则拦住，但**历史库**里可能躺着
一批在旧规则下合法、现在却会在导出时出问题的表名。本脚本把它们找出来并给建议。

检查项：
1. 表名 > 31 字符            → 导出时标题被截断，公式里的表名与 worksheet 标题漂移 → #REF!
2. 含 Excel 禁用字符 [] : * ? / \\ → 标题被替换成 `_`，同样漂移
3. 含空格 / 全角空格 / `!`   → 引用歧义（本应用约定禁用）
4. 单元格地址样式（A1/AB12） → 会被当成引用而非表名
5. 大小写不敏感重名          → Excel 表名不区分大小写
6. 跨表引用了**库里不存在的表** → 该公式永远算不出来，导出也是断链
7. 统计「需要引号才能引用的表名」（数字开头 / 含 - . 等）→ 只是提示，不是问题

用法：python scripts/audit_calc_sheet_names.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import DB_PATH  # noqa: E402
from app.engine.calc_formula import sheet_needs_quotes  # noqa: E402
from app.engine.calc_ref_rewrite import formula_sheet_refs  # noqa: E402
from app.engine.calc_sheet import (  # noqa: E402
    EXCEL_TITLE_MAX, _CELL_LIKE, _EXCEL_TITLE_BAD_RE, validate_sheet_name)

BAD_CHARS = "[]:*?/\\"


def main() -> int:
    if not DB_PATH.exists():
        print(f"库不存在：{DB_PATH}")
        return 1
    # 只读打开，杜绝任何误写
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, name, content FROM calc_sheet ORDER BY sheet_order, id").fetchall()
    except sqlite3.OperationalError as e:
        print(f"读取失败（表不存在？）：{e}")
        return 1

    if not rows:
        print("库内没有任何计算表，无需审计。")
        return 0

    names = [r["name"] for r in rows]
    lower = {}
    problems = []          # (级别, 表名, 问题, 建议)
    need_quote = []

    for r in rows:
        name = r["name"] or ""
        err = validate_sheet_name(name)
        if err:
            if len(name) > EXCEL_TITLE_MAX:
                advice = f"改名为 ≤{EXCEL_TITLE_MAX} 字符（可用「重命名表」，跨表引用会自动同步）"
            elif any(ch in name for ch in BAD_CHARS):
                advice = "改名去掉 " + BAD_CHARS + " 等 Excel 禁用字符"
            elif " " in name or "\u3000" in name:
                advice = "改名去掉空格（引用时会产生歧义）"
            elif "!" in name:
                advice = "改名去掉 !（它是引用分隔符）"
            elif _CELL_LIKE.match(name):
                advice = "改名，避免与单元格地址混淆"
            else:
                advice = "按提示改名"
            problems.append(("ERROR", name, err, advice))
        elif _EXCEL_TITLE_BAD_RE.search(name) or len(name) > EXCEL_TITLE_MAX:
            problems.append(("ERROR", name, "标题会被 _safe_title 改写",
                             "改名，使表名 == Excel 标题"))
        if sheet_needs_quotes(name):
            need_quote.append(name)
        k = name.casefold()
        if k in lower:
            problems.append(("ERROR", name, f"与「{lower[k]}」大小写重名",
                             "改名，Excel 表名不区分大小写"))
        else:
            lower[k] = name

    # 跨表引用是否存在悬空
    dangling = {}
    for r in rows:
        try:
            import json
            content = json.loads(r["content"] or "{}")
        except Exception:
            problems.append(("WARN", r["name"], "content 无法解析（损坏）",
                             "按 P0-4 流程处理：库外备份 + 只读锁定"))
            continue
        for key, cell in (content.get("cells") or {}).items():
            raw = cell.get("raw") if isinstance(cell, dict) else None
            if not isinstance(raw, str) or not raw.startswith("="):
                continue
            for s in formula_sheet_refs(raw):
                if s not in names:
                    dangling.setdefault((r["name"], s), []).append(key)

    print("=" * 72)
    print(f"计算表存量审计　库：{DB_PATH}")
    print(f"共 {len(rows)} 张表：{names}")
    print("=" * 72)

    if not problems and not dangling:
        print("\n✅ 未发现需要处理的存量问题。")
    if problems:
        print(f"\n【表名问题】{len(problems)} 条")
        for lvl, name, why, advice in problems:
            print(f"  [{lvl}] {name!r}：{why}\n        建议：{advice}")
    if dangling:
        print(f"\n【悬空跨表引用】{len(dangling)} 组（公式引用了库里不存在的表）")
        for (src, target), keys in sorted(dangling.items()):
            print(f"  表 {src!r} 引用了不存在的表 {target!r}（{len(keys)} 处，"
                  f"如 {keys[:3]}）\n        建议：补表，或改掉这些引用")
    if need_quote:
        print(f"\n【提示】{len(need_quote)} 张表引用时需要单引号（正常，引擎会自动加）："
              f"{need_quote}")

    print("\n（本脚本只读，未做任何修改。）")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
