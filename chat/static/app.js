const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) if (child != null && child !== false) node.append(child);
  return node;
};

// Sessions live per tab, so one browser can hold the salesperson and the customer side by side.
// Inside the split screen both frames share one tab, so a frame opened with ?as=USERNAME keeps its token in memory only.
// Inside an Odoo form (?frame=1&case=model:id) the person is whoever is signed in to Odoo, and the token stays in memory too.
const params = new URLSearchParams(location.search);
const fixedRole = params.get("as");
const framed = params.get("frame") === "1";
const wantedCase = params.get("case");
const store = fixedRole || framed ? { getItem: () => null, setItem() {}, removeItem() {} } : sessionStorage;
const state = { token: store.getItem("token"), user: null, groups: [], gid: null, view: "group",
                messages: new Map(), panel: null, header: null, audience: "all", ws: null,
                assistant: null, envelopes: new Map(), names: {} };

const TEXT = {
  zh: { logout: "换身份", send: "发送", placeholderAll: "对群里说点什么…", placeholderInternal: "告诉 AI 该做什么，对方看不到",
        you: "你" },
  en: { logout: "Switch role", send: "Send", placeholderAll: "Write a message…", placeholderInternal: "", you: "You" },
};
const isSales = () => ["sales", "admin"].includes(state.user?.role);
const lang = () => (isSales() || state.user?.lang === "zh" ? "zh" : "en");
const t = (key) => TEXT[lang()][key];
// what people are called in this group's playbook: 业务员 / 会计 / 招聘负责人, 顾客 / 供应商 / 候选人
const names = () => groupInfo().labels || { owner: "负责人", party: "对方", ai: "AI", ai_external: "AI", name: "事项" };
const aiName = () => (isSales() ? names().ai : state.header?.ai || names().ai_external);
const ownerWord = () => (lang() === "en" ? "Sales" : names().owner);
const partyWord = () => (lang() === "en" ? "Customer" : names().party);

async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.token || ""}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== "/api/login" && path !== "/api/odoo-sso") { logout(); throw new Error("请重新登录"); }
  if (!res.ok) throw new Error(data.detail?.[0]?.msg || data.detail || `请求失败（${res.status}）`);
  return data;
}

// login

async function enterAs(username) {
  $("#login-error").textContent = "";
  try {
    const data = await api("/api/login", { username });
    state.token = data.token;
    store.setItem("token", data.token);
    await start();
  } catch (err) {
    $("#login").hidden = false;
    $("#login-error").textContent = err.message;
  }
}

async function showLogin() {
  $("#login").hidden = false;
  const people = await api("/api/identities").catch(() => []);
  const button = (u, note) => el("button", { type: "button", class: "role", onclick: () => enterAs(u.username) },
    el("strong", {}, u.name), el("span", {}, note));
  $("#sales-list").replaceChildren(...people.filter((u) => u.role === "sales")
    .map((u) => button(u, "看得到 AI 的草稿、待决定和待确认的事，有自己的助理 agent")));
  $("#customer-list").replaceChildren(...people.filter((u) => u.role === "customer")
    .map((u) => el("button", { type: "button", class: "role compact", lang: /[\u4e00-\u9fff]/.test(u.name) ? "zh-CN" : "en",
                                onclick: () => enterAs(u.username) },
      el("strong", {}, u.name))));
}

function logout() {
  if (fixedRole || framed) { location.reload(); return; }
  store.removeItem("token");
  state.token = null;
  state.ws?.close();
  $("#app").hidden = true;
  showLogin();
}
$("#logout").addEventListener("click", logout);

async function start() {
  state.user = await api("/api/me");
  document.documentElement.lang = lang() === "zh" ? "zh-CN" : "en";
  document.title = isSales() ? "数字员工群" : lang() === "zh" ? "工作群" : "Order chat";
  $("#login").hidden = true;
  $("#app").hidden = false;
  $("#app").classList.toggle("customer", !isSales());
  $("#app").classList.toggle("embedded", !!fixedRole);
  $("#app").classList.toggle("framed", framed);
  $("#me-name").textContent = state.user.name;
  $("#logout").textContent = t("logout");
  $("#logout").hidden = !!fixedRole;
  $("#send-btn").textContent = t("send");
  $("#audience").hidden = !isSales();
  setAudience("all");
  $("#panel").hidden = !isSales() || matchMedia("(max-width: 1100px)").matches;
  state.groups = await api("/api/groups");
  $("#assistant-item").hidden = !state.user.assistant;
  $("#desk-item").hidden = !isSales();
  renderGroups();
  connect();
  const wanted = Number(params.get("group"));
  const first = wantedCase ? state.groups.find((g) => `${g.res_model}:${g.res_id}` === wantedCase)
    : state.groups.find((g) => g.id === wanted) || state.groups[0];
  if (first) await openGroup(first.id);
  else if (framed) frameMessage("这个事项还没有群，或者你不在这个群里。");
}

// groups

function renderGroups() {
  $("#assistant-item").setAttribute("aria-current", String(state.view === "assistant"));
  $("#group-list").replaceChildren(...state.groups.map((g) => el("button", {
    class: "group-item", type: "button", "aria-current": String(state.view === "group" && g.id === state.gid),
    onclick: () => openGroup(g.id),
  }, el("strong", {}, g.title, g.todo ? el("b", { class: "badge", title: "等你处理的草稿、决定和动作" }, g.todo) : null),
     el("span", {}, isSales() ? `${g.labels.name}，${g.customer}${g.step ? `，${g.step}` : ""}` : g.title))));
}

function showRoom(assistant) {
  state.view = assistant ? "assistant" : "group";
  $("#app").classList.toggle("assistant-view", assistant);
  $("#route").hidden = assistant;
  $("#audience").hidden = assistant || !isSales();
  $("#panel-toggle").hidden = assistant;
  if (assistant) $("#panel").hidden = true;
  renderGroups();
}

async function openGroup(id) {
  state.gid = id;
  showRoom(false);
  const group = state.groups.find((g) => g.id === id);
  const data = await api(`/api/groups/${id}`);
  state.messages = new Map(data.messages.map((m) => [m.id, m]));
  state.panel = data.panel;
  state.header = data.header;
  setAudience(state.audience);
  const n = group.labels;
  $("#room-title").textContent = isSales() ? group.title : data.header.title || group.title;
  $("#room-sub").textContent = isSales()
    ? `${n.name}，${n.party} ${group.customer}，${n.owner} ${group.sales.join("、")}，还有这个事项的${n.ai}`
    : data.header.subtitle;
  $("#panel-toggle").textContent = `${n.name}与待办`;
  $("#aud-internal").textContent = `只给${n.ai}`;
  renderStream();
  renderRoute();
  renderPanel();
  renderStatus(data.panel?.status);
}

// live updates

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(state.token)}`);
  state.ws = ws;
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === "envelope") { Object.assign(state.names, ev.names); onEnvelope(ev.envelope); return; }
    if (ev.type === "agent_status") { if (state.view === "assistant" && ev.agent === state.user.assistant) renderStatus(ev.status, "助理"); return; }
    if (ev.type === "panel" && ev.summary) {
      const g = state.groups.find((x) => x.id === ev.group_id);
      if (g) { Object.assign(g, ev.summary); renderGroups(); }
    }
    if (state.view !== "group" || ev.group_id !== state.gid) return;
    if (ev.type === "message" || ev.type === "message_update") upsert(ev.message);
    if (ev.type === "panel") { state.panel = ev.panel; renderRoute(); renderPanel(); }
    if (ev.type === "status") renderStatus(ev.status);
  };
  ws.onclose = (e) => {
    if (e.code === 4401 || !state.token || state.ws !== ws) return;
    setTimeout(() => { connect(); state.view === "assistant" ? openAssistant() : state.gid && openGroup(state.gid); }, 2000);
  };
}

// the salesperson's assistant agent: tasks you give it, its reports, and its traffic with the customer agents

async function openAssistant() {
  showRoom(true);
  const data = await api("/api/assistant");
  state.assistant = data;
  state.names = { ...state.names, ...data.names };
  state.envelopes = new Map(data.envelopes.map((e) => [e.id, e]));
  $("#room-title").textContent = "我的助理";
  $("#room-sub").textContent = "它统管你名下的客户 agent：能回答它们的问题、给它们派任务、向 root 提问。下面是你们的对话和它的往来记录。";
  $("#input").placeholder = "给助理派个任务，比如：把我名下所有客户的风险排个序";
  $("#composer").classList.remove("internal");
  renderEnvelopes();
  renderStatus(data.status, "助理");
}
$("#assistant-item").addEventListener("click", openAssistant);

function onEnvelope(env) {
  if (state.view !== "assistant") return;
  const mine = [env.sender, env.recipient].some((p) => p === `user:${state.user.id}` || p === `agent:${state.user.assistant}`);
  if (!mine) return;
  state.envelopes.set(env.id, env);
  const stream = $("#stream");
  const node = envelopeNode(env);
  const old = stream.querySelector(`[data-env="${env.id}"]`);
  if (old) old.replaceWith(node); else { stream.append(node); node.scrollIntoView({ block: "end" }); }
}

function renderEnvelopes() {
  const stream = $("#stream");
  stream.replaceChildren(...[...state.envelopes.values()].map(envelopeNode));
  stream.scrollTop = stream.scrollHeight;
}

const VERB = { ask: "提问", task: "任务", answer: "回答", report: "汇报", read: "读取" };
const who = (p) => p === `user:${state.user.id}` ? "你" : p === `agent:${state.user.assistant}` ? "你的助理"
  : state.names[p] || p.replace(/^resource:/, "资源 ");

function envelopeNode(env) {
  const me = `user:${state.user.id}`;
  const helper = `agent:${state.user.assistant}`;
  const talk = [env.sender, env.recipient].includes(me);
  if (talk && env.status !== "denied") {
    const mine = env.sender === me;
    return el("article", { class: `msg ${mine ? "from-sales ours" : "from-ai"}`, "data-env": env.id },
      el("div", { class: "who" }, el("b", {}, mine ? "你" : "你的助理"), el("span", {}, VERB[env.verb] || env.verb),
         el("time", { datetime: env.created_at }, fmtTime(env.created_at))),
      el("div", { class: "bubble" }, env.body));
  }
  const line = env.verb === "read" ? `${who(env.sender)} 读取 ${who(env.recipient)}`
    : `${who(env.sender)} → ${who(env.recipient)} ${VERB[env.verb] || env.verb}`;
  return el("article", { class: `envelope ${env.status}`, "data-env": env.id },
    el("div", { class: "env-head" }, el("span", { class: "env-id" }, `#${env.id}`), el("b", {}, line),
       env.status === "denied" && el("span", { class: "tag denied" }, "被拒绝"),
       env.status === "open" && env.recipient === helper && el("span", { class: "tag" }, "待助理处理"),
       el("time", { datetime: env.created_at }, fmtTime(env.created_at))),
    env.body && el("p", { class: "body", title: "点开看全文", onclick: (ev) => ev.currentTarget.classList.toggle("open") }, env.body),
    env.reason && el("p", { class: "reason" }, env.reason));
}

// the route across the top

function renderRoute() {
  const steps = isSales() ? state.panel?.steps : state.header?.steps;
  const current = isSales() ? state.panel?.current : state.header?.current;
  const route = $("#route");
  if (!steps?.length) { route.replaceChildren(); return; }
  route.style.setProperty("--n", steps.length);
  route.replaceChildren(...steps.map((s) => {
    const isCurrent = s.code === current;
    const cls = [s.state === "done" ? "done" : "", isCurrent ? `current ${s.state}` : "", s.needs_human ? "human" : ""].join(" ");
    const label = isSales() ? s.short_name || s.name : s.name;
    const stateText = isSales() ? { done: "已完成", waiting: "等你决定", blocked: "受阻", working: "进行中", todo: "待开始" }[s.state] : "";
    return el("li", { class: cls, title: isSales() ? `${s.name}：${stateText}${s.needs_human ? "，需要人拍板" : ""}` : label,
                      "aria-current": isCurrent ? "step" : false },
      isCurrent ? el("span", { class: "ship", "aria-hidden": "true" }) : null,
      el("span", { class: "port" }), el("span", { class: "name" }, label));
  }));
}

// messages

function upsert(message) {
  const known = state.messages.has(message.id);
  state.messages.set(message.id, message);
  const stream = $("#stream");
  const nearBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 120;
  const old = stream.querySelector(`[data-id="${message.id}"]`);
  const node = renderMessage(message);
  if (old) old.replaceWith(node);
  else if (!known) { stream.append(node); if (nearBottom || message.author === authorOf(state.user)) node.scrollIntoView({ block: "end" }); }
  if (message.kind === "decision" || message.kind === "proposal") renderPanel();
}

const authorOf = (user) => (user.role === "customer" ? "customer" : "sales");
const fmtTime = (ts) => new Date(ts).toLocaleTimeString(isSales() ? "zh-CN" : "en-GB", { hour: "2-digit", minute: "2-digit" });
const fmtDay = (ts) => new Date(ts).toLocaleDateString(isSales() ? "zh-CN" : "en-GB", { month: "long", day: "numeric", weekday: "short" });

function renderStream() {
  const nodes = [];
  let day = "";
  for (const m of state.messages.values()) {
    const d = fmtDay(m.created_at);
    if (d !== day) { nodes.push(el("p", { class: "day" }, d)); day = d; }
    nodes.push(renderMessage(m));
  }
  const stream = $("#stream");
  stream.replaceChildren(...nodes);
  stream.scrollTop = stream.scrollHeight;
}

function renderMessage(m) {
  if (m.kind === "draft") return draftCard(m);
  if (m.kind === "decision") return decisionCard(m);
  if (m.kind === "proposal") return proposalCard(m);
  const mine = m.author === authorOf(state.user) && (m.author !== "sales" || !m.meta?.by || m.meta.by === state.user.name);
  const ours = isSales() ? m.author !== "customer" : m.author === "customer";
  const name = mine ? t("you") : m.author === "ai" ? aiName() : m.author === "sales" ? (m.meta?.by || salesName()) : customerName();
  const role = !mine && m.author !== "ai" ? (m.author === "sales" ? ownerWord() : partyWord()) : null;
  return el("article", { class: `msg from-${m.author} ${ours ? "ours" : ""} ${m.audience === "internal" ? "internal" : ""}`, "data-id": m.id },
    el("div", { class: "who" }, el("b", {}, name), role && el("span", {}, role),
       m.meta?.simulated && isSales() && el("span", { class: "tag", title: `这条是 AI 扮演的${names().party}发的` }, "模拟"),
       m.meta?.approved_draft && el("span", { class: "tag", title: "你批准后发出的" }, "已批准"),
       el("time", { datetime: m.created_at }, fmtTime(m.created_at))),
    el("div", { class: "bubble" }, m.body));
}

const groupInfo = () => state.groups.find((g) => g.id === state.gid) || {};
const salesName = () => (isSales() ? groupInfo().sales?.join("、") : null) || ownerWord();
const customerName = () => groupInfo().customer || partyWord();

function busy(button, fn) {
  return async () => {
    const buttons = button.closest(".card")?.querySelectorAll("button") || [button];
    buttons.forEach((b) => (b.disabled = true));
    try { await fn(); } catch (err) { alert(err.message); buttons.forEach((b) => (b.disabled = false)); }
  };
}

function draftCard(m) {
  const pending = m.status === "pending";
  const card = el("article", { class: `card draft ${pending ? "" : "done"}`, "data-id": m.id });
  const text = el("p", { class: "text" }, m.body);
  const editor = el("textarea", { rows: 4, hidden: true }, m.body);
  const note = el("textarea", { rows: 2, hidden: true, placeholder: "为什么不发？AI 会照着改（可不填）" });
  card.append(el("h3", {}, pending ? `AI 想对${names().party}说这条，等你批准` : "AI 的草稿"), text, editor);
  if (pending) {
    if (m.meta?.reasons?.length) card.append(el("ul", { class: "why" }, m.meta.reasons.map((r) => el("li", {}, r))));
    const send = el("button", { class: "btn btn-primary", type: "button" }, "发送");
    const edit = el("button", { class: "btn btn-quiet", type: "button" }, "修改");
    const drop = el("button", { class: "btn btn-danger", type: "button" }, "不发");
    send.addEventListener("click", busy(send, () => api(`/api/groups/${state.gid}/drafts/${m.id}`,
      { send: true, body: editor.hidden ? null : editor.value })));
    edit.addEventListener("click", () => { editor.hidden = false; text.hidden = true; edit.hidden = true; send.textContent = "发送修改后的"; editor.focus(); });
    drop.addEventListener("click", () => {
      if (note.hidden) { note.hidden = false; drop.textContent = "确定不发"; note.focus(); return; }
      busy(drop, () => api(`/api/groups/${state.gid}/drafts/${m.id}`, { send: false, note: note.value }))();
    });
    card.append(note, el("div", { class: "actions" }, send, edit, drop));
  } else {
    const note = m.meta?.note ? `：${m.meta.note}` : "";
    card.append(el("p", { class: "result" }, m.status === "sent" ? (m.meta?.edited ? "你改过后发出了" : "你批准后发出了")
      : m.status === "withdrawn" ? `AI 撤回了${note}` : `没有发${note}`));
  }
  return card;
}

function decisionCard(m) {
  const meta = m.meta || {};
  const waiting = m.status === "waiting";
  const card = el("article", { class: `card decision ${waiting ? "" : "done"}`, "data-id": m.id, id: `card-${m.id}` },
    el("h3", {}, `${meta.step_name}：${waiting ? "等你决定" : "已决定"}`),
    el("p", { class: "text" }, meta.question));
  if (meta.summary) card.append(el("details", {}, el("summary", {}, "AI 的核对结果"), el("pre", {}, meta.summary)));
  if (!waiting) { card.append(el("p", { class: "result" }, meta.result || "")); return card; }
  const name = `opt-${m.id}`;
  const options = el("fieldset", { class: "options", "aria-label": "选项" },
    (meta.options || []).map((o) => el("label", {},
      el("input", { type: "radio", name, value: o, checked: o === meta.recommendation }),
      el("span", {}, o), o === meta.recommendation ? el("span", { class: "rec" }, "AI 推荐") : null)));
  const note = el("textarea", { rows: 2, placeholder: "备注（驳回时必填，AI 会按你的意见重做）" });
  const approve = el("button", { class: "btn btn-primary", type: "button" }, "按所选批准");
  const reject = el("button", { class: "btn btn-danger", type: "button" }, "驳回");
  approve.addEventListener("click", busy(approve, () => api(`/api/groups/${state.gid}/decisions/${m.id}`, {
    approve: true, choice: card.querySelector(`input[name="${name}"]:checked`)?.value || null, note: note.value })));
  reject.addEventListener("click", () => {
    if (!note.value.trim()) { note.focus(); note.placeholder = "写一句为什么驳回，AI 会照着改"; return; }
    busy(reject, () => api(`/api/groups/${state.gid}/decisions/${m.id}`, { approve: false, note: note.value }))();
  });
  card.append(options, note, el("div", { class: "actions" }, approve, reject));
  return card;
}

// an Odoo action the AI drafted: nothing happens in Odoo until the owner confirms it here or in Odoo

function proposalCard(m) {
  const meta = m.meta || {};
  const waiting = m.status === "waiting";
  const card = el("article", { class: `card proposal ${waiting ? "" : "done"} ${m.status}`, "data-id": m.id },
    el("h3", {}, el("span", { class: "odoo-mark" }, "Odoo"), `${meta.action_name}：${waiting ? "等你确认" : m.status === "done" ? "已执行" : "没有执行"}`),
    el("p", { class: "text" }, meta.summary),
    meta.preview && el("pre", { class: "preview" }, meta.preview));
  if (!waiting) {
    card.append(el("p", { class: "result" }, meta.result || "",
      meta.link && m.status === "done" ? el("a", { href: meta.link, target: "_blank", rel: "noopener" }, "在 Odoo 里看") : null));
    return card;
  }
  const note = el("textarea", { rows: 2, placeholder: "备注（驳回时必填，AI 会按你的意见重拟）" });
  const confirm = el("button", { class: "btn btn-primary", type: "button" }, "确认执行");
  const reject = el("button", { class: "btn btn-danger", type: "button" }, "驳回");
  confirm.addEventListener("click", busy(confirm, () => api(`/api/groups/${state.gid}/proposals/${m.id}`, { approve: true, note: note.value })));
  reject.addEventListener("click", () => {
    if (!note.value.trim()) { note.focus(); note.placeholder = "写一句为什么驳回，AI 会照着改"; return; }
    busy(reject, () => api(`/api/groups/${state.gid}/proposals/${m.id}`, { approve: false, note: note.value }))();
  });
  card.append(el("p", { class: "hint" }, "确认后，Odoo 以你的身份执行这一步；在这之前什么都不会发生。"), note,
              el("div", { class: "actions" }, confirm, reject));
  return card;
}

// AI status line

function renderStatus(status, label = aiName()) {
  const box = $("#ai-status");
  if (!isSales() || !status) { box.hidden = true; return; }
  box.hidden = false;
  box.classList.toggle("busy", !!status.busy);
  const text = (status.text || "无需行动").replace(/\*\*/g, "").trim();
  box.firstElementChild.textContent = status.busy ? `${label}正在处理：${text}` : `${label}上次：${text}`;
}
$("#ai-status").addEventListener("click", (e) => {
  const box = e.currentTarget;
  box.setAttribute("aria-expanded", String(box.getAttribute("aria-expanded") !== "true"));
});

// side panel

function renderPanel() {
  const panel = $("#panel");
  const p = state.panel;
  if (!isSales() || !p) { panel.replaceChildren(); return; }
  const n = p.labels;
  const waiting = [...state.messages.values()].filter((m) => (m.kind === "decision" || m.kind === "proposal") && m.status === "waiting");
  const drafts = [...state.messages.values()].filter((m) => m.kind === "draft" && m.status === "pending");
  const jump = (id) => { const c = document.querySelector(`[data-id="${id}"]`); c?.scrollIntoView({ block: "center" });
                         c?.classList.remove("flash"); void c?.offsetWidth; c?.classList.add("flash"); };
  const due = (ts) => { const min = Math.round((new Date(ts) - Date.now()) / 60000);
                        return min <= 0 ? "到期" : min < 60 ? `${min} 分钟后` : `${Math.round(min / 60)} 小时后`; };
  const setting = (key, value) => api(`/api/groups/${state.gid}/settings`, { [key]: value }).catch((e) => alert(e.message));

  const todo = [...waiting.map((m) => ({ id: m.id, text: m.kind === "proposal" ? `确认 Odoo 动作「${m.meta.action_name}」` : `决定「${m.meta.step_name}」` })),
                ...drafts.map((m) => ({ id: m.id, text: `批准 AI 的草稿：${m.body.slice(0, 28)}…` }))];
  panel.replaceChildren(
    el("section", {}, el("h4", {}, todo.length ? `需要你处理（${todo.length}）` : "需要你处理"),
      todo.length ? el("ul", { class: "todo-list" }, todo.map((x) => el("li", {}, el("button", { type: "button", onclick: () => jump(x.id) }, x.text))))
                  : el("p", {}, `现在没有要你决定的事，${n.ai}在跟。`)),
    el("section", {}, el("h4", {}, "AI 记着要跟进的事"),
      p.followups.length ? el("ul", { class: "followups" }, p.followups.map((f) =>
        el("li", {}, el("time", { datetime: f.due_at, title: new Date(f.due_at).toLocaleString("zh-CN") }, due(f.due_at)), el("span", {}, f.what))))
        : el("p", {}, `暂时没有。AI 向${n.party}要东西或等回复时会记在这里，到点自己去追。`)),
    el("section", {}, el("h4", {}, n.name),
      p.marks.length ? el("div", { class: "marks" }, ...p.marks.flatMap((m, i) => (i ? [el("br"), m] : [m]))) : null,
      el("dl", { class: "facts" }, ...p.facts.flatMap((f) => [el("dt", {}, f.label), el("dd", {}, f.value)]))),
    el("section", {}, el("h4", {}, n.ai),
      el("label", { class: "switch" }, "自动跟进",
        el("input", { type: "checkbox", checked: !!p.settings.ai_auto, onchange: (e) => setting("ai_auto", e.target.checked) })),
      el("p", { class: "hint" }, "关掉后，AI 只在你对它说话或点下面的按钮时才动。"),
      el("label", { class: "switch" }, `${n.party}多久没回就追问（分钟）`,
        el("input", { type: "number", min: 1, max: 10080, value: p.settings.chase_minutes,
                      onchange: (e) => setting("chase_minutes", Number(e.target.value)) })),
      el("label", { class: "switch" }, `AI 扮演${n.party}（演示用）`,
        el("input", { type: "checkbox", checked: !!p.settings.sim_customer, onchange: (e) => setting("sim_customer", e.target.checked) })),
      el("p", { class: "hint" }, `打开后，${n.party}那边由 AI 扮演 ${groupInfo().customer || n.party} 自动回复，方便一个人演示整个流程。`),
      el("button", { class: "btn btn-quiet", type: "button", onclick: (e) => busy(e.target, async () => {
        await api(`/api/groups/${state.gid}/nudge`, {}); e.target.disabled = false; })() }, "让 AI 现在跟进一下")),
  );
}

$("#panel-toggle").addEventListener("click", (e) => {
  const panel = $("#panel");
  panel.hidden = !panel.hidden;
  e.target.setAttribute("aria-expanded", String(!panel.hidden));
});

// composer

function setAudience(aud) {
  state.audience = aud;
  for (const b of document.querySelectorAll("#audience button")) b.setAttribute("aria-checked", String(b.dataset.aud === aud));
  $("#composer").classList.toggle("internal", aud === "internal");
  $("#input").placeholder = aud === "internal" ? t("placeholderInternal") : t("placeholderAll");
}
for (const b of document.querySelectorAll("#audience button")) b.addEventListener("click", () => setAudience(b.dataset.aud));

$("#composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#input");
  const body = input.value.trim();
  if (!body || (state.view === "group" && !state.gid)) return;
  $("#send-btn").disabled = true;
  try {
    if (state.view === "assistant") await api("/api/assistant", { agent: state.user.assistant, body });
    else await api(`/api/groups/${state.gid}/messages`, { body, internal: isSales() && state.audience === "internal" });
    input.value = "";
  } catch (err) {
    alert(err.message);
  } finally {
    $("#send-btn").disabled = false;
    input.focus();
  }
});
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); }
});

function frameMessage(text) {
  $("#app").hidden = true;
  $("#login").hidden = true;
  document.body.append(el("p", { class: "frame-note", role: "status" }, text));
}

async function enterFromOdoo() {
  try {
    const data = await api("/api/odoo-sso", {});
    state.token = data.token;
    await start();
  } catch (err) {
    frameMessage(err.message);
  }
}

if (framed) enterFromOdoo();
else if (fixedRole) enterAs(fixedRole);
else if (state.token) start().catch(() => logout());
else showLogin();
