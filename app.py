#!/usr/bin/env python3
"""Unified catalog and actions for original and converted video versions."""
import json, logging, queue, re, subprocess, sys, threading, uuid
from dataclasses import asdict, replace
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinterdnd2 import TkinterDnD, DND_FILES
from photos_browser import PhotosBrowser
from photos_catalog import Media
from photos_download import VIDEO_SUFFIXES, fetch_video
from jobs import JobRunner
from engine import Cancelled
from photos_catalog import ScanCancelled
from state_store import StateFile
from storage_cleanup import clean_storage, StorageLock
from photos_service import PhotosService
from photos_navigation import show_in_photos

def library_key(path):return str(Path(path).expanduser().resolve()) if path else ''
def asset_id(value):return str(value or '').split('/')[0].upper()

class App:
    def __init__(self,data=None):
        self.data=Path(data) if data is not None else Path.home()/'Library'/'Application Support'/'Video Queue'
        self.workflow=StateFile(self.data/'workflow.json');self.data.mkdir(parents=True,exist_ok=True)
        self.storage_lock=StorageLock(self.data)
        self.logfile=self.data/'conversion.log';self.logger=logging.getLogger('videoqueue');self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler=logging.FileHandler(self.logfile,encoding='utf-8');handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'));self.logger.addHandler(handler)
        self.items={};self.events=queue.Queue();self.active=False;self.current=None;self.runner=None;self.batch=[];self.pause_requested=False;self.closing=False;self.closed=False;self.cleanup_started=False;self.ui_ready=False
        self.navigating_photos=False;self.navigation_cancel=threading.Event()
        self.root=TkinterDnD.Tk();self.root.title('Video Library');self.root.geometry('1240x820');self.root.minsize(1000,650)
        self.activity=tk.StringVar(value='Downloads: idle · Conversion: idle')
        self.codec=tk.StringVar(value='HEVC');self.size=tk.StringVar(value='1080');self.quality=tk.StringVar(value='Balanced');self.container=tk.StringVar(value='MP4')
        self.folder=tk.StringVar(value=str(Path.home()/'Movies'/'Video Queue'))
        outer=ttk.Frame(self.root,padding=6);outer.pack(fill='both',expand=True)
        self.browser=PhotosBrowser(outer,self.convert_media,embedded=True,cache_dir=self.data,controller=self);self.status=self.browser.status
        self.keep_unsent=tk.BooleanVar(value=self.browser.saved_preferences.get('keep_unsent_conversions',True) is not False)
        def remember_cleanup(*args):
            self.browser.saved_preferences['keep_unsent_conversions']=self.keep_unsent.get();self.browser.save_library()
        self.keep_unsent.trace_add('write',remember_cleanup)
        self.options_open=False
        self.settingsbtn=ttk.Button(self.browser.heading,text='Settings ▸',command=self.toggle_settings);self.settingsbtn.pack(side='right',padx=4)
        ttk.Button(self.browser.heading,text='Add files…',command=self.pick).pack(side='right')
        self.settings_summary=tk.StringVar();ttk.Label(self.browser.heading,textvariable=self.settings_summary).pack(side='left',padx=12)
        for var in (self.codec,self.size,self.quality,self.container):var.trace_add('write',self.update_settings_summary)
        self.update_settings_summary()
        self.options_area=ttk.Frame(self.browser.frame);settings=ttk.Frame(self.options_area);settings.pack(fill='x');self.controls=[]
        for index,(label,var,values) in enumerate([('Codec',self.codec,['HEVC','H.264']),('Resolution',self.size,['720','1080','2160']),('Quality',self.quality,['High','Balanced','Small']),('File',self.container,['MP4','MOV'])]):
            cell=ttk.Frame(settings);cell.grid(row=0,column=index,sticky='ew',padx=(0,8),pady=2);settings.columnconfigure(index,weight=1)
            ttk.Label(cell,text=label).pack(side='left',padx=4)
            box=ttk.Combobox(cell,textvariable=var,values=values,state='readonly',width=9);box.pack(side='left',fill='x',expand=True);self.controls.append(box)
        destination=ttk.Frame(self.options_area);destination.pack(fill='x',pady=2)
        ttk.Button(destination,text='Output folder…',command=self.choose_folder).pack(side='left')
        self.folderentry=ttk.Entry(destination,textvariable=self.folder);self.folderentry.pack(side='left',fill='x',expand=True,padx=4)
        ttk.Checkbutton(self.options_area,text='Keep converted videos not sent to Photos when closing',variable=self.keep_unsent).pack(anchor='w',pady=2)
        self.convertbtn=self.browser.copybtn
        self.viewbtn=ttk.Button(self.browser.controls,text='Play converted',command=self.view_converted);self.viewbtn.pack(side='left',padx=4)
        self.uploadbtn=ttk.Button(self.browser.controls,text='Send to Photos',command=self.send_photos);self.uploadbtn.pack(side='left',padx=4)
        self.removebtn=ttk.Menubutton(self.browser.controls,text='Remove from Photos ▾');self.removebtn.pack(side='left',padx=4)
        self.remove_menu=tk.Menu(self.removebtn,tearoff=False)
        self.remove_menu.add_command(label='Original video…',command=lambda:self.remove_version(False))
        self.remove_menu.add_command(label='Converted video…',command=lambda:self.remove_version(True));self.removebtn.configure(menu=self.remove_menu)
        activity_row=ttk.Frame(self.browser.frame);activity_row.pack(fill='x',before=self.browser.controls,pady=(3,0))
        self.stopbtn=ttk.Button(activity_row,text='Stop conversion',command=self.stop_conversion);self.stopbtn.pack(side='right',padx=2)
        self.pausebtn=ttk.Button(activity_row,text='Pause',command=self.pause);self.pausebtn.pack(side='right',padx=2)
        activity_label=ttk.Label(activity_row,textvariable=self.activity,wraplength=850);activity_label.pack(side='left',fill='x',expand=True)
        activity_row.bind('<Configure>',lambda event:activity_label.configure(wraplength=max(100,event.width-260)))
        menu=self.browser.more_menu;menu.add_separator()
        menu.add_command(label='Show in Photos',command=self.show_photos);self.show_photos_index=menu.index('end')
        menu.add_command(label='Show converted in Photos',command=lambda:self.show_photos(True));self.show_converted_photos_index=menu.index('end')
        for label,fn in [('Video details…',self.details),('Check Photos status',self.check_photos),('Retry / review…',self.retry),('Show original in Finder',lambda:self.reveal(False)),('Show converted in Finder',lambda:self.reveal(True))]:menu.add_command(label=label,command=fn)
        self.browser.tree.configure(displaycolumns=('name','download','conversion','photos','pixels','size'))
        for widget in (self.browser.gallery.canvas,self.browser.tree):
            widget.drop_target_register(DND_FILES);widget.dnd_bind('<<Drop>>',lambda event:self.add_files(self.root.tk.splitlist(event.data)))
        self.load_queue()
        for error in clean_storage(self.data,list(self.items.values()),delete_unsent=not self.keep_unsent.get()):self.logger.warning('Startup cleanup: %s',error)
        self.ui_ready=True;self.add_files(sys.argv[1:]);self.refresh(True)
        self.photos_service=PhotosService(self)
        self.root.protocol('WM_DELETE_WINDOW',self.close);self.poll_after=self.root.after(100,self.poll)
        if sys.platform=='darwin':self.root.createcommand('tk::mac::Quit',self.close)
    def update_settings_summary(self,*args):self.settings_summary.set(f'{self.codec.get()} · {self.size.get()}p · {self.quality.get()} · {self.container.get()}')
    def toggle_settings(self):
        self.options_open=not self.options_open
        if self.options_open:self.options_area.pack(fill='x',after=self.browser.heading)
        else:self.options_area.pack_forget()
        self.settingsbtn.configure(text='Settings ▾' if self.options_open else 'Settings ▸')
    def choose_folder(self):
        if self.active:return
        path=filedialog.askdirectory(parent=self.root)
        if path:self.folder.set(path)
    def settings(self):return dict(codec=self.codec.get(),size=self.size.get(),quality=self.quality.get(),container=self.container.get(),folder=self.folder.get().strip(),replace=False)
    def pick(self):self.add_files(filedialog.askopenfilenames(parent=self.root,filetypes=[('Videos','*.mp4 *.mov *.m4v *.mkv *.avi *.mts *.m2ts *.webm'),('All files','*')]))
    def find_job(self,media):
        for identifier,job in self.items.items():
            if job.get('archived'):continue
            if job.get('media'):
                if asset_id(job['media'].uuid)==asset_id(media.uuid) and library_key(job.get('library'))==library_key(self.browser.actual_library):return identifier,job
            elif media.uuid=='file:'+identifier:return identifier,job
        return None,None
    def is_external(self,media):
        _,job=self.find_job(media);return job is not None and job.get('media') is None
    def selected_job(self):
        selected=self.browser.selected();return self.find_job(selected[0]) if len(selected)==1 else (None,None)
    def source_path(self,job):
        try:
            copy=job.get('backup')
            if copy and Path(copy).is_file():
                stat=Path(copy).stat()
                if [stat.st_size,stat.st_mtime_ns]==job.get('backup_fingerprint'):return copy
            path=job['media'].path if job.get('media') else job.get('path')
            return path if path and Path(path).is_file() else None
        except OSError:return None
    def record_media(self,identifier,job):
        source=self.source_path(job)
        if job.get('media'):return replace(job['media'],path=source,local=bool(source))
        size=job.get('source_size')
        try:
            if source:size=Path(source).stat().st_size
        except OSError:pass
        return Media('file:'+identifier,job['name'],'Video',size,None,None,'',source,bool(source))
    def merge_catalog(self,rows,library):
        relevant=[(identifier,job) for identifier,job in self.items.items() if not job.get('media') or library_key(job.get('library'))==library_key(library)]
        paired={asset_id(value) for _,job in relevant for value in [job.get('new_uuid'),job.get('last_new_uuid'),*job.get('uploaded_ids',[])] if value}
        merged=[row for row in rows if asset_id(row.uuid) not in paired];positions={asset_id(row.uuid):index for index,row in enumerate(merged)}
        for identifier,job in relevant:
            if job.get('media') and asset_id(job['media'].uuid) in positions:
                latest=merged[positions[asset_id(job['media'].uuid)]]
                job['media']=replace(job['media'],favorite=latest.favorite)
            if job.get('archived'):continue
            row=self.record_media(identifier,job);key=asset_id(row.uuid)
            if key not in positions:positions[key]=len(merged);merged.append(row)
            elif job.get('original_removed'):merged[positions[key]]=row
        return merged
    def media_status(self,media):
        _,job=self.find_job(media)
        if not job:return 'Not converted','Original in Photos · Not uploaded'
        if job.get('operation')=='convert':conversion=job['state']+(' '+job.get('progress','') if job.get('progress') else '')
        elif job.get('state') in ('Queued for conversion','Paused'):conversion=job['state']
        elif job.get('converted'):conversion='✓ Converted' if self.has_output(job) else '✓ Converted · in Photos' if job.get('verified') and job.get('local_output_cleared') else 'Converted · local file missing'
        elif job.get('state') in ('Failed','Cancelled','Needs review'):conversion=job['state']
        else:conversion='Not converted'
        if job.get('operation') in ('upload','inspect','delete_original','delete_converted','fetch_converted'):return conversion,job['state']+(' '+job.get('progress','') if job.get('progress') else '')
        if not job.get('retry_safe',True):return conversion,'Photos status needs review'
        original='Original removed' if job.get('original_removed') else 'Original local file' if not job.get('media') else 'Original in Photos'
        copy='Copy removed' if job.get('converted_removed') else 'Copy in Photos' if job.get('verified') else 'Not uploaded'
        return conversion,original+' · '+copy
    def has_output(self,job):
        try:return bool(job and job.get('converted') and job.get('expected') and job.get('output') and Path(job['output']).is_file())
        except OSError:return False
    def can_fetch_converted(self,job):return bool(job and job.get('verified') and job.get('new_uuid') and job.get('expected') and not job.get('converted_removed') and job.get('retry_safe',True))
    def converting(self):return bool(self.active and self.current in self.items and self.items[self.current].get('operation')=='convert')
    def allow_background_download(self):return not self.closing and (not self.active or self.converting())
    def update_activity(self):
        browser=self.browser
        if browser.task=='download':
            key=browser.downloading;name=browser.download_names.get(key,'')
            download=f'Download: {name[:35]} {browser.transfers.get(key,("Preparing…",))[0]}' if key else 'Downloads: finishing'
            if browser.download_queue:download+=f' · {len(browser.download_queue)} queued'
        elif browser.task:download='Library: reading metadata'
        else:download='Downloads: idle'
        if self.converting():
            job=self.items[self.current];conversion=f'Conversion: {job["name"][:35]} {job.get("progress","")}'
        elif self.active:conversion=f'Photos: {self.items.get(self.current,{}).get("state","Preparing selection")}'
        else:conversion='Conversion: paused' if self.batch else 'Conversion: idle'
        if self.batch:conversion+=f' · {len(self.batch)} queued'
        self.activity.set(download+'    |    '+conversion)
    def update_actions(self):
        if not self.ui_ready:return
        selected=self.browser.selected();idle=not self.active and not self.browser.busy and not self.closing
        ready_selected=bool(selected) and all(row.kind=='Video' and self.browser.available_media(row) for row in selected)
        queued=set(self.batch)
        if self.current:queued.add(self.current)
        pending=any(identifier not in queued and not (job or {}).get('converted') and (job or {}).get('retry_safe',True) for identifier,job in map(self.find_job,selected))
        queue_ready=not self.closing and self.browser.task in (None,'download') and (not self.active or self.converting())
        self.convertbtn.configure(text='Add to conversion' if self.converting() else 'Convert selected',state='normal' if queue_ready and ready_selected and pending else 'disabled')
        _,job=self.selected_job();ready=self.has_output(job);review=bool(job and not job.get('retry_safe',True))
        self.viewbtn.configure(text='Play converted',state='normal' if not self.closing and (ready or idle and self.can_fetch_converted(job)) else 'disabled')
        self.uploadbtn.configure(text='Check Photos' if review else 'Send to Photos',state='normal' if idle and job and (review and (job.get('media') or job.get('new_uuid')) or not review and ready and not job.get('verified')) else 'disabled')
        original_ok=False
        if len(selected)==1:
            original_ok=bool(idle and selected[0].kind=='Video' and self.browser.available_media(selected[0]) and not self.is_external(selected[0]) and not (job or {}).get('original_removed') and not review)
        converted_ok=bool(idle and self.can_fetch_converted(job))
        self.remove_menu.entryconfigure(0,state='normal' if original_ok else 'disabled');self.remove_menu.entryconfigure(1,state='normal' if converted_ok else 'disabled')
        self.removebtn.configure(state='normal' if original_ok or converted_ok else 'disabled')
        self.pausebtn.configure(text='Pausing…' if self.converting() and self.pause_requested else 'Pause' if self.converting() else 'Resume',state='normal' if not self.closing and (self.converting() and not self.pause_requested or not self.active and bool(self.batch) and self.browser.task in (None,'download')) else 'disabled')
        self.stopbtn.configure(state='normal' if not self.closing and (self.converting() or not self.active and self.batch) else 'disabled')
        navigation_ready=not self.closing and not self.navigating_photos
        primary=self.photos_target();converted=self.photos_target(True)
        self.browser.more_menu.entryconfigure(self.show_photos_index,state='normal' if navigation_ready and primary else 'disabled')
        self.browser.more_menu.entryconfigure(self.show_converted_photos_index,state='normal' if navigation_ready and converted and converted!=primary else 'disabled')
        self.update_activity()
    def selection_detail(self):
        if not self.ui_ready:return
        selected=self.browser.selected()
        if len(selected)==1:
            conversion,photos=self.media_status(selected[0]);self.status.set(f'{selected[0].name}\n{self.browser.download_status(selected[0])[0]} · {conversion} · {photos}')
        self.update_actions()
    def new_record(self,identifier,**values):
        return dict(id=identifier,run=True,state='Waiting',detail='',progress='',operation=None,output=None,converted=False,expected=None,output_fingerprint=None,backup=None,backup_fingerprint=None,new_uuid=None,verified=False,original_removed=False,converted_removed=False,retry_safe=True,uploaded_ids=[],**values)
    def ensure_job(self,media):
        identifier,job=self.find_job(media)
        if job:
            if job.get('media'):
                job['media']=replace(job['media'],favorite=media.favorite)
                ready=self.browser.available_media(media)
                if ready:job['media']=replace(job['media'],path=ready.path,local=True)
            return identifier,job
        ready=self.browser.available_media(media) or media;identifier=uuid.uuid4().hex
        job=self.new_record(identifier,name=media.name,media=ready,library=self.browser.actual_library,path=None)
        self.items[identifier]=job;return identifier,job
    def add_files(self,paths):
        if self.closing:return
        for path in paths:
            source=Path(path).expanduser().resolve()
            if not source.is_file() or source.suffix.lower() not in VIDEO_SUFFIXES:continue
            if any(not job.get('media') and job.get('path')==str(source) for job in self.items.values()):continue
            identifier=uuid.uuid4().hex;self.items[identifier]=self.new_record(identifier,name=source.name,media=None,library=None,path=str(source),source_size=source.stat().st_size)
        self.save_queue()
        if self.ui_ready:self.refresh(True)
    def convert_media(self,media,library,settings_override=None,start=True):
        if self.closing or self.browser.task not in (None,'download') or self.active and not self.converting():return
        batch=[]
        for row in media:
            if row.kind!='Video' or not self.browser.available_media(row):continue
            identifier,job=self.ensure_job(row)
            if identifier not in [self.current,*self.batch,*batch] and not job.get('converted') and job.get('retry_safe',True):batch.append(identifier)
        if not batch:self.status.set('Select downloaded videos that have not been converted.');return
        settings=dict(self.settings(),**(settings_override or {}))
        if not settings.get('folder','').strip():messagebox.showerror('Output folder','Choose an output folder in Settings.');return
        previous={identifier:dict(self.items[identifier]) for identifier in batch}
        for identifier in batch:self.items[identifier].update(state='Queued for conversion',progress='',queued_settings=dict(settings))
        if not self.save_queue():
            for identifier,job in previous.items():self.items[identifier]=job
            return
        self.batch.extend(batch)
        if self.converting():self.refresh(True);self.status.set(f'{len(batch)} videos added to conversion.');return
        if not start:
            self.pause_requested=True
            for identifier in self.batch:self.items[identifier]['state']='Paused'
            self.refresh(True);self.save_queue();return
        self.snapshot=settings
        try:self.runner=JobRunner(self.data/'Temporary work')
        except Exception as error:messagebox.showerror('Output folder',str(error));self.refresh(True);return
        self.pause_requested=False;self.set_active(True);self.next()
    def start(self):self.browser.enqueue_selected()
    def set_active(self,active):
        self.active=active
        for control in self.controls:control.configure(state='disabled' if active else 'readonly')
        self.folderentry.configure(state='disabled' if active else 'normal')
        for control in (self.browser.choose,self.browser.scanbtn):control.configure(state='disabled' if active or self.browser.busy else 'normal')
        self.browser.update_preview_button();self.update_actions()
    def refresh(self,rebuild=False):
        for job in self.items.values():
            if job.get('media') and job.get('backup') and library_key(job.get('library'))==library_key(self.browser.actual_library):
                self.browser.local_copies[(self.browser.actual_library,job['media'].uuid)]=(job['backup'],job.get('backup_fingerprint'))
        if rebuild:self.browser.rebuild_catalog()
        else:self.browser.refresh_workflow()
        self.update_actions()
    def next(self):
        if not self.batch or self.pause_requested or self.runner.cancel.is_set():
            for identifier in self.batch:self.items[identifier]['state']='Paused'
            self.current=None;self.set_active(False);self.save_queue();self.status.set('Conversion paused.' if self.pause_requested else 'Converted videos are ready to play or send to Photos.');return
        identifier=self.batch.pop(0);job=self.items[identifier];self.current=identifier
        self.snapshot=job.pop('queued_settings',None) or self.snapshot;snapshot=dict(self.snapshot)
        job.update(operation='convert',state='Converting',progress='0%',detail='',conversion_settings=dict(self.snapshot));self.refresh()
        if not self.save_queue():
            job.update(operation=None,state='Paused',queued_settings=snapshot);self.batch.insert(0,identifier);self.pause_requested=True;self.current=None;self.set_active(False);self.refresh();return
        work=dict(job,**{'import':False,'remove':False,'replace':False,'require_download':bool(job.get('media'))})
        if work.get('media'):work['media']=replace(work['media'],path=self.source_path(work),local=True)
        runner=self.runner
        def report(stage,percent=None,detail=''):
            if stage=='Downloading':
                stage='Preparing original';match=re.search(r'(\d+)%',detail);percent=float(match.group(1)) if match else None
            self.events.put(('progress',identifier,stage,percent,detail))
        def worker():
            try:
                output,detail=runner.run(work,snapshot,report)
                self.events.put(('converted',identifier,dict(output=output,detail=detail,**{key:work.get(key) for key in ('backup','backup_fingerprint','expected','output_fingerprint')})))
            except Exception as error:self.failure_event(identifier,error,work)
        threading.Thread(target=worker,daemon=True).start()
    def failure_event(self,identifier,error,work):
        self.logger.exception('Video action failed: %s',work['name'])
        self.events.put(('failed',identifier,dict(error=str(error),output=getattr(error,'output',None) or work.get('output'),journal=getattr(error,'journal',None) or work.get('journal'),retry_safe=getattr(error,'retry_safe',True),cancelled=isinstance(error,Cancelled),**{key:work.get(key) for key in ('backup','backup_fingerprint','expected','output_fingerprint')})))
    def view_converted(self):
        identifier,job=self.selected_job()
        if self.has_output(job):
            try:subprocess.Popen(['open',job['output']])
            except OSError as error:self.status.set(f'Could not play converted video: {error}')
        elif self.can_fetch_converted(job):self.fetch_converted(identifier)
    def fetch_converted(self,identifier,remove=False):
        if self.active or self.browser.busy or self.closing:return
        job=self.items[identifier]
        if not self.can_fetch_converted(job):return
        self.runner=JobRunner(self.data/'Temporary work');runner=self.runner;self.current=identifier
        job.update(operation='fetch_converted',state='Downloading converted',progress='…');self.set_active(True);self.refresh();self.save_queue()
        expected=job['expected'];media=Media(job['new_uuid'],job.get('converted_name') or Path(job.get('output') or job['name']).name,'Video',None,expected['width'],expected['height'],'',None,False)
        library=job.get('library');destination=self.data/'Converted previews'/identifier
        def report(detail):
            match=re.search(r'(\d+)%',detail)
            self.events.put(('progress',identifier,'Downloading converted',float(match.group(1)) if match else None,detail))
        def worker():
            try:
                selected_library=library
                if not selected_library:
                    from osxphotos.utils import get_system_library_path
                    selected_library=get_system_library_path()
                    if not selected_library:raise RuntimeError('The System Photo Library could not be found.')
                output=fetch_video(media,destination,selected_library,runner.cancel,report);stat=Path(output).stat()
                self.events.put(('converted_downloaded',identifier,output,[stat.st_size,stat.st_mtime_ns],remove))
            except ScanCancelled:self.failure_event(identifier,Cancelled(),dict(job))
            except Exception as error:self.failure_event(identifier,error,dict(job))
        threading.Thread(target=worker,daemon=True).start()
    def send_photos(self):
        identifier,job=self.selected_job()
        if not job or self.active or self.browser.busy or self.closing:return
        if not job.get('retry_safe',True):self.photo_action(identifier,'inspect');return
        if not self.has_output(job) or job.get('verified'):return
        self.photo_action(identifier,'upload')
    def check_photos(self):
        identifier,job=self.selected_job()
        if not job:
            selected=self.browser.selected()
            if len(selected)!=1 or self.is_external(selected[0]):return
            identifier,job=self.ensure_job(selected[0])
        if not self.active and not self.browser.busy and not self.closing:self.photo_action(identifier,'inspect')
    def remove_version(self,converted):
        if self.active or self.browser.busy or self.closing:return
        selected=self.browser.selected()
        if len(selected)!=1 or selected[0].kind!='Video':return
        identifier,job=self.ensure_job(selected[0])
        if not job.get('retry_safe',True):self.status.set('Check Photos status before another removal.');return
        if converted:
            if not self.can_fetch_converted(job):return
        elif not job.get('media') or job.get('original_removed') or not self.browser.available_media(selected[0]):return
        version='converted' if converted else 'original'
        other='The original is already removed from Photos.' if converted and job.get('original_removed') else 'The converted version is not in Photos.' if not converted and not job.get('verified') else 'The other version in Photos will remain.'
        retention='A local recovery copy will be kept.' if not converted or self.keep_unsent.get() else 'A local copy will be kept until you close the app; your Settings clear unsent conversions on close.'
        text=f'{job["name"]}\n\nMove the {version} video to Recently Deleted in Apple Photos?\n{other}\n\n{retention}\nThis deletion also affects synced devices.'
        if messagebox.askokcancel(f'Remove {version} from Photos?',text,parent=self.root):
            if converted and not self.has_output(job):self.fetch_converted(identifier,remove=True)
            else:self.photo_action(identifier,'delete_converted' if converted else 'delete_original')
    def photo_action(self,identifier,operation):
        job=self.items[identifier]
        try:self.runner=JobRunner(self.data/'Photos work')
        except Exception as error:messagebox.showerror('Photos',str(error));return
        prior=dict(job)
        if operation!='inspect':job['review_operation']=operation
        job.update(operation=operation,state={'upload':'Sending to Photos','inspect':'Checking Photos','delete_original':'Removing original','delete_converted':'Removing converted'}[operation],progress='…',journal=str(self.runner.workspace/'journals'/(uuid.uuid4().hex+'.json')),retry_safe=False)
        self.current=identifier;self.set_active(True);self.refresh()
        if not self.save_queue():self.items[identifier]=prior;self.set_active(False);self.refresh();return
        work=dict(job);runner=self.runner
        if operation=='delete_original' and work.get('media'):
            selected=self.browser.selected()
            ready=self.browser.available_media(selected[0]) if len(selected)==1 else None
            if ready:work['media']=ready
        def worker():
            try:
                if operation=='upload':result=runner.publish(work)
                elif operation=='inspect':result=runner.actions(dict(action='inspect',library=work.get('library'),uuid=work['media'].uuid if work.get('media') else None,new_uuid=work.get('new_uuid'),expected=work.get('expected'),output=work.get('output'),backup=work.get('backup'),journal=work['journal']))
                else:result=runner.delete_version(work,converted=operation=='delete_converted')
                self.events.put(('photo_done',identifier,operation,result,{key:work.get(key) for key in ('backup','backup_fingerprint')}))
            except Exception as error:self.failure_event(identifier,error,work)
        threading.Thread(target=worker,daemon=True).start()
    def poll(self):
        if self.closed:return
        self.root.after_cancel(self.poll_after)
        try:
            while True:
                event=self.events.get_nowait();kind,identifier=event[:2]
                if kind=='photos_navigation':
                    self.navigating_photos=False;self.update_actions()
                    if not self.closing:
                        self.status.set(event[2] or 'Showing the selected item in Photos.')
                        if event[2]:messagebox.showerror('Show in Photos',event[2],parent=self.root)
                    continue
                if kind.startswith('service_'):
                    self.photos_service.handle(event);continue
                if identifier not in self.items:continue
                job=self.items[identifier];operation=job.get('operation')
                if kind=='progress':
                    job.update(state=event[2],progress=f'{event[3]:.0f}%' if event[3] is not None else '…')
                    if event[4]:job['detail']=event[4]
                    self.status.set(f'{job["name"]}: {event[4] or event[2]}')
                    if operation=='service_download':
                        key=(self.browser.actual_library,job['media'].uuid)
                        self.browser.transfers[key]=('↓ Preparing '+job['progress'],None);self.browser.refresh_downloads(key)
                elif kind=='converted':
                    job.update(event[2]);job.update(converted=True,state='Converted',progress='100%',operation=None,retry_safe=True,converted_name=Path(job['output']).name,local_output_cleared=False)
                elif kind=='converted_downloaded':
                    job.update(output=event[2],output_fingerprint=event[3],converted=True,local_output_cleared=False,operation=None,state='In Photos',progress='')
                    self.current=None;self.set_active(False)
                    if not self.closing:
                        if event[4]:self.photo_action(identifier,'delete_converted')
                        else:
                            try:subprocess.Popen(['open',event[2]])
                            except OSError as error:self.status.set(f'Could not play converted video: {error}')
                elif kind=='photo_done':
                    result=event[3];job.update(event[4])
                    if operation=='upload':
                        if job.get('new_uuid'):job['last_new_uuid']=job['new_uuid']
                        job.update(new_uuid=result['new_uuid'],verified=True,converted_removed=False)
                        job.setdefault('uploaded_ids',[]).append(result['new_uuid'])
                    elif operation=='delete_original':job['original_removed']=True
                    elif operation=='delete_converted':job.update(converted_removed=True,verified=False)
                    elif operation=='inspect':
                        if job.get('media'):job['original_removed']=not result['original_present']
                        if result['converted_present']:job.update(verified=True,converted_removed=False)
                        elif job.get('new_uuid'):job.update(last_new_uuid=job['new_uuid'],new_uuid=None,verified=False,converted_removed=job.get('verified',False) or job.get('converted_removed',False))
                    uncertain=operation=='inspect' and job.get('review_operation')=='upload' and not result.get('new_uuid')
                    job.update(operation=None,retry_safe=not uncertain,state='Needs review' if uncertain else 'Original removed' if job.get('original_removed') else 'In Photos' if job.get('verified') else 'Converted' if job.get('converted') else 'Downloaded',progress='')
                    if not uncertain:job.pop('review_operation',None)
                    job['detail']=f'Photos: {result.get("phase","Status checked")}\nJournal: {job["journal"]}'
                    if uncertain:job['detail']+='\nThe interrupted upload has no recorded asset ID. Inspect Photos before adding this file again.'
                    self.status.set(job['detail']);self.current=None;self.set_active(False)
                elif kind=='failed':
                    metadata=event[2];cancelled=metadata.pop('cancelled');error=metadata.pop('error')
                    if operation=='inspect':metadata['retry_safe']=False
                    job.update(metadata);job.update(operation=None,state='Needs review' if not job.get('retry_safe',True) else 'Cancelled' if cancelled else 'Failed',detail=error,progress='')
                    if job.get('output') and job.get('expected'):job['converted']=True
                    self.recover_receipt(job);self.pause_requested=True
                    if operation!='convert':self.current=None;self.set_active(False)
                    self.status.set(f'{job["name"]}: {error}')
                    if not self.closing:self.details(identifier)
                self.refresh(rebuild=kind!='progress')
                if kind!='progress':self.save_queue()
                if operation=='convert' and kind in ('converted','failed'):self.next()
        except queue.Empty:pass
        self.photos_service.tick()
        self.poll_after=self.root.after(100,self.poll)
    def pause(self):
        if self.converting():self.pause_requested=True;self.update_actions();self.status.set('Will pause conversion after the current video. Downloads continue.');return
        if self.active or not self.batch or self.closing or self.browser.task not in (None,'download'):return
        try:self.runner=JobRunner(self.data/'Temporary work')
        except Exception as error:messagebox.showerror('Conversion',str(error));return
        self.pause_requested=False;self.set_active(True);self.next()
    def clear_conversion_queue(self):
        for identifier in self.batch:self.items[identifier].update(state='Waiting',queued_settings=None)
        self.batch=[]
    def stop_conversion(self):
        if self.closing:return
        self.pause_requested=True;self.clear_conversion_queue();self.photos_service.auto_convert=False
        if self.converting():self.runner.stop()
        self.refresh();self.save_queue();self.status.set('Stopping conversion. Downloads continue.')
    def stop(self):
        self.pause_requested=True;self.clear_conversion_queue();self.browser.cancel.set();self.photos_service.auto_convert=False;self.navigation_cancel.set()
        if self.active:self.runner.stop();self.status.set('Stopping. A Photos change already in progress will finish safely.')
    def save_queue(self):
        rows=[]
        for identifier,job in self.items.items():
            row=dict(job,id=identifier);row['media']=asdict(job['media']) if job.get('media') else None;rows.append(row)
        try:self.workflow.write({'version':2,'jobs':rows});return True
        except (OSError,ValueError,TypeError) as error:self.logger.exception('Could not save workflow');self.status.set(f'Could not save workflow: {error}');return False
    def load_queue(self):
        rows=self.workflow.read().get('jobs',[])
        if not isinstance(rows,list):return
        for row in rows:
            try:
                if not isinstance(row,dict) or not isinstance(row.get('name'),str):continue
                job=dict(row);job['media']=Media(**row['media']) if row.get('media') else None;identifier=job.get('id') or uuid.uuid4().hex;job['id']=identifier
                for key,value in [('converted_removed',False),('original_removed',False),('new_uuid',None),('verified',False),('converted',False),('uploaded_ids',[])]:job.setdefault(key,value)
                if job.get('state') in ('Queued for conversion','Paused'):job.update(state='Waiting',queued_settings=None)
                if job.get('operation'):
                    if job['operation'] not in ('convert','inspect','fetch_converted','service_download'):job['review_operation']=job['operation']
                    job.update(state='Cancelled' if job['operation'] in ('convert','fetch_converted','service_download') else 'Needs review',retry_safe=job['operation'] in ('convert','fetch_converted','service_download'),operation=None,progress='')
                if not job.get('retry_safe',True) and not job.get('new_uuid') and not job.get('review_operation') and row.get('operation')!='inspect':job['review_operation']='upload'
                self.recover_receipt(job);self.items[identifier]=job
            except (KeyError,TypeError,ValueError):self.logger.exception('Could not restore workflow row')
    def recover_receipt(self,job):
        if not job.get('journal'):return
        try:
            target=Path(job['journal']).resolve()
            if target.parent!=(self.data/'Photos work'/'journals').resolve():return
            receipt=json.loads(target.read_text());media=job.get('media')
            if not isinstance(receipt,dict) or receipt.get('library')!=job.get('library'):return
            expected_uuid=None if receipt.get('action')=='finish' and job.get('original_removed') else media.uuid if media else None
            if receipt.get('uuid')!=expected_uuid or receipt.get('output')!=job.get('output'):return
            phase=receipt.get('phase')
            if receipt.get('action')=='delete_original' and receipt.get('backup'):job.update(backup=receipt['backup'],backup_fingerprint=receipt.get('backup_fingerprint'))
            if receipt.get('new_uuid'):job['new_uuid']=receipt['new_uuid']
            if phase=='Original moved to Recently Deleted':
                if receipt.get('backup'):job.update(backup=receipt['backup'],backup_fingerprint=receipt.get('backup_fingerprint'))
                job.update(original_removed=True,retry_safe=True,state='Original removed',operation=None)
                if receipt.get('action')=='finish':job['verified']=True
            elif phase=='Converted moved to Recently Deleted':job.update(converted_removed=True,verified=False,retry_safe=True,state='Converted',operation=None)
            elif phase=='Import verified' and not receipt.get('remove') and receipt.get('expected')==job.get('expected'):job.update(verified=True,converted_removed=False,retry_safe=True,state='In Photos',operation=None)
            elif phase=='Photos inspected' and all(isinstance(receipt.get(key),bool) for key in ('original_present','converted_present')):
                if media:job['original_removed']=not receipt['original_present']
                if receipt['converted_present'] and receipt.get('expected')==job.get('expected'):job.update(verified=True,converted_removed=False)
                elif job.get('new_uuid'):job.update(last_new_uuid=job['new_uuid'],new_uuid=None,verified=False,converted_removed=job.get('verified',False) or job.get('converted_removed',False))
                if job.get('review_operation')!='upload' or receipt.get('new_uuid'):job.update(retry_safe=True,state='Original removed' if job.get('original_removed') else 'In Photos' if job.get('verified') else 'Converted' if job.get('converted') else 'Downloaded',operation=None)
            if job.get('retry_safe',True):job.pop('review_operation',None)
        except (OSError,ValueError,TypeError):pass
    def retry(self):
        if self.active or self.browser.busy:return
        identifier,job=self.selected_job()
        if not job:return
        if not job.get('retry_safe',True):self.status.set('Checking the interrupted Photos action.');self.check_photos();return
        if not job.get('converted'):job.update(state='Waiting',progress='');self.save_queue();self.refresh();self.start()
        else:self.status.set('The converted file is retained. Choose Send to Photos or Check Photos status.')
    def details(self,identifier=None):
        if identifier is None:identifier,job=self.selected_job()
        else:job=self.items.get(identifier)
        if not job:
            selected=self.browser.selected()
            if len(selected)==1:messagebox.showinfo('Video details',selected[0].detail(),parent=self.root)
            return
        media=self.record_media(identifier,job);conversion,photos=self.media_status(media)
        converted_path=job.get('output') or ('Local copy cleared on close; download from Photos to play' if job.get('local_output_cleared') and job.get('verified') else 'Not converted')
        body=f'{job["name"]}\n{conversion}\n{photos}\n\n{job.get("detail","")}\n\nOriginal: {self.source_path(job) or "Not downloaded"}\nConverted: {converted_path}\nPhotos original ID: {job["media"].uuid if job.get("media") else "External file"}\nPhotos converted ID: {job.get("new_uuid") or job.get("last_new_uuid") or "Not uploaded"}\nJournal: {job.get("journal") or "Not recorded"}\nLog: {self.logfile}'
        if job.get('cleanup_retained'):body+='\nA local recovery copy was kept because a version was removed from Photos or a Photos action needs review.'
        for previous in job.get('previous_conversions',[]):
            old=self.items.get(previous,{})
            body+=f'\nPrevious conversion: {old.get("output") or "Local copy cleared"} · Photos ID: {old.get("new_uuid") or old.get("last_new_uuid") or "Not uploaded"}'
        window=tk.Toplevel(self.root);window.title('Video details');window.geometry('780x460')
        text=tk.Text(window,wrap='word');text.pack(fill='both',expand=True,padx=10,pady=10);text.insert('1.0',body);text.configure(state='disabled')
        def copy():self.root.clipboard_clear();self.root.clipboard_append(body)
        ttk.Button(window,text='Copy details',command=copy).pack(side='left',padx=10,pady=6);ttk.Button(window,text='Close',command=window.destroy).pack(side='right',padx=10)
    def reveal(self,converted=False):
        _,job=self.selected_job()
        if job:path=job.get('output') if converted else self.source_path(job)
        else:
            selected=self.browser.selected();path=selected[0].path if len(selected)==1 and not converted else None
        if path:subprocess.Popen(['open','-R',path])
    def photos_target(self,converted=False):
        selected=self.browser.selected()
        if len(selected)!=1:return None
        media=selected[0];_,job=self.find_job(media)
        if not converted and not self.is_external(media) and not (job or {}).get('original_removed'):return media.uuid
        if job and job.get('new_uuid') and not job.get('converted_removed'):return job['new_uuid']
        return None
    def show_photos(self,converted=False):
        if self.closing or self.navigating_photos:return
        identifier=self.photos_target(converted)
        if not identifier:return
        self.navigating_photos=True;self.navigation_cancel=threading.Event();cancel=self.navigation_cancel
        self.update_actions();self.status.set('Opening the selected item in Photos…')
        def worker():
            error=None
            try:show_in_photos(identifier,cancel)
            except Exception as exception:
                self.logger.exception('Could not show Photos asset %s',identifier);error=str(exception)
            self.events.put(('photos_navigation',None,error))
        threading.Thread(target=worker,daemon=True).start()
    def close(self):
        if self.closing or self.closed:return
        if self.active or self.browser.busy or self.navigating_photos:
            if not messagebox.askyesno('Work running','Stop and close after the current operation has stopped safely?'):return
            self.closing=True;self.browser.closing=True;self.stop();self.wait_close()
        else:self.destroy()
    def wait_close(self):
        if self.active or self.browser.busy or self.navigating_photos:self.root.after(150,self.wait_close)
        else:self.destroy()
    def destroy(self):
        if self.closed or self.cleanup_started:return
        if self.active or self.browser.busy or self.navigating_photos:
            self.closing=True;self.browser.closing=True;self.stop();self.wait_close();return
        self.cleanup_started=True;self.closing=True;self.browser.closing=True;self.set_active(False);self.delete_unsent_on_close=not self.keep_unsent.get()
        self.status.set('Clearing temporary local storage…');self.save_queue();self.root.after_cancel(self.poll_after);self.browser.dispose()
        self.cleanup_events=queue.Queue()
        def worker():
            try:
                self.browser.gallery.dispose(wait=True)
                errors=clean_storage(self.data,list(self.items.values()),delete_unsent=getattr(self,'delete_unsent_on_close',False))
            except Exception as error:
                self.logger.exception('Local storage cleanup failed');errors=[str(error)]
            self.cleanup_events.put(errors)
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.finish_cleanup)
    def finish_cleanup(self):
        try:errors=self.cleanup_events.get_nowait()
        except queue.Empty:self.root.after(100,self.finish_cleanup);return
        self.save_queue()
        if errors:
            self.logger.error('Local storage cleanup incomplete: %s','; '.join(errors))
            messagebox.showerror('Some temporary files could not be cleared','\n'.join(errors[:6])+'\n\nSee conversion.log for details.',parent=self.root)
        self.closed=True;self.storage_lock.close();self.root.destroy()

if __name__=='__main__':
    try:App().root.mainloop()
    except RuntimeError as error:messagebox.showerror('Video Queue',str(error))
