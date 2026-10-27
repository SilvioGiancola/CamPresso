"""Regression: original Track4/5 become Track2/3 after extra video tracks drop."""
import tempfile, subprocess, unittest
from pathlib import Path
from campresso.quicktime_tracks import tracks, restore_track_dates, metadata_mismatches
from tests.test_engine import TestConverter

def set_dates(path):
    with open(path,'r+b') as f:
        for t in tracks(path):
            for tag,(offset,width,_) in t.dates.items():
                values=(3800000000+t.id*100,3800000050+t.id*100)
                f.seek(offset);f.write(b''.join(v.to_bytes(width,'big') for v in values))

def dates_metadata(path):
    result={'QuickTime:CreateDate':'2026:09:12 12:00:00'}
    for t in tracks(path):
        for header,(_,_,values) in t.dates.items():
            prefix='Track' if header=='tkhd' else 'Media'
            result[f'{t.group}:{prefix}CreateDate']=values[0]
            result[f'{t.group}:{prefix}ModifyDate']=values[1]
    return result

class TrackTests(unittest.TestCase):
    def test_regroup_keeps_retained_dates_and_ignores_omitted(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'DJI-five-tracks.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=64x64:rate=10','-f','lavfi','-i','sine=frequency=440',
                '-map','0:v','-map','0:v','-map','0:v','-map','1:a','-map','1:a','-t','0.5','-c:v','libx264','-crf','0','-c:a','aac',str(source)],check=True)
            set_dates(source);original=source.read_bytes();before=dates_metadata(source)
            converter=TestConverter();converter.metadata=dates_metadata
            settings=dict(replace=False,codec='H.264',size='1080',quality='Balanced',container='MP4',folder='')
            stages=[]
            output,_=converter.convert(source,settings,lambda state,p:stages.append((state,p)))
            after=dates_metadata(output)
            for tag in ('TrackCreateDate','TrackModifyDate','MediaCreateDate','MediaModifyDate'):
                self.assertEqual(before['Track4:'+tag],after['Track2:'+tag])
                self.assertEqual(before['Track5:'+tag],after['Track3:'+tag])
            self.assertEqual(before['Track1:TrackCreateDate'],after['Track1:TrackCreateDate'])
            self.assertEqual(source.read_bytes(),original)
            self.assertEqual(stages[-1],('Completed',100))
            self.assertTrue(all(p<100 for _,p in stages[:-1]))
    def test_capture_metadata_still_required(self):
        before={'QuickTime:CreateDate':'old','Keys:GPSCoordinates':'48 7','Track5:TrackCreateDate':'track'}
        after={'QuickTime:CreateDate':'new','Keys:GPSCoordinates':'48 7','Track2:TrackCreateDate':'track'}
        errors=metadata_mismatches(before,after,{'Track5'},{'Track5':'Track2'})
        self.assertEqual(len(errors),1);self.assertIn('QuickTime:CreateDate',errors[0])
    def test_omitted_track_only_not_arbitrary_missing_fields(self):
        errors=metadata_mismatches({'Track3:MediaCreateDate':'date','Keys:CreationDate':'capture'}, {}, {'Track3'}, {})
        self.assertEqual(len(errors),1);self.assertIn('Keys:CreationDate',errors[0])
    def test_retained_track_mismatch_reports_expected_actual(self):
        errors=metadata_mismatches({'Track4:MediaCreateDate':'old'},{'Track2:MediaCreateDate':'bad'},{'Track4'},{'Track4':'Track2'})
        self.assertIn('expected',errors[0]);self.assertIn('got',errors[0]);self.assertIn('Track4:MediaCreateDate → Track2',errors[0])

if __name__=='__main__':unittest.main()
