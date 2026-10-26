"""Preview/cache checks use disposable files, never a user's Photos library."""
import json, shutil, subprocess, tempfile, threading, unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from video_queue.photos_catalog import Media, ScanCancelled, copy_video
from video_queue.photos_previews import PreviewStore, ThumbnailStore, THUMBNAIL_SIZE

class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.library=self.root/'Test.photoslibrary';self.library.mkdir()
        self.source=self.library/'original.mp4';self.source.write_bytes(b'video fixture')
        self.media=Media('asset','trip.mp4','Video',13,128,72,'',str(self.source),True)
        self.cancel=threading.Event();self.validator=Mock()
        self.fetcher=Mock(side_effect=lambda m,d,l,c,r:copy_video(m,d,l,c))
        self.store=PreviewStore(self.root/'Previews',self.fetcher,self.validator)
    def tearDown(self):self.temp.cleanup()
    def test_independent_copy_and_reuse_after_restart(self):
        path=Path(self.store.prepare(self.media,self.library,self.cancel))
        self.assertEqual(path.read_bytes(),self.source.read_bytes())
        self.assertNotEqual(path.stat().st_ino,self.source.stat().st_ino)
        restarted=PreviewStore(self.store.root,self.fetcher,self.validator)
        self.assertEqual(restarted.prepare(self.media,self.library,self.cancel),str(path))
        self.fetcher.assert_called_once()
        queued=restarted.media_for_queue(self.media,self.library)
        self.assertEqual(queued.path,str(path));self.assertEqual(queued.uuid,self.media.uuid)
        self.assertEqual(self.media.path,str(self.source))
    def test_cloud_download_and_queue_reuse(self):
        cloud=replace(self.media,path=None,local=False)
        def fetch(media,dest,library,cancel,report):
            path=dest/'download.mp4';path.write_bytes(b'downloaded video');return str(path)
        self.store.fetcher=Mock(side_effect=fetch)
        self.assertEqual(self.store.media_for_queue(cloud,self.library),cloud)
        path=self.store.prepare(cloud,self.library,self.cancel)
        self.assertEqual(self.store.media_for_queue(cloud,self.library).path,path)
        self.assertFalse(cloud.local)
    def test_cancel_before_download(self):
        self.cancel.set()
        with self.assertRaises(ScanCancelled):self.store.prepare(self.media,self.library,self.cancel)
        self.fetcher.assert_not_called()
    def test_cancel_after_download_removes_unpublished_copy(self):
        self.validator.side_effect=lambda *args:self.cancel.set()
        with self.assertRaises(ScanCancelled):self.store.prepare(self.media,self.library,self.cancel)
        self.assertEqual(list(self.store.entry(self.media,self.library).iterdir()),[])
        self.assertTrue(self.source.is_file())
    def test_validation_failure_does_not_publish_preview(self):
        self.validator.side_effect=RuntimeError('Invalid video')
        with self.assertRaisesRegex(RuntimeError,'Invalid video'):self.store.prepare(self.media,self.library,self.cancel)
        self.assertIsNone(self.store.cached_path(self.media,self.library))
        self.assertEqual(list(self.store.entry(self.media,self.library).iterdir()),[])
    def test_manifest_traversal_is_not_reused(self):
        entry=self.store.entry(self.media,self.library);entry.mkdir(parents=True)
        (entry/'preview.json').write_text(json.dumps({'filename':'../../original.mp4','size':13}))
        self.assertIsNone(self.store.cached_path(self.media,self.library))
    def test_escaped_download_is_not_removed(self):
        self.store.fetcher=lambda *args:str(self.source)
        with self.assertRaisesRegex(RuntimeError,'outside'):self.store.prepare(self.media,self.library,self.cancel)
        self.assertTrue(self.source.is_file());self.validator.assert_not_called()
    def test_symlink_download_is_rejected(self):
        def fetch(media,dest,*args):
            path=dest/'link.mp4';path.symlink_to(self.source);return path
        self.store.fetcher=fetch
        with self.assertRaisesRegex(RuntimeError,'outside'):self.store.prepare(self.media,self.library,self.cancel)
        self.assertTrue(self.source.is_file())
    def test_preview_cache_rejects_photos_destination(self):
        with self.assertRaises(RuntimeError):PreviewStore(self.library/'Previews')
    def test_unavailable_cloud_thumbnail_never_downloads(self):
        thumbs=ThumbnailStore(self.root/'Thumbnails',self.store)
        self.assertIsNone(thumbs.image(replace(self.media,path=None,local=False),self.library,self.cancel))
        self.fetcher.assert_not_called()
    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_real_video_and_cached_poster_make_png_thumbnails(self):
        video=self.root/'video.mp4';poster=self.root/'poster.jpg'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=128x72','-t','0.2','-c:v','libx264',str(video)],check=True)
        subprocess.run(['ffmpeg','-v','error','-i',str(video),'-frames:v','1',str(poster)],check=True)
        thumbs=ThumbnailStore(self.root/'Thumbnails',self.store)
        for media in (replace(self.media,path=str(video)),replace(self.media,path=None,local=False,thumbnail=str(poster))):
            path=Path(thumbs.image(media,self.library,self.cancel))
            data=path.read_bytes();self.assertEqual(data[:8],b'\x89PNG\r\n\x1a\n')
            self.assertEqual((int.from_bytes(data[16:20],'big'),int.from_bytes(data[20:24],'big')),THUMBNAIL_SIZE)
            self.assertEqual(thumbs.image(media,self.library,self.cancel),str(path))
        self.fetcher.assert_not_called()

if __name__=='__main__':unittest.main()
