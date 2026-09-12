"""Actual ALT saver/Loop End acceptance preserves base lineage and authoring."""
import copy
import json
import unittest
from unittest.mock import patch
import uuid

import _storage_generation_integration_test as fixture

chain, module, torch = fixture.chain, fixture.module, fixture.torch
runtime, carriers, state = fixture.runtime, fixture.carriers, fixture.state


class AlternateDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.GenerationIntegrationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = self.f.store

    def candidate(self, scene=1, branch='main'):
        plan = dict(self.f.plan, _branch_id=branch)
        with runtime.runtime_access(self.store, selected=branch) as bound:
            plan['_storage_pin'] = bound.pin
            base = bound.reader.read(bound.reader.working_directory(self.f.run)+
                                     '/checkpoints/clip_%04d.json' % scene)['segment']
            alternate = chain._alternate_take_plan(plan, {'alternate_draft':{
                'enabled':True, 'scene':scene, 'scene_id':base['id'], 'base_revision':base['revision'],
                'prompt':'Selected alternate é 雪 '+uuid.uuid4().hex,
                'seed':18446744073709551603}})
        with carriers.node_host(self.store, generation_writers=(chain.MiniMaxH3ChainSegmentSave.save,)):
            incoming = chain.MiniMaxH3ChainLoopStart().start(alternate, scene, scene_range=str(scene))[1]
            saved = chain.MiniMaxH3ChainSegmentSave().save(incoming,
                self.f.frames[:alternate['shots'][scene-1]['delivered_frames']],
                fixture.av_latent(.4), self.f.audio(scene))['result'][0]
        return incoming, saved

    def end(self, incoming, saved, *, write=True):
        with carriers.node_host(self.store,
                generation_writers=(chain.MiniMaxH3ChainLoopEnd.end,) if write else (),
                input_adapters={chain.MiniMaxH3ChainLoopEnd.end:module('storage_continuation').loop_end_inputs}):
            return chain.MiniMaxH3ChainLoopEnd().end(['unused-start',0],incoming,
                self.f.frames[:saved['delivered_frames']],fixture.av_latent(.4),saved)

    def test_actual_acceptance_publishes_cut_and_frozen_manifest_without_changing_base(self):
        editorial = chain._normalize_run_editorial({'trims':[
            {'scene':2,'scene_id':'second','out_frame':4}], 'alternate_draft':{
                'scene':1,'scene_id':'first','base_revision':self.f.originals[0]['revision'],
                'prompt':'Pending alternate','seed':42,'enabled':True}},self.f.run)
        self.store.commit(self.store.snapshot(),{'editorial.json':dict(data=state._encode(editorial),
            scope='branch:main',category='branches',immutable=False)},operation_id=uuid.uuid4().hex)
        with carriers.node_host(self.store,generation_writers=(chain.MiniMaxH3ChainManifestLoad.load,)):
            chain.MiniMaxH3ChainManifestLoad().load(self.f.plan)
        incoming, saved = self.candidate()
        before = self.store.snapshot()
        input_plan = copy.deepcopy(incoming['plan'])
        manifest,raw,_,_ = self.end(incoming,saved)
        after = self.store.snapshot()
        cut = json.loads(after.read('editorial.json'))
        self.assertEqual(after.state['generation'],before.state['generation']+1)
        self.assertEqual(cut['trims'],editorial['trims'])
        self.assertIsNone(cut['alternate_draft'])
        self.assertEqual(cut['replacements'][0]['alternate_revision'],saved['revision'])
        self.assertEqual(manifest,json.loads(raw))
        self.assertEqual(manifest['editorial'],cut)
        self.assertEqual(manifest['_storage_pin']['root'],after.reference)
        self.assertEqual(incoming['plan'],input_plan)
        pointer_address = 'deliveries/main/alternates/scene_0001/latest.json'
        pointer = json.loads(after.read(pointer_address))
        frozen = json.loads(after.read(pointer['manifest']))
        self.assertEqual(frozen['_storage_pin'],saved['_storage_pin'])
        self.assertEqual(frozen['editorial'],cut)
        old,new = before.state['documents'],after.state['documents']
        self.assertEqual({p for p in old if old[p] != new[p]},{'editorial.json'})
        self.assertEqual(set(new)-set(old),{pointer_address,pointer['manifest']})
        self.assertFalse((self.store.project/'alternates').exists())
        with runtime.runtime_access(self.store) as bound:
            listing = chain._saved_checkpoint_listing(self.f.run,include_graph=True)
            presentation = chain._editorial_presentation_segments(self.f.run,self.f.originals,cut)
        first = listing['checkpoints'][0]
        self.assertEqual(first['revision'],self.f.originals[0]['revision'])
        self.assertEqual(first['presentation_revision'],saved['revision'])
        self.assertTrue(next(a for a in first['alternates'] if a['revision']==saved['revision'])['used_in_final_cut'])
        self.assertEqual(presentation[0]['revision'],saved['revision'])
        self.assertEqual(presentation[0]['presentation_base_revision'],self.f.originals[0]['revision'])
        self.assertEqual(presentation[1]['revision'],self.f.originals[1]['revision'])
        with carriers.node_host(self.store):
            base_manifest = chain.MiniMaxH3ChainManifestLoad().load(self.f.plan)[0]
        # Assembly reads audio from this generation manifest, independently of
        # the picture-only presentation list. The ALT does not replace that AV source.
        self.assertEqual(base_manifest['segments'][0]['generated_audio'],self.f.originals[0]['generated_audio'])
        self.assertEqual(self.store.verify_payloads(),10)

    def test_later_scene_alternate_keeps_prefix_and_existing_other_scene_selection(self):
        incoming,first = self.candidate()
        self.end(incoming,first)
        incoming,second = self.candidate(scene=2)
        before = self.store.snapshot()
        manifest = self.end(incoming,second)[0]
        after = self.store.snapshot()
        self.assertEqual([s['revision'] for s in manifest['segments']],
                         [self.f.originals[0]['revision'],second['revision']])
        cut = json.loads(after.read('editorial.json'))
        self.assertEqual([s['alternate_revision'] for s in cut['replacements']],[first['revision'],second['revision']])
        for scene in (1,2):
            address = 'checkpoints/clip_%04d.json' % scene
            self.assertEqual(before.read(address),after.read(address))

    def test_named_branch_selection_does_not_change_main_cut_or_assignments(self):
        branches = module('working_branches').WorkingBranches
        with runtime.runtime_access(self.store,branch_writes=True):
            named = branches(self.f.output,self.f.run).create('main','ALT fork',
                {'plan_json':json.dumps({'shots':[{'id':s['id'],'prompt':s['prompt']}
                    for s in self.f.plan['shots']]})},through_scene=2)['id']
        incoming,saved = self.candidate(branch=named)
        before = self.store.snapshot()
        manifest = self.end(incoming,saved)[0]
        after = self.store.snapshot()
        self.assertEqual(manifest['_branch_id'],named)
        self.assertEqual(manifest['_storage_pin']['branch_id'],named)
        self.assertIn('branches/'+named+'/editorial.json',after.state['documents'])
        self.assertEqual(after.state['scope_revisions']['branch:main'],before.state['scope_revisions']['branch:main'])
        for address,descriptor in before.state['documents'].items():
            if address != 'branches/'+named+'/editorial.json':
                self.assertEqual(descriptor,after.state['documents'][address])

    def test_failure_is_atomic_and_lost_ack_retry_does_not_reselect_after_later_alt(self):
        incoming,saved = self.candidate()
        before = self.store.snapshot()
        publisher = module('storage_delivery').RuntimeDelivery
        publish = publisher.publish
        def failing(service,*args,**kwargs):
            def stop(stage):
                if stage == 'document':
                    raise OSError('interrupted ALT selection')
            service.after_stage = stop
            return publish(service,*args,**kwargs)
        with patch.object(publisher,'publish',failing),self.assertRaises(OSError):
            self.end(incoming,saved)
        self.assertEqual(self.store.snapshot().reference,before.reference)
        real = self.store._publish
        def lost(*args,**kwargs):
            real(*args,**kwargs)
            raise OSError('lost ALT acknowledgement')
        with patch.object(self.store,'_publish',lost),self.assertRaises(OSError):
            self.end(incoming,saved)
        accepted = self.store.snapshot()
        old = self.end(incoming,saved)[0]
        self.assertEqual(self.store.snapshot().reference,accepted.reference)
        newer_input,newer = self.candidate()
        self.end(newer_input,newer)
        latest = self.store.snapshot()
        self.assertEqual(self.end(incoming,saved)[0],old)
        self.assertEqual(self.store.snapshot().reference,latest.reference)
        self.assertEqual(json.loads(latest.read('editorial.json'))['replacements'][0]['alternate_revision'],newer['revision'])
        with runtime.runtime_access(self.store):
            self.assertEqual(chain._manifest_editorial(old),old['editorial'])

    def test_concurrent_editorial_edit_is_not_overwritten_by_queued_acceptance(self):
        incoming,saved = self.candidate()
        editorial = chain._normalize_run_editorial({'trims':[
            {'scene':2,'scene_id':'second','out_frame':4}]},self.f.run)
        self.store.commit(self.store.snapshot(),{'editorial.json':dict(data=state._encode(editorial),
            scope='branch:main',category='branches',immutable=False)},operation_id=uuid.uuid4().hex)
        before = self.store.snapshot()
        with self.assertRaises(state.StateConflict):
            self.end(incoming,saved)
        self.assertEqual(self.store.snapshot().reference,before.reference)
        self.assertEqual(json.loads(before.read('editorial.json')),editorial)

    def test_new_base_missing_grant_or_owner_takeover_cannot_accept_stale_alternate(self):
        incoming,saved = self.candidate()
        before = self.store.snapshot().reference
        with self.assertRaisesRegex(ValueError,'generation writes'):
            self.end(incoming,saved,write=False)
        self.assertEqual(self.store.snapshot().reference,before)
        self.f.save()  # Real new generation take invalidates the candidate's base.
        after = self.store.snapshot().reference
        with self.assertRaises(state.StateConflict):
            self.end(incoming,saved)
        self.assertEqual(self.store.snapshot().reference,after)
        incoming,saved = self.candidate()
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store,ownership_writes=True):
            fixture.ownership.claim_project_ownership(self.f.output,self.f.run,
                'different-alt-test-owner',force=True)
        with self.assertRaises(fixture.ownership.ProjectOwnershipError):
            self.end(incoming,saved)
        self.assertEqual(self.store.snapshot().reference,before)

    def test_missing_original_audio_prevents_picture_only_acceptance(self):
        incoming,saved = self.candidate()
        before = self.store.snapshot()
        original = self.f.originals[0]
        path = self.store.payload_path(before,original['generated_audio'].split('h3_chains/'+self.f.run+'/')[1])
        path.write_bytes(b'corrupt test-only original audio')
        with self.assertRaises(ValueError):
            self.end(incoming,saved)
        self.assertEqual(self.store.snapshot().reference,before.reference)

    def test_incorrect_base_audio_checksum_prevents_acceptance_even_if_index_is_valid(self):
        base = self.store.snapshot()
        metadata = json.loads(base.read('checkpoints/clip_0001.json'))
        revision = uuid.uuid4().hex
        address = 'checkpoints/clip_0001.'+revision+'.json'
        metadata['segment'].update(revision=revision,generated_audio_sha256='0'*64,
            revision_metadata='h3_chains/'+self.f.run+'/'+address)
        # An imported/attributed take can share media paths. Check that its
        # declared audio checksum still agrees with the independently hashed index.
        self.store.commit(base,{
            address:dict(data=state._encode(metadata),scope='archive:'+revision,category='takes',immutable=True),
            'checkpoints/clip_0001.json':dict(data=state._encode(metadata),scope='branch:main',category='branches',immutable=False),
        },operation_id=uuid.uuid4().hex)
        incoming,saved = self.candidate()
        before = self.store.snapshot().reference
        with self.assertRaisesRegex(state.StateConflict,'saved checksum'):
            self.end(incoming,saved)
        self.assertEqual(self.store.snapshot().reference,before)

    def test_publisher_rejects_mismatched_target_prompt_seed_and_media_mode(self):
        incoming,saved = self.candidate()
        plan = incoming['plan']
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store,pin=saved['_storage_pin'],generation_writes=True) as bound:
            manifest = chain._partial_manifest(incoming,saved)
            manifest.update(format='h3_chain_alternate_manifest_v1',alternate_take=plan['alternate_take'])
            for key,value in [('seed',99),('prompt','not the saved prompt')]:
                bad = copy.deepcopy(plan)
                bad['shots'][0][key] = value
                with self.assertRaisesRegex(ValueError,'prompt or seed'):
                    bound.delivery.publish(bad,manifest,alternate_normalizer=chain._normalize_run_editorial)
            bad = copy.deepcopy(manifest)
            bad['alternate_take']['scene'] = 2
            with self.assertRaisesRegex(ValueError,'Plan target'):
                bound.delivery.publish(plan,bad,alternate_normalizer=chain._normalize_run_editorial)
            with self.assertRaisesRegex(ValueError,'host editorial normalizer'):
                bound.delivery.publish(plan,manifest)
        self.assertEqual(self.store.snapshot().reference,before)


if __name__ == '__main__':
    unittest.main(argv=['alternate-delivery-tests'],verbosity=2)
