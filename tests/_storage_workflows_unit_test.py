"""Saved workflow copies preserve exact seeds and never rewrite H3 contracts."""
import copy
import importlib
from pathlib import Path
import unittest

import _storage_resolver_unit_test as fixture

module=importlib.import_module('storage_workflows')


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.RelocationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.activate()
        self.output,self.root=self.fixture.output,self.fixture.root
        self.legacy='/media/original/output/h3_chains/demo'
        self.path=self.legacy+'/'+self.fixture.old

    def audit(self,value):
        return module.audit_workflow(value,self.output,self.root,legacy_project_root=self.legacy)

    def test_explicit_loader_copy_keeps_original_and_integer_precision(self):
        workflow={'10':{'class_type':'ExternalVideoLoader','inputs':{'path':self.path,'seed':18446744073709551601}},
                  '11':{'class_type':'MiniMaxH3ChainCheckpointManager','inputs':{'selection_json':
                      '{"checkpoint":"'+self.path+'"}'}}}
        original=copy.deepcopy(workflow)
        audit=self.audit(workflow)
        self.assertEqual(len(audit),2)
        copied=module.relink_workflow_copy(workflow,audit,approved_node_types={'ExternalVideoLoader'})
        self.assertEqual(workflow,original)
        self.assertEqual(copied['10']['inputs']['seed'],18446744073709551601)
        self.assertEqual(copied['11'],original['11'])
        self.assertEqual(Path(copied['10']['inputs']['path']),self.root/self.fixture.new)

    def test_embedded_or_unknown_paths_block_instead_of_substring_rewriting(self):
        for text in ('prefix '+self.path, self.path+'\nextra',self.legacy+'/absent.mp4'):
            workflow={'nodes':[{'type':'ExternalVideoLoader','widgets_values':[text]}]}
            with self.assertRaisesRegex(ValueError,'unapproved or ambiguous'):
                module.relink_workflow_copy(workflow,self.audit(workflow),approved_node_types={'ExternalVideoLoader'})

    def test_unapproved_node_and_stale_audit_are_rejected(self):
        value={'nodes':[{'type':'ExternalVideoLoader','widgets_values':[self.path]}]}
        audit=self.audit(value)
        with self.assertRaisesRegex(ValueError,'unapproved'):
            module.relink_workflow_copy(value,audit,approved_node_types=set())
        value['nodes'][0]['widgets_values'][0]='changed by user'
        with self.assertRaisesRegex(ValueError,'changed since'):
            module.relink_workflow_copy(value,audit,approved_node_types={'ExternalVideoLoader'})

    def test_only_known_plan_studio_preview_cache_is_cleared_in_copy(self):
        workflow={'nodes':[{'type':'MiniMaxH3ChainPlanStudio','widgets_values':['precise prompt',18446744073709551601],
            'properties':{'h3_plan_studio_checkpoint_cache_v1':{'checkpoints':[
                {'video':{'subfolder':self.legacy+'/segments','filename':'clip.mp4'}}]},
                'authoring':{'seed':18446744073709551601,'prompt':'precise prompt'}}}]}
        original=copy.deepcopy(workflow)
        audit=self.audit(workflow)
        self.assertEqual([r['status'] for r in audit],['refreshable_preview_cache'])
        clone=module.relink_workflow_copy(workflow,audit,approved_node_types=set())
        self.assertEqual(workflow,original)
        self.assertNotIn('h3_plan_studio_checkpoint_cache_v1',clone['nodes'][0]['properties'])
        self.assertEqual(clone['nodes'][0]['widgets_values'],original['nodes'][0]['widgets_values'])
        self.assertEqual(clone['nodes'][0]['properties']['authoring'],original['nodes'][0]['properties']['authoring'])


if __name__=='__main__':
    unittest.main()
