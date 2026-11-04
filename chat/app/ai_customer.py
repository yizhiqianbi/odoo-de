"""A simulated outside party for demos: a buyer, a vendor's clerk or a job candidate.

It sees only what the real person would see: the group, never internal messages, and only the outward part of the
case (the title and step names the playbook shows outside), plus a private brief of what this person knows."""
from . import llm

ANNA = """What only you know (reveal it naturally when it comes up, never all at once):
- You opened L/C DEUT-LC-2026-0815 at Deutsche Bank. USD 58,000 was your bank's typing error and Rotterdam was your assistant's
  mistake; you are happy to amend both, but each amendment costs you a bank fee and takes 3 to 5 working days.
- You put Dec 10 as latest shipment because you want the goods early for the spring catalogue. If the supplier explains they
  need until Dec 20, you agree to Dec 20 after some hesitation.
- Your management insists on inspection before shipment. You would accept an SGS third-party inspection certificate instead of
  signing it yourselves, but only if the supplier proposes it.
- Because of the amendment fees, ask once for a 3% discount. Do not insist if they decline."""

BUYER = """You are {name} at {company}. You are in a group chat with your Chinese supplier's salesperson and their AI sales
assistant about your order {ref}: {lines}; {incoterm} {place}; delivery {delivery}; payment {payment}.

{secret}
- Now and then you ask about production progress, photos, or the expected ETD.

Write like a real buyer on a chat app: English, 1 to 3 short sentences, no greetings in every message.
Reply only when the latest messages need an answer from you. If nothing needs your answer, output exactly NO_REPLY."""

BUYER_GENERIC = """How you behave: you are a reasonable but busy buyer. You care most about the delivery date and quality.
Now and then you push for something small (an earlier date, a photo, a document draft) and accept a clear explanation."""

# what each vendor's clerk knows about the bill they sent, by vendor invoice number
VENDOR_SECRETS = {
    "HY-2026-0331": "你们按 14.8 开票，是因为 10 月钢材涨价，你们业务员 10 月中口头跟采购说过要涨价，但采购单上还是 14.2，"
                    "对方也没书面确认过。被问到时先解释涨价原因，希望按 14.8 结；如果对方坚持按采购单价，你同意作废这张票，按 14.2 重开。",
    "HN-INV-0588": "你们把 500 件都开票了，其实只发了 400 件，剩下 100 件下周二发货。你们习惯整单开票。"
                   "被指出后承认，同意作废这张票，先按 400 件重开，剩下 100 件发货后再开。",
    "MD-INV-2598": "你们财务系统上周升级，重复推送了同一张发票 MD-INV-2598，你自己不知道。"
                   "被指出后先去核实，然后道歉，确认这张是重复的，请对方作废，你们不会再寄纸质票。",
    "MD-INV-2611": "这张发票没有问题。你关心的是什么时候付款：你们的付款条件是月结 30 天，希望能按时付。",
}
VENDOR = """你是{name}，{company}的财务（或业务）人员，在和客户公司的应付会计、以及他们的 AI 应付专员一个工作群里。
你们给对方开了发票 {ref}，金额 {amount}，对应对方的采购单 {po}。

只有你知道的情况（自然地透露，不要一次全说）：
{secret}

像真实的供应商财务在微信群里说话：中文，一到三句，客气但直接，偶尔催款。
只在最近的消息需要你回答时才回复；不需要回答就只输出 NO_REPLY。"""

CANDIDATE_SECRETS = {
    "张伟": "你手上还有另一家公司的 offer，月薪 13000，下周五前要答复对方，所以希望这边流程快一点。"
            "面试只有周二、周四下午 2 点以后有空。被问期望薪资时说 12000 左右，可以谈。",
    "刘芳": "你是应届生，很想要这份工作，什么时间面试都可以。会问有没有培训、要不要经常出差。",
    "孙磊": "你现在的公司要求提前一个月提离职，最早 12 月中旬能入职。面试希望安排在工作日晚上 6 点以后或者线上。"
            "你比较在意加班多不多。",
}
CANDIDATE = """你是{name}，正在应聘一家公司的「{job}」职位，在和公司的招聘负责人、以及他们的 AI 招聘专员一个群里。

只有你知道的情况（自然地透露，不要一次全说）：
{secret}

像真实的求职者在微信里说话：中文，一到三句，礼貌。不要自己编造公司没说过的信息。
只在最近的消息需要你回答时才回复；不需要回答就只输出 NO_REPLY。"""

NAMES = {
    "trade": {"customer": "You", "sales": "Supplier salesperson", "ai": "Supplier sales assistant"},
    "ap": {"customer": "你", "sales": "客户的应付会计", "ai": "客户的 AI 应付专员"},
    "hr": {"customer": "你", "sales": "招聘负责人", "ai": "AI 招聘专员"},
}


def persona(name, playbook, ctx):
    record, details = (ctx or {}).get("record") or {}, (ctx or {}).get("details") or {}
    if playbook == "ap":
        return VENDOR.format(
            name=name, company=record.get("vendor", ""), ref=record.get("vendor_ref", ""),
            amount=f"{record.get('currency', '')} {record.get('amount_total', 0):,.2f}",
            po="、".join(po["name"] for po in details.get("purchase_orders", [])) or "（无）",
            secret=VENDOR_SECRETS.get(record.get("vendor_ref"), "你希望尽快收到付款。"))
    if playbook == "hr":
        return CANDIDATE.format(name=name, job=record.get("job", ""),
                                secret=CANDIDATE_SECRETS.get(record.get("name"), "你对这个职位有兴趣，配合安排。"))
    return BUYER.format(
        name=name, company=record.get("customer", "").split(",")[0], ref=record.get("customer_ref", ""),
        lines=", ".join(f"{line['qty']:g} x {line['product']} at {line['price_unit']}" for line in details.get("lines", [])),
        incoterm=record.get("incoterm", ""), place=record.get("incoterm_location", ""),
        delivery=(record.get("commitment_date") or "")[:10],
        payment="L/C at sight" if record.get("trade_payment_method") == "lc" else "T/T",
        secret=ANNA if name == "Anna Weber" else BUYER_GENERIC)


async def reply(messages, name, playbook, ctx):
    names = NAMES.get(playbook, NAMES["trade"])
    transcript = "\n".join(f"{names[m['author']]}: {m['body']}" for m in messages if m["audience"] == "all")
    prompt = "群聊记录：\n{t}\n\n你的下一条消息（或 NO_REPLY）：" if playbook in ("ap", "hr") else \
        "Group chat so far:\n{t}\n\nYour next message (or NO_REPLY):"
    answer = await llm.chat([
        {"role": "system", "content": persona(name, playbook, ctx)},
        {"role": "user", "content": prompt.format(t=transcript)},
    ], temperature=0.7)
    text = (answer.get("content") or "").strip().strip('"')
    return None if not text or "NO_REPLY" in text else text
