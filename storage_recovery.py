"""Verified V1 recovery copies of a rehearsal project, including its new work.

This is not an in-place rollback or production cutover. The source is never
rewritten. A checksum-bound immutable plan freezes the current file/control
state; an incomplete marker gates the new copy until verification finishes.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid

if __package__:
    from . import storage_resolver as resolver
    from . import storage_journal as progress
    from . import storage_ownership_recovery as ownership
    from .storage_rehearsal import _copy_root, _lock, sha256
    from .processing_persistence import atomic_json, sync_directory, publish_new_file, require_atomic_control_files
    from .storage_layout import OrganizedStorageLayout
else:
    import storage_resolver as resolver
    import storage_journal as progress
    import storage_ownership_recovery as ownership
    from storage_rehearsal import _copy_root, _lock, sha256
    from processing_persistence import atomic_json, sync_directory, publish_new_file, require_atomic_control_files
    from storage_layout import OrganizedStorageLayout

PLAN = "h3_legacy_recovery_plan_v2"
LEGACY_PLAN = "h3_legacy_recovery_plan_v1"
JOURNAL = "h3_legacy_recovery_journal_v1"
GATE = "h3_legacy_recovery_incomplete_v1"
PROOF = "h3_legacy_recovery_verified_v1"
RECOVERY = ".h3-storage-recovery"


def _all_rows(plan):
    return plan['rows'] + _portable_entries(plan) + ownership.entries(plan)


def _portable_entries(plan):
    if 'portable_retention' not in plan:
        return []
    if __package__:
        from .storage_retention_portability import entries
    else:
        from storage_retention_portability import entries
    return entries(plan)


def _files(root):
    result = {}
    for path in root.rglob("*"):
        address = path.relative_to(root).as_posix()
        resolver.confined(root, address)
        mode = path.stat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError("Recovery cannot copy a special file: " + address)
        result[address] = path
    return result


def _digest_stable(path):
    # Opening refreshes stale pre-open CIFS attributes. Keep the handle bound
    # across hashing and compare its identity plus the final path identity.
    with path.open('rb') as handle:
        before = resolver._stat_signature(os.fstat(handle.fileno()))
        digest = sha256(path)
        after = resolver._stat_signature(os.fstat(handle.fileno()))
    if after != before or resolver._signature(path) != before:
        raise ValueError("Source changed while hashing; prepare a fresh recovery plan.")
    return digest, before


def _authority(address):
    return address == "storage.json" or re.fullmatch(
        r"project/aliases/[0-9a-f]{32}\.json", address) is not None


def _capture(root):
    """Freeze current bytes, not the baseline that predates new renders/edits."""
    output = root.parent.parent
    resolver.storage_state(root)
    rows, locks, signatures, names = [], [], {}, {}
    for address, path in sorted(_files(root).items()):
        if address.endswith(".lock"):
            locks.append(address)
            continue  # A new independent project needs independent coordination.
        if _authority(address):
            target = RECOVERY + "/authority/" + address
            role = "authority"
        else:
            logical = resolver.logical_output(output, path)
            prefix = "h3_chains/" + root.name + "/"
            if not logical.startswith(prefix):
                raise ValueError("Recovery address escapes its project.")
            relative = logical[len(prefix):]
            if relative.split("/")[0] in ("media", "exports", "project", "storage.json"):
                raise ValueError("Unmapped organized file requires explicit classification: " + address)
            if resolver.resolve_output(output, logical) != path:
                raise ValueError("Legacy destination collision; no overwrite permitted: " + relative)
            target = prefix + relative
            role = "control" if path.suffix in (".json", ".txt") else "payload"
        resolver.confined(root, address)
        # Case-folding is also required on Linux: this plan may be used on NTFS.
        folded = target.casefold()
        if folded in names:
            raise ValueError("Case-colliding recovery destinations: " + target)
        names[folded] = address
        digest, signature = _digest_stable(path)
        signatures[address] = signature
        rows.append({"source": address, "target": target, "sha256": digest,
                     "size": path.stat().st_size, "role": role})
    for name in names:
        parts = name.split("/")
        if any("/".join(parts[:i]) in names for i in range(1, len(parts))):
            raise ValueError("File/directory recovery destination collision.")
    current = _files(root)
    if set(current) != set(signatures) | set(locks) or any(
            resolver._signature(current[name]) != value for name, value in signatures.items()):
        raise ValueError("Source namespace/control state changed during recovery preparation.")
    return rows, locks


def prepare_legacy_copy(receipt_path, destination_output, journal_directory, *, path_budget=None,
                        rehearsal_store=None, commit_protocol=None):
    """Prepare a new, separately located legacy copy. Never mutates the source."""
    source, _ = _copy_root(receipt_path)
    lab = Path(receipt_path).absolute().parent
    target = Path(destination_output).absolute()
    folder = Path(journal_directory).absolute()
    progress.protocol(commit_protocol)
    for path in (target, folder):
        if not path.is_relative_to(lab) or path == lab or path.is_relative_to(source.parent.parent):
            raise ValueError("Recovery target and journal must be new paths beside the source output.")
        resolver.confined(lab, path.relative_to(lab).as_posix())
        if commit_protocol is None:
            require_atomic_control_files(path)
        if path.exists():
            raise FileExistsError("Recovery destination already exists; no overwrite: " + str(path))
    if target.is_relative_to(folder) or folder.is_relative_to(target):
        raise ValueError("Recovery journal and destination cannot contain each other.")
    with _lock(source), resolver.rehearsal_access(source), ownership.source_guard(source):
        external = ownership.capture(source)
        source_state = {}
        if rehearsal_store is not None:
            if __package__:
                from .storage_project_recovery import capture_project
            else:
                from storage_project_recovery import capture_project
            rows, locks, source_state = capture_project(source, rehearsal_store)
        else:
            rows, locks = _capture(source)
        source_state['external_ownership'] = external
        all_rows = _all_rows(dict(source=str(source),rows=rows,**source_state))
        ownership.verify(source, {'external_ownership': external})
        # Legacy names can be long. On Windows require a conservative budget;
        # callers on other hosts can request the same portability check.
        budget = 240 if path_budget is None and os.name == "nt" else path_budget
        if budget is not None:
            policy = OrganizedStorageLayout(str(target), budget)
            for row in all_rows:
                policy.check_budget(row["target"])
            policy.check_atomic_json_budget("h3_chains/" + source.name + "/storage.json")
            policy.check_atomic_json_budget(RECOVERY + "/verified.json")
        authority_budget = 240 if budget is None else budget
        if commit_protocol is not None:
            for directory, addresses in (
                (target, (RECOVERY+'/owner.json', RECOVERY+'/verified.json',
                          'h3_chains/'+source.name+'/storage.json')),
                (folder, ('plan.json', 'journal.json', 'project/commits/000000000001.ack.json',
                          'partials/'+('f'*32)+'.part', 'target-staging/'+RECOVERY+'/owner.json',
                          'target-staging/h3_chains/'+source.name+'/storage.json'))):
                policy = OrganizedStorageLayout(str(directory), authority_budget)
                for address in addresses:
                    policy.check_atomic_json_budget(address)
        identifier = uuid.uuid4().hex
        control = [row for row in all_rows if row["role"] in ("control", "ownership_control")]
        control_digest = hashlib.sha256(json.dumps(control, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        folder.mkdir(parents=True)
        plan = {"format": PLAN, "operation_id": identifier,
                "receipt": str(Path(receipt_path).absolute()), "source": str(source),
                "destination": str(target), "rows": rows, "excluded_locks": locks,
                "control_state_sha256": control_digest, **source_state}
        if commit_protocol is not None:
            plan.update(commit_protocol=commit_protocol, authority_path_budget=authority_budget)
        _publish_document(folder/'plan.json', plan, commit_protocol, path_budget=authority_budget)
        journal = folder / "journal.json"
        initial = {"format": JOURNAL, "phase": "prepared",
            "operation_id": identifier, "plan_sha256": sha256(folder / "plan.json")}
        if commit_protocol is None:
            atomic_json(journal, initial)
        else:
            progress.create(journal, initial, path_budget=authority_budget)
        return journal


def _publish_document(path, value, protocol, *, folder=None, path_budget=240):
    if protocol is None:
        atomic_json(path, value)
    else:
        progress.publish_once(path, value, path_budget=path_budget,
                              staging_directory=folder/'partials' if folder else None)


def _reserve_logged_target(target, source, folder, gate, budget):
    """Publish an independently gated empty tree, never an unowned empty target.

    A crash before publication leaves only this operation's journal staging.
    A crash after publication leaves both ownership and the reader gate. No
    incomplete metadata staging appears in the recovered V1 namespace.
    """
    staged = resolver.confined(folder, 'target-staging')
    progress.state._mkdir(staged, folder)
    owner = RECOVERY+'/owner.json'
    marker = 'h3_chains/'+source.name+'/storage.json'
    if set(_files(staged)) - {owner, marker}:
        raise ValueError('Untracked recovery reservation files; no target adoption.')
    for address in (owner, marker):
        _publish_document(resolver.confined(staged, address), gate, progress.PROTOCOL,
                          folder=folder, path_budget=budget)
    sync_directory(staged)
    progress.state.publish_new_file(staged, target)
    sync_directory(target.parent)
    sync_directory(folder)


def _advance_journal(path, journal, **changes):
    updated = dict(journal, **changes)
    if journal.get('commit_protocol') is None:
        atomic_json(path, updated)
    else:
        progress.advance(path, journal, updated)
    return updated


def _load(journal_path):
    path = Path(journal_path).absolute()
    resolver.confined(path.parent, path.name)
    journal, _ = progress.bootstrap(path)  # no lock/write before scope validation
    plan_path = path.parent / "plan.json"
    resolver.confined(path.parent, "plan.json")
    plan, raw = resolver._json_bytes(plan_path)
    if (journal.get("format") != JOURNAL or plan.get("format") not in (PLAN, LEGACY_PLAN)
            or not re.fullmatch(r"[0-9a-f]{32}", str(plan.get("operation_id")))
            or journal.get("operation_id") != plan["operation_id"]
            or hashlib.sha256(raw).hexdigest() != journal.get("plan_sha256")):
        raise ValueError("Invalid or changed immutable recovery plan.")
    if plan['format'] == PLAN and 'external_ownership' not in plan:
        raise ValueError('Missing explicit external ownership recovery inventory.')
    source, _ = _copy_root(plan["receipt"])
    lab = Path(plan["receipt"]).absolute().parent
    target = Path(plan["destination"])
    if (str(source) != plan["source"] or not path.is_relative_to(lab) or path.parent == lab
            or not target.is_relative_to(lab) or target == lab
            or target.is_relative_to(source.parent.parent) or path.is_relative_to(source.parent.parent)
            or path.is_relative_to(target)
            or target.is_relative_to(path.parent)):
        raise ValueError("Recovery journal/target escapes the receipted copy lab.")
    resolver.confined(lab, path.relative_to(lab).as_posix())
    resolver.confined(lab, target.relative_to(lab).as_posix())
    protocol = progress.protocol(plan.get('commit_protocol'))
    if journal.get('commit_protocol') != protocol:
        raise ValueError('Recovery journal and plan use different commit protocols.')
    if protocol is None:
        require_atomic_control_files(path.parent)
        require_atomic_control_files(target)
    else:
        if journal.get('path_budget') != plan.get('authority_path_budget', 240):
            raise ValueError('Recovery authority budget differs from the immutable plan.')
        current = progress.read(path)
        if any(current.get(key) != journal.get(key) for key in progress._IDENTITY):
            raise ValueError('Immutable recovery journal identity changed.')
        journal = current
    return path, journal, plan, source, target


def _verify_source(plan, source):
    # Full hashes catch changed/removed source records, including current
    # authoring, registry additions, history and newly generated checkpoints.
    ownership.verify(source, plan)
    current = _files(source)
    expected = {row["source"] for row in plan["rows"]}
    if {p for p in current if not p.endswith(".lock")} != expected:
        raise ValueError("Source namespace changed since recovery plan; no publication.")
    signatures = {}
    for row in plan["rows"]:
        digest, signature = _digest_stable(current[row["source"]])
        signatures[row["source"]] = signature
        if digest != row["sha256"]:
            raise ValueError("Source state changed since recovery plan; no publication: " + row["source"])
    latest = _files(source)
    if ({p for p in latest if not p.endswith(".lock")} != expected or any(
            resolver._signature(latest[name]) != value for name, value in signatures.items())):
        raise ValueError("Source namespace/control state changed during recovery verification.")
    ownership.verify(source, plan)
    return signatures


def _verify_target(plan, target, *, gate):
    marker = "h3_chains/" + Path(plan["source"]).name + "/storage.json"
    coordination = {"h3_chains/.run_locks/" + Path(plan["source"]).name + ".lock"} | ownership.coordination(plan)
    expected = {row["target"] for row in _all_rows(plan)}
    allowed = expected | {RECOVERY + "/owner.json", RECOVERY + "/verified.json"} | coordination
    if gate:
        allowed.add(marker)
    actual = _files(target)
    if set(actual) - allowed or expected - set(actual):
        raise ValueError("Recovery copy has missing or untracked files; no publication.")
    expected_hashes = {row['target']: row['sha256'] for row in _all_rows(plan)}
    signatures = {}
    for name, path in actual.items():
        if name in coordination:
            # Opening the recovered V1 project creates this stable run lock,
            # outside the project tree. POSIX uses an empty file; Windows uses
            # one NUL byte. Never ignore arbitrary *.lock files or user data
            # merely because it occupies this coordination name.
            with path.open('rb') as handle:
                if handle.read(2) not in (b'', b'\0'):
                    raise ValueError('Unexpected recovery coordination lock contents; no overwrite.')
        digest, signatures[name] = _digest_stable(path)
        if name in expected_hashes and digest != expected_hashes[name]:
            raise ValueError("Recovery copy changed/corrupt; no overwrite: " + name)
    latest = _files(target)
    if set(latest) != set(actual) or any(
            resolver._signature(latest[name]) != value for name, value in signatures.items()):
        raise ValueError("Recovery copy changed during verification; no publication.")


def _copy_row(row, source, target, journal_folder, *, data=None):
    path = resolver.confined(target, row["target"])
    if path.exists():
        if not path.is_file() or sha256(path) != row["sha256"]:
            raise ValueError("Recovery destination collision; both copies kept: " + row["target"])
        sync_directory(path.parent)
        return False
    original = resolver.confined(source, row["source"]) if data is None else None
    partials = journal_folder / "partials"
    partials.mkdir(exist_ok=True)
    temporary = resolver.confined(partials, uuid.uuid4().hex + ".part")
    digest = hashlib.sha256()
    with (original.open("rb") if original is not None else io.BytesIO(data)) as src, temporary.open("xb") as dst:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(block)
            dst.write(block)
        dst.flush()
        os.fsync(dst.fileno())
    if digest.hexdigest() != row["sha256"]:
        raise ValueError("Source changed while copying; partial retained beside journal.")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Both destinations are private to this operation, under the project lock.
    # Interrupted partials remain outside the published output tree.
    if path.exists():
        raise FileExistsError("Recovery target appeared during copying; no overwrite.")
    # Atomic no-replace publication, even if an external process races us.
    # This links our independent staging bytes, NEVER the source artifact;
    # the staging name is immediately removed. Unsupported filesystems fail
    # safely with the partial retained, rather than falling back to overwrite.
    publish_new_file(temporary, path)
    sync_directory(path.parent)
    return True


def recover_legacy_copy(journal_path, *, after_copy=None):
    """Resume copying and publish only a verified, independently usable V1 tree."""
    path, journal, plan, source, target = _load(journal_path)
    with _lock(source), resolver.rehearsal_access(source), ownership.source_guard(source):
        loaded = _load(path)
        if loaded[2:] != (plan, source, target):
            raise ValueError('Recovery plan changed while waiting for the source lock.')
        journal = loaded[1]
        ownership.verify(source, plan)
        protocol = plan.get('commit_protocol')
        budget = plan.get('authority_path_budget', 240)
        if journal.get("phase") not in ("prepared", "copying", "verified", "published"):
            raise ValueError("Unknown legacy recovery phase.")
        if 'source_storage_format' in plan:
            if __package__:
                from .storage_project_recovery import validate_source
            else:
                from storage_project_recovery import validate_source
            validate_source(source, plan)
        else:
            resolver.storage_state(source)
        gate = {"format": GATE, "version": 1, "operation_id": plan["operation_id"],
                "plan_sha256": journal["plan_sha256"]}
        owner = target / RECOVERY / "owner.json"
        marker = target / "h3_chains" / source.name / "storage.json"
        proof_path = target / RECOVERY / "verified.json"
        proof = {"format": PROOF, "operation_id": plan["operation_id"],
                 "plan_sha256": journal["plan_sha256"], "files": len(_all_rows(plan)),
                 "control_state_sha256": plan["control_state_sha256"]}
        if journal["phase"] == "prepared":
            _verify_source(plan, source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(target.parent).free < sum(row["size"] for row in _all_rows(plan)):
                raise OSError("Insufficient space for an independent recovery copy.")
            resolver.confined(target, RECOVERY + "/owner.json")
            resolver.confined(target, marker.relative_to(target).as_posix())
            if protocol is not None and not target.exists():
                _reserve_logged_target(target, source, path.parent, gate, budget)
            if not target.exists():
                target.mkdir()  # exclusive directory reservation; never adopt a user folder
            elif not owner.is_file() or resolver._json_bytes(owner)[0] != gate:
                raise FileExistsError("Recovery destination is not owned by this operation.")
            if not owner.exists():
                _publish_document(owner, gate, protocol, folder=path.parent, path_budget=budget)
            if marker.exists() and resolver._json_bytes(marker)[0] != gate:
                raise ValueError("Recovery marker belongs to another operation.")
            _publish_document(marker, gate, protocol, folder=path.parent, path_budget=budget)
            if protocol is not None:
                # A prior directory rename may be visible despite a failed
                # acknowledgement. Retry its durability boundary, not only
                # the marker's nested directory, before accepting progress.
                sync_directory(target.parent)
                sync_directory(path.parent)
            journal = _advance_journal(path, journal, phase='copying')
        resolver.confined(target, RECOVERY + "/owner.json")
        resolver.confined(target, marker.relative_to(target).as_posix())
        resolver.confined(target, RECOVERY + "/verified.json")
        if not owner.is_file() or resolver._json_bytes(owner)[0] != gate:
            raise ValueError("Missing/changed recovery ownership proof.")
        if proof_path.exists() and resolver._json_bytes(proof_path)[0] != proof:
            raise ValueError("Recovery verification proof changed; no overwrite performed.")
        if not marker.exists():
            # Crash after removing the gate but before acknowledging completion.
            # Never reinstall a gate over a copy that may already be in use.
            if (journal["phase"] not in ("verified", "published") or not proof_path.is_file()
                    or resolver._json_bytes(proof_path)[0] != proof):
                raise ValueError("Recovery gate disappeared before verified publication.")
            _verify_target(plan, target, gate=False)
        else:
            if journal["phase"] == "published" or resolver._json_bytes(marker)[0] != gate:
                raise ValueError("Recovery gate changed; no overwrite performed.")
            _verify_source(plan, source)
            for index, row in enumerate(plan["rows"]):
                _copy_row(row, source, target, path.parent)
                if after_copy:
                    after_copy(index + 1)
            portable = _portable_entries(plan)
            for index,row in enumerate(portable,start=len(plan['rows'])+1):
                raw = progress.state._encode(plan['portable_retention'][row['source']])
                _copy_row(row,source,target,path.parent,data=raw)
                if after_copy:
                    after_copy(index)
            ownership.copy_files(source, target, path.parent, plan, after_copy=after_copy, offset=len(portable))
            _verify_target(plan, target, gate=True)
            _verify_source(plan, source)
            # Recheck our authority after the long copy/hash phase. Never
            # overwrite a proof placed there or release a gate changed while
            # that work was in progress, even by a non-cooperating process.
            for authority, expected in ((owner, gate), (marker, gate), (proof_path, proof)):
                resolver.confined(target, authority.relative_to(target).as_posix())
                if authority == proof_path and not authority.exists():
                    continue
                if not authority.is_file() or resolver._json_bytes(authority)[0] != expected:
                    raise ValueError("Recovery ownership/gate/proof changed before publication.")
            _publish_document(proof_path, proof, protocol, folder=path.parent, path_budget=budget)
            journal = _advance_journal(path, journal, phase='verified')
            if resolver._json_bytes(marker)[0] != gate:
                raise ValueError("Recovery marker changed before publication.")
            marker.unlink()  # our temporary gate only; user/source files are never removed
            sync_directory(marker.parent)
        _advance_journal(path, journal, phase='published')
        return {"destination": str(target), "files": len(_all_rows(plan)),
                "excluded_locks": len(plan["excluded_locks"]),
                "control_state_sha256": plan["control_state_sha256"], "source_unchanged": True}
