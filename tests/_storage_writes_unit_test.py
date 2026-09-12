"""Writer reservation publication, migration fencing, and recovery boundaries."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import _storage_resolver_unit_test as fixture

resolver = fixture.resolver
rehearsal = fixture.rehearsal
writes = importlib.import_module('storage_writes')
locks = importlib.import_module('checkpoint_manager')


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.RelocationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.output = self.fixture.root, self.fixture.output
        self.journal = rehearsal.prepare(self.fixture.receipt, self.fixture.proposal,
            self.fixture.lab/'journal', organized_writers=True)
        rehearsal.relocate(self.journal)
        scope = resolver.rehearsal_access(self.root)
        scope.__enter__()
        self.addCleanup(scope.__exit__, None, None, None)

    def payload(self, suffix='d'):
        token = suffix*32
        return {'video': str(self.root/'segments'/('clip_0002.'+token+'.mp4')),
                'checkpoint': str(self.root/'checkpoints'/('clip_0002.'+token+'.safetensors'))}

    def reserve(self, suffix='d'):
        return writes.reserve_take(self.output, self.payload(suffix), stage='generation', identity=suffix*32)

    def test_payloads_group_by_take_and_logical_identity_survives(self):
        old = self.payload()
        paths = self.reserve()
        self.assertEqual(Path(paths['video']).parent, Path(paths['checkpoint']).parent)
        self.assertEqual(Path(paths['video']).name, 'video.mp4')
        for role, path in paths.items():
            self.assertTrue(str(path).startswith(str(self.root/'media/generation')))
            self.assertEqual(resolver.logical_output(self.output, path),
                             Path(old[role]).relative_to(self.output).as_posix())
        self.assertEqual(self.reserve(), paths)
        self.assertEqual(json.loads((self.root/'storage.json').read_text())['writer_generation'], 1)

    def test_all_processing_stages_and_pass_scopes_are_distinct(self):
        paths = []
        for offset, stage in enumerate(('alternate', 'derope', 'latent_upscale', 'pixel_upscale', 'video_refine', 'custom')):
            value = writes.reserve_take(self.output, self.payload(str(offset)), stage=stage,
                identity=stage, pass_identity=None if stage=='alternate' else stage)
            self.assertIn('/media/'+stage+'/', Path(value['video']).as_posix())
            paths.append(value['video'])
        self.assertEqual(len(set(paths)),6)

    def test_export_reserves_provenance_and_audio_with_logical_stem(self):
        folder=self.root/'final'
        paths={role:str(folder/('cut'+suffix)) for role,suffix in (
            ('video','.mp4'),('metadata','.json'),('audio','.generated.wav'),('subtitles','.srt'))}
        saved=writes.reserve_export(self.output,paths,identity='cut')
        self.assertEqual(Path(saved['metadata']).name,'video.json')
        self.assertEqual(Path(saved['audio']).name,'audio.wav')
        for role,path in saved.items():
            self.assertEqual(resolver.logical_output(self.output,path),
                             Path(paths[role]).relative_to(self.output).as_posix())

    def test_unowned_destination_and_existing_legacy_data_are_not_adopted(self):
        source = Path(self.payload()['video'])
        source.write_bytes(b'external')
        with self.assertRaisesRegex(resolver.StorageError, 'Unmigrated'):
            self.reserve()
        self.assertEqual(source.read_bytes(), b'external')
        self.assertEqual(json.loads((self.root/'storage.json').read_text())['writer_generation'], 0)

    def test_failed_alias_publication_never_switches_pointer(self):
        original = (self.root/'storage.json').read_bytes()
        def fail(*args, **kwargs):
            raise OSError('simulated disk full')
        with patch.object(writes, 'atomic_json', fail), self.assertRaisesRegex(OSError,'disk full'):
            self.reserve()
        self.assertEqual((self.root/'storage.json').read_bytes(), original)
        self.assertEqual(resolver.resolve_output(self.output, self.payload()['video']),Path(self.payload()['video']))

    def test_dangling_link_in_legacy_directory_is_not_hidden(self):
        directory=self.root/'frames/unsafe'
        directory.mkdir(parents=True)
        try:
            (directory/'dangling').symlink_to(self.root/'not-present')
        except OSError:
            self.skipTest('Symlinks not permitted on this platform')
        before=(self.root/'storage.json').read_bytes()
        with self.assertRaises(resolver.StorageError):
            writes.reserve_directory(self.output,directory,'png')
        self.assertEqual((self.root/'storage.json').read_bytes(),before)

    def test_failed_pointer_publication_retains_old_root_and_new_authority(self):
        old = (self.root/'storage.json').read_bytes()
        publish = writes.atomic_json
        def fail(path, value):
            if Path(path).name == 'storage.json':
                raise OSError('simulated before replace')
            return publish(path,value)
        with patch.object(writes, 'atomic_json', fail), self.assertRaisesRegex(OSError,'before replace'):
            self.reserve()
        self.assertEqual((self.root/'storage.json').read_bytes(),old)
        self.assertEqual(len(list((self.root/'project/aliases').glob('*.json'))),2)
        # An orphan authority is not guessed or auto-adopted. Retry publishes a
        # new complete root and leaves the failed attempt for inspection.
        self.reserve()
        self.assertEqual(json.loads((self.root/'storage.json').read_text())['writer_generation'],1)

    def test_lost_ack_after_pointer_replace_retains_valid_mapping_for_retry(self):
        publish = writes.atomic_json
        def fail(path, value):
            result = publish(path,value)
            if Path(path).name == 'storage.json':
                raise OSError('simulated acknowledgement lost')
            return result
        with patch.object(writes, 'atomic_json', fail), self.assertRaisesRegex(OSError,'acknowledgement'):
            self.reserve()
        result = self.reserve()
        self.assertIn('/media/generation/',Path(result['video']).as_posix())
        self.assertEqual(json.loads((self.root/'storage.json').read_text())['writer_generation'],1)

    def test_rollback_cannot_discard_new_work_or_alias_authority(self):
        value = self.reserve()
        path = Path(value['video'])
        path.parent.mkdir(parents=True)
        path.write_bytes(b'new movie')
        marker = (self.root/'storage.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'New organized reservations'):
            rehearsal.rollback(self.journal)
        self.assertEqual(path.read_bytes(),b'new movie')
        self.assertEqual((self.root/'storage.json').read_bytes(),marker)

    def test_gate_rechecked_at_lock_acquisition_after_migration_begins(self):
        lock = locks.checkpoint_run_lock(self.output,'demo')
        marker = json.loads((self.root/'storage.json').read_text())
        marker['phase']='moving'
        fixture.atomic_json(self.root/'storage.json',marker)
        with self.assertRaisesRegex(resolver.StorageError,'incomplete'),lock:
            self.fail('entered stale writer')

    def test_rollback_cannot_strand_ready_project_after_new_unreserved_file(self):
        extra=self.root/'new-control.json'
        extra.write_text('{}')
        before=(self.root/'storage.json').read_bytes()
        journal=self.journal.read_bytes()
        with self.assertRaisesRegex(ValueError,'untracked'):
            rehearsal.rollback(self.journal)
        self.assertEqual((self.root/'storage.json').read_bytes(),before)
        self.assertEqual(self.journal.read_bytes(),journal)
        self.assertEqual(extra.read_text(),'{}')

    def test_history_and_handoff_writers_obey_incomplete_storage_gate(self):
        from prompt_history import PromptHistoryStore
        from handoff_state import HandoffStore
        marker=json.loads((self.root/'storage.json').read_text())
        marker['phase']='moving'
        fixture.atomic_json(self.root/'storage.json',marker)
        for call in (lambda: PromptHistoryStore(str(self.output)).save_draft('demo','first','new prompt'),
                     lambda: HandoffStore(str(self.output)).create('demo',action='next_scene',scene=2)):
            with self.assertRaisesRegex(resolver.StorageError,'incomplete'):
                call()
        self.assertFalse((self.root/'prompt_history').exists())
        self.assertFalse((self.root/'orchestration').exists())

    def test_two_threads_do_not_lose_independent_reservations(self):
        errors=[]
        def task(token):
            try:
                with resolver.rehearsal_access(self.root):
                    self.reserve(token)
            except Exception as exc:
                errors.append(exc)
        workers=[threading.Thread(target=task,args=(c,)) for c in ('d','e')]
        for worker in workers: worker.start()
        for worker in workers: worker.join(5)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors,[])
        state=resolver.storage_state(self.root)
        self.assertEqual(len(state['files']),5)

    def test_separate_process_waits_for_same_maintenance_lock(self):
        script = '''import sys
from pathlib import Path
from storage_resolver import rehearsal_access
from storage_writes import reserve_directory
root=Path(sys.argv[1]); output=root.parent.parent
print("started",flush=True)
with rehearsal_access(root):
    print(reserve_directory(output,root/"frames/new","png"),flush=True)
'''
        with locks._raw_checkpoint_run_lock(self.output,'demo'):
            child=subprocess.Popen([sys.executable,'-c',script,str(self.root)],
                cwd=str(Path(writes.__file__).parent), stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            self.addCleanup(lambda: child.kill() if child.poll() is None else None)
            self.assertEqual(child.stdout.readline().strip(),'started')
            self.assertIsNone(child.poll())
            self.assertEqual(json.loads((self.root/'storage.json').read_text())['writer_generation'],0)
        out,err=child.communicate(timeout=10)
        self.assertEqual(child.returncode,0,err)
        self.assertIn('/exports/png/',out.replace('\\','/'))


if __name__ == '__main__':
    unittest.main()
