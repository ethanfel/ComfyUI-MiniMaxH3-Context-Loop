"""Conditional prompt writes, durable retries and named-branch isolation."""
import concurrent.futures
import hashlib
import json
import pathlib
import shutil
import tempfile
import uuid

import prompt_history as history
from branch_scope import branch_scope

def command(store, root, scene, action, **fields):
    return dict(command_version=1, operation_id=uuid.uuid4().hex,
                base_revision=store.list(root, scene)["history_revision"], action=action, **fields)

with tempfile.TemporaryDirectory() as temporary:
    store = history.PromptHistoryStore(temporary)
    empty = store.list("film", "scene")
    assert empty["command_version"] == 1 and empty["working_branch_id"] == "main"
    assert not list(pathlib.Path(temporary).rglob("*.json"))
    first = command(store, "film", "scene", "save", prompt="First draft")
    out = store.command("film", "scene", first)
    root = out["history"]["active_revision"]
    assert store.command("film", "scene", first)["replayed"]
    assert len(store.list("film", "scene")["revisions"]) == 1
    try:
        store.command("film", "scene", {**first, "prompt": "Changed request"})
        raise AssertionError("Reused ID accepted different contents")
    except ValueError:
        pass
    stale = command(store, "film", "scene", "label", revision=root, label="Stale label")
    store.mark_executed("film", "scene", "First draft")
    try:
        store.command("film", "scene", stale)
        raise AssertionError("Execution failed to invalidate preview")
    except history.PromptHistoryConflict:
        pass
    fork = command(store, "film", "scene", "fork", revision=root, prompt="Child draft")
    child = store.command("film", "scene", fork)["history"]["active_revision"]
    assert store.get("film", "scene", root)["prompt"] == "First draft"
    assert store.get("film", "scene", child)["parent_id"] == root
    # Fork a mutable draft without rewriting it or collapsing identical text.
    newer = command(store, "film", "scene", "fork", revision=child, prompt="Child draft")
    new_id = store.command("film", "scene", newer)["history"]["active_revision"]
    assert new_id != child
    # A crash after the index commit, before returning, retains the receipt.
    crashing = command(store, "film", "scene", "fork", revision=new_id, prompt="Crash-safe child")
    original_write = history._atomic_json
    def crash_after_commit(path, value):
        original_write(path, value)
        if path.endswith("index.json"):
            raise OSError("Simulated lost acknowledgement")
    history._atomic_json = crash_after_commit
    try:
        store.command("film", "scene", crashing)
        raise AssertionError("Missing simulated failure")
    except OSError:
        pass
    finally:
        history._atomic_json = original_write
    count = len(store.list("film", "scene")["revisions"])
    assert history.PromptHistoryStore(temporary).command("film", "scene", crashing)["replayed"]
    assert len(store.list("film", "scene")["revisions"]) == count
    # A process can stop after replacing a mutable prompt file but before its
    # index/receipt. A new store must roll forward the durable intent on read.
    mutable_id = store.list("film", "scene")["active_revision"]
    interrupted = command(store, "film", "scene", "save", prompt="Recovered mutable text")
    def crash_between_files(path, value):
        original_write(path, value)
        if path.endswith(mutable_id + ".json"):
            raise OSError("Simulated process interruption before index")
    history._atomic_json = crash_between_files
    try:
        store.command("film", "scene", interrupted)
        raise AssertionError("Missing simulated interruption")
    except OSError:
        pass
    finally:
        history._atomic_json = original_write
    restarted = history.PromptHistoryStore(temporary)
    recovered = restarted.list("film", "scene")
    text = restarted.get("film", "scene", mutable_id)["prompt"]
    assert text == "Recovered mutable text"
    assert next(item for item in recovered["revisions"] if item["id"] == mutable_id)["prompt_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert restarted.command_status("film", "scene", interrupted["operation_id"])["receipt"]
    assert restarted.command("film", "scene", interrupted)["replayed"]
    assert len(restarted.list("film", "scene")["revisions"]) == count
    assert not list(pathlib.Path(temporary).rglob(".command-journal.json"))
    # Two authors reviewing the same index: exactly one change can commit.
    left = command(store, "film", "scene", "label", revision=root, label="Left")
    right = {**left, "operation_id": uuid.uuid4().hex, "label": "Right"}
    def apply(value):
        try:
            history.PromptHistoryStore(temporary).command("film", "scene", value)
            return True
        except history.PromptHistoryConflict:
            return False
    with concurrent.futures.ThreadPoolExecutor() as pool:
        assert sorted(pool.map(apply, [left, right])) == [False, True]
    # Named branch reads and commands cannot use Original's review stamp.
    branch = "1" * 32
    directory = pathlib.Path(temporary, "h3_chains", "film", "branches", branch)
    directory.mkdir(parents=True); (directory / "branch.json").write_text("{}")
    with branch_scope("film", branch):
        assert store.list("film", "scene")["working_branch_id"] == branch
        assert store.list("film", "scene")["revisions"] == []
        try:
            store.command("film", "scene", first)
            raise AssertionError("Original stamp accepted in named branch")
        except history.PromptHistoryConflict:
            pass
        separate = command(store, "film", "scene", "save", prompt="Branch prompt")
        store.command("film", "scene", separate)
    assert store.get("film", "scene", root)["prompt"] == "First draft"
    assert store.command_status("film", "scene", separate["operation_id"])["receipt"] is None
    copied_branch = "2" * 32
    copied = pathlib.Path(temporary, "h3_chains", "film", "branches", copied_branch)
    copied.mkdir(); (copied / "branch.json").write_text("{}")
    shutil.copytree(pathlib.Path(temporary, "h3_chains", "film", "prompt_history"), copied / "prompt_history")
    with branch_scope("film", copied_branch):
        assert store.command_status("film", "scene", first["operation_id"])["receipt"] is None
        try:
            store.command("film", "scene", first)
            raise AssertionError("Copied Original receipt replayed in another branch")
        except ValueError as error:
            assert "different project, scene or branch" in str(error)
    # All native restrictions still apply, including executed and parent deletion.
    for revision in (root, child):
        try:
            store.command("film", "scene", command(store, "film", "scene", "delete", revision=revision))
            raise AssertionError("Protected revision deleted")
        except ValueError:
            pass
    store.command("film", "scene", command(store, "film", "scene", "archive", revision=root, archived=True))
    assert store.get("film", "scene", root)["archived_at"]
    store.command("film", "scene", command(store, "film", "scene", "activate", revision=root))
    assert not store.get("film", "scene", root)["archived_at"]
print("Prompt history commands: conditional writes, forks, durable retries, races and branches passed")
