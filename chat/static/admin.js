const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "value") node.value = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) if (child != null && child !== false) node.append(child);
  return node;
};

const state = { token: sessionStorage.getItem("admin-token"), data: null, selected: "root", filter: "all", envelopes: new Map() };
const KIND = { root: "Root", assistant: "业务员助理", customer: "客户 agent" };
const VERB = { ask: "提问", task: "派任务", read: "读取", answer: "回答", report: "汇报" };
const STATUS = { open: "待处理", answered: "已回复", denied: "被拒绝", delivered: "已送达" };

async function api(path, body, method) {
  const res = await fetch(path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.token || ""}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail?.[0]?.msg || data.detail || `请求失败（${res.status}）`);
  return data;
}

async function login() {
  const data = await api("/api/login", { username: "admin" });
  state.token = data.token;
  sessionStorage.setItem("admin-token", data.token);
}

async function load() {
  state.data = await api("/api/admin/overview");
  state.envelopes = new Map(state.data.envelopes.map((e) => [e.id, e]));
  render();
}

const level = (n) => `${n} ${state.data.levels[n]}`;
const agentOf = (key) => state.data.agents.find((a) => a.key === key);
const groupOf = (customer) => state.data.groups.find((g) => g.customer_key === customer);
function label(target) {
  if (target === "*") return "全部（公司级资源和所有客户）";
  if (target === "agent:*") return "所有 agent";
  if (target.startsWith("customer:")) { const g = groupOf(target.slice(9)); return g ? `客户 ${g.title}（${g.order_name}）` : target; }
  if (target.startsWith("resource:")) {
    const [name, customer] = target.slice(9).split("@");
    const r = state.data.resources[name];
    return `${r ? r.title : name}${customer ? `，${groupOf(customer)?.title || customer}` : ""}`;
  }
  return state.data.names[target] || target;
}

function render() {
  renderTree();
  renderDetail();
  renderLog();
}

// the organisation, drawn from the grant table: an assistant's children are the customer agents it may task

function renderTree() {
  const { agents, grants } = state.data;
  const node = (a, note) => el("button", {
    type: "button", class: `node ${a.kind} ${a.enabled ? "" : "off"} ${a.status?.busy ? "busy" : ""}`,
    "aria-current": String(a.key === state.selected), onclick: () => { state.selected = a.key; render(); },
  }, el("span", { class: "lvl", title: `信息级别上限 ${level(a.clearance)}` }, `L${a.clearance}`),
     el("strong", { title: a.name }, a.kind === "customer" ? a.name.replace(/ 客户 agent$/, "") : a.name),
     note && el("span", { class: "note" }, note));
  const tasks = (key) => grants.filter((g) => g.subject === key && g.verb === "task").map((g) => g.target);
  const root = agents.find((a) => a.kind === "root");
  const shared = (c) => agents.filter((a) => a.kind === "assistant" && tasks(a.key).includes(`agent:${c.key}`)).length;
  const children = (a) => agents.filter((c) => c.kind === "customer" && tasks(a.key).includes(`agent:${c.key}`));
  const orphans = agents.filter((c) => c.kind === "customer" && !shared(c));
  $("#tree").replaceChildren(el("ul", {},
    el("li", {}, node(root, "管理员"), el("ul", {},
      ...agents.filter((a) => a.kind === "assistant").map((a) => el("li", {}, node(a),
        el("ul", {}, children(a).map((c) => el("li", {}, node(c, shared(c) > 1 ? "共管" : null)))))),
      orphans.length ? el("li", {}, el("span", { class: "orphan" }, "没有助理能派任务的客户 agent"),
                          el("ul", {}, orphans.map((c) => el("li", {}, node(c))))) : null))));
}

// one agent: its clearance, its grants, and a box to give it a task

function renderDetail() {
  const a = agentOf(state.selected);
  if (!a) { $("#detail").replaceChildren(); return; }
  const { grants, agents, groups, resources } = state.data;
  const mine = grants.filter((g) => g.subject === a.key);
  const save = (fn) => async (e) => { try { await fn(e); await load(); } catch (err) { alert(err.message); } };
  const levelSelect = (value, onchange) => el("select", { onchange },
    [0, 1, 2, 3].map((n) => el("option", { value: n, selected: n === value }, level(n))));

  const targets = (verb) => verb === "read"
    ? [["*", label("*")], ...groups.map((g) => [`customer:${g.customer_key}`, label(`customer:${g.customer_key}`)])]
    : [["agent:*", "所有 agent"], ...agents.filter((x) => x.key !== a.key).map((x) => [`agent:${x.key}`, x.name])];
  const verb = el("select", {}, ["ask", "task", "read"].map((v) => el("option", { value: v }, VERB[v])));
  const target = el("select", {});
  const fill = () => target.replaceChildren(...targets(verb.value).map(([v, t]) => el("option", { value: v }, t)));
  verb.addEventListener("change", fill);
  fill();
  const max = levelSelect(Math.min(2, a.clearance));
  const task = el("textarea", { rows: 3, placeholder: `给 ${a.name} 派个任务，比如：盘一下所有客户里交期有风险的，按严重程度排序汇报给我` });

  $("#detail").replaceChildren(
    el("div", { class: "detail-head" },
      el("h2", {}, a.name), el("p", {}, `${KIND[a.kind]}，key ${a.key}`),
      a.status && el("p", { class: `agent-status ${a.status.busy ? "busy" : ""}` },
        `${a.status.busy ? "正在处理" : "上次"}：${(a.status.text || "无需行动").replace(/\*\*/g, "")}`)),
    el("div", { class: "detail-controls" },
      el("label", {}, "信息级别上限",
        levelSelect(a.clearance, save((e) => api(`/api/admin/agents/${a.key}`, { clearance: Number(e.target.value) })))),
      el("label", {}, el("input", { type: "checkbox", checked: !!a.enabled,
                                     onchange: save((e) => api(`/api/admin/agents/${a.key}`, { enabled: e.target.checked })) }),
         "启用")),
    el("p", { class: "hint" }, "级别上限封顶它所有的读取授权；它收到的内容也按这个级别检查，比如低于 3 级的 agent 收不到成本数字。"),
    el("h3", {}, "授权"),
    el("table", { class: "grants" },
      el("thead", {}, el("tr", {}, el("th", {}, "动作"), el("th", {}, "对象"), el("th", {}, "最高级别"), el("th", {}, "来源"), el("th", {}))),
      el("tbody", {}, mine.map((g) => el("tr", {},
        el("td", {}, VERB[g.verb]), el("td", {}, label(g.target)),
        el("td", {}, g.verb === "read"
          ? levelSelect(g.max_level, save((e) => api("/api/admin/grants", { subject: a.key, verb: g.verb, target: g.target, max_level: Number(e.target.value) })))
          : "—"),
        el("td", { class: "muted" }, g.source === "default" ? "默认" : g.source),
        el("td", {}, el("button", { type: "button", class: "link-btn", onclick: save(() => api(`/api/admin/grants/${g.id}`, undefined, "DELETE")) }, "收回")))))),
    el("form", { class: "grant-form", onsubmit: save((e) => { e.preventDefault();
      return api("/api/admin/grants", { subject: a.key, verb: verb.value, target: target.value, max_level: Number(max.value) }); }) },
      verb, target, max, el("button", { class: "btn btn-quiet", type: "submit" }, "加一条授权")),
    el("p", { class: "hint" }, "可读资源：" + Object.entries(resources).map(([k, r]) => `${r.title}（${level(r.level)}${r.per_customer ? "，按客户" : ""}）`).join("；")),
    el("h3", {}, "派任务"),
    el("form", { class: "task-form", onsubmit: async (e) => {
      e.preventDefault();
      if (!task.value.trim()) return;
      try { await api("/api/admin/task", { agent: a.key, body: task.value.trim() }); task.value = ""; } catch (err) { alert(err.message); }
    } }, task, el("button", { class: "btn btn-primary", type: "submit" }, "交给它")),
  );
}

// the envelope log

function shown(e) {
  if (state.filter === "denied") return e.status === "denied";
  if (state.filter === "agent") return [e.sender, e.recipient].includes(`agent:${state.selected}`);
  return true;
}

function envelopeNode(e) {
  const line = e.verb === "read" ? `${label(e.sender)} 读取 ${label(e.recipient)}` : `${label(e.sender)} → ${label(e.recipient)}`;
  return el("article", { class: `envelope ${e.status}`, "data-env": e.id },
    el("div", { class: "env-head" }, el("span", { class: "env-id" }, `#${e.id}`), el("b", {}, line),
       el("span", { class: `tag ${e.status === "denied" ? "denied" : ""}` }, `${VERB[e.verb] || e.verb}，${STATUS[e.status] || e.status}`),
       e.level != null && el("span", { class: "muted" }, `级别 ${level(e.level)}`),
       e.reply_to && el("span", { class: "muted" }, `回复 #${e.reply_to}`),
       el("time", { datetime: e.created_at }, new Date(e.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }))),
    e.body && el("p", { class: "body", title: "点开看全文", onclick: (ev) => ev.currentTarget.classList.toggle("open") }, e.body),
    e.reason && el("p", { class: "reason" }, `拒绝原因：${e.reason}`));
}

function renderLog() {
  const log = $("#log");
  const items = [...state.envelopes.values()].filter(shown);
  log.replaceChildren(...(items.length ? items.map(envelopeNode) : [el("p", { class: "muted" }, "还没有往来。")]));
  log.scrollTop = log.scrollHeight;
}
for (const b of document.querySelectorAll("[data-filter]")) b.addEventListener("click", () => {
  state.filter = b.dataset.filter;
  for (const x of document.querySelectorAll("[data-filter]")) x.setAttribute("aria-checked", String(x === b));
  renderLog();
});

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(state.token)}`);
  ws.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "envelope") {
      Object.assign(state.data.names, ev.names);
      const known = state.envelopes.has(ev.envelope.id);
      state.envelopes.set(ev.envelope.id, ev.envelope);
      if (ev.envelope.reply_to && state.envelopes.has(ev.envelope.reply_to)) state.envelopes.get(ev.envelope.reply_to).status = "answered";
      if (!known && shown(ev.envelope)) { const n = envelopeNode(ev.envelope); $("#log").append(n); n.scrollIntoView({ block: "end" }); }
      else renderLog();
    }
    if (ev.type === "agent_status") {
      const a = agentOf(ev.agent);
      if (a) {
        a.status = ev.status;
        renderTree();
        if (ev.agent === state.selected && !$(".task-form textarea")?.value) renderDetail();
      }
    }
    if (ev.type === "grants") load();
  };
  ws.onclose = (e) => { if (e.code !== 4401) setTimeout(connect, 2000); };
}

(async () => {
  try {
    if (state.token) await load().catch(async () => { await login(); await load(); });
    else { await login(); await load(); }
    connect();
  } catch (err) {
    $("#admin-error").textContent = err.message;
  }
})();
