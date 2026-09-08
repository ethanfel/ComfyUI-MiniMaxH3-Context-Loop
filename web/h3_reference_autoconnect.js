import {app} from "/scripts/app.js";
import {
    SCHEDULED_REF2VA_TYPE,
    VIDEO_REF_TYPE,
    migrateLegacyVideoScheduleWidgets,
    migrateReferenceComplianceWidget,
} from "./h3_reference_autoconnect_core.mjs?v=0.6.8";

const EXTENSION = "minimax_h3_context_loop.reference_autoconnect";

app.registerExtension({
    name: EXTENSION,
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === VIDEO_REF_TYPE) {
            const originalConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function (info) {
                const result = originalConfigure?.apply(this, arguments);
                migrateLegacyVideoScheduleWidgets(this, info);
                return result;
            };
        }
        if (nodeData.name === SCHEDULED_REF2VA_TYPE) {
            const originalConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function () {
                const result = originalConfigure?.apply(this, arguments);
                migrateReferenceComplianceWidget(this);
                return result;
            };
        }
    },
});
