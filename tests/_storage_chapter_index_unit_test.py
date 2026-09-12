"""Real import/join/reverse-copy ordering, including legacy timestamp ties."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

import storage_chapter_index as index
import storage_state as state
import storage_project as project
import storage_project_migration as migration
import storage_recovery as recovery


class ChapterIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.lab = Path(temporary.name)
        self.original = self.lab/'source-output/h3_chains/demo'
        self.source = self.lab/'copy-output/h3_chains/demo'
        self.raw, self.by_prefix = {}, {}
        for prefix in ('', 'branches/'+'c'*32+'/'):
            addresses = []
            for i in range(2):
                document = dict(format='h3_chain_chapter_manifest_v1', run_name='demo',
                    chapter=dict(number=1, id='first', title='First'),
                    segments=[dict(index=1, revision=str(i)*32)], note=str(i))
                canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
                token = hashlib.sha256(canonical).hexdigest()[:32]
                address = prefix+'chapters/01_first/manifests/'+token+'.json'
                document.update(chapter_manifest_id=token, chapter_manifest_path='h3_chains/demo/'+address,
                                sealed_at='2026-09-01T10:00:00Z')  # Deliberate equal seal times.
                raw = state._encode(document)
                path = self.original/address
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                os.utime(path, ns=((i+1)*1_000_000_000, (i+1)*1_000_000_000))
                self.raw[address] = raw
                addresses.append(address)
            self.by_prefix[prefix] = addresses
        shutil.copytree(self.original, self.source, copy_function=shutil.copy2)
        self.receipt = self.lab/'receipt.json'
        state.atomic_json(self.receipt, dict(source=str(self.original), copy=str(self.source), independent_copies=True,
            files=[dict(path=address, sha256=state._hash(raw)) for address,raw in self.raw.items()]))

    def prepare(self, *, label='', contracts=None):
        documents = {address:dict(source=address, sha256=state._hash(raw),
            scope='branch:'+('main' if not address.startswith('branches/') else address.split('/')[1]),
            category='cuts', immutable=not bool(index.HEAD_PATTERN.fullmatch(address))) for address,raw in self.raw.items()}
        for address, values in (contracts or {}).items():
            documents[address].update(values)
        self.control = state.create_control_rehearsal(self.receipt, self.lab/(label+'combined-output'), documents,
                                                      commit_protocol='immutable_slots_v1')
        access = state.control_rehearsal_access(self.control.project)
        access.__enter__()
        self.addCleanup(access.__exit__, None, None, None)
        self.store = project.ProjectStore(self.control.project)
        self.journal = migration.prepare_join(self.receipt, self.control, self.lab/(label+'join'), {})
        return self.journal

    def add_control(self, address, raw):
        for root in (self.original, self.source):
            path = root/address
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.raw[address] = raw
        state.atomic_json(self.receipt, dict(source=str(self.original), copy=str(self.source), independent_copies=True,
            files=[dict(path=a, sha256=state._hash(r)) for a,r in self.raw.items()]))

    def recover(self, label):
        receipt = self.lab/(label+'combined-receipt.json')
        state.atomic_json(receipt, dict(copy=str(self.store.project), source=str(self.source), independent_copies=True))
        output = self.lab/(label+'restored-output')
        journal = recovery.prepare_legacy_copy(receipt, output, self.lab/(label+'reverse'), rehearsal_store=self.store)
        self.assertTrue(recovery.recover_legacy_copy(journal)['source_unchanged'])
        return output/'h3_chains/demo'

    def test_import_captures_original_latest_per_branch_not_equal_seal_times(self):
        self.prepare()
        result = migration.join_payloads(self.journal)
        after = self.store.snapshot()
        for prefix, addresses in self.by_prefix.items():
            record = state._decode(after.read(index.head(prefix, 1)))
            self.assertEqual([e['manifest'] for e in record['history']], addresses)
            actual, _ = index.select(record, prefix, 1, 'demo', after.read)
            self.assertEqual(actual, addresses[-1])
            for address in addresses:
                self.assertEqual(after.read(address), self.raw[address])
        self.assertEqual(result, migration.join_payloads(self.journal))
        self.assertEqual({a:(self.source/a).read_bytes() for a in self.raw}, self.raw)

    def test_mtime_only_change_after_prepare_refuses_publication_and_can_retry(self):
        self.prepare()
        path = self.source/self.by_prefix[''][0]
        stamp = path.stat().st_mtime_ns
        os.utime(path, ns=(stamp+7, stamp+7))
        with self.assertRaisesRegex(ValueError, 'Chapter order changed'):
            migration.join_payloads(self.journal)
        self.assertEqual(self.control.snapshot().state['generation'], 0)
        os.utime(path, ns=(stamp, stamp))
        migration.join_payloads(self.journal)
        record = state._decode(self.store.snapshot().read(index.head('', 1)))
        self.assertEqual(record['manifest'], self.by_prefix[''][-1])

    def test_reverse_copy_keeps_order_when_copied_file_times_change(self):
        self.prepare()
        migration.join_payloads(self.journal)
        receipt = self.lab/'combined-receipt.json'
        state.atomic_json(receipt, dict(copy=str(self.store.project), source=str(self.source), independent_copies=True))
        output = self.lab/'restored-output'
        journal = recovery.prepare_legacy_copy(receipt, output, self.lab/'reverse', rehearsal_store=self.store)
        result = recovery.recover_legacy_copy(journal)
        self.assertTrue(result['source_unchanged'])
        root = output/'h3_chains/demo'
        for prefix, addresses in self.by_prefix.items():
            record = json.loads((root/index.head(prefix, 1)).read_bytes())
            for i,address in enumerate(addresses):
                os.utime(root/address, ns=((9-i)*1_000_000_000,(9-i)*1_000_000_000))
            chosen, _ = index.select(record, prefix, 1, 'demo', lambda a:(root/a).read_bytes())
            self.assertEqual(chosen, addresses[-1])
            retired = root/addresses[-1].replace('/manifests/', '/retired_manifests/')
            retired.parent.mkdir(parents=True)
            (root/addresses[-1]).rename(retired)
            chosen, _ = index.select(record, prefix, 1, 'demo', lambda a:(root/a).read_bytes())
            self.assertEqual(chosen, addresses[0])
            retired.write_bytes(retired.read_bytes()+b' ')
            with self.assertRaisesRegex(ValueError, 'Retired chapter differs'):
                index.select(record, prefix, 1, 'demo', lambda a:(root/a).read_bytes())

    def test_selector_rejects_cross_branch_bad_hash_missing_and_duplicate_history(self):
        addresses = self.by_prefix['']
        record = index.record(1, [index.entry(a,self.raw[a]) for a in addresses])
        for bad in (dict(record, sha256='0'*64),
                    index.record(1, record['history']*2),
                    index.record(1, [index.entry(self.by_prefix['branches/'+'c'*32+'/'][0], self.raw[addresses[0]])])):
            with self.assertRaises(ValueError):
                index.history(bad, '', 1)
        def missing(_):
            raise FileNotFoundError('gone')
        with self.assertRaisesRegex(FileNotFoundError, 'without a retirement record'):
            index.select(record, '', 1, 'demo', missing)
        corrupted = dict(self.raw, **{addresses[-1]:self.raw[addresses[-1]]+b' '})
        with self.assertRaisesRegex(ValueError, 'differs from its snapshot'):
            index.select(record, '', 1, 'demo', corrupted.__getitem__)

    def test_reimport_appends_new_legacy_seal_after_index_despite_older_file_time(self):
        # A recovered project's old snapshots have portable order. New ordinary
        # legacy saves do not modify its index and may have earlier mtimes.
        self.prepare()
        migration.join_payloads(self.journal)
        recovered = self.recover('first-')
        for prefix, addresses in self.by_prefix.items():
            document = json.loads(self.raw[addresses[0]])
            for field in ('chapter_manifest_id','chapter_manifest_path','sealed_at'):
                document.pop(field)
            document['note'] = 'new legacy save after recovery'
            token = hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True,
                                              separators=(',', ':')).encode()).hexdigest()[:32]
            address = prefix+'chapters/01_first/manifests/'+token+'.json'
            document.update(chapter_manifest_id=token, chapter_manifest_path='h3_chains/demo/'+address,
                            sealed_at='2026-09-01T10:00:00Z')
            (recovered/address).write_bytes(state._encode(document))
            os.utime(recovered/address, ns=(0,0))
            addresses.append(address)
        self.original, self.source = recovered, self.lab/'second-copy-output/h3_chains/demo'
        shutil.copytree(self.original, self.source, copy_function=shutil.copy2)
        self.raw = {a:p.read_bytes() for a,p in recovery._files(self.source).items() if not a.endswith('.lock')}
        self.receipt = self.lab/'second-copy-receipt.json'
        state.atomic_json(self.receipt, dict(source=str(self.original), copy=str(self.source), independent_copies=True,
            files=[dict(path=a, sha256=state._hash(raw)) for a,raw in self.raw.items()]))
        self.prepare(label='second-')
        old = self.control.snapshot()
        migration.join_payloads(self.journal)
        current = self.store.snapshot()
        again = self.recover('second-')
        for prefix, addresses in self.by_prefix.items():
            key = index.head(prefix, 1)
            self.assertEqual(json.loads(old.read(key))['manifest'], addresses[-2])
            value = json.loads(current.read(key))
            self.assertEqual([i['manifest'] for i in value['history']], addresses)
            self.assertEqual(json.loads((again/key).read_bytes()), value)
            self.assertEqual(index.select(value, prefix, 1, 'demo', lambda a:(again/a).read_bytes())[0], addresses[-1])
            for address in addresses:
                self.assertEqual(current.read(address), self.raw[address])
        self.assertEqual({a:(self.source/a).read_bytes() for a in self.raw}, self.raw)

    def test_existing_index_is_verified_entirely_before_creating_join_journal(self):
        addresses = self.by_prefix['']
        value = index.record(1, [index.entry(a,self.raw[a]) for a in addresses])
        value['history'][0]['sha256'] = '0'*64  # Latest seal still looks valid.
        self.add_control(index.head('', 1), state._encode(value))
        with self.assertRaisesRegex(ValueError, 'history differs'):
            self.prepare()
        self.assertFalse((self.lab/'join').exists())
        self.assertEqual(self.control.snapshot().state['generation'], 0)

    def test_existing_index_contract_cannot_be_overwritten_as_a_derived_selector(self):
        address = self.by_prefix[''][0]
        key = index.head('', 1)
        self.add_control(key, state._encode(index.record(1,[index.entry(address,self.raw[address])])))
        with self.assertRaisesRegex(ValueError, 'mutable branch-owned'):
            self.prepare(contracts={key:dict(immutable=True)})
        self.assertFalse((self.lab/'join').exists())

    def test_equal_filesystem_times_keep_legacy_path_tiebreaker(self):
        for prefix, addresses in self.by_prefix.items():
            for address in addresses:
                os.utime(self.source/address, ns=(1,1))
        self.prepare()
        migration.join_payloads(self.journal)
        for prefix, addresses in self.by_prefix.items():
            value = json.loads(self.store.snapshot().read(index.head(prefix,1)))
            self.assertEqual([i['manifest'] for i in value['history']], sorted(addresses))

    def test_partial_early_selector_does_not_guess_order_of_unknown_snapshots(self):
        address = self.by_prefix[''][-1]
        value = index.record(1,[index.entry(address,self.raw[address])])
        del value['history']
        self.add_control(index.head('',1), state._encode(value))
        with self.assertRaisesRegex(ValueError, 'lacks complete publication history'):
            self.prepare()
        self.assertFalse((self.lab/'join').exists())

    def test_all_retired_history_survives_import_without_reintroducing_a_snapshot(self):
        for prefix, addresses in self.by_prefix.items():
            self.add_control(index.head(prefix,1), state._encode(index.record(1,
                [index.entry(a,self.raw[a]) for a in addresses])))
            for address in addresses:
                archived = address.replace('/manifests/', '/retired_manifests/')
                self.add_control(archived, self.raw[address])
                for root in (self.original, self.source):
                    (root/address).unlink()
                del self.raw[address]
        # Refresh the independent-copy inventory after the fixture retirements.
        key = index.head('',1)
        self.add_control(key,self.raw[key])
        self.prepare()
        migration.join_payloads(self.journal)
        current = self.store.snapshot()
        for prefix in self.by_prefix:
            key = index.head(prefix,1)
            self.assertEqual(current.read(key), self.raw[key])
            def read(address):
                if address not in current.state['documents']:
                    raise FileNotFoundError(address)
                return current.read(address)
            with self.assertRaisesRegex(FileNotFoundError, 'No unretired'):
                index.select(json.loads(current.read(key)), prefix, 1, 'demo', read)


if __name__ == '__main__':
    unittest.main(argv=[__file__])
