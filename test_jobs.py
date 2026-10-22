"""Unified pipeline sequencing tests, with dependency-injected Photos operations."""
import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from photos_catalog import Media
from jobs import JobRunner, JobFailure
from engine import Cancelled

class FakeConverter:
    def stop(self):pass
    def convert(self,path,settings,report):
        self.settings=settings;report('Completed',100)
        output=Path(path).parent/'converted.mp4';output.write_bytes(b'converted');return str(output),'converted'
    def probe(self,path):return dict(format=dict(duration='10'),streams=[dict(codec_type='video',width=1920,height=1080,disposition={})])

class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.original=self.root/'original.mp4';self.original.write_bytes(b'original')
        self.runner=JobRunner(self.root/'Work');self.calls=[];self.runner.actions=lambda r:self.calls.append(r) or dict(phase='Import verified',new_uuid='NEW')
        self.media=Media('OLD','video.mp4','Video',100,1920,1080,'',None,False)
        self.job=dict(media=self.media,library='/Library.photoslibrary',path=None,import_=True,remove=False,replace=False)
        self.job['import']=self.job.pop('import_')
        self.settings=dict(folder=str(self.root/'output'),codec='HEVC',size='1080',quality='Balanced',container='MP4',replace=False)
    def tearDown(self):self.temp.cleanup()
    def test_auto_fetch_then_convert_then_import(self):
        stages=[]
        with patch('jobs.Converter',FakeConverter),patch('jobs.fetch_video',return_value=str(self.original)) as fetch:
            self.runner.run(self.job,self.settings,lambda s,p=None,*a:stages.append((s,p)))
        fetch.assert_called_once();self.assertEqual([c['action'] for c in self.calls],['check','finish'])
        self.assertEqual(stages[-1],('Completed',100));self.assertTrue(all(p!=100 for _,p in stages[:-1]))
    def test_folder_only_does_not_change_photos(self):
        self.job['import']=False
        with patch('jobs.Converter',FakeConverter),patch('jobs.fetch_video',return_value=str(self.original)):
            self.runner.run(self.job,self.settings,lambda *a:None)
        self.assertEqual(self.calls,[])
    def test_no_removal_without_import(self):
        self.job.update(remove=True);self.job['import']=False
        with patch('jobs.Converter',FakeConverter),patch('jobs.fetch_video') as fetch:
            with self.assertRaisesRegex(RuntimeError,'requires importing'):self.runner.run(self.job,self.settings,lambda *a:None)
        fetch.assert_not_called()
    def test_failed_conversion_cannot_import_delete(self):
        class Failing(FakeConverter):
            def convert(self,*a):raise RuntimeError('encode failed')
        with patch('jobs.Converter',Failing),patch('jobs.fetch_video',return_value=str(self.original)):
            with self.assertRaisesRegex(RuntimeError,'encode failed'):self.runner.run(self.job,self.settings,lambda *a:None)
        self.assertEqual([c['action'] for c in self.calls],['check'])
    def test_reject_output_inside_library(self):
        with self.assertRaisesRegex(RuntimeError,'outside'):JobRunner(self.root/'Library.photoslibrary'/'Work')
    def test_external_files_bypass_fetch_and_optional_import(self):
        self.job.update(media=None,path=str(self.original));self.job['import']=False
        with patch('jobs.Converter',FakeConverter),patch('jobs.fetch_video') as fetch:self.runner.run(self.job,self.settings,lambda *a:None)
        fetch.assert_not_called();self.assertEqual(self.calls,[])
    def test_import_failure_retains_output_and_blocks_automatic_retry(self):
        def actions(request):
            if request['action']=='finish':raise JobFailure('native helper crashed',journal='recovery.json',retry_safe=False)
            return {'checked':True}
        self.runner.actions=actions
        with patch('jobs.Converter',FakeConverter),patch('jobs.fetch_video',return_value=str(self.original)):
            with self.assertRaises(JobFailure) as caught:self.runner.run(self.job,self.settings,lambda *a:None)
        error=caught.exception
        self.assertTrue(Path(error.output).is_file());self.assertEqual(error.journal,'recovery.json');self.assertFalse(error.retry_safe)
    def test_preflight_failure_remains_safe_to_retry(self):
        runner=JobRunner(self.root/'AnotherWork')
        result=SimpleNamespace(returncode=1,stdout='{"error":"Access denied"}',stderr='')
        with patch('jobs.subprocess.run',return_value=result):
            with self.assertRaises(JobFailure) as caught:runner.actions({'action':'check'})
        self.assertTrue(caught.exception.retry_safe);self.assertIsNone(caught.exception.output)
    def test_native_crash_is_not_safe_to_retry_after_import_request(self):
        runner=JobRunner(self.root/'AnotherWork')
        result=SimpleNamespace(returncode=-6,stdout='',stderr='OC_PythonException')
        with patch('jobs.subprocess.run',return_value=result):
            with self.assertRaises(JobFailure) as caught:runner.actions({'action':'finish'})
        self.assertFalse(caught.exception.retry_safe);self.assertIn('OC_PythonException',str(caught.exception));self.assertTrue(caught.exception.journal.endswith('.json'))
    def test_empty_success_response_does_not_mark_import_complete(self):
        runner=JobRunner(self.root/'AnotherWork')
        result=SimpleNamespace(returncode=0,stdout='{}',stderr='')
        with patch('jobs.subprocess.run',return_value=result):
            with self.assertRaisesRegex(JobFailure,'did not confirm'):runner.actions({'action':'finish'})
    def test_unverified_or_incomplete_removal_response_is_rejected(self):
        runner=JobRunner(self.root/'AnotherWork')
        for response in ('{"new_uuid":"NEW","phase":"Imported","removed":false}',
                         '{"new_uuid":"NEW","phase":"Import verified","removed":false}'):
            result=SimpleNamespace(returncode=0,stdout=response,stderr='')
            with patch('jobs.subprocess.run',return_value=result):
                with self.assertRaisesRegex(JobFailure,'did not confirm'):runner.actions({'action':'finish','remove':True})
    def test_stop_after_conversion_retains_saved_output(self):
        runner=self.runner
        class StopsAfterSaving(FakeConverter):
            def convert(self,*args):
                result=super().convert(*args);runner.cancel.set();return result
        with patch('jobs.Converter',StopsAfterSaving),patch('jobs.fetch_video',return_value=str(self.original)):
            with self.assertRaises(Cancelled) as caught:runner.run(self.job,self.settings,lambda *a:None)
        self.assertTrue(Path(caught.exception.output).is_file());self.assertTrue(caught.exception.retry_safe)
        self.assertEqual([c['action'] for c in self.calls],['check'])

if __name__=='__main__':unittest.main()
