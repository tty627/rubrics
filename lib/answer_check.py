"""答案程序化核验共享模块（s12L 判分 + s10L 反向校验共用）。

2026-08-14 修复（48 试点审计，docs/reports/AUDIT_48PILOT_PHASE4.md）：

1. option 正则 bug（q0179）：负向断言禁止字母后跟句点，`A.`/`A）` 永远不命中，
   强档明确写了 `\\boxed{\\text{A. }...}` 却被判未命中，8 分闸门清零。
   修复：断言放宽为只禁止字母数字前后邻接，句点/顿号不再阻断。
2. 短数字误命中（q0166）：canon='2' 全文本词边界匹配命中公式常数 `2π`。
   修复：短数字（≤2 位）改为**结论上下文匹配**——canon 前 12 字内必须有
   结论标记（答案/故/解得/=/result 等），且 canon 后不得紧跟 π/√/字母
   （公式常数特征）；上下文不命中时只回落到末尾 3 行再做词边界匹配。
3. 新增 has_correct_answer()：s10L 用它给对抗/弱档做反向校验——
   对抗档「最终结论不能等于标准答案」、gated 弱档不能把答案答对。
   只认结论区，正文过程里顺带提到正确答案不算（那正是对抗档该有的样子）。
"""
import re

_TRIM = re.compile(r'[\s　*`"\'“”‘’]+')
_ZH_PUNCT = str.maketrans('，；：（）［］｛｝＜＞', ',;:()[]{}<>')

# 结论标记：答案串通常紧跟在这些词/符号之后（20 字窗口内）
_MARK_NUM = r'(?:答案|故|所以|因此|解得|求得|可得|结果|等于|结论|=>|→|∴|=|＝)'
_MARK_OPT = r'(?:答案|故|所以|因此|结果|结论|=>|→|∴|=|＝)'
# 兜底匹配要求 canon 后跟行尾或标点（防「选项分析：A 是…」里的讨论性出现）
_PUNC_END = r'(?=[.。,，:;:：)）]|\s*$)'


def norm_txt(t):
    """轻量归一：只统一空白、装饰符号和中英标点，不删内容字符。

    旧版把逗号也删了，`0,1,1,1` 和 `0111` 等价——比对失去意义。只做安全规范化。
    """
    return _TRIM.sub('', str(t or '').translate(_ZH_PUNCT)).lower()


def concl_zone(text, frac=0.25, min_chars=120):
    """结论区 = 文本末尾约 25%（至少 120 字）。答案/结论通常写在最后。"""
    n = len(text or '')
    keep = max(int(n * frac), min_chars)
    return text[-keep:] if n > keep else (text or '')


def _last_lines(text, n=3):
    lines = [norm_txt(x) for x in re.split(r'[\r\n]+', text or '') if x.strip()]
    return lines[-n:] if lines else [norm_txt(text or '')]


def _pat_num(canon):
    """数字词边界匹配，后邻 π/√/字母 视为公式常数不命中。"""
    esc = re.escape(canon)
    return re.compile(rf'(?<![0-9A-Za-z.]){esc}(?![0-9A-Za-zπΠ√×·])', re.I)


def _pat_num_ctx(canon):
    """数字结论上下文：前 20 字内有结论标记，后邻非公式常数。"""
    esc = re.escape(canon)
    return re.compile(rf'{_MARK_NUM}[^\n]{{0,20}}?'
                      rf'(?<![0-9A-Za-z.]){esc}(?![0-9A-Za-zπΠ√×·])', re.I)


def _pat_opt_ctx(canon):
    esc = re.escape(canon)
    return re.compile(rf'{_MARK_OPT}[^\n]{{0,20}}?'
                      rf'(?<![0-9A-Za-z]){esc}(?![0-9A-Za-z])', re.I)


def _pat_line_end(canon, anti_letter):
    """行尾/标点兜底：canon 后必须是行尾或标点，才算「作为答案写出」。"""
    esc = re.escape(canon)
    tail = r'(?![0-9A-Za-zπΠ√×·])' if anti_letter else r'(?![0-9A-Za-z])'
    return re.compile(rf'(?<![0-9A-Za-z.]){esc}{tail}{_PUNC_END}', re.I)


def _search_lines(pat, text, n=3):
    return any(pat.search(x) for x in _last_lines(text, n))


def check_program(kind, canon, text):
    """判分用程序化核验。返回 (是否可判定, 是否命中)。

    numeric —— 长数字（>2 位）全文本词边界匹配（含公式常数后瞻）；
              短数字（≤2 位）只在结论区按「结论标记上下文」匹配，
              不中再回落末尾 3 行，防 canon='2' 命中公式常数 '2π'。
    option  —— 结论区上下文优先（答案/选/故…），不中再回落末尾 3 行
              宽松词边界（允许 A. / A)）。
    token   —— 短标识（IP、参数名、术语），正文里出现过就算。
    exact_text —— 逐行相等（滑窗支持多行答案）。
    其余 / 无 canon —— 不可判定，交 LLM。
    """
    canon = (canon or '').strip()
    if kind not in ('numeric', 'option', 'token', 'exact_text') or not canon:
        return False, False

    if kind == 'token':
        if len(canon) < 3:      # "1"/"是" 会随机命中，退回 LLM
            return False, False
        return True, norm_txt(canon) in norm_txt(text)

    if kind == 'exact_text':
        want = norm_txt(canon)
        if len(want) < 4:
            return False, False
        lines = [norm_txt(x) for x in re.split(r'[\r\n]+', text) if x.strip()]
        if want in lines:
            return True, True
        want_lines = [norm_txt(x) for x in re.split(r'[\r\n]+', canon) if x.strip()]
        if len(want_lines) > 1:
            k = len(want_lines)
            for i in range(len(lines) - k + 1):
                if lines[i:i + k] == want_lines:
                    return True, True
        return True, False

    if kind == 'option':
        z = norm_txt(concl_zone(text))
        if _pat_opt_ctx(canon).search(z):
            return True, True
        return True, _search_lines(_pat_line_end(canon, anti_letter=False), text)

    if len(canon) <= 2:
        z = norm_txt(concl_zone(text))
        if _pat_num_ctx(canon).search(z):
            return True, True
        return True, _search_lines(_pat_line_end(canon, anti_letter=True), text)
    return True, bool(_pat_num(canon).search(norm_txt(text)))


def has_correct_answer(kind, canon, text):
    """反向校验（s10L 用）：该回复的最终结论是否等于标准答案。

    只认结论区——过程里顺带提到正确答案不算（对抗档本来就该过程全、结论错）。
    返回 True = 造法失败（把答案答对了）。
    """
    canon = (canon or '').strip()
    if kind not in ('numeric', 'option', 'token', 'exact_text') or not canon:
        return False
    z = concl_zone(text)
    if kind == 'token':
        return len(canon) >= 3 and norm_txt(canon) in norm_txt(text)
    if kind == 'exact_text':
        want = norm_txt(canon)
        if len(want) < 4:
            return False
        return want in [norm_txt(x) for x in re.split(r'[\r\n]+', z) if x.strip()]
    if kind == 'option':
        z = norm_txt(concl_zone(text))
        return bool(_pat_opt_ctx(canon).search(z)
                    or _search_lines(_pat_line_end(canon, anti_letter=False), text))
    z = norm_txt(concl_zone(text))
    return bool(_pat_num_ctx(canon).search(z)
                or _search_lines(_pat_line_end(canon, anti_letter=True), text))
