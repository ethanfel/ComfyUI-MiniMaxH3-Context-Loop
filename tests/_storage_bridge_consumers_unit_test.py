"""Real PNG export/reuse/fork and destructive cleanup, on tiny temporary fixtures."""
import hashlib
import importlib
import json
from pathlib import Path
import unittest

import _png_video_export_unit_test as png_fixture
import _processing_checkpoint_delete_unit_test as delete_fixture


def activate(output, package, files=None, directories=None, organized_writers=False):
    resolver = importlib.import_module(package.__name__ + '.storage_resolver')
    root = output / 'h3_chains/demo'
    aliases = {'format':resolver.ALIASES, 'files':files or {}, 'directories':directories or {}}
    resolver.validate_aliases(aliases)
    for source,target in aliases['files'].items():
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        (root/source).rename(path)
    for source,target in aliases['directories'].items():
        for path in list((root/source).rglob('*')):
            if path.is_file() and not path.name.endswith('.lock'):
                destination = root/target/path.relative_to(root/source)
                destination.parent.mkdir(parents=True, exist_ok=True)
                path.rename(destination)
    address = 'project/aliases/' + 'f'*32 + '.json'
    path = root/address
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(aliases))
    (root/'storage.json').write_text(json.dumps({'format':resolver.FORMAT, 'version':1,
        'phase':'ready','mode':'rehearsal','aliases':address,
        'aliases_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        **({'writer_policy':'organized_payloads_v1','writer_generation':0} if organized_writers else {})}))
    return resolver.rehearsal_access(root)


class PNGBridgeTests(unittest.TestCase):
    def test_numbered_fork_and_retry_stay_in_organized_exports(self):
        fixture = png_fixture.PNGVideoTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        first = fixture.export()
        old = Path(first['result'][0])
        root = fixture.root/'h3_chains/demo'
        source = old.relative_to(root).as_posix()
        with activate(fixture.root,png_fixture.package,directories={source:'exports/png/'+'a'*32},
                      organized_writers=True):
            changed,_=png_fixture.make_video(fixture.root/'changed.mkv',seed=44)
            variant=fixture.export(video=changed)
            actual=Path(variant['result'][0])
            self.assertEqual(actual.parent,root/'exports/png')
            self.assertFalse(old.with_name(old.name+'_2').joinpath('export.json').exists())
            resolver=importlib.import_module(png_fixture.package.__name__+'.storage_resolver')
            self.assertTrue(resolver.logical_output(fixture.root,actual).endswith(old.name+'_2'))
            retried=fixture.export(video=changed)
            self.assertEqual(retried['result'][0],variant['result'][0])
            self.assertIn('reused',retried['result'][2])

    def test_recovery_archive_owner_is_checked_after_location_resolution(self):
        fixture = png_fixture.PNGVideoTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        chain = png_fixture.chain
        plan, revision = {'run_name':'demo'}, 'a'*32
        paths = chain._run_archive_snapshot_paths(plan, revision)
        archives = {}
        for key, text in paths.items():
            path = Path(text)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'seed':'18446744073709551615','kind':key}))
            archives[key] = chain._relative_output_path(text)
        with activate(fixture.root, png_fixture.package, directories={'recovery_archives':'project/recovery'}):
            resolved, actual_revision = chain._validated_run_archive_snapshot(plan, archives, revision)
            self.assertEqual(actual_revision, revision)
            self.assertEqual({k:chain._relative_output_path(p) for k,p in resolved.items()}, archives)
            self.assertTrue(all('/project/recovery/' in p and Path(p).is_file() for p in resolved.values()))

    def test_existing_binding_reuse_append_and_numbered_fork(self):
        fixture = png_fixture.PNGVideoTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        first = fixture.export()
        old = Path(first['result'][0])
        root = fixture.root/'h3_chains/demo'
        relative = old.relative_to(root).as_posix()
        target = 'exports/png/' + 'a'*32
        original = {p.name:p.read_bytes() for p in old.glob('frame_*.png')}
        with activate(fixture.root, png_fixture.package, directories={relative:target}):
            reused = fixture.export(checkpoint_verification='strict')
            self.assertIn('reused', reused['result'][2])
            self.assertEqual(Path(reused['result'][0]), root/target)
            second = fixture.export(2)
            self.assertEqual(second['result'][1],6)
            self.assertEqual(original,{p:(root/target/p).read_bytes() for p in original})
            changed, _ = png_fixture.make_video(fixture.root/'changed.mkv', seed=44)
            variant = fixture.export(video=changed)
            expected = old.with_name(old.name+'_2')
            self.assertEqual(Path(variant['result'][0]), expected)
            self.assertNotEqual(expected.name, 'a'*32+'_2')
            retried = fixture.export(video=changed)
            self.assertIn('reused', retried['result'][2])
            self.assertEqual(Path(retried['result'][0]), expected)


class DeleteBridgeTests(unittest.TestCase):
    def test_exact_ownership_then_actual_delete_keeps_other_scene(self):
        fixture = delete_fixture.DeleteTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        take = fixture.pixel_save()
        owner = fixture.png_owner(take)
        relative = 'upscaled/hq/frames/upscale'
        directory = fixture.png_sequence([(1,[owner]),(2,['2'*64])], folder='h3_chains/demo/'+relative)
        before = fixture.preview(take)
        root = fixture.root/'h3_chains/demo'
        files = {}
        for field in delete_fixture.module.ARTIFACTS:
            address = take.get(field)
            if address:
                path = fixture.root/address
                files[path.relative_to(root).as_posix()] = 'media/pixel_upscale/'+'a'*32+'/'+path.name
        target = 'exports/png/' + 'c'*32
        with activate(fixture.root, delete_fixture.package, files, {relative:target}):
            after = fixture.preview(take)
            self.assertTrue(after['allowed'])
            self.assertEqual(before['owned_file_count'], after['owned_file_count'])
            self.assertEqual({f['path'] for f in before['files']},{f['path'] for f in after['files']})
            result = fixture.delete(take)
            self.assertGreater(result['deleted_files'],1)
            self.assertFalse((root/target/'frame_00000101.png').exists())
            self.assertTrue((root/target/'frame_00000102.png').is_file())
            self.assertFalse((fixture.root/take['revision_metadata']).exists())
            self.assertEqual(json.loads((root/target/'export.json').read_text())['deleted_scenes'],[1])


if __name__ == '__main__':
    unittest.main(argv=['storage-bridge-tests'])
