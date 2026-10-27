"""Safety ordering tests; native Photos changes are simulated on Linux."""
from pathlib import Path
from types import SimpleNamespace
import tempfile, unittest
from campresso.photos_actions import workflow, save_journal

class Asset:
    def localIdentifier(self):return 'NEW/L0/001'
    def mediaType(self):return 2
    def duration(self):return 10
    def pixelWidth(self):return 1920
    def pixelHeight(self):return 1080
class FakeActions:
    P=SimpleNamespace(PHAssetMediaTypeVideo=2)
    def __init__(self):self.calls=[];self.new=Asset();self.fail_import=False;self.fail_delete=False
    def check(self,uuid):self.calls.append(('check',uuid));return 'original'
    def import_video(self,path,old=None):
        self.calls.append(('import',str(path)))
        if self.fail_import:raise RuntimeError('Import denied')
        return self.new
    def remove(self,asset):
        self.calls.append(('remove',asset))
        if self.fail_delete:raise RuntimeError('Deletion cancelled')

class ActionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.backup=root/'original.mp4';self.backup.write_bytes(b'a'*100)
        self.output=root/'converted.mp4';self.output.write_bytes(b'b'*10)
        self.request=dict(action='finish',uuid='ORIGINAL',output=str(self.output),backup=str(self.backup),remove=True,expected=dict(duration=10,width=1920,height=1080))
        self.adapter=FakeActions();self.journal={}
    def tearDown(self):self.temp.cleanup()
    def test_order_verify_before_remove_and_checkpoint(self):
        snapshots=[]
        result=workflow(self.request,self.adapter,self.journal,lambda:snapshots.append(dict(self.journal)))
        self.assertEqual([x[0] for x in self.adapter.calls],['check','import','remove'])
        phases=[s['phase'] for s in snapshots]
        self.assertLess(phases.index('Import verified'),phases.index('Removal pending'))
        self.assertEqual(phases[-1],'Original moved to Recently Deleted');self.assertTrue(result['removed'])
        self.assertTrue(self.backup.exists());self.assertEqual(self.journal['new_uuid'],'NEW/L0/001')
    def test_import_failure_never_deletes(self):
        self.adapter.fail_import=True
        with self.assertRaisesRegex(RuntimeError,'Import denied'):workflow(self.request,self.adapter,self.journal)
        self.assertNotIn('remove',[x[0] for x in self.adapter.calls])
    def test_wrong_import_duration_never_deletes(self):
        self.adapter.new.duration=lambda:2
        with self.assertRaisesRegex(RuntimeError,'duration'):workflow(self.request,self.adapter,self.journal)
        self.assertNotIn('remove',[x[0] for x in self.adapter.calls]);self.assertIn('new_uuid',self.journal)
    def test_wrong_import_dimensions_never_deletes(self):
        self.adapter.new.pixelWidth=lambda:1280
        with self.assertRaisesRegex(RuntimeError,'dimensions'):workflow(self.request,self.adapter,self.journal)
        self.assertNotIn('remove',[x[0] for x in self.adapter.calls])
    def test_missing_backup_prevents_import_and_delete(self):
        self.backup.unlink()
        with self.assertRaisesRegex(RuntimeError,'backup'):workflow(self.request,self.adapter,self.journal)
        self.assertEqual([x[0] for x in self.adapter.calls],['check'])
    def test_bigger_result_keeps_original(self):
        self.output.write_bytes(b'b'*200)
        with self.assertRaisesRegex(RuntimeError,'not smaller'):workflow(self.request,self.adapter,self.journal)
        self.assertEqual([x[0] for x in self.adapter.calls],['check'])
    def test_import_only_keeps_original(self):
        self.request['remove']=False
        self.assertFalse(workflow(self.request,self.adapter,self.journal)['removed'])
        self.assertNotIn('remove',[x[0] for x in self.adapter.calls])
    def test_external_import_no_original_required(self):
        self.request.update(uuid=None,remove=False)
        workflow(self.request,self.adapter,self.journal)
        self.assertEqual([x[0] for x in self.adapter.calls],['import'])
    def test_delete_declined_keeps_new_id_for_recovery(self):
        self.adapter.fail_delete=True
        with self.assertRaisesRegex(RuntimeError,'cancelled'):workflow(self.request,self.adapter,self.journal)
        self.assertEqual(self.journal['phase'],'Removal pending');self.assertEqual(self.journal['new_uuid'],'NEW/L0/001')
    def test_import_intent_checkpoint_survives_failure(self):
        self.adapter.fail_import=True;snapshots=[]
        with self.assertRaises(RuntimeError):workflow(self.request,self.adapter,self.journal,lambda:snapshots.append(dict(self.journal)))
        self.assertEqual(snapshots[-1]['phase'],'Import pending')
    def test_invalid_expectations_prevent_import(self):
        self.request['expected']['duration']=float('nan')
        with self.assertRaisesRegex(RuntimeError,'Invalid expected'):workflow(self.request,self.adapter,self.journal)
        self.assertNotIn('import',[x[0] for x in self.adapter.calls])
    def test_nonfinite_imported_duration_prevents_removal(self):
        self.adapter.new.duration=lambda:float('nan')
        with self.assertRaisesRegex(RuntimeError,'duration'):workflow(self.request,self.adapter,self.journal)
        self.assertNotIn('remove',[x[0] for x in self.adapter.calls])
    def test_unknown_action_prevents_photos_operations(self):
        self.request['action']='unexpected'
        with self.assertRaisesRegex(RuntimeError,'Unknown Photos action'):workflow(self.request,self.adapter,self.journal)
        self.assertEqual(self.adapter.calls,[])
    def test_atomic_journal_failure_retains_previous_contents(self):
        target=Path(self.temp.name)/'journal.json'
        save_journal(target,{'phase':'Import pending'})
        original=target.read_bytes()
        with self.assertRaises(ValueError):save_journal(target,{'invalid':float('nan')})
        self.assertEqual(target.read_bytes(),original)
        self.assertFalse(list(target.parent.glob('.journal.json-*')))
    def test_journal_alias_cannot_write_inside_photos(self):
        library=Path(self.temp.name)/'Library.photoslibrary';library.mkdir()
        alias=Path(self.temp.name)/'alias';alias.symlink_to(library,target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError,'outside'):save_journal(alias/'journal.json',{'phase':'Import pending'})
        self.assertEqual(list(library.iterdir()),[])

if __name__=='__main__':unittest.main()
