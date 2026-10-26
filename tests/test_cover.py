"""Regression for ExifTool-restored artwork presenting as an extra video stream."""
import subprocess, tempfile, unittest
from pathlib import Path
from tests.test_engine import TestConverter

class CoverTests(unittest.TestCase):
    def test_cover_art_not_a_retained_movie_track(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'video.mp4';cover=root/'cover.jpg'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=128x72:rate=10','-f','lavfi','-i','sine=frequency=440','-t','0.5','-c:v','libx264','-c:a','aac',str(source)],check=True)
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=32x32','-frames:v','1','-threads','1',str(cover)],check=True)
            converter=TestConverter();originalrun=converter.run
            def run(args,*a,**kw):
                if args[0]=='/mock/exiftool' and '-All:All' in args:
                    target=Path(args[-1]);replacement=root/'with-art.mp4'
                    subprocess.run(['ffmpeg','-v','error','-i',str(target),'-i',str(cover),'-map','0','-map','1:v','-c','copy','-disposition:v:1','attached_pic',str(replacement)],check=True)
                    replacement.replace(target)
                    return ''
                return originalrun(args,*a,**kw)
            converter.run=run
            settings=dict(replace=False,codec='H.264',size='1080',quality='Balanced',container='MP4',folder='')
            out,_=converter.convert(source,settings,lambda *a:None)
            streams=converter.probe(out)['streams']
            self.assertEqual(len([s for s in streams if s['codec_type']=='video']),2)
            self.assertEqual(len([s for s in streams if s.get('disposition',{}).get('attached_pic')]),1)
            self.assertTrue(source.exists())

if __name__=='__main__':unittest.main()
