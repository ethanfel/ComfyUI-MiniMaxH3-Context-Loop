"""Actual Review selection/graph/resume across reversible catalogue quarantine.

These exercise the storage primitive, not public deletion permission or purge.
All media are CPU-generated in independently imported disposable projects.
"""
import json
import unittest
import uuid

import _storage_review_execution_integration_test as fixture

generation, chain, module = fixture.generation, fixture.chain, fixture.module
quarantine = module('storage_quarantine')
reads = module('storage_project_reads')
manager = module('checkpoint_manager')


class QuarantineIntegrationTests(unittest.TestCase):
    setUp = fixture.ReviewExecutionTests.setUp
    audio = fixture.ReviewExecutionTests.audio
    save_loop_scene = fixture.ReviewExecutionTests.save_loop_scene
    expand_loop = fixture.ReviewExecutionTests.expand_loop
    host = fixture.ReviewExecutionTests.host
    execute = fixture.ReviewExecutionTests.execute
    next_state = fixture.ReviewExecutionTests.next_state
    two_candidates = fixture.ReviewExecutionTests.two_candidates

    def graph(self, base=None):
        view = reads.ProjectReadView(self.store, base=base)
        return manager.CheckpointGraphManager(self.output, rehearsal_view=view).graph(
            self.run, adopt_legacy=False)

    def accepted_candidates(self):
        incoming, first, second, decision = self.two_candidates()
        job = uuid.uuid4().hex
        with self.host(job, select=True):
            accepted = self.execute(incoming, second, decision, candidate_count=2)['result'][0]
        return incoming, first, second, decision, job, accepted

    def retire(self, segment):
        base = self.store.snapshot()
        prefix = 'h3_chains/'+self.run+'/'
        addresses = [segment[key].removeprefix(prefix) for key in
                     ('revision_metadata', 'segment', 'checkpoint', 'generated_audio', 'prompt_file')]
        self.assertTrue(all(address in base.state['documents'] or
                            generation.project.payload_key(address) in base.state['documents']
                            for address in addresses))
        service = quarantine.ProjectQuarantine(self.store)
        preview = service.preview(base, addresses, reason='Rejected disposable Review candidate')
        result = service.quarantine(preview, operation_id=uuid.uuid4().hex)
        return service, base, preview, result

    def assert_source_unchanged(self):
        self.assertEqual(self.source_files, {p.relative_to(self.source).as_posix():p.read_bytes()
                                            for p in self.source.rglob('*') if p.is_file()})

    def test_graph_hides_rejected_candidate_old_pin_keeps_it_and_undo_restores_exact_graph(self):
        incoming, first, second, decision, job, accepted = self.accepted_candidates()
        before_graph = self.graph()
        self.assertTrue({first['revision'], second['revision']} <=
                        {r['revision'] for r in before_graph['revisions']})
        service, before, preview, receipt = self.retire(second)
        after = self.store.snapshot()
        rows = {r['revision']:r for r in self.graph()['revisions']}
        self.assertNotIn(second['revision'], rows)
        self.assertTrue(rows[first['revision']]['ready'])
        self.assertEqual(self.graph(before), before_graph)
        self.assertEqual(after.read('checkpoints/clip_0001.json'), before.read('checkpoints/clip_0001.json'))
        with self.host(job, select=True):
            repeated = self.execute(incoming, second, decision, candidate_count=2)['result'][0]
            advanced = self.next_state(incoming, repeated)
        self.assertFalse(self.events)
        self.assertEqual(advanced['segments'][0]['revision'], first['revision'])
        self.assertEqual(advanced['plan']['shots'][0]['seed'], first['seed'])
        self.assertEqual(advanced['plan']['shots'][0]['prompt'], first['prompt'])
        self.assertTrue(fixture.torch.equal(advanced['previous_latent']['samples'][0],
                                           fixture.av_latent(.4)['samples'][0]))
        self.assertEqual(after.reference, self.store.snapshot().reference)
        service.undo(after, receipt['operation_id'], operation_id=uuid.uuid4().hex)
        self.assertEqual(self.graph(), before_graph)
        restored = self.store.snapshot()
        for item in preview['items']:
            self.assertEqual(restored.read(item['key']), before.read(item['key']))
            self.assertEqual(restored.state['documents'][item['key']], item['descriptor'])
        self.store.verify_payloads()
        self.assert_source_unchanged()

    def test_ordinary_recovery_does_not_resurrect_rejected_take_and_resumes_approved_context(self):
        incoming, first, second, decision, job, accepted = self.accepted_candidates()
        service, before, preview, receipt = self.retire(second)
        quarantined = self.store.snapshot()
        copy_receipt = self.lab/'quarantined-copy.json'
        generation.state.atomic_json(copy_receipt, dict(copy=str(self.store.project), source=str(self.source),
                                                        independent_copies=True))
        recovery = module('storage_recovery')
        output = self.lab/'quarantined-recovered-output'
        journal = recovery.prepare_legacy_copy(copy_receipt, output, self.lab/'quarantine-recovery',
                                               rehearsal_store=self.store)
        recovered = recovery.recover_legacy_copy(journal)
        self.assertTrue(recovered['source_unchanged'])
        root = output/'h3_chains'/self.run
        for item in preview['items']:
            self.assertFalse((root/item['address']).exists())
        pointer = json.loads((root/'checkpoints/clip_0001.json').read_bytes())
        self.assertEqual(pointer['segment']['revision'], first['revision'])
        old_graph = manager.CheckpointGraphManager(output).graph(self.run, adopt_legacy=False)
        self.assertNotIn(second['revision'], {r['revision'] for r in old_graph['revisions']})
        plan = dict(accepted['_h3_review_decision']['plan'])
        plan.pop('_storage_pin')
        generation.folder_paths.output_directory = str(output)
        resumed = chain.MiniMaxH3ChainLoopStart().start(plan, 2)[1]
        self.assertEqual(resumed['segments'][0]['revision'], first['revision'])
        self.assertEqual(resumed['plan']['shots'][0]['seed'], first['seed'])
        self.assertEqual(resumed['plan']['shots'][0]['prompt'], first['prompt'])
        self.assertTrue(fixture.torch.equal(resumed['previous_latent']['samples'][0],
                                           fixture.av_latent(.4)['samples'][0]))
        self.assertEqual(self.store.snapshot().reference, quarantined.reference)
        self.assertEqual(recovered, recovery.recover_legacy_copy(journal))
        self.assert_source_unchanged()


if __name__ == '__main__':
    unittest.main(argv=[__file__])
