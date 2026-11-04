"""Decides whether an AI message may go to the outside party now or must wait for the person who decides.

A message is held when any one of these says so, so a wrong label from the AI alone cannot let a commitment out:
the AI's own label, an independent reviewer that sees only the message, anything the case marks confidential,
and figures (amounts, dates) that Odoo does not have.
"""
import json
import re

from . import config, llm

MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DATE_PATTERNS = (
    (re.compile(r"\b(20\d\d)[-/.](\d{1,2})[-/.](\d{1,2})\b"), lambda m: (m[2], m[3])),
    (re.compile(rf"\b{MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I), lambda m: (MONTHS[m[1][:3].lower()], m[2])),
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{MONTH}", re.I), lambda m: (MONTHS[m[2][:3].lower()], m[1])),
    (re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]"), lambda m: (m[1], m[2])),
    (re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])"), lambda m: (m[1], m[2])),
)
NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?")

REVIEW_PROMPT = """你在公司做对外消息审核。下面是一条准备由公司的 AI 员工直接发给外部联系人（客户、供应商或求职者）的消息。
判断它是否包含公司承诺，也就是会让公司承担义务或向对方让步的内容：报价或改价、折扣、数量变化、交期或装运日期的承诺、
付款条件、接受或拒绝信用证 / 单据 / 改证条件、索赔赔偿、确认订单或合同条款、
承诺付款或付款日期、接受或拒绝发票和差价、同意扣款或对账结论、
录用或不录用、薪资福利、入职日期、职级，以及答应对方的任何请求。
单纯转述已有事实、汇报进度、向对方索要资料、请对方处理他们自己那边的事、提供可选的时间让对方挑，不算承诺。
只输出 JSON：{"commitment": true 或 false, "reason": "一句中文"}"""


def figures(text):
    """Dates as 'MM-DD' and numbers worth checking (100 or more, or with decimals), normalized."""
    found, rest = set(), text
    for pattern, month_day in DATE_PATTERNS:
        for m in pattern.finditer(rest):
            month, day = (int(v) for v in month_day(m))
            if 1 <= month <= 12 and 1 <= day <= 31:
                found.add(f"date {month:02d}-{day:02d}")
        rest = pattern.sub(" ", rest)
    for m in NUMBER.finditer(rest):
        whole, frac = m[1].replace(",", ""), (m[2] or "").rstrip("0")
        if frac or int(whole) >= 100:
            found.add(f"num {int(whole)}{'.' + frac if frac else ''}")
    return found


def known_figures(context):
    """Everything Odoo knows about the case, minus the group chat itself so the outside party cannot plant figures."""
    facts = {k: v for k, v in (context or {}).items() if k not in ("chat", "confidential", "identity")}
    return figures(json.dumps(facts, ensure_ascii=False))


async def review(text, kind, context):
    """Returns (send_now, reasons)."""
    reasons = []
    if kind == "commitment":
        reasons.append("AI 自己标为承诺")
    lowered = text.lower()
    leaked = [c for c in (context or {}).get("confidential", []) if c and str(c).lower() in lowered]
    if leaked:
        reasons.append("提到了不能对外的信息：" + "、".join(f"「{c}」" for c in leaked))
    unknown = sorted(figures(text) - known_figures(context))
    if unknown:
        shown = [u.split(" ", 1)[1] for u in unknown]
        reasons.append("出现 Odoo 里没有的数字或日期：" + "、".join(shown))
    try:
        reply = await llm.chat([{"role": "system", "content": REVIEW_PROMPT}, {"role": "user", "content": text}],
                               model=config.REVIEW_MODEL, temperature=0)
        verdict = llm.parse_json(reply.get("content"))
    except RuntimeError:
        verdict = None
    if verdict is None:
        reasons.append("审核没有给出结果，按承诺处理")
    elif verdict.get("commitment"):
        reasons.append("审核判为承诺：" + str(verdict.get("reason", "")))
    return not reasons, reasons
