"""Opt-in actual-backend filesystem probe, never an existing chain.

Use a NEW mkdtemp '.h3-commit-log-test-*' directory. Writes are audit-confined
there. Dummy controls/media only; no GPU, model or user workflow. Results are
retained, not recursively deleted. This is not a server power-loss test.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid


def guard_for(root):
    def guard(event, args):
        paths = []
        if event == 'open':
            path, mode, flags = args
            if isinstance(path, (str, bytes, os.PathLike)) and flags & (
                    os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                paths = [path]
        elif event in ('os.rename', 'os.link', 'os.symlink'):
            paths = args[:2]
        elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.truncate', 'os.chmod', 'os.utime'):
            paths = args[:1]
        for path in paths:
            if not Path(os.fsdecode(path)).resolve().is_relative_to(root):
                raise PermissionError('Commit probe write outside isolated directory rejected')
    return guard


def main():
    root = Path(sys.argv[1]).absolute()
    if not root.name.startswith('.h3-commit-log-test-') or not root.is_dir() or root.is_symlink():
        raise ValueError('Commit probe requires its own new diagnostic directory.')
    sys.addaudithook(guard_for(root))
    import storage_state as state
    import storage_project as project
    import storage_commit_log as ledger
    import processing_persistence as persistence
    run = root/'h3_chains'/'probe'
    address, second = 'branches/main.json', 'checkpoints/clip_0001.json'
    def changes(value):
        return {name: {'data': state._encode({'value': value, 'seed': 18446744073709551613,
                            'prompt': 'Private storage probe. 雪'}), 'scope': 'branch:main',
                       'category': 'branches', 'immutable': False} for name in (address, second)}
    if len(sys.argv) > 2:
        stage, op = sys.argv[2:4]
        with state.control_rehearsal_access(run):
            store = project.ProjectStore(run)
            base = store.snapshot()
            def stop(point):
                if point == stage:
                    os._exit(19)
            store.commit(base, changes(stage), operation_id=op, after_stage=stop)
        return
    if run.parent.exists() or (root/'report.json').exists():
        raise ValueError('Probe refuses existing data/results; use a new diagnostic directory.')
    started = time.monotonic()
    report = {'directory': str(root), 'kernel': os.uname().release if hasattr(os, 'uname') else os.name,
              'platform': sys.platform, 'python': sys.version.split()[0], 'checks': [],
              'chain_files_modified': False, 'power_loss_tested': False, 'errors': []}
    def check(name, value):
        report['checks'].append({'name': name, 'passed': bool(value)})
        if not value:
            raise AssertionError(name)
    try:
        run.mkdir(parents=True, exist_ok=False)
        report['filesystem'] = persistence._linux_mount_type(run) if sys.platform.startswith('linux') else 'unmeasured'
        documents = {}
        for name, item in changes('initial').items():
            ref = state._immutable(run, 'project/branches/'+uuid.uuid4().hex+'.json', item['data'], 240)
            documents[name] = {k: v for k, v in item.items() if k != 'data'} | {'file': ref}
        initial = {'format': state.ROOT, 'run_name': 'probe', 'epoch': 1, 'generation': 0,
                   'documents': documents, 'scope_revisions': {'branch:main': uuid.uuid4().hex},
                   'operations': {}, 'parent': None}
        ref = state._immutable(run, 'project/roots/'+uuid.uuid4().hex+'.json', state._encode(initial), 240)
        marker = {'format': project.FORMAT, 'version': 1, 'run_name': 'probe',
                  'mode': project.ProjectStore.MODE, 'phase': 'ready', 'path_budget': 240,
                  'root': ref, 'epoch': 1, 'generation': 0, 'commit_protocol': ledger.PROTOCOL}
        state._immutable(run, 'storage.json', state._encode(marker), 240)
        bootstrap = (run/'storage.json').read_bytes()
        with state.control_rehearsal_access(run):
            store, reads, failures = project.ProjectStore(run), [], []
            base = store.snapshot()
            done, ready = threading.Event(), threading.Event()
            def reader():
                try:
                    with state.control_rehearsal_access(run):
                        ready.set()
                        while not done.is_set():
                            snap = store.snapshot()
                            if snap.read(address) != snap.read(second):
                                raise ValueError('Mixed control generation')
                            if json.loads(snap.read(address))['seed'] != 18446744073709551613:
                                raise ValueError('Seed changed')
                            reads.append(snap.state['generation'])
                            done.wait(0.005)
                except BaseException as error:
                    failures.append(type(error).__name__+': '+str(error))
            thread = threading.Thread(target=reader, daemon=True)
            thread.start()
            if not ready.wait(5):
                raise TimeoutError('Probe reader did not start')
            try:
                for index in range(12):
                    store.commit(store.snapshot(), changes(index), operation_id=uuid.uuid4().hex)
            finally:
                done.set()
                thread.join(20)
            check('concurrent reads contain complete exact controls', reads and not failures and not thread.is_alive())
            report['reads'] = len(reads)
            check('twelve commits accepted', store.snapshot().state['generation'] == 12)
            check('historical pin unchanged', json.loads(base.read(address))['value'] == 'initial')
            for stage in ('document', 'root', 'commit', 'acknowledged'):
                pinned, op = store.snapshot(), uuid.uuid4().hex
                child = subprocess.run([sys.executable, '-B', '-S', __file__, str(root), stage, op],
                    capture_output=True, text=True, timeout=90)
                check('process terminated at '+stage, child.returncode == 19)
                current = store.snapshot()
                check('crash visibility at '+stage, current.state['generation'] ==
                      pinned.state['generation']+(1 if stage in ('commit', 'acknowledged') else 0))
                first = store.commit(pinned, changes(stage), operation_id=op)
                duplicate = store.commit(pinned, changes(stage), operation_id=op)
                check('exact retry at '+stage, first == duplicate and
                      store.snapshot().state['generation'] == pinned.state['generation']+1)
            source = root/'independent-source.bin'
            with source.open('xb') as handle:
                handle.write(b'private dummy video payload, not a real render')
                handle.flush()
                os.fsync(handle.fileno())
            pinned = store.snapshot()
            target = 'media/generation/'+uuid.uuid4().hex+'/video.mp4'
            logical = 'segments/clip_0001.'+uuid.uuid4().hex+'.mp4'
            staged = store.stage_payload(logical, source, target, scope='branch:main', operation_id=uuid.uuid4().hex)
            check('staged media not visible', not project.payload_catalog(store.snapshot()))
            store.commit_artifacts(pinned, changes('media accepted'), [staged], operation_id=uuid.uuid4().hex)
            check('media and controls accepted together', store.verify_payloads() == 1 and
                  json.loads(store.snapshot().read(address))['value'] == 'media accepted')
            check('media is independent', (source.stat().st_dev, source.stat().st_ino) !=
                  ((run/target).stat().st_dev, (run/target).stat().st_ino))
            check('bootstrap never overwritten', (run/'storage.json').read_bytes() == bootstrap)
            # Inject tail loss by preserving the dummy commit outside authority.
            head = ledger.CommitLog(run, bootstrap).read()
            os.rename(run/head.reference['path'], root/'withheld-last-commit.json')
            try:
                store.snapshot()
            except ValueError as error:
                check('missing acknowledged tail rejected', 'Missing immutable commit' in str(error))
            else:
                check('missing acknowledged tail rejected', False)
            persistence.publish_new_file(root/'withheld-last-commit.json', run/head.reference['path'])
            persistence.sync_directory((run/head.reference['path']).parent)
            check('restored diagnostic record validates', store.snapshot().verify() == 3)
        # Exercise the actual importer too, not only the ready-store fixture.
        original = root/'dummy-original/h3_chains/import_probe'
        copied = root/'dummy-copy/h3_chains/import_probe'
        raw = state._encode({'seed': 18446744073709551613, 'prompt': 'Private import probe. 雪'})
        for directory in (original, copied):
            directory.mkdir(parents=True)
            state._immutable(directory, 'branches/main.json', raw, 240)
        receipt = {'source': str(original), 'copy': str(copied), 'independent_copies': True}
        with (root/'import-receipt.json').open('x') as handle:
            json.dump(receipt, handle)
            handle.flush()
            os.fsync(handle.fileno())
        imported = state.create_control_rehearsal(root/'import-receipt.json', root/'imported-output',
            {'branches/main.json': {'source': 'branches/main.json', 'sha256': state._hash(raw),
                'scope': 'branch:main', 'category': 'branches', 'immutable': False}}, commit_protocol=ledger.PROTOCOL)
        with state.control_rehearsal_access(imported.project):
            check('actual importer keeps exact bytes', imported.snapshot().read('branches/main.json') == raw)
            bootstrap = (imported.project/'storage.json').read_bytes()
            check('write-once import gate remains while accepted state is ready',
                  json.loads(bootstrap)['phase'] == 'building' and imported._marker()[0]['phase'] == 'ready')
            imported.commit(imported.snapshot(), {'branches/main.json': changes('after import')[address]},
                            operation_id=uuid.uuid4().hex)
            check('imported store accepts edits without replacing bootstrap',
                  imported.snapshot().state['generation'] == 1 and
                  (imported.project/'storage.json').read_bytes() == bootstrap and
                  (copied/'branches/main.json').read_bytes() == raw and (original/'branches/main.json').read_bytes() == raw)
        # Qualify the actual copy/journal services on the same filesystem.
        import storage_project_migration as migration
        import storage_recovery as recovery
        import storage_journal as progress
        from unittest.mock import patch
        media_address = 'segments/clip_0001.'+'a'*32+'.mp4'
        media_bytes = b'Independent dummy media for migration/recovery qualification.'
        for directory in (original, copied):
            state._immutable(directory, media_address, media_bytes, 240)
        joined = state.create_control_rehearsal(root/'import-receipt.json', root/'joined-output',
            {'branches/main.json': {'source': 'branches/main.json', 'sha256': state._hash(raw),
                'scope': 'branch:main', 'category': 'branches', 'immutable': False}}, commit_protocol=ledger.PROTOCOL)
        with state.control_rehearsal_access(joined.project):
            copied_before = {name: recovery.sha256(path) for name, path in recovery._files(copied).items()
                             if not name.endswith('.lock')}
            bootstrap = (joined.project/'storage.json').read_bytes()
            path = migration.prepare_join(root/'import-receipt.json', joined, root/'join-journal',
                {media_address: {'target': 'media/generation/'+'a'*32+'/video.mp4',
                                 'scope': 'branch:main', 'immutable': True}})
            def interrupt(_):
                raise OSError('deliberate copy interruption')
            try:
                migration.join_payloads(path, after_copy=interrupt)
            except OSError as error:
                check('forward copy deliberately interrupted', str(error) == 'deliberate copy interruption')
            else:
                check('forward copy deliberately interrupted', False)
            store = project.ProjectStore(joined.project)
            try:
                store.snapshot()
            except ValueError as error:
                check('interrupted forward copy is gated', 'incomplete' in str(error))
            else:
                check('interrupted forward copy is gated', False)
            migration.join_payloads(path)
            check('forward copy resumed with immutable journal', store.verify_payloads() == 1 and
                  progress.read(path)['phase'] == 'published' and (joined.project/'storage.json').read_bytes() == bootstrap)
            store.commit(store.snapshot(), {'branches/main.json': changes('after join')[address]},
                         operation_id=uuid.uuid4().hex)
            newest = store.snapshot()
            migration.join_payloads(path)
            check('completed join retry preserves newer edits', store.snapshot().reference == newest.reference)
            progress.publish_once(root/'joined-receipt.json', {'source': str(copied),
                'copy': str(joined.project), 'independent_copies': True})
            target = root/'recovered-output'
            reverse = recovery.prepare_legacy_copy(root/'joined-receipt.json', target, root/'reverse-journal',
                rehearsal_store=store, commit_protocol=ledger.PROTOCOL)
            real_publish = state.publish_new_file
            for name in ('owner.json', 'verified.json'):
                if name == 'verified.json':
                    real_sync = recovery.sync_directory
                    def fail_directory(directory):
                        if Path(directory) == target.parent:
                            raise OSError('deliberate directory flush failure')
                        return real_sync(directory)
                    with patch.object(recovery, 'sync_directory', fail_directory):
                        for attempt in range(2):
                            try:
                                recovery.recover_legacy_copy(reverse)
                            except OSError as error:
                                check('directory flush enforced on attempt '+str(attempt),
                                      str(error) == 'deliberate directory flush failure' and
                                      progress.read(reverse)['phase'] == 'prepared')
                            else:
                                check('directory flush enforced on attempt '+str(attempt), False)
                def fail_publication(source, destination):
                    if Path(destination).name == name:
                        raise OSError('deliberate '+name+' publication failure')
                    return real_publish(source, destination)
                with patch.object(state, 'publish_new_file', fail_publication):
                    try:
                        recovery.recover_legacy_copy(reverse)
                    except OSError as error:
                        check('reverse copy retained at '+name, str(error) == 'deliberate '+name+' publication failure')
                    else:
                        check('reverse copy retained at '+name, False)
                check('no incomplete authority in recovery target at '+name,
                      not target.exists() if name == 'owner.json' else not list(target.rglob('.tmp-*')))
            reverse_result = recovery.recover_legacy_copy(reverse)
            restored = target/'h3_chains/import_probe'
            check('reverse copy resumed and released gate', not (restored/'storage.json').exists() and
                  progress.read(reverse)['phase'] == 'published')
            check('reverse copy preserves exact new authoring and media',
                  (restored/'branches/main.json').read_bytes() == newest.read('branches/main.json') and
                  (restored/media_address).read_bytes() == media_bytes)
            media = store.payload_path(newest, media_address)
            check('reverse media independently copied', (media.stat().st_dev, media.stat().st_ino) !=
                  ((restored/media_address).stat().st_dev, (restored/media_address).stat().st_ino))
            from checkpoint_manager import checkpoint_run_lock
            with checkpoint_run_lock(str(target), 'import_probe'):
                pass
            check('normal checkpoint reader lock preserves recovery retry',
                  recovery.recover_legacy_copy(reverse) == reverse_result)
            from project_ownership import ownership_status
            unowned_status = ownership_status(target, copied.name)
            check('unowned status reader lock preserves recovery retry', not unowned_status['enabled'] and
                  recovery.recover_legacy_copy(reverse) == reverse_result)
            check('migration source untouched', copied_before ==
                  {name: recovery.sha256(path) for name, path in recovery._files(copied).items() if not name.endswith('.lock')})
            # The normal service constructors now share an operation-local
            # root. Exercise this on the live filesystem, without installing
            # code or activating any existing project.
            import asyncio
            from storage_runtime import runtime_access
            from working_branches import WorkingBranches
            from checkpoint_manager import CheckpointGraphManager
            from checkpoint_variants import saved_checkpoint_variants
            runtime_output, runtime_run = joined.project.parent.parent, joined.project.name
            authored = {'width': 960, 'height': 544, 'base_seed': '18446744073709551615',
                'plan_json': json.dumps({'shots': [{'id': 'scene_1', 'prompt': 'Runtime probe 雪',
                                                    'seed': '18446744073709551613'}]})}
            record = {'format': 'h3_working_branch_v1', 'id': 'main', 'run_name': runtime_run,
                      'name': 'Original', 'revision': uuid.uuid4().hex, 'authoring_version': 2,
                      'authoring': authored}
            store.commit(store.snapshot(), {'branches/main.json': {'data': state._encode(record),
                'scope': 'branch:main', 'category': 'branches', 'immutable': False}}, operation_id=uuid.uuid4().hex)
            with runtime_access(store, branch_writes=True) as runtime:
                from contextvars import copy_context
                inherited = copy_context()
                old_pin = runtime.pin
                branches = WorkingBranches(runtime_output, runtime_run)
                check('runtime normal branch load keeps exact seed and canvas', branches.load() == record)
                saved = branches.save('main', dict(authored, base_seed='18446744073709551611'),
                                      record['revision'], uuid.uuid4().hex)
                check('runtime same-operation reads stay pinned after save', branches.load() == record)
                graph = CheckpointGraphManager(runtime_output).graph(runtime_run, adopt_legacy=False)
                check('runtime normal graph and processing catalogue share reader', not graph['revisions'] and
                      not saved_checkpoint_variants(runtime_output, runtime_run, graph['revisions'])['variants'])
            try:
                inherited.run(branches.load)
            except ValueError as error:
                check('runtime closed operation revokes inherited task contexts', 'escaped' in str(error))
            else:
                check('runtime closed operation revokes inherited task contexts', False)
            async def worker_read():
                return await asyncio.wait_for(asyncio.to_thread(
                    lambda: WorkingBranches(runtime_output, runtime_run).load()), timeout=10)
            with runtime_access(store):
                check('runtime next worker sees complete accepted branch edit', asyncio.run(worker_read()) == saved)
            with runtime_access(store, pin=old_pin, branch_writes=True):
                before = store.snapshot().reference
                try:
                    WorkingBranches(runtime_output, runtime_run).save('main', authored, record['revision'])
                except state.StateConflict:
                    check('runtime old pin cannot clobber a newer save', store.snapshot().reference == before)
                else:
                    check('runtime old pin cannot clobber a newer save', False)
            check('runtime branch edit keeps bootstrap and media intact',
                  (joined.project/'storage.json').read_bytes() == bootstrap and store.verify_payloads() == 1)
            import project_ownership as ownership
            from storage_ownership import authority_directory
            from unittest.mock import patch
            owner_a, owner_b = 'probe-owner-a-1234567890', 'probe-owner-b-1234567890'
            before = store.snapshot().reference
            with runtime_access(store, branch_writes=True, ownership_writes=True) as runtime:
                owned = ownership.claim_project_ownership(runtime_output, runtime_run, owner_a)
                proof = {'owner_id': owner_a, 'epoch': owned['epoch']}
                check('ownership claim uses external no-overwrite authority', owned['owned_by_requester'] and
                      authority_directory(runtime_output, runtime_run).is_dir())
                ownership_pin = runtime.pin
                directory = authority_directory(runtime_output, runtime_run)
                versions = {path: path.read_bytes() for path in directory.rglob('*') if path.is_file()}
                with patch.object(ledger.CommitLog, 'acknowledge', side_effect=OSError('lost ownership ack')):
                    try:
                        ownership.claim_project_ownership(runtime_output, runtime_run, owner_b, force=True)
                    except OSError:
                        pass
                    else:
                        raise AssertionError('Expected lost ownership acknowledgement.')
                retry = ownership.claim_project_ownership(runtime_output, runtime_run, owner_b, force=True)
                check('ownership takeover retry preserves accepted owner and epoch',
                      retry['owned_by_requester'] and retry['epoch'] == proof['epoch']+1)
                check('ownership changes preserve earlier authority bytes',
                      all(path.read_bytes() == raw for path, raw in versions.items()))
                newer_proof = {'owner_id': owner_b, 'epoch': retry['epoch']}
                entered, finished = threading.Event(), threading.Event()
                failures = []
                with ownership.project_write_guard(runtime_output, runtime_run, newer_proof):
                    inherited_guard = copy_context()
                    def takeover():
                        entered.set()
                        try:
                            inherited_guard.run(ownership.claim_project_ownership,
                                runtime_output, runtime_run, owner_a, force=True)
                        except BaseException as error:
                            failures.append(str(error))
                        finally:
                            finished.set()
                    worker = threading.Thread(target=takeover)
                    worker.start()
                    if not entered.wait(5):
                        raise AssertionError('Ownership contender did not start.')
                    check('ownership contender waits for protected commit', not finished.wait(.05))
                worker.join(10)
                check('ownership contender succeeds after guard releases', not worker.is_alive() and not failures)
            with runtime_access(store, pin=ownership_pin):
                try:
                    ownership.require_project_ownership(runtime_output, runtime_run, newer_proof)
                except ownership.ProjectOwnershipError:
                    check('historical runtime pin cannot restore former owner', True)
                else:
                    check('historical runtime pin cannot restore former owner', False)
            check('ownership-only operations preserve project root and payloads',
                  store.snapshot().reference == before and store.verify_payloads() == 1)
            owned_target, owned_journal = root/'owned-recovery-output', root/'owned-recovery-journal'
            with runtime_access(store):
                source_owner = ownership.ownership_status(runtime_output, runtime_run, owner_a)
            owned_reverse = recovery.prepare_legacy_copy(root/'joined-receipt.json', owned_target, owned_journal,
                rehearsal_store=store, commit_protocol=ledger.PROTOCOL)
            owned_plan = json.loads((owned_journal/'plan.json').read_text())
            check('recovery plan explicitly inventories external ownership',
                  owned_plan['format'] != recovery.LEGACY_PLAN and
                  bool(owned_plan['external_ownership']['files']))
            active_path = owned_target/'h3_chains/.project_ownership'/f'{runtime_run}.json'
            actual_publish = recovery.publish_new_file
            def lose_ownership_ack(source, destination):
                result = actual_publish(source, destination)
                if Path(destination) == active_path:
                    raise OSError('deliberate recovered ownership ack failure')
                return result
            with patch.object(recovery, 'publish_new_file', lose_ownership_ack):
                try:
                    recovery.recover_legacy_copy(owned_reverse)
                except OSError as error:
                    check('ownership recovery interrupted after file publication',
                          'recovered ownership ack failure' in str(error) and
                          (owned_target/'h3_chains'/runtime_run/'storage.json').is_file())
                else:
                    raise AssertionError('Expected ownership recovery publication failure.')
            active_bytes = active_path.read_bytes()
            recovered_owner = recovery.recover_legacy_copy(owned_reverse)
            check('ownership recovery resumes without overwriting effective fence',
                  active_path.read_bytes() == active_bytes and
                  progress.read(owned_reverse)['phase'] == 'published')
            verified_owner = ownership.ownership_status(owned_target, runtime_run, owner_a)
            check('recovered ownership keeps latest owner and epoch', verified_owner['owned_by_requester'] and
                  verified_owner['epoch'] == source_owner['epoch'])
            try:
                ownership.require_project_ownership(owned_target, runtime_run, None)
            except ownership.ProjectOwnershipError:
                check('recovered project rejects anonymous writes', True)
            else:
                check('recovered project rejects anonymous writes', False)
            check('normal recovered ownership reader lock preserves retry',
                  recovery.recover_legacy_copy(owned_reverse) == recovered_owner)
            check('owned recovery keeps latest controls media and source root',
                  (owned_target/'h3_chains'/runtime_run/media_address).read_bytes() == media_bytes and
                  (owned_target/'h3_chains'/runtime_run/'branches/main.json').read_bytes() ==
                      store.snapshot().read('branches/main.json') and store.snapshot().reference == before)
            from storage_carriers import node_host, PIN_KEY
            from branch_scope import scoped_node
            @scoped_node
            def carrier_start(run_name, project_assets=None):
                run_name = (project_assets or {}).get('project') or run_name
                return ({'run_name': run_name, 'saved': WorkingBranches(runtime_output, run_name).load()},)
            @scoped_node
            async def carrier_read(state):
                value = await asyncio.wait_for(asyncio.to_thread(
                    lambda: WorkingBranches(runtime_output, runtime_run).load()), timeout=10)
                return ({'run_name': runtime_run, 'saved': value},)
            @scoped_node
            def carrier_merge(state, manifest):
                raise AssertionError('Conflicting roots reached a node.')
            with node_host(store):
                current_carrier = carrier_start(runtime_run)[0]
            check('first node stamps current main storage pin',
                  current_carrier[PIN_KEY]['root'] == before and current_carrier['_branch_id'] == 'main')
            with node_host(store):
                routed = carrier_start('stale_widget', {'project': runtime_run})[0]
            check('connected project overrides old run widget for carriers', routed == current_carrier)
            with runtime_access(store) as bound:
                malformed = dict(bound.pin, epoch=float(bound.pin['epoch']))
                try:
                    asyncio.run(carrier_read({'run_name': runtime_run, PIN_KEY: malformed}))
                except ValueError as error:
                    check('active node binding rejects float epoch identity', 'active runtime pin' in str(error))
                else:
                    check('active node binding rejects float epoch identity', False)
            historical_carrier = {'run_name': runtime_run, '_branch_id': 'main', PIN_KEY: old_pin}
            serialized = json.dumps(historical_carrier)
            with node_host(store):
                history_output = asyncio.run(carrier_read(json.loads(serialized)))[0]
                next_output = asyncio.run(carrier_read(current_carrier))[0]
                captured_host = copy_context()
                try:
                    carrier_merge(historical_carrier, current_carrier)
                except ValueError as error:
                    check('node merge rejects mixed storage roots', 'mixed-root' in str(error))
                else:
                    check('node merge rejects mixed storage roots', False)
            check('independent async node keeps historical prompt and exact seed', history_output['saved'] == record and
                  history_output[PIN_KEY] == old_pin)
            check('fresh node keeps new settings without altering old carrier', next_output['saved'] == saved and
                  json.dumps(historical_carrier) == serialized)
            for name, operation in (
                    ('pin alone cannot authorize a node', lambda: asyncio.run(carrier_read(historical_carrier))),
                    ('closed node host revokes inherited contexts', lambda: captured_host.run(carrier_start, runtime_run))):
                try:
                    operation()
                except ValueError:
                    check(name, True)
                else:
                    check(name, False)
            check('node carrier reads preserve root bootstrap and payloads', store.snapshot().reference == before and
                  (joined.project/'storage.json').read_bytes() == bootstrap and store.verify_payloads() == 1)
            import copy
            @scoped_node
            def carrier_save(state, operation_id=''):
                branches = WorkingBranches(runtime_output, runtime_run)
                old = branches.load()
                authored = copy.deepcopy(old['authoring'])
                authored['base_seed'] = '18446744073709551607'
                plan = json.loads(authored['plan_json'])
                plan['shots'][0]['prompt'] = 'Saved by isolated writer probe. 雪'
                plan['shots'][0]['seed'] = '18446744073709551609'
                authored['plan_json'] = json.dumps(plan, ensure_ascii=False)
                saved_result = branches.save('main', authored, old['revision'], operation_id or uuid.uuid4().hex)
                return (dict(state, saved=saved_result),)
            owner_proof = {'owner_id': owner_a, 'epoch': source_owner['epoch']}
            with node_host(store, branch_writers=(carrier_save,)):
                try:
                    carrier_save(current_carrier)
                except ownership.ProjectOwnershipError:
                    check('writer without current proof cannot publish', store.snapshot().reference == before)
                else:
                    check('writer without current proof cannot publish', False)
                writer_input = dict(current_carrier, _project_ownership=owner_proof)
                writer_output = carrier_save(writer_input)[0]
            writer_root = store.snapshot().reference
            check('writer returns its accepted root without mutating cached input',
                  writer_output[PIN_KEY]['root'] == writer_root and writer_root != before and
                  writer_input[PIN_KEY]['root'] == before)
            with node_host(store):
                saved_output = asyncio.run(carrier_read(writer_output))[0]
                old_output = asyncio.run(carrier_read(current_carrier))[0]
            authored_plan = json.loads(saved_output['saved']['authoring']['plan_json'])
            check('next node gets exact saved prompt seed and settings',
                  saved_output['saved'] == writer_output['saved'] and
                  authored_plan['shots'][0]['seed'] == '18446744073709551609' and
                  authored_plan['shots'][0]['prompt'] == 'Saved by isolated writer probe. 雪')
            check('old pinned node keeps original authoring after save', old_output['saved'] == saved)
            with node_host(store, branch_writers=(carrier_save,)):
                try:
                    carrier_save(writer_input)
                except state.StateConflict:
                    check('stale queued writer cannot overwrite accepted settings', store.snapshot().reference == writer_root)
                else:
                    check('stale queued writer cannot overwrite accepted settings', False)
            @scoped_node
            def carrier_parent(state):
                return carrier_save(state)
            for name, grants in (
                    ('nested helper does not inherit parent writer grant', (carrier_parent,)),
                    ('nested writer cannot elevate read-only parent', (carrier_save,))):
                with node_host(store, branch_writers=grants):
                    try:
                        carrier_parent(writer_output)
                    except ValueError as error:
                        check(name, 'read-only' in str(error) and store.snapshot().reference == writer_root)
                    else:
                        check(name, False)
            with node_host(store, branch_writers=(carrier_parent, carrier_save)):
                nested_output = carrier_parent(writer_output)[0]
            with node_host(store):
                nested_read = asyncio.run(carrier_read(nested_output))[0]
            check('granted nested writer keeps exact child save result',
                  nested_output[PIN_KEY]['root'] != writer_root and
                  nested_read['saved'] == nested_output['saved'])
            writer_root, writer_output = store.snapshot().reference, nested_output
            with runtime_access(store, ownership_writes=True):
                ownership.claim_project_ownership(runtime_output, runtime_run, owner_b, force=True)
            with node_host(store, branch_writers=(carrier_save,)):
                try:
                    carrier_save(writer_output)
                except ownership.ProjectOwnershipError:
                    check('ownership takeover fences queued node commit', store.snapshot().reference == writer_root)
                else:
                    check('ownership takeover fences queued node commit', False)
            check('writer and takeover preserve immutable bootstrap and media',
                  (joined.project/'storage.json').read_bytes() == bootstrap and store.verify_payloads() == 1)
            with runtime_access(store):
                retry_owner = ownership.ownership_status(runtime_output, runtime_run, owner_b)
            with node_host(store):
                retry_input = dict(carrier_start(runtime_run)[0],
                    _project_ownership={'owner_id': owner_b, 'epoch': retry_owner['epoch']})
            operation = uuid.uuid4().hex
            with node_host(store, branch_writers=(carrier_save,)), patch.object(
                    ledger.CommitLog, 'acknowledge', side_effect=OSError('lost node save ack')):
                try:
                    carrier_save(retry_input, operation_id=operation)
                except OSError as error:
                    check('node save lost acknowledgement reports failure', 'lost node save ack' in str(error))
                else:
                    check('node save lost acknowledgement reports failure', False)
            retry_root = store.snapshot().reference
            with node_host(store, branch_writers=(carrier_save,)):
                recovered = carrier_save(retry_input, operation_id=operation)[0]
            check('queued retry acknowledges original save without republishing',
                  recovered[PIN_KEY]['root'] == retry_root and store.snapshot().reference == retry_root and
                  retry_input[PIN_KEY]['root'] != retry_root)
            # A separate project's selection scope may advance without changing
            # this branch's saved authoring; the retry result must stay exact.
            with runtime_access(store, branch_writes=True):
                WorkingBranches(runtime_output, runtime_run).make_default('main')
            unrelated_root = store.snapshot().reference
            with node_host(store, branch_writers=(carrier_save,)):
                recovered_again = carrier_save(retry_input, operation_id=operation)[0]
            check('retry after unrelated commit keeps original accepted result',
                  recovered_again == recovered and unrelated_root != retry_root and
                  store.snapshot().reference == unrelated_root)
            with runtime_access(store, ownership_writes=True):
                ownership.claim_project_ownership(runtime_output, runtime_run, owner_a, force=True)
            with node_host(store, branch_writers=(carrier_save,)):
                try:
                    carrier_save(retry_input, operation_id=operation)
                except ownership.ProjectOwnershipError:
                    check('former owner cannot recover queued save after takeover',
                          store.snapshot().reference == unrelated_root)
                else:
                    check('former owner cannot recover queued save after takeover', False)
            from handoff_state import HandoffStore, HandoffClaimError
            @scoped_node
            def create_handoff(state):
                result = HandoffStore(runtime_output).create(runtime_run, action='next_scene', scene=2,
                    handoff_id='probe-next', seed=18446744073709551613, working_branch_id='main')
                return (dict(state, handoff=result),)
            @scoped_node
            def handoff_step(state, status):
                service = HandoffStore(runtime_output)
                result = (service.claim(runtime_run, 'probe-next', claimant='isolated-probe')
                          if status == 'claimed' else service.transition(runtime_run, 'probe-next', status))
                return (dict(state, handoff=result),)
            with runtime_access(store):
                handoff_owner = ownership.ownership_status(runtime_output, runtime_run, owner_a)
                check('normal handoff service uses runtime control port',
                      HandoffStore(runtime_output).controls is not None)
            with node_host(store):
                handoff_input = dict(carrier_start(runtime_run)[0],
                    _project_ownership={'owner_id': owner_a, 'epoch': handoff_owner['epoch']})
            before_handoff = store.snapshot().reference
            with node_host(store, branch_writers=(create_handoff,)):
                try:
                    create_handoff(handoff_input)
                except ValueError as error:
                    check('branch grant does not enable handoff publication',
                          'handoff writes' in str(error) and store.snapshot().reference == before_handoff)
                else:
                    check('branch grant does not enable handoff publication', False)
            with node_host(store, handoff_writers=(create_handoff, handoff_step)):
                pending = create_handoff(handoff_input)[0]
                claimed = handoff_step(pending, 'claimed')[0]
                try:
                    handoff_step(pending, 'claimed')
                except state.StateConflict:
                    check('stale duplicate handoff claim cannot publish twice',
                          store.snapshot().reference == claimed[PIN_KEY]['root'])
                else:
                    check('stale duplicate handoff claim cannot publish twice', False)
                queued = handoff_step(claimed, 'queued')[0]
                consumed = handoff_step(queued, 'consumed')[0]
            # These are fixture state transitions only: no actual queue request.
            check('independent handoff nodes keep exact terminal result and seed',
                  consumed['handoff']['status'] == 'consumed' and
                  consumed['handoff']['seed'] == 18446744073709551613 and
                  consumed[PIN_KEY]['root'] == store.snapshot().reference)
            check('handoff state transitions preserve media and immutable bootstrap',
                  store.verify_payloads() == 1 and (joined.project/'storage.json').read_bytes() == bootstrap)
            from handoff_route_fixture import routes, request
            from storage_branch_controls import BranchControlDocuments
            api = routes(runtime_output, source=Path(__file__).with_name('handoff_route_source.py'))
            plan_scope, plan_category, plan_immutable = BranchControlDocuments._contract('plan.json')
            store.commit(store.snapshot(), {'plan.json': {'data': state._encode(json.loads(authored['plan_json'])),
                'scope': plan_scope, 'category': plan_category, 'immutable': plan_immutable}}, operation_id=uuid.uuid4().hex)
            with runtime_access(store, handoff_writes=True) as runtime:
                HandoffStore(runtime_output).create(runtime_run, action='next_scene',
                    scene=1, start_clip=1, end_clip=1, seed=18446744073709551613,
                    handoff_id='probe-http-next', working_branch_id='main')
                http_pending_pin = runtime.output_pin
            http_proof = {'owner_id': owner_a, 'epoch': handoff_owner['epoch']}
            body = {'run_name': runtime_run, 'handoff_id': 'probe-http-next'}
            with runtime_access(store):
                listed = asyncio.run(api['_list_handoffs'](request(body)))
            item = next(item for item in listed['body']['handoffs'] if item.get('handoff_id') == body['handoff_id'])
            check('actual handoff list resolves accepted plan without legacy files',
                  listed['status'] == 200 and item['resume']['total_scenes'] == 1 and
                  not (store.project/'plan.json').exists())
            with runtime_access(store, handoff_writes=True) as runtime:
                anonymous = asyncio.run(api['_claim_handoff'](request(body)))
                check('actual handoff route rejects anonymous owner before save', anonymous['status'] == 423)
                claimed = asyncio.run(api['_claim_handoff'](request(body, http_proof)))
                http_claimed_pin = runtime.output_pin
            check('actual handoff claim uses async worker and exact seed',
                  claimed['status'] == 200 and claimed['body']['handoff']['status'] == 'claimed' and
                  claimed['body']['handoff']['seed'] == 18446744073709551613)
            with runtime_access(store, pin=http_pending_pin, handoff_writes=True):
                duplicate = asyncio.run(api['_claim_handoff'](request(body, http_proof)))
            check('actual stale HTTP handoff returns conflict without republishing',
                  duplicate['status'] == 409 and store.snapshot().reference == http_claimed_pin['root'])
            for handler, expected, extra in (('_release_handoff', 'pending', {}),
                    ('_claim_handoff', 'claimed', {}), ('_transition_handoff', 'cancelled', {'status': 'cancelled'})):
                with runtime_access(store, handoff_writes=True):
                    response = asyncio.run(api[handler](request(dict(body, **extra), http_proof)))
                check('actual HTTP handoff '+handler+' is durable',
                      response['status'] == 200 and response['body']['handoff']['status'] == expected)
            check('HTTP handoff mutations preserve media and bootstrap',
                  store.verify_payloads() == 1 and (joined.project/'storage.json').read_bytes() == bootstrap)
            loop_plan = {'run_name': runtime_run, 'plan_hash': 'private-loop-end-fixture',
                'shots': [{'id': 'one', 'seed': 1}, {'id': 'two', 'seed': 18446744073709551613}]}
            loop_segment = {'index': 1, 'revision': 'f'*32}
            loop_raw = state._encode({'segment': loop_segment})
            store.commit(store.snapshot(), {
                'plan.json': {'data': state._encode(loop_plan), 'scope': plan_scope,
                              'category': plan_category, 'immutable': plan_immutable},
                'checkpoints/clip_0001.json': {'data': loop_raw, 'scope': plan_scope,
                              'category': plan_category, 'immutable': False}}, operation_id=uuid.uuid4().hex)
            @scoped_node
            def loop_end_handoff(state):
                record = api['_write_next_scene_handoff'](loop_plan, 1, 2, loop_segment)
                return (dict(state, handoff=record),)
            with node_host(store):
                loop_input = dict(carrier_start(runtime_run)[0], _project_ownership=http_proof)
            with node_host(store, handoff_writers=(loop_end_handoff,)):
                loop_output = loop_end_handoff(loop_input)[0]
            check('actual Loop End handoff uses accepted metadata and uint64 next seed',
                  loop_output['handoff']['source_checkpoint_sha256'] == state._hash(loop_raw) and
                  loop_output['handoff']['source_revision'] == loop_segment['revision'] and
                  loop_output['handoff']['seed'] == 18446744073709551613 and
                  loop_output[PIN_KEY]['root'] == store.snapshot().reference)
            with node_host(store, handoff_writers=(loop_end_handoff,)):
                loop_retry = loop_end_handoff(loop_output)[0]
            check('actual Loop End retry retains existing handoff and accepted root',
                  loop_retry == loop_output and store.snapshot().reference == loop_output[PIN_KEY]['root'])
            with runtime_access(store, handoff_writes=True):
                try:
                    api['_write_next_scene_handoff'](loop_plan, 1, 2, dict(loop_segment, revision='e'*32))
                except ValueError as error:
                    check('actual Loop End rejects mismatched source without another commit',
                          'accepted scene checkpoint' in str(error) and
                          store.snapshot().reference == loop_output[PIN_KEY]['root'])
                else:
                    check('actual Loop End rejects mismatched source without another commit', False)
            check('Loop End handoff fixture preserves media and bootstrap',
                  store.verify_payloads() == 1 and (joined.project/'storage.json').read_bytes() == bootstrap)
            from storage_runtime import current_runtime
            pointer_before = store.snapshot()
            @scoped_node
            def retire_assignment(state, interrupt=False):
                runtime = current_runtime(runtime_output, runtime_run)
                if interrupt:
                    def fail(point):
                        if point == 'retired_pointer':
                            raise OSError('private retirement interruption')
                    runtime.branches.after_stage = fail
                with runtime.branches.operation():
                    runtime.branches.retire_pointer(store.project/'checkpoints/clip_0001.json')
                return (dict(state, retired=True),)
            with node_host(store):
                try:
                    retire_assignment(loop_output)
                except ValueError as error:
                    check('pointer retirement requires explicit node writer grant',
                          'read-only' in str(error) and store.snapshot().reference == pointer_before.reference)
                else:
                    check('pointer retirement requires explicit node writer grant', False)
            with node_host(store, branch_writers=(retire_assignment,)):
                try:
                    retire_assignment(loop_output, interrupt=True)
                except OSError:
                    check('interrupted pointer retirement leaves accepted assignment intact',
                          store.snapshot().reference == pointer_before.reference and
                          store.snapshot().read('checkpoints/clip_0001.json') == loop_raw)
                else:
                    check('interrupted pointer retirement leaves accepted assignment intact', False)
                retired_output = retire_assignment(loop_output)[0]
            retired_root = store.snapshot()
            check('retirement is accepted atomically without removing old checkpoint bytes',
                  retired_output[PIN_KEY]['root'] == retired_root.reference and
                  'checkpoints/clip_0001.json' not in retired_root.state['documents'] and
                  pointer_before.read('checkpoints/clip_0001.json') == loop_raw)
            with runtime_access(store):
                active, _ = CheckpointGraphManager(runtime_output).active_selection(runtime_run)
            check('normal checkpoint reader no longer sees retired assignment', not active)
            store.commit(retired_root, {'checkpoints/clip_0001.json': {'data': loop_raw, 'scope': plan_scope,
                'category': plan_category, 'immutable': False}}, operation_id=uuid.uuid4().hex)
            check('explicit checkpoint reassignment restores exact bytes',
                  store.snapshot().read('checkpoints/clip_0001.json') == loop_raw)
            check('retirement and reassignment preserve media and bootstrap',
                  store.verify_payloads() == 1 and (joined.project/'storage.json').read_bytes() == bootstrap)
            from restore_route_fixture import routes as restore_routes
            restore_api = restore_routes(runtime_output, source=Path(__file__).with_name('restore_route_source.py'))
            restore_controls, restore_payloads, restore_metadata = {}, [], {}
            for scene in (1, 2):
                revision = str(scene)*32
                stem = 'clip_%04d.%s' % (scene, revision)
                segment = {'index': scene, 'id': 'private-scene-'+str(scene), 'revision': revision,
                    'seed': 18446744073709551615-scene, 'steps': 29, 'raw_frames': 124,
                    'delivered_frames': 124, 'resolved_context_length': 0, 'resolved_audio_context_length': 0,
                    'scene_prompt_template': 'Private restore fixture é 雪',
                    'predecessor_revision': '1'*32 if scene == 2 else '',
                    'resolution': {'width': 960, 'height': 544}}
                for key, folder, ext in (('segment', 'segments', '.mp4'),
                        ('checkpoint', 'checkpoints', '.safetensors'),
                        ('generated_audio', 'generated_audio', '.wav'),
                        ('prompt_file', 'checkpoints', '.txt')):
                    tiny = root/('restore-'+stem+ext)
                    raw = ('Private exact '+key+' '+str(scene)+' é\r\n').encode()
                    tiny.write_bytes(raw)
                    address = folder+'/'+stem+ext
                    segment[key] = 'h3_chains/'+runtime_run+'/'+address
                    segment[key+'_sha256'] = state._hash(raw)
                    restore_payloads.append(store.stage_payload(address, tiny,
                        'media/generation/'+revision+'/'+key+ext,
                        scope='archive:'+revision, operation_id=uuid.uuid4().hex))
                metadata = {'run_name': runtime_run, 'segment': segment}
                restore_metadata[scene] = metadata
                restore_controls['checkpoints/'+stem+'.json'] = dict(data=state._encode(metadata),
                    scope='archive:'+revision, category='takes', immutable=True)
                restore_controls['checkpoints/clip_%04d.json' % scene] = dict(data=state._encode(metadata),
                    scope='branch:main', category='branches', immutable=False)
            store.commit_artifacts(store.snapshot(), restore_controls, restore_payloads, operation_id=uuid.uuid4().hex)
            restore_before = store.snapshot()
            restore_body = dict(run_name=runtime_run, resume_scene=2, scope_start_scene=1, scope_end_scene=2,
                activate_only=True, revisions=[dict(scene=1, revision='1'*32)])
            with runtime_access(store, branch_writes=True) as runtime:
                restore_input_pin = runtime.pin
                anonymous = asyncio.run(restore_api['_restore_checkpoint_revisions'](request(restore_body)))
                check('actual restore route requires ownership', anonymous['status'] == 423)
                response = asyncio.run(restore_api['_restore_checkpoint_revisions'](request(restore_body, http_proof)))
                check('actual restore route accepts one atomic assignment update', response['status'] == 200 and
                    response['body']['retired_scope_pointers'] == 1 and
                    response['body']['storage_pin'] == runtime.output_pin and
                    store.snapshot().state['generation'] == restore_before.state['generation']+1)
                check('restore route keeps input graph pinned',
                    CheckpointGraphManager(runtime_output).active_selection(runtime_run)[0] == {1: '1'*32, 2: '2'*32})
            restore_after = store.snapshot()
            item = response['body']['restored'][0]
            check('restore response retains exact uint64 seed prompt and readable media',
                item['seed'] == '18446744073709551614' and
                item['scene_prompt'] == restore_metadata[1]['segment']['scene_prompt_template'] and
                (runtime_output/item['video']['subfolder']/item['video']['filename']).is_file())
            with runtime_access(store, pin=restore_input_pin, branch_writes=True):
                stale = asyncio.run(restore_api['_restore_checkpoint_revisions'](request(restore_body, http_proof)))
            check('stale restore API request does not rebase or repeat publication', stale['status'] == 409 and
                store.snapshot().reference == restore_after.reference)
            restored_body = dict(restore_body, resume_scene=3,
                revisions=[dict(scene=i, revision=str(i)*32) for i in (1, 2)])
            with runtime_access(store, branch_writes=True):
                restored_response = asyncio.run(restore_api['_restore_checkpoint_revisions'](request(restored_body, http_proof)))
            check('restore API can reassign retired checkpoint from retained take', restored_response['status'] == 200 and
                state._decode(store.snapshot().read('checkpoints/clip_0002.json'))['segment'] == restore_metadata[2]['segment'])
            before_failure = store.snapshot().reference
            with runtime_access(store, branch_writes=True) as runtime:
                def stop_restore(stage):
                    if stage == 'retired_pointer':
                        raise OSError('private restore interruption')
                runtime.branches.after_stage = stop_restore
                interrupted = asyncio.run(restore_api['_restore_checkpoint_revisions'](request(restore_body, http_proof)))
            check('restore API interrupted before publication keeps old assignments', interrupted['status'] == 503 and
                interrupted['body']['retry_automatically'] is False and store.snapshot().reference == before_failure)
            restore_before.verify()
            check('restore API preserves nine payloads bootstrap and historical controls',
                store.verify_payloads() == 9 and (joined.project/'storage.json').read_bytes() == bootstrap)
            # Full listing and immutable reuse must use the same accepted files
            # as restore, including the selected picture-only alternate.
            from listing_route_fixture import routes as listing_routes
            listing_api = listing_routes(runtime_output, source=Path(__file__).with_name('listing_route_source.py'))
            alias_controls, alias_payloads = {}, []
            for revision, alternate in (('3'*32, False), ('4'*32, True)):
                segment = dict(restore_metadata[1]['segment'], revision=revision)
                stem = 'clip_0001.'+revision
                for key, folder, ext in (('segment', 'segments', '.mp4'),
                        ('checkpoint', 'checkpoints', '.safetensors'),
                        ('generated_audio', 'generated_audio', '.wav'),
                        ('prompt_file', 'checkpoints', '.txt')):
                    tiny = root/('listing-'+stem+ext)
                    raw = ('Private listing '+revision+' '+key).encode()
                    tiny.write_bytes(raw)
                    address = folder+'/'+stem+ext
                    segment[key] = 'h3_chains/'+runtime_run+'/'+address
                    segment[key+'_sha256'] = state._hash(raw)
                    alias_payloads.append(store.stage_payload(address, tiny,
                        'media/generation/'+revision+'/'+key+ext,
                        scope='archive:'+revision, operation_id=uuid.uuid4().hex))
                if alternate:
                    segment.update(take_kind='editorial_alternate', alternate_of_revision='1'*32)
                archives = {}
                if not alternate:
                    archived_workflow = 'recovery_archives/'+revision+'/workflow.json'
                    archives['workflow'] = 'h3_chains/'+runtime_run+'/'+archived_workflow
                    alias_controls[archived_workflow] = dict(
                        data=b'{"nodes":[{"widgets_values":[Infinity,NaN,-Infinity]}]}',
                        scope='archive:'+revision, category='takes', immutable=True)
                alias_controls['checkpoints/'+stem+'.json'] = dict(data=state._encode(
                    dict(run_name=runtime_run, segment=segment, archives=archives)), scope='archive:'+revision,
                    category='takes', immutable=True)
            alias_controls['editorial.json'] = dict(data=state._encode(dict(replacements=[dict(
                scene=1, scene_id='private-scene-1', base_revision='1'*32, alternate_revision='4'*32)])),
                scope='branch:main', category='branches', immutable=False)
            store.commit_artifacts(store.snapshot(), alias_controls, alias_payloads, operation_id=uuid.uuid4().hex)
            listing_before = store.snapshot()
            with runtime_access(store) as runtime:
                listing_response = asyncio.run(listing_api['_list_saved_checkpoints'](request(
                    dict(run_name=runtime_run, include_graph='true'))))
                check('actual listing API reads combined checkpoint graph and exact pin',
                    listing_response['status'] == 200 and listing_response['body']['storage_pin'] == runtime.pin
                    and len(listing_response['body']['checkpoints']) == 2)
                rows = listing_response['body']['checkpoints']
                check('actual listing marks selected ALT and serves accepted video paths',
                    rows[0]['presentation_revision'] == '4'*32 and any(
                        alt['revision'] == '4'*32 and alt['used_in_final_cut'] for alt in rows[0]['alternates'])
                    and all((runtime_output/row['video']['subfolder']/row['video']['filename']).is_file() for row in rows))
                presented = listing_api['_editorial_presentation_segments'](runtime_run,
                    [restore_metadata[i]['segment'] for i in (1, 2)])
                check('actual downstream presentation selects ALT with base provenance',
                    presented[0]['presentation_alternate_revision'] == '4'*32 and
                    presented[0]['presentation_base_revision'] == '1'*32 and
                    presented[0]['presentation_media_mode'] == 'picture_only' and
                    presented[1] == restore_metadata[2]['segment'])
            check('listing and ALT reads are nonmutating', store.snapshot().reference == listing_before.reference)
            attribution_body = dict(run_name=runtime_run, parent_scene=1, parent_revision='3'*32,
                candidate_scene=2, candidate_revision='2'*32)
            def attribute(proof=http_proof):
                return asyncio.run(restore_api['_attribute_checkpoint_revision'](request(attribution_body, proof)))
            with runtime_access(store, branch_writes=True):
                check('actual attribution API requires ownership', attribute(None)['status'] == 423)
            with runtime_access(store):
                check('actual attribution API requires explicit writer grant', attribute()['status'] == 400)
            with runtime_access(store, branch_writes=True) as runtime:
                def stop_alias(stage):
                    if stage == 'document':
                        raise OSError('private attribution interruption')
                runtime.branches.after_stage = stop_alias
                interrupted = attribute()
            check('interrupted attribution leaves media and assignments accepted unchanged',
                interrupted['status'] == 503 and store.snapshot().reference == listing_before.reference)
            with runtime_access(store, branch_writes=True) as runtime:
                attribution_pin = runtime.pin
                attributed = attribute()
                check('actual attribution publishes one alias and exact output root',
                    attributed['status'] == 200 and attributed['body']['created'] and
                    attributed['body']['storage_pin'] == runtime.output_pin)
            attribution_after = store.snapshot()
            before_documents = listing_before.state['documents']
            after_documents = attribution_after.state['documents']
            added = set(after_documents)-set(before_documents)
            check('attribution changes no previous control pointer or authoring data',
                len(added) == 1 and all(after_documents[name] == descriptor
                    for name, descriptor in before_documents.items()))
            saved_alias = state._decode(attribution_after.read(next(iter(added))))
            check('attribution retains original seed prompt and shared audio video checkpoint',
                all(saved_alias['segment'][key] == restore_metadata[2]['segment'][key] for key in
                    ('seed', 'scene_prompt_template', 'segment', 'generated_audio', 'checkpoint', 'prompt_file')))
            with runtime_access(store, branch_writes=True, pin=attribution_pin):
                check('stale attribution cannot publish duplicate alias', attribute()['status'] == 409)
            with runtime_access(store, branch_writes=True):
                repeated = attribute()
                check('fresh attribution retry finds existing immutable alias',
                    repeated['status'] == 200 and not repeated['body']['created'] and
                    repeated['body']['revision'] == attributed['body']['revision'])
                refreshed = asyncio.run(listing_api['_list_saved_checkpoints'](request(
                    dict(run_name=runtime_run, include_graph='true'))))
                check('actual listing includes newly attributed revision', refreshed['status'] == 200 and
                    any(row['revision'] == attributed['body']['revision'] for row in refreshed['body']['revisions']))
            check('listing attribution and retry preserve seventeen payloads and bootstrap',
                store.snapshot().reference == attribution_after.reference and store.verify_payloads() == 17 and
                (joined.project/'storage.json').read_bytes() == bootstrap)
    except BaseException as error:
        report['errors'].append({'type': type(error).__name__, 'message': str(error)})
    report['seconds'] = round(time.monotonic()-started, 3)
    report['passed'] = not report['errors'] and all(item['passed'] for item in report['checks'])
    with (root/'report.json').open('x', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps(report), flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
