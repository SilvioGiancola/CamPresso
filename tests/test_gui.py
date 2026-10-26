"""GUI and recovery checks using temporary files, without Photos access."""
import queue, tempfile, time, unittest, tkinter as tk
from pathlib import Path
from unittest.mock import patch
from video_queue.photos_catalog import Media

try:
    from video_queue.app import App
except ModuleNotFoundError as error:
    if error.name!='tkinterdnd2':raise
    App=None

@unittest.skipIf(App is None,'Install tkinterdnd2 to run GUI checks')
class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=App(data=self.temp.name)
        self.app.root.geometry('1180x780');self.app.root.update()
        self.app.folder.set(str(Path(self.temp.name)/'Converted'))
        self.app.details=lambda *args:None
    def tearDown(self):
        self.app.destroy()
        while not self.app.closed:self.app.root.update();time.sleep(.01)
        for handler in list(self.app.logger.handlers):
            handler.close();self.app.logger.removeHandler(handler)
        self.temp.cleanup()
    def add_job(self):
        path=Path(self.temp.name)/'video.mp4';path.write_bytes(b'fixture')
        self.app.add_files([str(path)])
        return next(iter(self.app.items))
    def test_minimum_window_keeps_merged_catalog_usable(self):
        self.assertGreaterEqual(self.app.browser.gallery.winfo_width(),1000)
        self.assertGreaterEqual(self.app.browser.gallery.winfo_height(),150)
        self.assertIn('conversion',self.app.browser.tree.cget('columns'))
        self.assertIn('photos',self.app.browser.tree.cget('columns'))
    def prepare_library(self):
        browser=self.app.browser;browser.actual_library=str(Path(self.temp.name)/'Test.photoslibrary')
        browser.thumbnails.image=lambda *args:None
        browser.catalog_rows=[Media(str(index),f'video{index}.mp4','Video',100-index,128,72,'',None,False) for index in range(4)]
        browser.rebuild_catalog();self.app.root.update()
        return browser
    def test_gallery_selection_and_list_switch_preserve_selected_videos(self):
        browser=self.prepare_library();browser.gallery.select(0);browser.gallery.select(2,8)
        self.assertEqual([r.uuid for r in browser.selected()],['0','2'])
        browser.viewmode.set('List');browser.change_view()
        self.assertEqual(browser.tree.selection(),('0','2'))
        browser.viewmode.set('Thumbnails');browser.change_view()
        self.assertEqual(browser.gallery.selection(),('0','2'))
        browser.gallery.select(0);browser.gallery.select(2,1)
        self.assertEqual(browser.gallery.selection(),('0','1','2'))
    def test_preview_opens_player_without_queueing_then_reuses_download(self):
        browser=self.prepare_library();browser.gallery.select(0)
        def fetch(media,dest,*args):
            path=dest/'download.mp4';path.write_bytes(b'fixture');return str(path)
        browser.previews.fetcher=fetch;browser.previews.validator=lambda *args:None
        with patch('video_queue.photos_browser.subprocess.Popen') as player:
            browser.preview_selected();deadline=time.monotonic()+5
            while browser.busy and time.monotonic()<deadline:self.app.root.update();time.sleep(.01)
            self.assertFalse(browser.busy);player.assert_called_once()
            self.assertEqual(player.call_args.args[0][0],'open')
        self.assertEqual(self.app.items,{})
        self.assertEqual(browser.previewbtn.cget('text'),'Play original')
        with patch.object(self.app,'next'):browser.enqueue_selected()
        self.app.set_active(False);job=next(iter(self.app.items.values()))
        self.assertEqual(job['media'].path,player.call_args.args[0][1]);self.assertTrue(job['media'].local)
    def test_gallery_loads_png_on_gui_thread_and_ignores_stale_result(self):
        browser=self.prepare_library();path=Path(self.temp.name)/'poster.png'
        picture=tk.PhotoImage(master=self.app.root,width=176,height=100);picture.put('red',to=(0,0,176,100));picture.write(str(path),format='png')
        browser.gallery.accept_thumbnail(browser.gallery.generation-1,'0',str(path))
        self.assertNotIn('0',browser.gallery.results)
        browser.gallery.accept_thumbnail(browser.gallery.generation,'0',str(path))
        self.assertIn('0',browser.gallery.images)
        self.assertEqual(browser.gallery.images['0'].width(),176)
    def test_failure_records_output_and_recovery_metadata(self):
        iid=self.add_job();output=str(Path(self.temp.name)/'converted.mp4')
        self.app.events.put(('failed',iid,{'output':output,'error':'Native helper crashed','retry_safe':False,'journal':'journal.json','cancelled':False}))
        self.app.poll()
        self.assertEqual(self.app.items[iid]['output'],output)
        self.assertFalse(self.app.items[iid]['retry_safe'])
        self.app.browser.gallery.select(0)
        with patch.object(self.app,'check_photos') as review:self.app.retry()
        review.assert_called_once();self.assertEqual(self.app.items[iid]['state'],'Needs review')
    def test_preflight_failure_can_retry_despite_journal(self):
        iid=self.add_job()
        self.app.items[iid].update(state='Failed',detail='Access denied\nJournal: recovery.json',retry_safe=True)
        self.app.browser.gallery.select(0)
        with patch.object(self.app,'start') as start:self.app.retry()
        start.assert_called_once();self.assertEqual(self.app.items[iid]['state'],'Waiting')
    def test_external_original_is_kept_and_photos_removal_disabled(self):
        iid=self.add_job();path=Path(self.app.items[iid]['path'])
        self.app.browser.gallery.select(0);self.app.update_actions()
        self.assertEqual(self.app.remove_menu.entrycget(0,'state'),'disabled')
        self.assertTrue(path.is_file());self.assertIn(iid,self.app.items)

if __name__=='__main__':unittest.main()
