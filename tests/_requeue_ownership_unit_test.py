"""Nightly integration: handoff mutations respect workflow ownership."""
import asyncio
import pathlib
import runpy
import tempfile

fixture = runpy.run_path(str(pathlib.Path(__file__).with_name(
    "_top_level_requeue_unit_test.py")))
chain = fixture["chain"]
FakeRequest = fixture["FakeRequest"]


async def scenario(root):
    chain._output_root = lambda: root
    run = "requeue_owned"
    store = chain._handoff_store()
    record = store.create(run, action="next_scene", scene=2,
                          handoff_id="ownership_probe")
    owner_a = "requeue-owner-a-1234567890"
    owner_b = "requeue-owner-b-1234567890"
    claim_a = chain.claim_project_ownership(root, run, owner_a, "Workflow A")

    def request(owner=None, epoch=None, **extra):
        result = FakeRequest(body={"run_name": run,
                                  "handoff_id": record["handoff_id"], **extra})
        result.headers = ({} if owner is None else {
            "X-H3-Workflow-Owner": owner,
            "X-H3-Ownership-Epoch": str(epoch)})
        return result

    assert (await chain._claim_handoff(request())).status == 423
    assert store.load(run, record["handoff_id"])["status"] == "pending"
    assert (await chain._claim_handoff(
        request(owner_a, claim_a["epoch"]))).status == 200

    claim_b = chain.claim_project_ownership(root, run, owner_b,
                                           "Workflow B", force=True)
    for route in (chain._transition_handoff, chain._release_handoff):
        assert (await route(request(owner_a, claim_a["epoch"],
                                    status="queued"))).status == 423
        assert store.load(run, record["handoff_id"])["status"] == "claimed"

    # Ownership may change after the route's preliminary check. The mutation
    # must check again under the existing project write guard.
    original_check = chain._project_write_rejection

    def takeover_after_check(req, run_name, operation):
        result = original_check(req, run_name, operation)
        chain.claim_project_ownership(root, run, owner_a,
                                      "Workflow A", force=True)
        return result

    chain._project_write_rejection = takeover_after_check
    try:
        assert (await chain._release_handoff(
            request(owner_b, claim_b["epoch"]))).status == 423
        assert store.load(run, record["handoff_id"])["status"] == "claimed"
    finally:
        chain._project_write_rejection = original_check

    current = chain.ownership_status(root, run, owner_a)
    assert (await chain._release_handoff(
        request(owner_a, current["epoch"]))).status == 200
    assert store.load(run, record["handoff_id"])["status"] == "pending"
    assert (await chain._list_handoffs(FakeRequest(
        query={"run_name": run}))).status == 200


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="h3-requeue-ownership-") as root:
        asyncio.run(scenario(root))
    print("Requeue ownership: read-only, stale epoch, commit fencing and legacy routes pass")
