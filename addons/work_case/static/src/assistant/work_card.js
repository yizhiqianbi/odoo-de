import { useService } from "@web/core/utils/hooks";

import { Component, onWillStart, proxy, t, useProps } from "@odoo/owl";

const KINDS = { decision: "等你拍板", proposal: "等你确认", message: "对外消息" };
const DONE = {
    decision: { done: "已批准", working: "已驳回", todo: "已关闭", blocked: "已关闭" },
    proposal: { done: "已执行", rejected: "已驳回", failed: "执行失败" },
    message: { done: "已发出", rejected: "没发", failed: "发送失败" },
};

/** A decision or a drafted action, in the chat with the digital employee; the buttons act as the person clicking. */
export class WorkCard extends Component {
    static template = "work_case.WorkCard";

    props = useProps({ messageId: t.number() });

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.kinds = KINDS;
        this.state = proxy({ data: null, choice: null, note: "", busy: false, error: "", sealed: "" });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.data = await this.orm.call("work.assistant", "card_data", [this.props.messageId]);
    }

    get d() {
        return this.state.data;
    }

    get choice() {
        return this.state.choice ?? this.d.recommendation;
    }

    get outcome() {
        const d = this.d;
        if (d.state === "rejected" && d.result.startsWith("撤回")) {
            return "已撤回";
        }
        return DONE[d.kind][d.state] || "已处理";
    }

    get approveLabel() {
        return { decision: "批准", proposal: "确认执行", message: "照发" }[this.d.kind];
    }

    when(ts) {
        if (!ts) {
            return "";
        }
        const d = new Date(`${ts.replace(" ", "T")}Z`);
        return `${d.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })} ${d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
    }

    setChoice(option) {
        this.state.choice = option;
    }

    setNote(ev) {
        this.state.note = ev.target.value;
    }

    openCase() {
        this.actionService.doAction({ type: "ir.actions.act_window", res_model: this.d.res_model, res_id: this.d.res_id,
                                      views: [[false, "form"]], target: "current" });
    }

    async decide(approve) {
        const d = this.d;
        const note = this.state.note.trim();
        this.state.error = "";
        if (!approve && !note) {
            this.state.error = "驳回时写一句为什么，数字员工会照着改。";
            return;
        }
        this.state.busy = true;
        try {
            if (d.model === "work.step") {
                await this.orm.write("work.step", [d.id], { human_note: note || false });
                await this.orm.call("work.step", approve ? "action_approve" : "action_reject", [[d.id]],
                                    approve ? { choice: this.choice || null } : {});
            } else {
                await this.orm.write("work.proposal", [d.id], { human_note: note || false });
                await this.orm.call("work.proposal", approve ? "action_confirm" : "action_reject", [[d.id]]);
            }
            this.state.sealed = approve ? "approved" : "returned";
            this.state.note = "";
            await this.load();
        } catch (err) {
            this.state.error = err.data?.message || err.message || String(err);
            await this.load();
        } finally {
            this.state.busy = false;
        }
    }
}
