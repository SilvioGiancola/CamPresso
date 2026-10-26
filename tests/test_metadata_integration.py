"""Run real FFmpeg/ExifTool metadata preservation on disposable fixtures."""
import shutil, subprocess, tempfile, unittest
from pathlib import Path
from video_queue.engine import Converter

@unittest.skipUnless(all(shutil.which(tool) for tool in ('ffmpeg','ffprobe','exiftool')),'FFmpeg and ExifTool required')
class MetadataIntegrationTests(unittest.TestCase):
    def test_rotation_is_applied_without_double_rotating_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();source=root/'rotated.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=128x72:rate=10','-t','1','-c:v','libx264','-crf','0',str(source)],check=True)
            subprocess.run(['exiftool','-overwrite_original','-Rotation=90',str(source)],check=True,capture_output=True)
            converter=Converter();original=source.read_bytes()
            self.assertTrue(any(abs(side.get('rotation',0))==90 for side in converter.probe(source)['streams'][0].get('side_data_list',[])))
            settings=dict(replace=False,codec='H.264',size='720',quality='Balanced',container='MOV',folder=str(root/'output'))
            output,_=converter.convert(source,settings,lambda *args:None)
            video=next(s for s in converter.probe(output)['streams'] if s['codec_type']=='video')
            self.assertEqual((video['width'],video['height']),(72,128))
            self.assertTrue(all(side.get('rotation',0)==0 for side in video.get('side_data_list',[])))
            self.assertEqual(source.read_bytes(),original)
    def test_real_metadata_artwork_and_multiple_tracks(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();source=root/'original.mp4';cover=root/'art.jpg'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=128x72:rate=10','-f','lavfi','-i','sine=frequency=440',
                '-map','0:v','-map','0:v','-map','1:a','-t','1','-c:v','libx264','-crf','0','-c:a','aac',str(source)],check=True)
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=32x32','-frames:v','1',str(cover)],check=True)
            subprocess.run(['exiftool','-overwrite_original','-QuickTime:CreateDate=2026:02:12 12:31:20',
                '-Keys:CreationDate=2026:02:12 12:31:20+03:00','-Keys:GPSCoordinates=+48.0000+007.0000+0.000',
                '-Keys:Title=Metadata regression','-CoverArt<='+str(cover),str(source)],check=True,capture_output=True)
            original=source.read_bytes()
            for codec in ('H.264','HEVC'):
                with self.subTest(codec=codec):
                    converter=Converter();before=converter.metadata(source)
                    settings=dict(replace=False,codec=codec,size='720',quality='Balanced',container='MP4',folder=str(root/'output'))
                    output,_=converter.convert(source,settings,lambda *args:None)
                    after=converter.metadata(output)
                    for key in ('QuickTime:CreateDate','Keys:CreationDate','Keys:GPSCoordinates','Keys:Title'):
                        self.assertIn(key,before);self.assertEqual(before[key],after[key])
                    streams=converter.probe(output)['streams']
                    self.assertEqual(len([s for s in streams if s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic')]),1)
                    self.assertEqual(len([s for s in streams if s.get('disposition',{}).get('attached_pic')]),1)
                    self.assertEqual(source.read_bytes(),original)

if __name__=='__main__':unittest.main()
