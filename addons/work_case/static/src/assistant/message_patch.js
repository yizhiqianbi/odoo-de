import { Message } from "@mail/core/common/message";
import { Message as MessageModel } from "@mail/core/common/message_model";

import { patch } from "@web/core/utils/patch";

import { WorkCard } from "./work_card";

Message.components = { ...Message.components, WorkCard };

patch(MessageModel.prototype, {
    setup() {
        super.setup(...arguments);
        this.work_ref = undefined;
    },
    get isWorkCard() {
        return /^work\.(step|proposal),/.test(this.work_ref || "");
    },
});
