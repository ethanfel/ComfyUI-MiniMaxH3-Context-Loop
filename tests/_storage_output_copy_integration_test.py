"""Ordinary-output copies keep accepted renders and recover without overwrite."""
from pathlib import Path
import contextvars
import unittest
from unittest.mock import patch
import uuid

import _storage_assembly_integration_test as fixture

chain, carriers, state = fixture.chain, fixture.carriers, fixture.state
copy_module = fixture.fixture.module('storage_output_copy')
assembly = fixture.assembly


class OutputCopyTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.AssemblyTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = self.f.store
        self.output = self.store.project.parent.parent

    def export(self, manifest=None, **options):
        return self.f.assemble(manifest, **dict(dict(copy_to_output=True,output_subfolder='renders'), **options))

    def copied(self, result):
        item = result['ui']['images'][0]
        return self.output/item['subfolder']/item['filename']

    def subtitles(self):
        base = self.store.snapshot()
        catalog = dict(format='h3_project_assets_v1', version=1, project=self.store.project.name,
            assets=[dict(id='lyrics', kind='audio', tag='words', lyrics='[00:00.00]Copy this subtitle')])
        self.store.commit_artifacts(base, {'project_assets/catalog.json':dict(data=state._encode(catalog),
            scope='project', category='assets', immutable=False)}, [], operation_id=uuid.uuid4().hex)
        source = self.f.f.repin()
        source['editorial'] = dict(subtitles=dict(mode='preview_srt', asset_id='lyrics', offset_seconds=0))
        return source

    def test_real_copy_keeps_chain_return_path_and_previews_independent_output_bytes(self):
        result = self.export()
        original, copied = Path(result['result'][0]), self.copied(result)
        self.assertEqual(copied, self.output/'renders/Final.mp4')
        self.assertNotEqual(original,copied)
        self.assertEqual(original.read_bytes(),copied.read_bytes())
        self.assertNotEqual(original.stat().st_ino,copied.stat().st_ino)
        self.assertIn('output copy ->',result['ui']['text'][0])
        self.assertFalse(copied.with_suffix('.generated.wav').exists())
        root = self.store.snapshot().reference
        with patch.object(chain.MiniMaxH3ChainAssemble,'_assemble',side_effect=AssertionError('rendered again')):
            self.assertEqual(self.export(),result)
        self.assertEqual(self.store.snapshot().reference,root)

    def test_existing_video_or_orphan_subtitle_gets_a_numbered_variant(self):
        directory = self.output/'renders'
        directory.mkdir()
        video, subtitle = directory/'Final.mp4',directory/'Final_2.srt'
        video.write_bytes(b'existing unrelated video')
        subtitle.write_bytes(b'keep this existing subtitle')
        result = self.export()
        self.assertEqual(self.copied(result),directory/'Final_3.mp4')
        self.assertEqual(video.read_bytes(),b'existing unrelated video')
        self.assertEqual(subtitle.read_bytes(),b'keep this existing subtitle')

    def test_interrupted_copy_retry_does_not_render_or_commit_again(self):
        original = copy_module.publish_new_file
        def interrupted(staging,destination):
            if Path(destination).suffix == '.mp4':
                raise OSError('injected copy publish failure')
            return original(staging,destination)
        with patch.object(copy_module,'publish_new_file',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'injected'):
                self.export()
        after = self.store.snapshot().reference
        saved = self.f.witness()
        rendered = self.store.payload_path(self.store.snapshot(),saved['logical']['video'],verify=True)
        self.assertTrue(rendered.is_file())
        self.assertFalse((self.output/'renders/Final.mp4').exists())
        partials = list((self.output/'renders').glob('.h3copy-*.part'))
        self.assertEqual(len(partials),1)
        with patch.object(chain.MiniMaxH3ChainAssemble,'_assemble',side_effect=AssertionError('rendered again')):
            result = self.export()
        self.assertEqual(self.store.snapshot().reference,after)
        self.assertTrue(partials[0].is_file(), 'Retain failed private staging for recovery')
        self.assertEqual(self.copied(result).read_bytes(),rendered.read_bytes())

    def test_lost_post_rename_acknowledgement_recovers_same_copy(self):
        original = copy_module.publish_new_file
        def ambiguous(staging,destination):
            original(staging,destination)
            raise OSError('lost rename acknowledgement')
        with patch.object(copy_module,'publish_new_file',side_effect=ambiguous):
            with self.assertRaisesRegex(OSError,'lost rename'):
                self.export()
        copied = self.output/'renders/Final.mp4'
        before = copied.stat().st_ino
        root = self.store.snapshot().reference
        with patch.object(chain.MiniMaxH3ChainAssemble,'_assemble',side_effect=AssertionError('rendered again')):
            result = self.export()
        self.assertEqual(self.copied(result),copied)
        self.assertEqual(copied.stat().st_ino,before)
        self.assertEqual(self.store.snapshot().reference,root)

    def test_non_h3_race_cannot_overwrite_existing_destination(self):
        original = copy_module.publish_new_file
        raced = []
        def race(staging,destination):
            if not raced:
                Path(destination).write_bytes(b'other application won this filename')
                raced.append(Path(destination))
            original(staging,destination)
        with patch.object(copy_module,'publish_new_file',side_effect=race):
            result = self.export()
        self.assertEqual(raced[0].read_bytes(),b'other application won this filename')
        self.assertEqual(self.copied(result),self.output/'renders/Final_2.mp4')

    def test_unsafe_folders_and_links_reject_before_rendering(self):
        before = self.store.snapshot().reference
        with patch.object(chain.MiniMaxH3ChainAssemble,'_assemble',side_effect=AssertionError('rendered')):
            for folder in ('../escape','/tmp/escape','h3_chains/another','H3_CHAINS/another'):
                with self.subTest(folder=folder),self.assertRaises(ValueError):
                    self.export(output_subfolder=folder,unique_id=folder)
            (self.output/'linked').symlink_to(self.store.project,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'symlinks|junctions'):
                self.export(output_subfolder='linked',unique_id='link')
        self.assertEqual(self.store.snapshot().reference,before)

    def test_changed_completed_copy_is_not_replaced_on_retry(self):
        result = self.export()
        copied = self.copied(result)
        copied.write_bytes(b'user replaced this file')
        root = self.store.snapshot().reference
        with self.assertRaisesRegex(ValueError,'Completed output copy was changed'):
            self.export()
        self.assertEqual(copied.read_bytes(),b'user replaced this file')
        self.assertEqual(self.store.snapshot().reference,root)

    def test_subtitle_publishes_before_video_and_partial_pair_resumes_without_render(self):
        source = self.subtitles()
        original, seen = copy_module.publish_new_file, []
        def interrupted(staging, destination):
            seen.append(Path(destination).suffix)
            if Path(destination).suffix == '.mp4':
                self.assertTrue(Path(destination).with_suffix('.srt').is_file())
                raise OSError('injected video copy failure after subtitle')
            return original(staging, destination)
        with patch.object(copy_module, 'publish_new_file', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'after subtitle'):
                self.export(source)
        self.assertEqual(seen, ['.srt', '.mp4'])
        subtitle = self.output/'renders/Final.srt'
        self.assertIn('Copy this subtitle', subtitle.read_text())
        inode, root = subtitle.stat().st_ino, self.store.snapshot().reference
        with patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('rendered again')):
            result = self.export(source)
        self.assertEqual(subtitle.stat().st_ino, inode)
        self.assertEqual(self.store.snapshot().reference, root)
        self.assertEqual(self.copied(result).with_suffix('.srt'), subtitle)
        self.assertIn('subtitle copy ->', result['ui']['text'][0])

    def test_another_h3_claim_winning_before_immutable_write_gets_a_variant(self):
        original, won = state._immutable, []
        def race(project, address, raw, budget):
            if address.startswith('.h3_export_copies/claims/') and not won:
                foreign = state._decode(raw)
                foreign['operation'] = uuid.uuid4().hex
                original(project, address, state._encode(foreign), budget)
                won.append((Path(project)/address, state._encode(foreign)))
            return original(project, address, raw, budget)
        with patch.object(state, '_immutable', side_effect=race):
            result = self.export()
        self.assertEqual(self.copied(result), self.output/'renders/Final_2.mp4')
        self.assertEqual(won[0][0].read_bytes(), won[0][1])

    def test_claim_publication_io_error_is_not_treated_as_filename_collision(self):
        original = state._immutable
        def fail(project, address, raw, budget):
            if address.startswith('.h3_export_copies/claims/'):
                raise OSError('copy claim storage unavailable')
            return original(project, address, raw, budget)
        with patch.object(state, '_immutable', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'storage unavailable'):
                self.export()
        root = self.store.snapshot().reference
        self.assertTrue(self.f.witness())
        with patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('rendered again')):
            self.export()
        self.assertEqual(self.store.snapshot().reference, root)

    def test_forged_copy_plan_cannot_redirect_accepted_assembly(self):
        with patch.object(copy_module.OutputCopy, '_copy_file', side_effect=OSError('stop after plan')):
            with self.assertRaisesRegex(OSError, 'stop after plan'):
                self.export()
        plan = next(self.store.project.glob('project/jobs/*/copy-plan-0000.json'))
        forged = state._decode(plan.read_bytes())
        forged['paths']['video'] = 'elsewhere.mp4'
        plan.write_bytes(state._encode(forged))
        root = self.store.snapshot().reference
        with self.assertRaisesRegex(ValueError, 'destinations changed'):
            self.export()
        self.assertFalse((self.output/'elsewhere.mp4').exists())
        self.assertEqual(self.store.snapshot().reference, root)

    def test_copy_and_chain_numbering_are_independent_and_old_retry_retains_both(self):
        first = self.export()
        original = self.copied(first).read_bytes()
        source = self.f.f.repin()
        second = self.export(source, unique_id='second', overwrite_existing=True)
        self.assertEqual(self.copied(second), self.output/'renders/Final_2.mp4')
        self.assertNotEqual(second['result'], first['result'])
        root = self.store.snapshot().reference
        self.assertEqual(self.export(), first)
        self.assertEqual(self.copied(first).read_bytes(), original)
        self.assertEqual(self.store.snapshot().reference, root)

    def test_ownership_takeover_between_copy_staging_and_publication_denies_write(self):
        ownership = fixture.fixture.module('project_ownership')
        original, open_file = copy_module.os.fsync, Path.open
        triggered = []
        staged_fds = set()
        def remember(path, *args, **kwargs):
            handle = open_file(path, *args, **kwargs)
            if path.name.startswith('.h3copy-') and args == ('xb',):
                staged_fds.add(handle.fileno())
            return handle
        def separate_owner_request():
            with state.control_rehearsal_access(self.store.project), \
                    fixture.runtime.runtime_access(self.store, ownership_writes=True):
                ownership.claim_project_ownership(self.output, self.store.project.name,
                    'private-copy-owner-'+uuid.uuid4().hex, force=True)
        def takeover(fd):
            original(fd)
            # Trigger only on the ordinary-output staged video, not controls.
            if fd in staged_fds and not triggered:
                triggered.append(fd)
                contextvars.Context().run(separate_owner_request)
        with patch.object(Path, 'open', remember), patch.object(copy_module.os, 'fsync', side_effect=takeover):
            with self.assertRaises(ownership.ProjectOwnershipError):
                self.export()
        self.assertEqual(len(triggered), 1)
        self.assertFalse((self.output/'renders/Final.mp4').exists())
        self.assertTrue(self.f.witness(), 'Accepted chain render is retained after ownership changes')


if __name__ == '__main__':
    unittest.main(argv=[__file__])
