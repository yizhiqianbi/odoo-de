# 通用数字员工内核：设计文档

> 状态：第一期实施中。本文描述目标设计，以及第一期实际落地的范围（标注「本期」「后续」）。

## 1. 目标

让同一套系统覆盖公司里大部分岗位的大部分日常工作，而不是每个岗位写一套。

判断标准：接入一个新岗位时，**只新增配置和少量工具，不改内核代码**。第一期用三个差异很大的场景检验这一点：

| 场景 | 岗位 | 工作形态 | 业务对象 | 外部联系人 |
|---|---|---|---|---|
| 外贸跟单 | 业务员 | 流程单据型 + 对外沟通 | 销售订单 `sale.order` | 客户 |
| 应付发票审核 | 财务应付 | 流程单据型，重核对 | 供应商账单 `account.move` | 供应商 |
| 招聘候选人跟进 | HR 招聘 | 沟通协调型 | 应聘申请 `hr.applicant` | 候选人 |

不追求覆盖创造、判断型岗位（研发、设计、管理决策），这些岗位只配个人助理。

## 2. 从跟单原型里抽出来的东西

跟单原型（`trade_sop` 模块加群聊服务）已经跑通，问题在于很多东西写死在销售订单上：

| 写死的地方 | 通用化以后 |
|---|---|
| `trade.sop.stage` 和 `trade.sop.step` 只能挂在 `sale.order` 上 | `work.stage` 和 `work.step`，按 `res_model` + `res_id` 挂在任意业务记录上 |
| `sop_agent_*` 方法写在 `sale.order` 里 | `work.case.mixin` 混入类，业务模型继承它就有全套 agent 接口 |
| 关卡类型 `lc_only` 是给信用证专门加的 | 关卡分三种：人拍板、agent 完成、按条件（一个作用在业务记录上的 domain）|
| 批准 PO 这一步时顺手确认订单，写在 `action_approve` 里 | 步骤上配置 `on_approve` 钩子名，由场景模块实现 `_work_approve_<名字>` |
| 群聊服务按订单号找订单、读 `order` 和 `lines` 字段 | 群按 `(model, id, playbook)` 绑定，读内核返回的统一上下文 |
| 提示词里写死“工厂、成本、信用证” | 提示词 = 内核通用规则 + playbook 的岗位卡和对外沟通规则 |
| `gate.py` 只认工厂名 | 机密值由场景声明（`confidential`），门控统一检查 |
| 协议资源固定是产品目录、工厂负荷、成本 | 资源注册表：通用资源，加上每个 playbook 自己的资源 |

## 3. 核心概念

### 3.1 事项（case）

一条业务记录，加上作用在它上面的一份 playbook 实例，就是一个事项。事项有：
- 负责人：拍板的人，由 playbook 的 `owner_field` 指定，比如 `user_id`、`invoice_user_id`、`recruiter_id.user_id`。
- 外部联系人：由 `party_field` 指定，比如客户、供应商、候选人。
- 步骤：每一步一条 `work.step` 记录。
- 留痕：步骤进展、人的决定、群聊记录，都以内部备注写在业务记录的留言区里。

### 3.2 playbook（流程定义）

playbook 是数据，存在 `work.playbook` 和 `work.stage` 里，管理员可以在 Odoo 里改。

| 字段 | 含义 |
|---|---|
| `code`、`name`、`model` | 唯一代码、名称、作用在哪个模型上 |
| `role` | 岗位卡：这个 agent 是谁、服务谁、语气 |
| `external_rules` | 对外沟通规则：能说什么、绝不能说什么 |
| `party_label` | 外部联系人叫什么（客户、供应商、候选人） |
| `owner_field`、`party_field` | 负责人和外部联系人字段，支持点号路径 |
| `agent_fields` | agent 可以直接填写的字段，一行一个 |
| `group_id` | 哪个权限组的人能看到并处理这份 playbook 的步骤 |

每个阶段（`work.stage`）：

| 字段 | 含义 |
|---|---|
| `sequence`、`code`、`name`、`short_name` | 顺序、代码、名称、进度条上的短名 |
| `external_name` | 给外部联系人看的名字（比如英文） |
| `goal`、`agent_task`、`human_question` | 目标、agent 要做什么、人要决定什么 |
| `gate` | `human` 人拍板 / `agent` agent 完成 / `condition` 记录符合 `gate_domain` 时才要人拍板 |
| `on_approve` | 批准时调用业务记录上的 `_work_approve_<on_approve>`，以批准人的身份执行 |

### 3.3 agent 的三种绑定

| agent | 绑定对象 | 职责 |
|---|---|---|
| 事项 agent（`customer:<key>`） | 一个事项及其外部联系人 | 在群里推进 playbook、和外部联系人沟通、向上级提问 |
| 个人助理（`assistant:<username>`） | 一个员工 | 统管这个人名下的事项 agent、回答它们的问题、给它们派任务、汇报给本人 |
| root | 全公司 | 统筹所有助理，对管理员负责 |

“事项 agent”的 key 沿用原型的 `customer:` 前缀，这样原型的授权和往来记录都不用迁移。key 的后半段在跟单里是客户的 partner id，在应付和招聘里是 `ap-<账单id>`、`hr-<申请id>`。

**后续**：部门 agent（比如财务部 agent，统管所有应付事项），对应组织架构的中间层。

### 3.4 工具与风险等级

agent 的每个工具都标一个风险等级，审批策略按等级统一执行，不再在每个场景里各写一套：

| 等级 | 含义 | 工具举例 | 策略 |
|---|---|---|---|
| R0 | 只读 | `read_resource`、读上下文 | 自动 |
| R1 | 写内部记录 | `tell_owner`、`schedule_followup`、`work_report`（只写结论）、`ask_agent` | 自动，受频率上限约束 |
| R2 | 改业务数据 | `work_report` 带字段值、`work_complete` | 只能改 `agent_fields`，只能完成不需要人拍板的步骤，由 Odoo 强制执行 |
| R3 | 对外沟通 | `send_to_group` | 门控：承诺、陌生数字、机密值、频率，命中任何一条就转草稿 |
| R4 | 钱、合同、不可逆 | 确认订单、过账、付款、发 offer | agent 根本没有这类工具；只能 `work_ask` 请人决定，由人的批准钩子执行 |

R2 和 R4 的边界在 Odoo 里执行：agent 的 Odoo 账号对业务记录只读，所有写入走 `work_agent_*` 方法，`guards.py` 拒绝 agent 调用确认、过账、出入库、对外发消息。服务端的门控只是第二道保险。

**事实引用规则**（来自原型里 agent 虚构“已订舱”的教训）：对外说“我方已经做了某事”，必须能对应到 Odoo 记录或负责人的原话；只是别的 agent 这么说不算。本期靠提示词加审核模型约束，**后续**改成结构化：对外消息附带引用的记录 ID，由门控核验。

### 3.5 权限协议

沿用原型的协议，没有改动：
- 级别：0 公开、1 内部、2 外部联系人机密、3 公司机密。
- 授权是 `(主体 agent, 动作 ask/task/read, 对象, 最高级别)`，agent 的级别上限封顶所有读取授权。
- 同级不能互访，只能通过共同上级；越权一律拒绝，不升级。
- 信封正文送达前，按接收方检查：不能出现接收方范围之外的事项身份信息（名称、单号、金额），不能把成本类机密数字发给 3 级以下的接收方。

通用化以后，“事项身份信息”和“机密数字”由每个场景通过钩子声明：`_work_identity()` 返回标识这个事项的字符串和数字；资源提供者声明自己的机密数字。

**后续**：授权从组织架构自动生成（上级的助理能问下属的助理），管理员只配例外。

### 3.6 资源注册表

| 资源 | 级别 | 按事项 | 属于 |
|---|---|---|---|
| `customer_overview` | 2 | 是 | 通用：事项概况、当前步骤、待决定的事 |
| `customer_chat` | 2 | 是 | 通用：事项群最近的消息 |
| `catalog` | 0 | 否 | 跟单：产品目录和标准价 |
| `factory_load` | 1 | 否 | 跟单：各工厂按交货月的未出运量，不含客户名 |
| `costs` | 3 | 否 | 跟单：产品成本 |
| `vendor_ledger` | 1 | 否 | 应付：各供应商未付账单的数量和金额 |
| `payment_plan` | 3 | 否 | 应付：按周的付款计划和资金占用 |
| `job_openings` | 0 | 否 | 招聘：在招职位和要求 |
| `pipeline` | 1 | 否 | 招聘：各职位的候选人数量和阶段分布，不含姓名 |
| `salary_bands` | 3 | 否 | 招聘：职位薪资区间 |

资源名沿用原型（`customer_*`），表示“这个事项的外部联系人”的数据。

### 3.7 人的入口

| 入口 | 谁用 | 内容 |
|---|---|---|
| 事项群 | 负责人、外部联系人、事项 agent | 对话、草稿、决定卡片 |
| 我的助理 | 每个员工 | 给助理派任务、看汇报和它的往来 |
| 管理控制台 | 管理员 | 组织树、授权表、往来日志 |
| Odoo「数字员工 / 待我决定」 | 每个员工 | 跨所有 playbook 的待决定步骤，Odoo 原生界面 |
| Odoo 后台 | 每个员工 | 原始记录和留痕 |

外部联系人那一侧的语言由外部联系人的 `lang` 决定：客户用英文，国内供应商和候选人用中文。

**后续**：把“待我决定”和助理汇报推到公司 IM。

## 4. Odoo 内核模块 `work_case`

### 4.1 模型

```
work.playbook ──< work.stage
      │
work.step（res_model, res_id）── 挂在任意继承了 work.case.mixin 的业务记录上
```

### 4.2 `work.case.mixin` 提供的接口

agent 只能调用这些方法（JSON-2：`POST /json/2/<model>/<method>`）：

| 方法 | 作用 | 风险 |
|---|---|---|
| `work_agent_cases()`（model 级） | 列出这份 playbook 下的所有事项：负责人、外部联系人、当前步骤 | R0 |
| `work_agent_context()` | 一个事项的全部上下文，结构见 4.3 | R0 |
| `work_agent_report(step_code, summary, values)` | 记录进展；`values` 只能是 `agent_fields` | R1 / R2 |
| `work_agent_complete(step_code, summary)` | 完成不需要人拍板的步骤 | R2 |
| `work_agent_request_decision(step_code, question, options, recommendation, summary)` | 请负责人决定，1 到 3 个选项 | R1 |
| `work_agent_block(step_code, reason)` | 需要有人在流程之外先处理 | R1 |
| `work_agent_log_chat(lines)` | 把群聊记录留痕到业务记录 | R1 |

所有方法都检查：调用者在 agent 组里、能读这条记录、只能操作当前步骤、等人决定的步骤不能再改。

### 4.3 统一上下文

```json
{
  "case": {"model", "id", "name", "playbook", "owner", "owner_login", "party", "party_lang"},
  "playbook": {"code", "name", "role", "external_rules", "party_label"},
  "record": { "playbook 里列出的业务字段": "..." },
  "details": { "场景钩子 _work_context_details() 返回的明细，比如订单行、账单行与 PO 对照" },
  "facts": [{"label": "金额", "value": "USD 37,000.00"}],
  "external": {"title", "subtitle", "steps": [{"code", "name", "done"}]},
  "confidential": ["宁波明达家具厂"],
  "identity": {"tokens": ["Hamburg Home GmbH", "S00025"], "figures": [60000.0]},
  "steps": [...], "current_step": {...}, "messages": [...], "chat": [...]
}
```

- `record` 由场景钩子 `_work_context_record()` 给出。
- `facts` 是给负责人侧栏看的事实列表，`external` 是给外部联系人看的内容。
- `confidential` 是绝不能对外说的值，`identity` 用于协议的跨事项泄露检查。

### 4.4 场景模块要实现的钩子

| 钩子 | 默认 | 说明 |
|---|---|---|
| `_work_context_record()` | 名称 | 业务字段 |
| `_work_context_details()` | 空 | 明细 |
| `_work_facts()` | 空 | 侧栏事实 |
| `_work_external()` | 名称 + 进度 | 外部联系人看到的标题 |
| `_work_confidential()` | 空 | 不能对外的值 |
| `_work_identity()` | 外部联系人名称 + 记录名称 | 身份信息 |
| `_work_approve_<名字>(step)` | 无 | 批准钩子 |

### 4.5 防护

- agent 组 `work_case.group_agent`：内核表只读；业务记录的读权限由各场景自己的 agent 组授予。
- 每个场景一个独立的 bot 账号（跟单、应付、招聘），权限不叠加。
- `guards.py` 移到内核：agent 不能确认销售单和采购单、不能验证出入库、不能过账、不能对外发消息或通知外部联系人；继承混入类的模型在非 sudo 下拒绝 agent 直接写入。

## 5. 运行时（群聊服务）

| 模块 | 通用化后的职责 |
|---|---|
| `odoo.py` | 每个 playbook 一个 bot 客户端，按 `(model, id)` 调用 |
| `ai_case.py`（原 `ai_sales.py`） | 事项 agent：提示词由内核规则、岗位卡、对外规则拼成；工具带风险等级 |
| `agents.py` | root 和助理，不变 |
| `resources.py`（新） | 资源注册表和各场景的资源提供者 |
| `gate.py` | 对外门控：承诺、陌生数字、`confidential`、频率 |
| `protocol.py` | 协议，资源表改为从注册表读取 |
| `service.py` | 群按事项绑定；上下文、侧栏、外部视图都读统一上下文 |
| `ai_customer.py` | 模拟外部联系人，人设按 playbook 区分 |
| `seed.py` | 遍历所有 playbook 的事项，建身份、群、agent 和默认授权 |

## 6. 三个场景的 playbook

### 6.1 外贸跟单（`trade`）

沿用原型的 12 步：打样、下单、付款或信用证、下生产单、生产跟踪、验货、包装唛头、订舱报关、装柜出运、装船通知、单据核算、归档复盘。其中付款这一步原来的 `lc_only` 改成按条件：`[("trade_payment_method", "=", "lc")]`；下单这一步的 `on_approve = confirm_order`。

### 6.2 应付发票审核（`ap`）

| 步骤 | 关卡 | agent 做什么 | 批准时 |
|---|---|---|---|
| 收票核对 | agent | 核对供应商、发票号、日期、金额、税；查重复发票 | — |
| 三单匹配 | 按条件：`ap_match_status != matched` | 逐行比对账单、采购单、实收数量和单价，写入匹配结论；一致就自己完成，有差异就请人决定 | — |
| 过账审批 | 人 | 汇总核对结果，推荐过账或退回 | `post_bill`：过账 |
| 付款安排 | agent | 按付款条款排入付款计划 | — |

外部联系人是供应商的对账联系人。对外规则：可以向供应商核对他自己的发票和采购单；不能透露别的供应商的价格、内部审批意见、付款优先级。

### 6.3 招聘候选人跟进（`hr`）

| 步骤 | 关卡 | agent 做什么 |
|---|---|---|
| 简历初筛 | 人 | 对照职位要求总结匹配点和疑点，推荐约面或婉拒 |
| 约面试 | agent | 在群里和候选人约时间（约定时间属于承诺，走草稿） |
| 收集面试反馈 | agent | 提醒面试官给反馈，汇总 |
| 录用决定 | 人 | 推荐录用与否，以及建议薪资（不超出薪资区间） |
| 发 Offer | 人 | 起草 offer 要点；由人确认后发出 |
| 入职准备 | agent | 入职清单 |

对外规则：不透露其他候选人、面试官的评价、薪资区间和公司给出的薪资（在人决定之前）。`confidential` 包括建议薪资和面试反馈原文。

## 7. 迁移方案

1. 新建 `work_case` 模块，`trade_sop` 升到 20.0.2.0.0，改为依赖它。
2. `trade_sop` 的迁移脚本：把 `trade_sop_step` 表里的每一行复制成 `work.step`，按步骤代码对应到新的阶段，保留状态、结论、问题、决定和决定人。
3. 旧模型 `trade.sop.stage` 和 `trade.sop.step` 删除，由 Odoo 在升级结束时清理。
4. 保留 `sop_agent_*` 作为 `work_agent_*` 的别名，DSH 批处理 agent 的 `sop` 命令行工具不用改。
5. 群聊库：`groups` 表加上 `res_model`、`res_id`、`playbook` 三列，已有的 10 个群回填为 `sale.order`、`trade`。agent、授权、往来记录不动。

## 8. 放权与评估（后续）

每个 playbook 的每一步，按四档逐步放权：影子、建议、审批后执行、低风险自动。升档看三个指标：批准率、修改幅度、驳回原因的分布。驳回意见沉淀为 playbook 的规则。按部门设大模型费用预算。

## 9. 已知风险

- 群聊服务目前是单进程、SQLite，适合几十个事项的演示，不适合全公司规模。
- agent 之间的连锁调用成本不可预测，现在只有频率上限，没有预算上限。
- “已经做了某事”的事实核验，本期只靠提示词和审核模型。
- playbook 写得好不好直接决定效果；需要业务人员参与写和改，后续要做 playbook 编辑和版本对比。
