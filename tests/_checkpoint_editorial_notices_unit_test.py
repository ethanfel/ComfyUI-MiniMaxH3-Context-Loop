"""Polling notices are branch-scoped, bounded, and never edit project files."""
import concurrent.futures
import importlib.util
from pathlib import Path
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("notice_helpers", ROOT / "tests/_checkpoint_revision_unit_test.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
chain = helpers.chain

with tempfile.TemporaryDirectory() as temporary, patch.object(chain._LOG, "warning") as warning:
    helpers.folder_paths.output_directory = temporary
    notices = ["scene 1 final-cut alternate"]
    chain._CHECKPOINT_EDITORIAL_NOTICES.clear()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(lambda _: chain._log_checkpoint_editorial_notices("demo", notices), range(60)))
    assert warning.call_count == 1, "Concurrent polls must not flood the log"
    with chain.branch_scope("demo", "a" * 32):
        chain._log_checkpoint_editorial_notices("demo", notices)
    assert warning.call_count == 2, "Notices belong to the exact branch, not its label"
    chain._log_checkpoint_editorial_notices("demo", [])
    chain._log_checkpoint_editorial_notices("demo", notices)
    assert warning.call_count == 3, "A resolved then recurring notice is reported again"
    chain._log_checkpoint_editorial_notices("demo", ["scene 2 alternate draft"])
    assert warning.call_count == 4, "A changed notice is not swallowed"
    helpers.folder_paths.output_directory = str(Path(temporary) / "another-output")
    chain._log_checkpoint_editorial_notices("demo", notices)
    assert warning.call_count == 5, "Identical project names on different outputs remain independent"
    for index in range(300):
        chain._log_checkpoint_editorial_notices(f"project_{index}", notices)
    assert len(chain._CHECKPOINT_EDITORIAL_NOTICES) == 256
    assert list(Path(temporary).iterdir()) == [], "Notice deduplication never writes project data"
print("Checkpoint editorial notices: concurrent poll deduplication, scope, recurrence and bounded read-only cache pass")
