"""Owned lifecycle result gates remain active in normal and optimized Python."""
import copy
from importlib.util import module_from_spec,spec_from_file_location
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=spec_from_file_location('carat_owned_lifecycle_driver',ROOT/'scripts/compare_lifecycle.py')
driver=module_from_spec(spec)
spec.loader.exec_module(driver)


class LifecycleDriverTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir=os.environ.get('CARAT_WORK_ROOT'))
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        first,second,facts=driver.two_window_fixture()
        payload={'batches':[first,second],
                 'initial':[driver.canonical_text(f) for f in facts if f['batch']==first],
                 'later':[driver.canonical_text(f) for f in facts if f['batch']==second]}
        self.input=self.root/'input.json'
        self.input.write_text(json.dumps(payload),encoding='utf-8')

    def test_valid_storage_paths_keep_complete_same_information_contract(self):
        pair=[driver.trial(mode,self.root/mode,self.input,2) for mode in ('projection','sqlite')]
        driver.validate_pair(pair)
        self.assertEqual([r['replay_added'] for r in pair],[0,0])
        self.assertEqual([r['appended'] for r in pair],[10,10])
        self.assertEqual(len(pair[0]['recovery']['canonical_facts']),20)

    def test_bad_initial_result_is_rejected_in_optimized_mode_too(self):
        for mode,cls in (('projection',driver.ProjectionArchive),('sqlite',driver.SQLiteArchive)):
            original=cls.query
            def wrong(store):
                answer=original(store)
                answer['accounting']['gross_mass']+=1
                return answer
            with self.subTest(mode=mode),patch.object(cls,'query',wrong):
                with self.assertRaisesRegex(ValueError,'initial accounting'):
                    driver.trial(mode,self.root/mode,self.input,1)

    def test_pair_gate_checks_each_scientific_output(self):
        row={'initial_queries':[{'mass':5}],'later_queries':[{'mass':10}],
             'recovery':{'canonical_facts':['owned'],'query':{'mass':10}}}
        for field in row:
            pair=[copy.deepcopy(row),copy.deepcopy(row)]
            pair[1][field]=None
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,field):
                driver.validate_pair(pair)
        with self.assertRaisesRegex(ValueError,'both storage paths'):
            driver.validate_pair([row])

    def test_inconsistent_pair_never_publishes_samples_or_success_summary(self):
        def inconsistent(mode,work,input_path,queries):
            return {'initial_queries':[mode],'later_queries':['same'],'recovery':{'same':True}}
        work,out=self.root/'work',self.root/'results'
        with patch.object(sys,'argv',['compare_lifecycle.py','--work',str(work),'--output',str(out),'--repeats','1']), \
                patch.object(driver,'trial',side_effect=inconsistent):
            with self.assertRaisesRegex(ValueError,'initial_queries'):
                driver.main()
        self.assertFalse((out/'samples.json').exists())
        self.assertFalse((out/'summary.json').exists())
        self.assertFalse((out/'bindings.json').exists())


if __name__=='__main__':
    unittest.main()
