"""Download orchestration tests; Photos/iCloud requests are simulated."""
import tempfile, threading, subprocess, unittest
from pathlib import Path
from dataclasses import replace
from photos_catalog import Media, ScanCancelled
from photos_download import fetch_video, command, wait_process

class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.lib=self.root/'Library.photoslibrary';self.lib.mkdir();self.dest=self.root/'downloads'
        self.cli=self.root/'osxphotos';self.cli.write_text('fake')
        self.media=Media('selected-only','CloudClip.mp4','Video',None,64,64,'2026-09-12',None,False)
        self.cancel=threading.Event()
    def tearDown(self):self.temp.cleanup()
    def runner(self,args,cancel,report):
        self.assertEqual(args.count('--uuid'),1);self.assertEqual(args[args.index('--uuid')+1],self.media.uuid)
        self.assertIn('--use-photokit',args);self.assertIn('--download-missing',args);self.assertIn('--skip-edited',args)
        stage=Path(args[-1]);target=stage/'CloudClip.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=64x64:rate=10','-t','0.3','-c:v','libx264',str(target)],check=True)
        return 'export complete'
    def fetch(self,**kwargs):return fetch_video(self.media,self.dest,self.lib,self.cancel,runner=self.runner,cli=self.cli,**kwargs)
    def test_download_publishes_valid_video_and_cleans_stage(self):
        result=Path(self.fetch());self.assertEqual(result.parent,self.dest.resolve());self.assertTrue(result.stat().st_size>0)
        self.assertFalse(list(self.dest.glob('.videoqueue-download-*')))
    def test_existing_name_not_overwritten(self):
        one=Path(self.fetch());content=one.read_bytes();two=Path(self.fetch())
        self.assertNotEqual(one,two);self.assertEqual(one.read_bytes(),content)
    def test_cancel_no_output(self):
        self.cancel.set()
        with self.assertRaises(ScanCancelled):self.fetch()
        self.assertFalse(self.dest.exists())
    def test_download_failure_no_partial_publish(self):
        def failed(args,cancel,report):
            (Path(args[-1])/'partial.mp4').write_bytes(b'partial');raise RuntimeError('iCloud unavailable')
        with self.assertRaisesRegex(RuntimeError,'iCloud'):fetch_video(self.media,self.dest,self.lib,self.cancel,runner=failed,cli=self.cli)
        self.assertEqual(list(self.dest.iterdir()),[])
    def test_wrong_dimensions_not_queued(self):
        self.media=replace(self.media,width=128)
        with self.assertRaisesRegex(RuntimeError,'dimensions differ'):self.fetch()
        self.assertEqual(list(self.dest.iterdir()),[])
    def test_local_shortcut_no_cloud_request(self):
        original=self.lib/'local.mp4';original.write_bytes(b'original local bytes')
        local=replace(self.media,path=str(original),local=True)
        result=fetch_video(local,self.dest,self.lib,self.cancel,runner=lambda *a:self.fail('Must not request cloud'),cli=self.cli)
        self.assertEqual(Path(result).read_bytes(),original.read_bytes());self.assertNotEqual(Path(result).stat().st_ino,original.stat().st_ino)
    def test_real_helper_cancelled(self):
        c=threading.Event()
        def report(message):c.set()
        with self.assertRaises(ScanCancelled):wait_process(['python3','-c','import time; time.sleep(15)'],c,report)
    def test_photo_rejected_before_download(self):
        with self.assertRaisesRegex(RuntimeError,'Only videos'):fetch_video(replace(self.media,kind='Photo'),self.dest,self.lib,self.cancel,runner=self.runner,cli=self.cli)

if __name__=='__main__':unittest.main()
