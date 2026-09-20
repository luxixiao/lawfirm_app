"""皮肤契约：把「加第二套皮肤不会漏」这件事变成可回归的断言。

背景（2026-09-20）：
用户要求「取消夜间模式相关代码，只保留一个皮肤」，但**明确说后期可能还要做皮肤切换**。
当时审计发现真正的成本不在 style.py 那 18 行 _dark()，而在**散落 18 个视图 / 72 行硬编码
十六进制色号**——它们在加皮肤时不会自动跟着变，视觉上会「浅底配深字」或反过来。
本批已把这些色号全部收口成 style.PALETTES 的语义 token，并由本测试看住三条：

  A. 调色板完备性：所有 PALETTES 的 key 集合必须完全一致（加皮肤不许少 key）。
  B. 引用完备性：源码里出现的每个 token 名（style.qcolor("x") / palette()["x"]）
     必须存在于**每一个**调色板（防手滑写错名字、防加皮肤时漏定义）。
  C. 硬编码禁令：app/ui/ 下除 style.py 外，代码里不得出现十六进制色号或
     QColor(r,g,b) 字面量；确需例外（半透明叠加、Windows 系统色）必须在 ALLOWLIST 里
     登记并写明理由，且**登记项必须真的命中**（否则说明白名单过期，会掩盖新违规）。

运行：python tests/test_skin_contract.py
"""
import io
import os
import re
import sys
import tokenize
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ui import style  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


# ---------------------------------------------------------------------------
# 依赖 style.py 自身色号的视图（除它以外的地方都不许硬编码）
# ---------------------------------------------------------------------------
SOURCE_EXEMPT = {"app/ui/style.py"}

# 「允许出现的硬编码色」白名单：{文件: [(正则, 理由), ...]}
# 规则：白名单项必须**至少命中一次**，否则判 FAIL（防止过期白名单变成后门）。
ALLOWLIST = {
    "app/ui/main_window.py": [
        (r"QColor\(0,\s*0,\s*0,\s*(?:0|26|51)\)",
         "纯 alpha 叠加（透明/淡黑 hover、按下），与皮肤色号无关"),
        (r"QColor\(232,\s*17,\s*35\)",
         "Windows 关闭按钮 hover 底色（系统语义色，换皮肤也不该变）"),
        (r"QColor\(241,\s*112,\s*122\)",
         "Windows 关闭按钮 pressed 底色（系统语义色，换皮肤也不该变）"),
    ],
    "app/ui/table_features.py": [
        (r"QColor\(0,\s*0,\s*0,\s*0\)",
         "全透明底：生成漏斗图标 QPixmap 的空白画布"),
    ],
}

HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
QCOLOR_RGB_RE = re.compile(r"QColor\(\s*\d+\s*,\s*\d+\s*,\s*\d+")
# 视图侧的 token 引用：style.qcolor("x") / style.palette()["x"] / style.palette().get("x")
TOKEN_REF_RE = re.compile(
    r"""(?:qcolor\(\s*["']([A-Za-z_]\w*)["']"""
    r"""|palette\(\)\s*\[["']([A-Za-z_]\w*)["']\]"""
    r"""|palette\(\)\.get\(\s*["']([A-Za-z_]\w*)["'])"""
)
# style.py 内部 build_qss 用的是局部形参 p，形如 {p['bg']} / p.get('x', ...)
# （style.py 自身要被排除在 TOKEN_REF_RE 之外：它的 docstring 里举例写了 qcolor("x")）
QSS_REF_RE = re.compile(
    r"""(?:p\[["']([A-Za-z_]\w*)["']\]|\bp\.get\(\s*["']([A-Za-z_]\w*)["'])"""
)
# 「把 palette() 存进局部变量再用」的间接引用：`_p = style.palette()` 之后 `_p["notice_bg"]`。
# 这类写法绕过 TOKEN_REF_RE，必须单独看住（否则改名只在打开那一页时才炸 KeyError）。
# 只扫**出现过 `= style.palette()` 赋值**的文件，避免把无关模块里 `p["invoice_no"]`
# 这类普通字典下标误判成调色板 token。
ALIAS_REF_RE = re.compile(r"""\b(?:_p|pal|p)\[\s*["']([A-Za-z_]\w*)["']\s*\]""")
ALIAS_GATE_RE = re.compile(r"=\s*style\.palette\(\)")


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _iter_ui_sources():
    for p in sorted((ROOT / "app").rglob("*.py")):
        yield p, _rel(p)


def _comment_spans(src: str):
    """返回 [(line_idx0, col_start, col_end), ...]：tokenize 识别的注释区间。"""
    spans = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                spans.append((tok.start[0] - 1, tok.start[1], tok.end[1]))
    except (tokenize.TokenError, IndentationError):
        pass
    return spans


def _blank_comments(lines, spans):
    out = list(lines)
    for ln, cs, ce in spans:
        if 0 <= ln < len(out):
            out[ln] = out[ln][:cs] + " " * (ce - cs) + out[ln][ce:]
    return out


def _string_literals(src: str):
    """返回 [(line_no, 文本), ...]：源码里的字符串字面量（不含注释）。"""
    res = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.STRING:
                res.append((tok.start[0], tok.string))
    except (tokenize.TokenError, IndentationError):
        pass
    return res


def main() -> int:
    # ---------- A. 调色板完备性 ----------
    check("至少有一套调色板", bool(style.PALETTES), list(style.PALETTES))
    base_name = style.DEFAULT_SKIN
    check("默认皮肤在 PALETTES 里", base_name in style.PALETTES, base_name)
    base_keys = set(style.PALETTES[base_name])
    check("默认调色板非空", len(base_keys) > 20, len(base_keys))
    for name, pal in style.PALETTES.items():
        check(f"调色板 key 集合一致：{name}", set(pal) == base_keys,
              f"多={sorted(set(pal) - base_keys)} 少={sorted(base_keys - set(pal))}")
    # 每个色号都得是合法颜色
    bad = []
    for name, pal in style.PALETTES.items():
        for k, v in pal.items():
            if not (isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v.strip())):
                bad.append(f"{name}.{k}={v!r}")
    check("所有色号都是 6 位十六进制", not bad, bad)

    # ---------- B. 引用完备性（源码出现的 token 必须存在于每个调色板）----------
    referenced = {}
    for p, rel in _iter_ui_sources():
        if rel in SOURCE_EXEMPT:
            continue          # style.py 的 docstring 里举了 qcolor("x") 的例子
        src = p.read_text(encoding="utf-8")
        for m in TOKEN_REF_RE.finditer(src):
            tok = m.group(1) or m.group(2) or m.group(3)
            referenced.setdefault(tok, set()).add(rel)
    # QSS 模板侧（style.py 的 build_qss 形如 {p['bg']}）
    style_src = (ROOT / "app/ui/style.py").read_text(encoding="utf-8")
    for m in QSS_REF_RE.finditer(style_src):
        referenced.setdefault(m.group(1) or m.group(2), set()).add("app/ui/style.py")
    # 间接引用侧（`_p = style.palette()` → `_p["notice_bg"]`）
    indirect_files = []
    for p, rel in _iter_ui_sources():
        if rel in SOURCE_EXEMPT:
            continue
        src = p.read_text(encoding="utf-8")
        if not ALIAS_GATE_RE.search(src):
            continue
        indirect_files.append(rel)
        for m in ALIAS_REF_RE.finditer(src):
            referenced.setdefault(m.group(1), set()).add(rel)
    check("间接引用扫描确实覆盖到文件（防门控失效假绿）",
          len(indirect_files) >= 4, indirect_files)
    check("源码里确实有 token 引用（防正则失效假绿）",
          len(referenced) >= 15, sorted(referenced))
    missing = {t: sorted(f) for t, f in referenced.items() if t not in base_keys}
    check("所有被引用的 token 都在调色板里定义", not missing, missing)
    per_pal_missing = {}
    for name, pal in style.PALETTES.items():
        lack = sorted(t for t in referenced if t not in pal)
        if lack:
            per_pal_missing[name] = lack
    check("每个调色板都覆盖了被引用的 token", not per_pal_missing, per_pal_missing)

    # 有引用但从未用过的 token（提示性，不算失败）——只打印，便于清理
    unused = sorted(k for k in base_keys if k not in referenced)
    print(f"[INFO] 调色板里未被源码引用的 token（{len(unused)}）：{unused}")

    # ---------- C. 硬编码禁令 ----------
    offenders = []
    allow_hits = {f: [0] * len(items) for f, items in ALLOWLIST.items()}
    for p, rel in _iter_ui_sources():
        if rel in SOURCE_EXEMPT:
            continue
        src = p.read_text(encoding="utf-8")
        spans = _comment_spans(src)
        code_lines = _blank_comments(src.splitlines(), spans)

        # C1 字符串字面量里的十六进制色号（注释已在 code_lines 里抹掉，不会被扫到）
        for ln, lit in _string_literals(src):
            for m in HEX_RE.finditer(lit):
                offenders.append((rel, ln, m.group(0), "字符串字面量"))
        # C2 QColor(r, g, b[, a]) 字面量
        for i, line in enumerate(code_lines):
            for m in QCOLOR_RGB_RE.finditer(line):
                frag = m.group(0)
                hit = False
                for idx, (pat, _reason) in enumerate(ALLOWLIST.get(rel, [])):
                    if re.search(pat, line):
                        allow_hits[rel][idx] += 1
                        hit = True
                        break
                if not hit:
                    offenders.append((rel, i + 1, frag, "QColor(r,g,b)"))

    check("app/ui 下无未登记的硬编码色号", not offenders, offenders[:12])

    stale = []
    for rel, items in ALLOWLIST.items():
        for idx, (_pat, reason) in enumerate(items):
            if allow_hits[rel][idx] == 0:
                stale.append(f"{rel}: 未命中 → {reason}")
    check("白名单没有过期项（每项都真的命中）", not stale, stale)

    # ---------- D. 运行时行为 ----------
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])

    check("available_skins 只含注册皮肤",
          sorted(k for k, _ in style.available_skins()) == sorted(style.SKINS),
          style.available_skins())
    style.apply_skin(app, "no_such_skin_xxx")
    check("未知皮肤名回退到默认", style.current_skin() == style.DEFAULT_SKIN,
          style.current_skin())
    style.apply_skin(app, style.DEFAULT_SKIN)

    qc = style.qcolor("neg_fg")
    check("qcolor 返回颜色值", qc.isValid(), qc.name())
    check("qcolor 与调色板一致",
          qc.name().lower() == style.PALETTES[style.DEFAULT_SKIN]["neg_fg"].lower(),
          qc.name())
    try:
        style.qcolor("definitely_not_a_token")
        raised = False
    except KeyError:
        raised = True
    check("qcolor 遇到未定义 token 直接抛 KeyError（不静默变黑）", raised)

    # 切换皮肤后 palette() 真的换了一份（用临时副本验证机制还在）
    # 探针挑 `text`：它既在 QSS 模板里（{p['text']}）又被 qcolor() 取，两条链路都能验到。
    style.PALETTES["__probe__"] = dict(style.PALETTES[style.DEFAULT_SKIN],
                                      text="#000001")
    style.SKINS["__probe__"] = {"label": "探针", "palette": "__probe__"}
    try:
        style.apply_skin(app, "__probe__")
        check("加皮肤后 qcolor 跟着换色",
              style.qcolor("text").name().lower() == "#000001",
              style.qcolor("text").name())
        check("加皮肤后 QSS 也重建", "#000001" in app.styleSheet())
    finally:
        style.PALETTES.pop("__probe__", None)
        style.SKINS.pop("__probe__", None)
        style.apply_skin(app, style.DEFAULT_SKIN)
    check("探针皮肤已清理", "__probe__" not in style.PALETTES
          and "__probe__" not in style.SKINS)
    check("清理后恢复默认色",
          style.qcolor("neg_fg").name().lower()
          == style.PALETTES[style.DEFAULT_SKIN]["neg_fg"].lower())
    check("清理后 QSS 不留探针色", "#000001" not in app.styleSheet())

    print(f"\n{OK}/{OK + len(FAILS)} passed")
    if FAILS:
        print("FAILED: " + " | ".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
