"""Real quarantine/recovery/re-import transactions retain portable custody."""
import json
import shutil
import unittest
import uuid

import _storage_project_recovery_unit_test as fixture
import storage_state as state
import storage_project as project
import storage_recovery as recovery
import storage_project_migration as migration
import storage_retention_portability as portable
from storage_quarantine import ProjectQuarantine


class PortableTests(fixture.ProjectRecoveryTests):
    def quarantine(self, addresses=None, **options):
        self.operation = uuid.uuid4().hex
        self.preview = ProjectQuarantine(self.store).preview(self.store.snapshot(),addresses or [self.address],
            reason='typed fixture quarantine',**options)
        ProjectQuarantine(self.store).quarantine(self.preview,operation_id=self.operation)
        self.current = self.store.snapshot()

    def recovered_capsule(self):
        journal = self.prepare()
        result = recovery.recover_legacy_copy(journal)
        self.assertTrue(result['source_unchanged'])
        capsule = portable.PortableQuarantine.from_legacy(self.restored,self.operation)
        return capsule,journal

    def reimport(self):
        copied = self.lab/'project-only-copy-output/h3_chains/demo'
        shutil.copytree(self.restored,copied,copy_function=shutil.copy2)
        receipt = self.lab/'project-only-copy-receipt.json'
        state.atomic_json(receipt,dict(copy=str(copied),source=str(self.restored),independent_copies=True))
        old = self.store.snapshot().state['documents']
        documents,targets = {},{}
        for path in copied.rglob('*'):
            if not path.is_file() or path.name.endswith('.lock'):
                continue
            key = path.relative_to(copied).as_posix()
            if path.suffix in ('.json','.txt'):
                contract = {k:old[key][k] for k in ('scope','category','immutable')} if key in old else dict(
                    scope='archive:portable',category='recovery',immutable=True)
                documents[key] = dict(source=key,sha256=state._hash(path.read_bytes()),**contract)
            else:
                targets[key] = dict(target='project/recovery/'+state._hash(key.encode())+'.bin',
                                    scope='archive:portable',immutable=True)
        imported = state.create_control_rehearsal(receipt,self.lab/'reimport-output',documents)
        access = state.control_rehearsal_access(imported.project)
        access.__enter__(); self.addCleanup(access.__exit__,None,None,None)
        journal = migration.prepare_join(receipt,imported,self.lab/'reimport-journal',targets)
        migration.join_payloads(journal)
        return project.ProjectStore(imported.project)

    def test_undo_evidence_is_inside_recovered_project_and_independent(self):
        self.quarantine()
        before = {key:path.read_bytes() for key,path in recovery._files(self.root).items() if not key.endswith('.lock')}
        capsule,journal = self.recovered_capsule()
        self.assertEqual(capsule.record['preview'],self.preview)
        original = self.preview['items'][0]['payload']['file']
        path = capsule.file(original)
        self.assertEqual(path.read_bytes(),b'new post-join video')
        self.assertNotEqual((path.stat().st_dev,path.stat().st_ino),
                            ((self.root/original['path']).stat().st_dev,(self.root/original['path']).stat().st_ino))
        self.assertFalse((self.restored/self.address).exists())
        self.assertEqual(before,{key:path.read_bytes() for key,path in recovery._files(self.root).items()
                                 if not key.endswith('.lock')})
        self.assertEqual(recovery.recover_legacy_copy(journal)['source_unchanged'],True)

    def test_project_only_reimport_then_second_recovery_keep_the_same_closure(self):
        self.quarantine(); capsule,_ = self.recovered_capsule()
        store = self.reimport()
        imported = portable.PortableQuarantine.from_store(store,store.snapshot(),self.operation)
        self.assertEqual(imported.value,capsule.value)
        receipt = self.lab/'second-recovery-receipt.json'
        state.atomic_json(receipt,dict(copy=str(store.project),source=str(self.root),independent_copies=True))
        output = self.lab/'second-recovery-output'
        journal = recovery.prepare_legacy_copy(receipt,output,self.lab/'second-recovery-journal',rehearsal_store=store)
        plan = json.loads((journal.parent/'plan.json').read_bytes())
        self.assertEqual(plan['portable_retention'],{})  # Existing evidence travels unchanged.
        self.assertTrue(recovery.recover_legacy_copy(journal)['source_unchanged'])
        again = portable.PortableQuarantine.from_legacy(output/'h3_chains/demo',self.operation)
        self.assertEqual(again.value,capsule.value)

    def test_capsule_publication_interruption_keeps_gate_and_resumes(self):
        self.quarantine()
        journal = self.prepare()
        plan = json.loads((journal.parent/'plan.json').read_bytes())
        def stop(index):
            if index == len(plan['rows'])+1:
                raise OSError('capsule copied before recovery acknowledgement')
        with self.assertRaisesRegex(OSError,'capsule copied'):
            recovery.recover_legacy_copy(journal,after_copy=stop)
        self.assertTrue((self.restored/'storage.json').exists())
        self.assertTrue(recovery.recover_legacy_copy(journal)['source_unchanged'])
        portable.PortableQuarantine.from_legacy(self.restored,self.operation)

    def test_control_updates_and_archives_keep_before_and_after_bytes(self):
        token = uuid.uuid4().hex
        key = 'chapters/01_demo/snapshots/'+token+'.json'
        self.store.commit(self.store.snapshot(),{key:dict(data=b'{"sealed":"original"}',
            scope='branch:A',category='cuts',immutable=True)},operation_id=uuid.uuid4().hex)
        archived = 'chapters/01_demo/retired/'+token+'.json'
        update = 'checkpoints/clip_0001.json'
        self.quarantine([self.address,key],updates={update:b'{"seed":88}'},archive_controls={key:archived})
        capsule,_ = self.recovered_capsule()
        self.assertEqual(capsule.data(capsule.before['documents'][update]['file']),self.new)
        self.assertEqual(capsule.data(capsule.published['documents'][update]['file']),b'{"seed":88}')
        self.assertEqual(capsule.data(capsule.before['documents'][key]['file']),b'{"sealed":"original"}')
        self.assertEqual((self.restored/archived).read_bytes(),b'{"sealed":"original"}')

    def test_edited_payload_custody_is_preserved_not_relabelled_intact(self):
        self.store.payload_path(self.store.snapshot(),self.address).write_bytes(b'user edited video')
        self.quarantine(payload_exceptions={self.address:['edited']})
        capsule,_ = self.recovered_capsule()
        item = self.preview['items'][0]
        self.assertEqual(capsule.file(item['payload']['file'],custody=item['custody']).read_bytes(),b'user edited video')
        with self.assertRaisesRegex(ValueError,'recorded condition'):
            capsule.file(item['payload']['file'])

    def test_missing_payload_remains_explicitly_missing_after_reimport(self):
        source = self.store.payload_path(self.store.snapshot(),self.address)
        source.rename(self.lab/'deliberately-missing-source-preserved.mp4')
        self.quarantine(payload_exceptions={self.address:['missing']})
        capsule,_ = self.recovered_capsule()
        item = self.preview['items'][0]
        self.assertIsNone(capsule.file(item['payload']['file'],custody=item['custody']))
        store = self.reimport()
        imported = portable.PortableQuarantine.from_store(store,store.snapshot(),self.operation)
        self.assertIsNone(imported.file(item['payload']['file'],custody=item['custody']))

    def test_tampered_capsule_cannot_read_an_outside_file(self):
        self.quarantine(); capsule,_ = self.recovered_capsule()
        value = capsule.value
        value['references'][0]['object'] = '../../outside.json'
        (self.restored/portable.address(self.operation)).write_bytes(state._encode(value))
        with self.assertRaisesRegex(ValueError,'does not match'):
            portable.PortableQuarantine.from_legacy(self.restored,self.operation)

    def test_changed_retained_bytes_block_preparation_before_journal(self):
        self.quarantine()
        path = self.root/self.preview['items'][0]['payload']['file']['path']
        path.write_bytes(b'changed after quarantine')
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.folder.exists())
        self.assertFalse(self.output.exists())

    def test_shared_object_bytes_are_copied_once_for_two_quarantines(self):
        self.quarantine()
        first = self.operation
        source = self.lab/'another-independent-video.mp4'
        source.write_bytes(b'new post-join video')
        key = 'segments/clip_0003.'+'e'*32+'.mp4'
        staged = self.store.stage_payload(key,source,'media/generation/'+'e'*32+'/video.mp4',
            scope='branch:A',operation_id=uuid.uuid4().hex)
        self.store.commit_artifacts(self.store.snapshot(),{},[staged],operation_id=uuid.uuid4().hex)
        self.quarantine([key])
        capsule,journal = self.recovered_capsule()
        other = portable.PortableQuarantine.from_legacy(self.restored,first)
        one = capsule.file(capsule.preview['items'][0]['payload']['file'])
        two = other.file(other.preview['items'][0]['payload']['file'])
        self.assertEqual(one,two)
        plan = json.loads((journal.parent/'plan.json').read_bytes())
        self.assertEqual(sum(row['target'] == one.relative_to(self.output).as_posix() for row in plan['rows']),1)


if __name__ == '__main__':
    suite = unittest.TestSuite(PortableTests(name) for name in PortableTests.__dict__ if name.startswith('test_'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
