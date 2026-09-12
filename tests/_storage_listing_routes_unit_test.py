"""Real listing/editorial/final-cut consumers on one accepted combined root."""
import asyncio
import ast
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from typing import Any
import unittest
import uuid

import _storage_restore_routes_unit_test as fixture
from _storage_branch_routes_unit_test import request
from branch_scope import current_branch
from chapter_resolution import normalize_resolution
from checkpoint_manager import CheckpointGraphManager, checkpoint_revision_token, checkpoint_audio_context_length, _strict_run_name
from working_branches import WorkingBranches
from storage_runtime import runtime_access
from storage_resolver import resolve_output
from storage_branch_controls import BranchControlDocuments
import storage_state as state


def routes(output, *, source=None):
    source = Path(source) if source else Path(__file__).resolve().parents[1]/'chain_nodes.py'
    names = {'_saved_checkpoint_listing', '_list_saved_checkpoints', '_load_run_editorial',
        '_checkpoint_review_preview', '_checkpoint_audio_sidecar', '_video_output_item',
        '_read_json', '_file_sha256', '_safe_name', '_canonical_json', '_fingerprint',
        '_normalize_run_editorial', '_editorial_for_base_segments', '_editorial_after_base_revision_change',
        '_log_checkpoint_editorial_notices', '_editorial_trimmed_segment', '_h3_prefix_frame_boundary_step',
        '_editorial_stale_dependencies', '_editorial_dependency_mismatches', '_editorial_dependency_sources',
        '_saved_audio_dependency_length', '_editorial_segment_delivered_frames',
        '_editorial_presentation_segments', '_load_checkpoint_revision', '_verify_segment_artifacts'}
    nodes = [node for node in ast.parse(source.read_text()).body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in nodes} == names
    # Only transport/environment are substituted. The helper module identity
    # lets the real final_cut_contexts receive this exact chain namespace.
    module = ModuleType('_h3_storage_listing_fixture_'+uuid.uuid4().hex)
    sys.modules[module.__name__] = module
    namespace = module.__dict__
    namespace.update(__package__='', Any=Any, os=os, re=re, json=json, hashlib=hashlib,
        sys=sys, asyncio=asyncio, time=time, math=math, datetime=datetime, timezone=timezone,
        MAX_SHOTS=128, MAX_SEED=0xFFFFFFFFFFFFFFFF, MAX_H3_FRAMES=3592, FPS=24, AUDIO_HZ=40,
        WorkingBranches=WorkingBranches, CheckpointGraphManager=CheckpointGraphManager,
        checkpoint_revision_token=checkpoint_revision_token,
        checkpoint_audio_context_length=checkpoint_audio_context_length,
        current_branch=current_branch, normalize_resolution=normalize_resolution,
        _strict_run_name=_strict_run_name, _output_root=lambda: str(output),
        _absolute_output_path=lambda value: str(resolve_output(output, value)),
        _CHECKPOINT_EDITORIAL_NOTICES=OrderedDict(), _CHECKPOINT_EDITORIAL_NOTICE_LOCK=threading.Lock(),
        _LOG=logging.getLogger('h3_storage_listing_fixture'),
        web=SimpleNamespace(json_response=lambda value, status=200, **kwargs:
                            {'status': status, 'body': value, **kwargs}))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


class ListingTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RestoreTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.named, self.output = self.f.store, self.f.named, self.f.output
        self.ns = routes(self.output)
        self.addCleanup(sys.modules.pop, self.ns['__name__'])
        base = self.store.snapshot()
        changes = {}
        for prefix in ('', 'branches/'+self.named+'/'):
            changes.update(self.f.change(prefix+'editorial.json', {}))
            for scene in (1, 2):
                metadata = state._decode(base.read('checkpoints/clip_%04d.json' % scene))
                metadata['segment']['delivered_frames'] = 124
                changes.update(self.f.change(prefix+'checkpoints/clip_%04d.json' % scene, metadata))
        self.store.commit(base, changes, operation_id=uuid.uuid4().hex,
            retire_pointers=['checkpoints/clip_0003.json', 'branches/'+self.named+'/checkpoints/clip_0003.json'])

    def call(self, graph=True):
        return asyncio.run(self.ns['_list_saved_checkpoints'](request(
            dict(run_name='demo', include_graph=str(graph).lower()), method='GET')))

    def write_cut(self, selected, value):
        address = ('branches/'+selected+'/' if selected != 'main' else '')+'editorial.json'
        self.store.commit(self.store.snapshot(), self.f.change(address, value), operation_id=uuid.uuid4().hex)

    def test_actual_listing_has_graph_checkpoints_pins_and_readable_view_urls(self):
        before = self.store.snapshot()
        with runtime_access(self.store) as runtime:
            response = self.call()
            self.assertEqual(response['status'], 200, response)
            result = response['body']
            self.assertEqual(result['storage_pin'], runtime.pin)
            self.assertEqual([row['scene'] for row in result['checkpoints']], [1, 2])
            self.assertEqual(len(result['revisions']), 2)
            self.assertEqual(result['processing_variants'], [])
            self.assertEqual({row['id'] for row in result['final_cut_contexts']}, {'main', self.named})
            for row in result['checkpoints']:
                self.assertEqual(row['metadata_sha256'], state._hash(before.read('checkpoints/clip_%04d.json' % row['scene'])))
                for role in ('video', 'audio'):
                    item = row[role]
                    self.assertTrue((self.output/item['subfolder']/item['filename']).is_file())
        self.assertEqual(before.reference, self.store.snapshot().reference)
        self.assertFalse((self.store.project/'checkpoints').exists())

    def test_alt_selection_is_branch_specific_in_listing_and_final_cut_contexts(self):
        revision = self.f.alternative(1, take_kind='editorial_alternate', alternate_of_revision='1'*32)
        cut = dict(replacements=[dict(scene=1, scene_id='scene_1', base_revision='1'*32, alternate_revision=revision)])
        self.write_cut(self.named, cut)
        with runtime_access(self.store, selected=self.named):
            response = self.call()
            self.assertEqual(response['status'], 200, response)
            item = response['body']['checkpoints'][0]
            self.assertEqual(item['presentation_revision'], revision)
            self.assertTrue(item['alternates'][0]['used_in_final_cut'])
            contexts = {row['id']: row for row in response['body']['final_cut_contexts']}
            self.assertEqual(contexts[self.named]['replacements'][0]['alternate_revision'], revision)
            self.assertEqual(contexts['main']['replacements'], [])
        with runtime_access(self.store):
            response = self.call(False)
            self.assertEqual(response['status'], 200, response)
            self.assertNotIn('presentation_revision', response['body']['checkpoints'][0])
            self.assertFalse(response['body']['checkpoints'][0]['alternates'][0]['used_in_final_cut'])

    def test_stale_alt_warns_without_editing_saved_cut_or_hiding_available_take(self):
        revision = self.f.alternative(1, take_kind='editorial_alternate', alternate_of_revision='a'*32)
        cut = dict(replacements=[dict(scene=1, scene_id='scene_1', base_revision='a'*32, alternate_revision=revision)])
        self.write_cut('main', cut)
        before = self.store.snapshot()
        with runtime_access(self.store):
            for _ in range(2):
                response = self.call(False)
                self.assertEqual(response['status'], 200, response)
                self.assertTrue(response['body']['editorial_notices'])
                row = response['body']['checkpoints'][0]
                self.assertNotIn('presentation_revision', row)
                self.assertEqual(row['alternates'][0]['revision'], revision)
            self.assertEqual(len(self.ns['_CHECKPOINT_EDITORIAL_NOTICES']), 1)
        self.assertEqual(self.store.snapshot().read('editorial.json'), before.read('editorial.json'))

    def test_old_pin_keeps_old_assignments_after_restore(self):
        with runtime_access(self.store) as runtime:
            pin = runtime.pin
            original = self.call(False)
        with runtime_access(self.store, branch_writes=True):
            self.assertEqual(self.f.call()['status'], 200)
        with runtime_access(self.store, pin=pin):
            self.assertEqual(self.call(False), original)
        with runtime_access(self.store):
            current = self.call(False)
            self.assertEqual([row['scene'] for row in current['body']['checkpoints']], [1])

    def test_invalid_accepted_editorial_fails_instead_of_returning_original(self):
        self.write_cut('main', {'replacements': 'broken'})
        before = self.store.snapshot().reference
        with runtime_access(self.store):
            result = self.call(False)
            self.assertEqual(result['status'], 500, result)
            self.assertIn('replacements', result['body']['error'])
        self.assertEqual(before, self.store.snapshot().reference)

    def test_read_hash_corruption_is_reported_without_adopting_legacy_file(self):
        with runtime_access(self.store) as runtime:
            path = runtime.reader.path(self.store.project/'editorial.json')
            raw = path.read_bytes()
            try:
                path.write_bytes(b'{}')
                response = self.call(False)
                self.assertEqual(response['status'], 500, response)
            finally:
                path.write_bytes(raw)

    def test_unrendered_slot_evidence_does_not_include_retained_or_orphan_media(self):
        self.write_cut('main', {'scene_order': [dict(scene=i, scene_id='scene_'+str(i)) for i in range(1, 5)]})
        with runtime_access(self.store):
            response = self.call(False)
            self.assertEqual(response['body']['editorial_unused_scene_ids'], ['scene_3', 'scene_4'])
        tiny = self.output/'private-orphan.mp4'
        tiny.write_bytes(b'private retained orphan')
        receipt = self.store.stage_payload('segments/clip_0003.'+'f'*32+'.mp4', tiny,
            'media/generation/'+'f'*32+'/video.mp4', scope='archive:'+'f'*32, operation_id=uuid.uuid4().hex)
        self.store.commit_artifacts(self.store.snapshot(), {}, [receipt], operation_id=uuid.uuid4().hex)
        with runtime_access(self.store):
            response = self.call(False)
            self.assertEqual(response['body']['editorial_unused_scene_ids'], ['scene_4'])

    def test_optional_review_and_partial_previews_use_accepted_physical_paths(self):
        base = self.store.snapshot()
        segment = state._decode(base.read('checkpoints/clip_0001.json'))['segment']
        receipts = []
        for i, address in enumerate(('reviews/clip_0001.'+segment['segment_sha256'][:12]+'.test.review.mp4',
                                    'final/partial_through_clip_0001.mp4')):
            path = self.output/('private-preview-'+str(i)+'.mp4')
            path.write_bytes(b'private optional preview')
            receipts.append(self.store.stage_payload(address, path, 'exports/video/'+uuid.uuid4().hex+'/video.mp4',
                scope='exports:private'+str(i), operation_id=uuid.uuid4().hex))
        self.store.commit_artifacts(base, {}, receipts, operation_id=uuid.uuid4().hex)
        with runtime_access(self.store):
            response = self.call(False)
            self.assertEqual(response['status'], 200, response)
            item = response['body']['checkpoints'][0]
            for role in ('preview_video', 'partial_video'):
                video = item[role]
                self.assertTrue((self.output/video['subfolder']/video['filename']).is_file())

    def test_selected_alt_picture_is_used_by_normal_presentation_reader(self):
        revision = self.f.alternative(1, take_kind='editorial_alternate', alternate_of_revision='1'*32,
                                      delivered_frames=124)
        self.write_cut(self.named, dict(replacements=[dict(scene=1, scene_id='scene_1',
            base_revision='1'*32, alternate_revision=revision)]))
        base = self.store.snapshot()
        segments = [state._decode(base.read('checkpoints/clip_%04d.json' % i))['segment'] for i in (1, 2)]
        with runtime_access(self.store, selected=self.named):
            presented = self.ns['_editorial_presentation_segments']('demo', segments)
            self.assertEqual(presented[0]['presentation_alternate_revision'], revision)
            self.assertEqual(presented[0]['presentation_base_revision'], '1'*32)
            self.assertEqual(presented[0]['presentation_media_mode'], 'picture_only')
            self.assertEqual(presented[1], segments[1])
        self.assertNotIn('presentation_alternate_revision', segments[0])
        self.assertEqual(self.store.snapshot().reference, base.reference)

    def test_presentation_reader_validates_alt_media_and_duration(self):
        revision = self.f.alternative(1, take_kind='editorial_alternate', alternate_of_revision='1'*32,
                                      delivered_frames=123)
        self.write_cut('main', dict(replacements=[dict(scene=1, scene_id='scene_1',
            base_revision='1'*32, alternate_revision=revision)]))
        with runtime_access(self.store) as runtime:
            base = runtime.reader.read(self.store.project/'checkpoints/clip_0001.json')['segment']
            with self.assertRaisesRegex(ValueError, 'changes delivered_frames'):
                self.ns['_editorial_presentation_segments']('demo', [base])
            metadata = runtime.reader.read(self.store.project/('checkpoints/clip_0001.'+revision+'.json'))
            path = runtime.reader.path(metadata['segment']['segment'])
            raw = path.read_bytes()
            try:
                path.write_bytes(b'corrupt')
                with self.assertRaisesRegex(ValueError, 'integrity'):
                    self.ns['_editorial_presentation_segments']('demo', [base])
            finally:
                path.write_bytes(raw)


if __name__ == '__main__':
    unittest.main(verbosity=2)
