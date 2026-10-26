"""Real FFmpeg integration checks; ExifTool calls simulated where unavailable."""
import hashlib, shutil, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest.mock import patch
from video_queue.engine import Converter, Cancelled

class TestConverter(Converter):
    __test__=False
    def __init__(self):
        realwhich = shutil.which
        with patch('video_queue.engine.shutil.which', side_effect=lambda t: '/mock/exiftool' if t=='exiftool' else realwhich(t)):
            super().__init__()
        self.fail_metadata=False
    def run(self,args,*a,**kw):
        if args[0]=='/mock/exiftool':
            if self.fail_metadata:raise RuntimeError('Metadata copy failed')
            return ''
        return super().run(args,*a,**kw)
    def metadata(self,path):return {'QuickTime:CreateDate':'2026:09:12 12:00:00'}

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.source=self.root/'a video $ name.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=240x320:rate=10','-f','lavfi','-i','sine=frequency=440','-t','1','-c:v','libx264','-crf','0','-c:a','aac',str(self.source)],check=True)
        self.original=self.source.read_bytes()
        self.c=TestConverter()
        self.settings=dict(replace=False,codec='H.264',size='1080',quality='Balanced',container='MP4',folder='')
    def tearDown(self):self.tmp.cleanup()
    def convert(self):return self.c.convert(self.source,self.settings,lambda *a:None)
    def test_new_and_collision_portrait(self):
        one,_=self.convert();two,_=self.convert()
        self.assertNotEqual(one,two);self.assertEqual(self.source.read_bytes(),self.original)
        v=self.c.probe(one)['streams'][0];self.assertEqual((v['width'],v['height']),(240,320));self.assertEqual(v['codec_name'],'h264')
    def test_hevc(self):
        self.settings['codec']='HEVC';out,_=self.convert()
        v=self.c.probe(out)['streams'][0];self.assertEqual(v['codec_name'],'hevc');self.assertEqual(v['codec_tag_string'],'hvc1')
    def test_replacement_backup(self):
        self.settings['replace']=True;out,_=self.convert()
        self.assertEqual(out,str(self.source));backups=list((self.root/'Video Queue Originals').iterdir())
        self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),self.original);self.assertLess(self.source.stat().st_size,len(self.original))
    def test_failure_keeps_original(self):
        self.settings['replace']=True;self.c.fail_metadata=True
        with self.assertRaisesRegex(RuntimeError,'Metadata copy'):self.convert()
        self.assertEqual(self.source.read_bytes(),self.original);self.assertFalse(list(self.root.glob('.videoqueue-*')))
    def test_cancel_keeps_original(self):
        self.c.cancel.set()
        with self.assertRaises(Cancelled):self.convert()
        self.assertEqual(self.source.read_bytes(),self.original)
    def test_metadata_mismatch_keeps_original(self):
        self.c.metadata=lambda path:{'QuickTime:CreateDate':'old' if Path(path)==self.source else 'new'}
        with self.assertRaisesRegex(RuntimeError,'Metadata verification'):self.convert()
        self.assertEqual(self.source.read_bytes(),self.original)
    def test_live_cancellation(self):
        def report(state,p):
            if state=='Converting' and p>0:self.c.stop()
        with self.assertRaises(Cancelled):self.c.convert(self.source,self.settings,report)
        self.assertEqual(self.source.read_bytes(),self.original)
        self.assertFalse(list(self.root.glob('.videoqueue-*')))
    def test_hdr_refused(self):
        realprobe=self.c.probe
        def probe(path):
            data=realprobe(path);data['streams'][0]['color_transfer']='smpte2084';return data
        self.c.probe=probe
        with self.assertRaisesRegex(RuntimeError,'HDR'):self.convert()
        self.assertEqual(self.source.read_bytes(),self.original)
    def test_photos_refused(self):
        lib=self.root/'x.photoslibrary';lib.mkdir();f=lib/'x.mp4';f.write_bytes(self.original)
        with self.assertRaisesRegex(RuntimeError,'Photos'):self.c.convert(f,self.settings,lambda *a:None)
    def test_parent_alias_cannot_bypass_photos_protection(self):
        lib=self.root/'Library.photoslibrary';lib.mkdir();(lib/'video.mp4').write_bytes(self.original)
        alias=self.root/'alias';alias.symlink_to(lib,target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError,'Photos'):self.c.convert(alias/'video.mp4',self.settings,lambda *a:None)
    def test_output_alias_cannot_write_inside_photos(self):
        lib=self.root/'Library.photoslibrary';lib.mkdir();alias=self.root/'alias';alias.symlink_to(lib,target_is_directory=True)
        self.settings['folder']=str(alias)
        with self.assertRaisesRegex(RuntimeError,'outside'):self.convert()
        self.assertEqual(list(lib.iterdir()),[])
    def test_invalid_settings_do_not_leave_temporary_files(self):
        self.settings['size']='not a resolution'
        with self.assertRaises(RuntimeError):self.convert()
        self.assertFalse(list(self.root.glob('.videoqueue-*')))
    def test_nonfinite_duration_is_rejected(self):
        realprobe=self.c.probe
        def probe(path):
            data=realprobe(path);data['format']['duration']='nan';return data
        self.c.probe=probe
        with self.assertRaisesRegex(RuntimeError,'duration'):self.convert()
        self.assertFalse(list(self.root.glob('.videoqueue-*')))
    def test_source_changed(self):
        originalrun=self.c.run
        def run(args,*a,**kw):
            result=originalrun(args,*a,**kw)
            if '-f' in args and 'null' in args:self.source.write_bytes(self.original+b'changed')
            return result
        self.c.run=run;self.settings['replace']=True
        with self.assertRaisesRegex(RuntimeError,'Original changed'):self.convert()
        self.assertTrue(self.source.read_bytes().endswith(b'changed'))

class ProcessTests(unittest.TestCase):
    def test_callback_failure_reaps_child(self):
        converter=TestConverter()
        def failed(progress):raise ValueError('callback failed')
        with self.assertRaisesRegex(ValueError,'callback failed'):
            converter.run([sys.executable,'-u','-c',"import time; print('out_time_us=1000'); time.sleep(15)"],failed,1)
        self.assertIsNone(converter.proc)
    def test_cancellation_kills_child_that_ignores_terminate(self):
        converter=TestConverter();started=time.monotonic()
        script="import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('out_time_us=1000',flush=True); time.sleep(15)"
        with self.assertRaises(Cancelled):converter.run([sys.executable,'-u','-c',script],lambda p:converter.stop(),1)
        self.assertLess(time.monotonic()-started,6)
        self.assertIsNone(converter.proc)

if __name__=='__main__':unittest.main()
