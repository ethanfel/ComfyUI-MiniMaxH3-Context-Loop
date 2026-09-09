// Shared request/selection protocol. Branch names are labels, never paths.
export function workingBranchId(value) {
    const id = String(value || "main");
    if (id !== "main" && !/^[0-9a-f]{32}$/.test(id)) throw new Error("Invalid working branch id.");
    return id;
}

export function branchRequestPath(path, id = "main") {
    if (workingBranchId(id) === "main") return path;
    return `${path}${path.includes("?") ? "&" : "?"}branch_id=${encodeURIComponent(workingBranchId(id))}`;
}

export function branchSelectionJson(value, id) {
    if (!value) return value;
    const selection = JSON.parse(value);
    const selected = workingBranchId(id);
    if (selected === "main") delete selection._branch_id;
    else selection._branch_id = selected;
    return JSON.stringify(selection);
}

export class StudioBranches {
    constructor({request, capture, apply, flush, changed, selected = "main"}) {
        Object.assign(this, {request, capture, apply, flush, changed});
        this.selected = workingBranchId(selected);
        this.records = [];
        this.defaultBranch = "main";
        this.busy = false;
        this.error = "";
        this.run = "";
        this.epoch = 0;
    }

    async refresh(run) {
        const epoch = ++this.epoch;
        this.run = run;
        const data = await this.request({action:"list", run_name:run});
        if (run !== this.run || epoch !== this.epoch) return;
        this.records = data.branches;
        this.defaultBranch = data.default_branch;
        this.changed();
    }

    async save() {
        const record = this.records.find(item => item.id === this.selected);
        if (!record) throw new Error("Refresh working branches before editing.");
        const saved = await this.request({action:"save", run_name:this.run,
            branch_id:this.selected, revision:record.revision, authoring:this.capture()});
        Object.assign(record, saved);
    }

    async perform(action) {
        if (this.busy) return;
        const run = this.run, epoch = this.epoch;
        const assertCurrent = () => {
            if (this.run !== run || this.epoch !== epoch) throw new Error("Project changed during the branch operation; refresh before continuing.");
        };
        this.busy = true; this.error = ""; this.changed();
        try {
            await this.flush();
            assertCurrent();
            await this.save();
            assertCurrent();
            await action(assertCurrent);
        } catch (error) {
            this.error = error?.message || String(error);
        } finally {
            this.busy = false; this.changed();
        }
    }

    async switchTo(id) {
        id = workingBranchId(id);
        if (id === this.selected) return;
        return this.perform(async (assertCurrent) => {
            const record = await this.request({action:"load", run_name:this.run, branch_id:id});
            assertCurrent();
            if (!record.authoring) throw new Error("This branch has no saved authoring snapshot yet.");
            await this.apply(record);
            this.selected = id;
        });
    }

    async create(name, throughScene = 0, newSeeds = false) {
        return this.perform(async (assertCurrent) => {
            const authoring = this.capture();
            if (newSeeds) {
                const plan = JSON.parse(authoring.plan_json);
                for (const shot of plan.shots) {
                    const words = crypto.getRandomValues(new Uint32Array(2));
                    shot.seed = ((BigInt(words[0]) << 32n) | BigInt(words[1])).toString();
                }
                authoring.plan_json = JSON.stringify(plan, null, 2);
            }
            const record = await this.request({action:"create", run_name:this.run,
                branch_id:this.selected, name, through_scene:throughScene, authoring});
            assertCurrent();
            await this.apply(record);
            this.selected = record.id;
            this.records.push(record);
        });
    }

    async makeDefault() {
        return this.perform(async (assertCurrent) => {
            const result = await this.request({action:"default", run_name:this.run, branch_id:this.selected});
            assertCurrent();
            this.defaultBranch = result.default_branch;
        });
    }
}
