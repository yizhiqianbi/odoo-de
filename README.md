# 数字员工

人在 Odoo「讨论」里和数字员工一对一说话，只在卡片上批准、驳回或确认。干活、记账和三方群聊都在后台。

三个场景接在同一套内核上：外贸跟单（`trade_sop`）、应付发票审核（`work_ap`）、招聘跟进（`work_hr`）。内核在 `addons/work_case`，群聊服务在 `chat/`。

## 目录

| 路径 | 内容 |
|---|---|
| `addons/work_case` | Playbook、步骤、起草的动作、动作账本、全局看板、讨论里的卡片 |
| `addons/trade_sop` `work_ap` `work_hr` | 三个场景 |
| `chat/` | 三方群聊、事项 agent、讨论里的助理 |
| `scripts/` | 装机器人、演示数据和检查脚本 |
| `docs/kernel-design.md` | 内核设计 |

## 不在这个仓库里

Odoo 20 社区版源码、Python 虚拟环境、数据库、日志、备份，以及 `secrets/` 和 `odoo.conf`（里面有数据库口令和主密码）。密钥只放在本机权限为 600 的文件里。

Odoo 源码单独克隆：

```bash
git clone --depth 1 -b 20.0 https://github.com/odoo/odoo.git odoo
```
