# Plays the person who decides, in odoo shell: approve or reject the waiting step of a case.
# MODEL=sale.order (default) ORDER=S00025 STEP=po ACTION=approve|reject NOTE="..." odoo-bin shell -c odoo.conf -d de --no-http < scripts/human_decide.py
import os

order = env[os.environ.get("MODEL", "sale.order")].search([("name", "=", os.environ["ORDER"])])
step = order.work_step_ids.filtered(lambda s: s.code == os.environ["STEP"])
step.human_note = os.environ.get("NOTE", "")
if os.environ["ACTION"] == "approve":
    step.action_approve()
else:
    step.action_reject()
env.cr.commit()
print("decided", order.name, step.code, step.state, "state:", order.state, "current:", order.work_current_step_id.code)
