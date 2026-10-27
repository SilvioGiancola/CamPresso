"""Photos Services handoff: exact selected IDs, queued into the existing GUI."""
import json, re, subprocess, sys, threading, time, uuid
from pathlib import Path
from .state_store import StateFile
from .photos_catalog import media_from_photo, resolve_library, ScanCancelled
from .jobs import JobRunner

def selected_ids(values):
    if not isinstance(values,list) or not 1<=len(values)<=500:raise ValueError('Select between 1 and 500 videos in Photos.')
    if any(not isinstance(value,str) for value in values):raise ValueError('Invalid Photos selection identifiers.')
    return list(dict.fromkeys(str(uuid.UUID(value.split('/')[0])).upper() for value in values))

def enqueue(values):
    identifiers=selected_ids(values)
    if not identifiers:raise ValueError('Select at least one video in Photos.')
    app=Path.home()/'Applications'/'CamPresso.app'
    if not app.is_dir():raise RuntimeError('Install CamPresso before using this Service.')
    data=Path.home()/'Library'/'Application Support'/'CamPresso'
    target=data/'Requests'/(uuid.uuid4().hex+'.json')
    # The Service runner may not be allowed to read Photos' preference files.
    # Resolve the library inside CamPresso, with its own access permissions.
    StateFile(target).write(dict(version=1,action='convert1080',library=None,uuids=identifiers))
    result=subprocess.run(['/usr/bin/open',str(app)],capture_output=True,text=True,timeout=15)
    if result.returncode:raise RuntimeError('The request was queued, but CamPresso could not open: '+result.stderr.strip())

def library_candidates(request,cancel):
    """Use remembered and native paths; never require an enlarged Photos item."""
    from osxphotos.utils import get_last_library_path, get_system_library_path
    seen=set()
    def normalize(value):
        if not value:return None
        try:
            result=str(Path(resolve_library(value)).expanduser().resolve())
            if result not in seen:seen.add(result);return result
        except (OSError,ValueError,TypeError):pass
        return None
    for value in (request.get('library'),request.get('preferred_library'),request.get('saved_library')):
        if cancel.is_set():raise ScanCancelled()
        if path:=normalize(value):yield path
    for lookup in (get_last_library_path,get_system_library_path):
        if cancel.is_set():raise ScanCancelled()
        try:value=lookup()
        except Exception:value=None
        if path:=normalize(value):yield path
    try:
        for value in sorted((Path.home()/'Pictures').glob('*.photoslibrary')):
            if cancel.is_set():raise ScanCancelled()
            if path:=normalize(value):yield path
    except OSError:pass

def load_selection(request,cancel):
    import osxphotos
    errors=[]
    for library in library_candidates(request,cancel):
        if cancel.is_set():raise ScanCancelled()
        try:
            database=osxphotos.PhotosDB(library)
            if cancel.is_set():raise ScanCancelled()
            photos=database.photos(uuid=request['uuids'],images=True,movies=True,intrash=False)
            if {str(photo.uuid).upper() for photo in photos}!=set(request['uuids']):continue
            rows={row.uuid.upper():row for row in map(media_from_photo,photos)}
            actual=str(Path(database.library_path).expanduser().resolve())
        except ScanCancelled:raise
        except Exception as error:errors.append(f'{library}: {error}');continue
        videos=[rows[identifier] for identifier in request['uuids'] if rows[identifier].kind=='Video']
        if not videos:raise RuntimeError('Select a video in Photos. Still pictures and Live Photo pictures cannot be converted to a 1080p video.')
        return videos,actual,len(rows)-len(videos)
    detail='\n\n'+ '\n'.join(errors[:2]) if errors else ''
    raise RuntimeError('The selected items could not be read from an accessible Photos library. Choose the correct Library in CamPresso, click Scan, then run the Service again. Allow Full Disk Access if macOS denies access. Selecting a thumbnail in Photos is sufficient; full-screen view is not required.'+detail)

class PhotosService:
    def __init__(self,app):
        self.app=app;self.directory=app.data/'Requests';self.path=None;self.last_check=0;self.ignored=set()
        if self.directory.is_dir():
            for path in self.directory.glob('*.running'):
                if not path.is_symlink():
                    try:path.rename(path.with_suffix('.interrupted'))
                    except OSError:app.logger.exception('Could not retain interrupted Photos request')
    def tick(self):
        app=self.app
        if self.path or app.active or app.browser.busy or app.closing or time.monotonic()-self.last_check<1:return
        self.last_check=time.monotonic()
        if not self.directory.is_dir():return
        paths=sorted((path for path in self.directory.glob('*.json') if path.name not in self.ignored),key=lambda path:path.name)
        if not paths:return
        path=paths[0]
        try:
            if path.is_symlink() or path.stat().st_size>131072 or not re.fullmatch(r'[0-9a-f]{32}',path.stem):raise ValueError('Invalid Photos conversion request.')
            request=json.loads(path.read_text())
            if request.get('version')!=1 or request.get('action')!='convert1080':raise ValueError('Unknown Photos conversion request.')
            request['uuids']=selected_ids(request.get('uuids'))
            if request.get('library') is not None and not isinstance(request.get('library'),str):raise ValueError('Invalid Photos library path.')
            request['preferred_library']=app.browser.library.get().strip() or app.browser.actual_library
            request['saved_library']=app.browser.saved_preferences.get('library')
            self.path=path.with_suffix('.running');path.rename(self.path)
            app.runner=JobRunner(app.data/'Temporary work');runner=app.runner;app.pause_requested=False;app.current=None;app.set_active(True)
            app.root.deiconify();app.root.lift();app.status.set('Reading videos selected in Photos…')
        except Exception as error:
            self.ignored.add(path.name)
            try:
                if path.exists() or path.is_symlink():path.rename(path.with_suffix('.failed'))
            except OSError:app.logger.exception('Could not retain invalid Photos request')
            self.fail(error);return
        def worker():
            try:app.events.put(('service_loaded',None,load_selection(request,runner.cancel)))
            except Exception as error:app.events.put(('service_failed',None,str(error)))
        app.start_worker(worker,'photos-service-selection')
    def finish_request(self):
        if self.path:
            try:self.path.unlink(missing_ok=True)
            except OSError:self.app.logger.exception('Could not remove completed Photos request')
            self.path=None
    def fail(self,error):
        app=self.app;app.logger.error('Photos Service: %s',error);app.status.set('Photos conversion: '+str(error))
        if self.path:
            try:self.path.rename(self.path.with_suffix('.failed'))
            except OSError:app.logger.exception('Could not retain failed Photos request')
            self.path=None
        app.current=None;app.set_active(False);app.refresh(True);app.save_queue()
        if not app.closing:
            from tkinter import messagebox
            messagebox.showerror('Convert video to 1080p',str(error),parent=app.root)
    def handle(self,event):
        app=self.app;kind=event[0]
        if kind=='service_failed':self.fail(event[2]);return
        if kind=='service_loaded':
            if app.closing or app.runner.cancel.is_set():self.finish_request();app.set_active(False);return
            rows,library,skipped=event[2];browser=app.browser
            if browser.actual_library!=library:browser.catalog_rows=[]
            browser.actual_library=library;browser.library.set(library)
            identifiers=[]
            for media in rows:
                identifier,job=app.ensure_job(media)
                if not job.get('retry_safe',True):self.fail('A selected video needs attention. Check its Photos status before compressing.');return
                identifiers.append(identifier)
            browser.save_library();app.refresh(True);self.select(identifiers)
            app.current=None;app.set_active(False)
            # The Service and the main Compress button use exactly the same flow.
            # Requests never authorize replacement or deletion.
            accepted=app.compress_media(rows,library,settings_override={'size':'1080'})
            if accepted or all(app.items[identifier].get('converted') or app.items[identifier].get('original_removed') for identifier in identifiers):
                self.finish_request()
                if not accepted:app.status.set('Existing results are ready to review, or already replaced in Photos.')
            else:self.fail('Compression could not start. Check the selected System Photo Library and video status.')
    def select(self,identifiers):
        app=self.app;browser=app.browser;wanted=set(identifiers)
        browser.kind.set('Videos');browser.search.set('')
        if browser.debounce:browser.win.after_cancel(browser.debounce);browser.debounce=None
        for value in browser.minimum.values():value.set('')
        browser.apply()
        indexes=[index for index,row in enumerate(browser.filtered) if app.find_job(row)[0] in wanted]
        if indexes:browser.page=indexes[0]//browser.PAGE_SIZE;browser.render()
        items=[str(index) for index in indexes if index//browser.PAGE_SIZE==browser.page]
        if browser.viewmode.get()=='List':browser.tree.selection_set(items)
        else:browser.gallery.selection_set(items)

if __name__=='__main__':
    try:enqueue(sys.argv[1:])
    except Exception as error:print(str(error),file=sys.stderr);sys.exit(1)
