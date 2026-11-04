import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

import { Component, onMounted, onWillDestroy, onWillStart, proxy, useProps } from "@odoo/owl";

const TIERS = { R0: "只读", R1: "内部记录", R2: "对外说话", R3: "Odoo 动作" };
const STATES = { todo: "未开始", working: "进行中", waiting: "等人决定", blocked: "受阻", done: "已完成" };
const KINDS = { decision: "流程决定", proposal: "Odoo 动作", message: "对外消息" };

/** The digital employees' workbench: what waits on me, each playbook as a board, and the roster of digital employees. */
export class Workbench extends Component {
    static template = "work_case.Workbench";

    props = useProps(standardActionServiceProps);

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.tiers = TIERS;
        this.states = STATES;
        this.kinds = KINDS;
        this.state = proxy({
            tab: this.props.action.params?.tab || "home",
            data: null, notes: {}, choices: {}, busy: {}, sealed: {}, errors: {}, open: {},
        });
        onWillStart(() => this.load());
        onMounted(() => { this.timer = setInterval(() => this.load(true), 20000); });
        onWillDestroy(() => clearInterval(this.timer));
    }

    async load(quiet = false) {
        const typing = Object.values(this.state.notes).some((v) => v);
        if (quiet && typing) {
            return;
        }
        this.state.data = await this.orm.call("work.playbook", "workbench_data", []);
    }

    key(item) {
        return `${item.kind}-${item.id}`;
    }

    when(ts) {
        if (!ts) {
            return "";
        }
        const d = new Date(`${ts.replace(" ", "T")}Z`);
        return `${d.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })} ${d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
    }

    setTab(tab) {
        this.state.tab = tab;
    }

    openRecord(model, id) {
        this.actionService.doAction({ type: "ir.actions.act_window", res_model: model, res_id: id,
                                      views: [[false, "form"]], target: "current" });
    }

    openLedger(extra = {}) {
        this.actionService.doAction("work_case.work_action_action", { additionalContext: extra });
    }

    choice(item) {
        return this.state.choices[this.key(item)] ?? item.recommendation;
    }

    async decide(item, approve) {
        const key = this.key(item);
        const note = (this.state.notes[key] || "").trim();
        this.state.errors[key] = "";
        if (!approve && !note) {
            this.state.errors[key] = "驳回时写一句为什么，AI 会照着改。";
            return;
        }
        this.state.busy[key] = true;
        try {
            if (item.kind === "decision") {
                await this.orm.write("work.step", [item.id], { human_note: note || false });
                await this.orm.call("work.step", approve ? "action_approve" : "action_reject", [[item.id]],
                                    approve ? { choice: this.choice(item) || null } : {});
            } else {
                await this.orm.write("work.proposal", [item.id], { human_note: note || false });
                await this.orm.call("work.proposal", approve ? "action_confirm" : "action_reject", [[item.id]]);
            }
            this.state.sealed[key] = approve ? "approved" : "returned";
            this.state.notes[key] = "";
            setTimeout(() => this.load(), 1400);
        } catch (err) {
            this.state.errors[key] = err.data?.message || err.message || String(err);
        } finally {
            this.state.busy[key] = false;
        }
    }

    setNote(item, ev) {
        this.state.notes[this.key(item)] = ev.target.value;
    }

    setChoice(item, option) {
        this.state.choices[this.key(item)] = option;
    }

    toggle(key) {
        this.state.open[key] = !this.state.open[key];
    }

    tierWidth(row, tier) {
        const total = Object.values(row.tiers).reduce((a, b) => a + b, 0) || 1;
        return `width: ${(row.tiers[tier] / total) * 100}%`;
    }

    get summary() {
        const d = this.state.data;
        const waiting = d.waiting.length ? `${d.waiting.length} 件事等你拍板` : "现在没有等你拍板的事";
        const refused = d.refused_today ? `，其中 ${d.refused_today} 次被系统拦下` : "";
        return `${d.user}：${waiting}（也会发到「讨论」里你和数字员工的私聊）。今天数字员工在 Odoo 里做了 ${d.today} 件事${refused}。`;
    }
}

registry.category("actions").add("work_case.workbench", Workbench);
