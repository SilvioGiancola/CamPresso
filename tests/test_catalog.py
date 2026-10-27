"""Photos data and safe-copy tests, without a user library or macOS runtime."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile, threading, unittest
from campresso.photos_catalog import Media, ScanCancelled, scan_library, media_from_photo, view, copy_video, ensure_outside_library

class CatalogTests(unittest.TestCase):
    def row(self, **changes):
        values=dict(uuid='123',name='clip.MOV',kind='Video',size=100,width=1920,height=1080,date='2026-09-12',path=None,local=False)
        values.update(changes);return Media(**values)
    def test_numeric_size_sort_unknown_last(self):
        rows=[self.row(uuid='unknown',size=None),self.row(uuid='big',size=1000),self.row(uuid='small',size=2)]
        self.assertEqual([r.uuid for r in view(rows)],['big','small','unknown'])
        self.assertEqual([r.uuid for r in view(rows,descending=False)],['small','big','unknown'])
    def test_resolution_pixel_sort(self):
        rows=[self.row(uuid='4k',width=3840,height=2160),self.row(uuid='portrait',width=1080,height=1920),self.row(uuid='small',width=640,height=480)]
        self.assertEqual([r.uuid for r in view(rows,sort='pixels')],['4k','portrait','small'])
    def test_filters_combined_and_case_search(self):
        rows=[self.row(uuid='a',size=300*1024**2,name='Trip.MOV',width=3840,height=2160), self.row(uuid='b',kind='Photo',size=500*1024**2), self.row(uuid='c',size=None)]
        got=view(rows,kind='Videos',search='TRIP',min_mb=200,min_mp=8,min_width=3000,min_height=2000)
        self.assertEqual([r.uuid for r in got],['a'])
        self.assertEqual([r.uuid for r in view(rows,kind='Photos')],['b'])
    def test_unknown_size_not_zero(self):
        p=SimpleNamespace(uuid='live',original_filename='IMG.HEIC',filename='internal.heic',ismovie=False,original_filesize=0,
            original_width=4000,original_height=3000,date=datetime(2026,9,12,tzinfo=timezone.utc),path=None,live_photo=True,hasadjustments=True)
        r=media_from_photo(p)
        self.assertEqual(r.kind,'Photo');self.assertTrue(r.live);self.assertIsNone(r.size);self.assertFalse(r.local)
    def test_scan_readonly_contract(self):
        p=SimpleNamespace(uuid='1',original_filename='x.mov',filename='x.mov',ismovie=True,original_filesize=55,
            original_width=10,original_height=20,date=None,path='/local/x.mov',live_photo=False,hasadjustments=False)
        class DB:
            library_path='/test.photoslibrary'
            def photos(self,**kw):
                self.kw=kw;return [p]
        db=DB();paths=[];progress=[]
        rows,actual=scan_library('/test.photoslibrary',lambda *x:progress.append(x),db_factory=lambda path: paths.append(path) or db)
        self.assertEqual(paths,['/test.photoslibrary']);self.assertEqual(db.kw,dict(images=True,movies=True,intrash=False))
        self.assertEqual(actual,'/test.photoslibrary');self.assertEqual(progress,[(1,1)]);self.assertEqual(len(rows),1)
    def test_video_uses_cached_image_derivative_not_video_derivative(self):
        p=SimpleNamespace(uuid='1',original_filename='x.mov',filename='x.mov',ismovie=True,original_filesize=55,
            original_width=10,original_height=20,date=None,path=None,live_photo=False,hasadjustments=False,
            path_derivatives=['/cached/x.mov','/cached/poster.jpeg'])
        row=media_from_photo(p)
        self.assertEqual(row.thumbnail,'/cached/poster.jpeg');self.assertIsNone(row.path)
    def test_cancel_scan(self):
        cancelled=threading.Event();cancelled.set()
        with self.assertRaises(ScanCancelled):scan_library(cancel=cancelled,db_factory=lambda p:self.fail('Must not open DB'))
    def test_copy_independent_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);lib=root/'Photos.photoslibrary';lib.mkdir();source=lib/'id.mov';source.write_bytes(b'original video')
            dest=root/'export';r=self.row(name='trip.MOV',path=str(source),local=True)
            one=Path(copy_video(r,dest,lib));two=Path(copy_video(r,dest,lib))
            self.assertNotEqual(one,two);self.assertEqual(one.read_bytes(),source.read_bytes());self.assertNotEqual(one.stat().st_ino,source.stat().st_ino)
            one.write_bytes(b'converted');self.assertEqual(source.read_bytes(),b'original video')
    def test_copy_rejects_cloud_and_photos(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);lib=root/'Library.photoslibrary';lib.mkdir()
            with self.assertRaisesRegex(RuntimeError,'not local'):copy_video(self.row(),root/'out',lib)
            with self.assertRaisesRegex(RuntimeError,'Only videos'):copy_video(self.row(kind='Photo'),root/'out',lib)
    def test_destination_protection_and_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);lib=root/'Library.photoslibrary';lib.mkdir();link=root/'link';link.symlink_to(lib,target_is_directory=True)
            for dest in [lib,lib/'sub',link/'sub']:
                with self.assertRaises(RuntimeError):ensure_outside_library(dest,lib)
    def test_cancel_copy_and_filename_sanitization(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);lib=root/'Library.photoslibrary';lib.mkdir();source=lib/'id.mov';source.write_bytes(b'video')
            r=self.row(path=str(source),local=True,name='../../escaped.mov');dest=root/'out'
            copied=Path(copy_video(r,dest,lib));self.assertEqual(copied.parent,dest.resolve());self.assertEqual(source.read_bytes(),b'video')
            c=threading.Event();c.set()
            with self.assertRaises(ScanCancelled):copy_video(r,dest,lib,c)
            self.assertEqual(len(list(dest.iterdir())),1)


class LibraryPathTests(unittest.TestCase):
    def test_pictures_resolves_single_library(self):
        from campresso.photos_catalog import resolve_library
        with tempfile.TemporaryDirectory() as d:
            pictures=Path(d);lib=pictures/'Photos Library.photoslibrary';lib.mkdir()
            self.assertEqual(resolve_library(pictures),str(lib))
            self.assertEqual(resolve_library(lib),str(lib))
    def test_multiple_library_names_are_actionable(self):
        from campresso.photos_catalog import resolve_library
        with tempfile.TemporaryDirectory() as d:
            for name in ('Personal.photoslibrary','Work.photoslibrary'):(Path(d)/name).mkdir()
            with self.assertRaisesRegex(ValueError,'Several Photos libraries'):resolve_library(d)
    def test_not_permission_error_for_wrong_folder(self):
        from campresso.photos_catalog import resolve_library
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'No Photos libraries'):resolve_library(d)

if __name__=='__main__':unittest.main()
