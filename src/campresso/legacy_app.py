#!/usr/bin/env python3
"""Unified catalog and actions for original and converted video versions."""
import argparse, json, logging, queue, re, subprocess, sys, threading, uuid, webbrowser
from dataclasses import asdict, replace
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinterdnd2 import TkinterDnD
from .photos_browser import PhotosBrowser
from .photos_catalog import Media, resolve_library
from .photos_download import VIDEO_SUFFIXES, fetch_video
from .jobs import JobRunner
from .engine import Cancelled, Skipped
from .photos_catalog import ScanCancelled
from .state_store import StateFile
from .storage_cleanup import clean_storage, StorageLock
from .photos_service import PhotosService
from .photos_navigation import show_in_photos
from .workflow_logic import file_matches, human_size, savings, reviewable, can_compress, status as workflow_status, require_system_library

def library_key(path):return str(Path(path).expanduser().resolve()) if path else ''
def asset_id(value):return str(value or '').split('/')[0].upper()

class App:
    SETTING_CHOICES={'codec':('Video codec','HEVC',('HEVC','H.264')),'size':('Resolution (p)','1080',('720','1080','2160')),'quality':('Quality','Balanced',('High','Balanced','Small')),'container':('File format','MP4',('MP4','MOV'))}
    def __init__(self,data=None,files=()):
        self.data=Path(data) if data is not None else Path.home()/'Library'/'Application Support'/'CamPresso'
        self.workflow=StateFile(self.data/'workflow.json');self.data.mkdir(parents=True,exist_ok=True)
        self.storage_lock=StorageLock(self.data)
        from .native_store import read_strict
        if read_strict(self.workflow.path).get("version", 3) >= 4:
            self.storage_lock.close()
            raise RuntimeError("This data was migrated to the native app. Use CamPresso 4 or later.")
        self.logfile=self.data/'conversion.log';self.logger=logging.getLogger('campresso');self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler=logging.FileHandler(self.logfile,encoding='utf-8');handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'));self.logger.addHandler(handler)
        self.items={};self.events=queue.Queue();self.active=False;self.current=None;self.runner=None;self.batch=[];self.pause_requested=False;self.closing=False;self.closed=False;self.cleanup_started=False;self.ui_ready=False
        self.navigating_photos=False;self.navigation_cancel=threading.Event()
        self.replacements=[];self.compression_generation=0
        self.workers=set();self.close_prompt_open=False
        self.root=TkinterDnD.Tk();self.root.title('Video Library');self.root.geometry('1240x820');self.root.minsize(1000,650)
        self.activity=tk.StringVar(value='Downloads: idle · Conversion: idle')
        self.codec=tk.StringVar(value='HEVC');self.size=tk.StringVar(value='1080');self.quality=tk.StringVar(value='Balanced');self.container=tk.StringVar(value='MP4')
        outer=ttk.Frame(self.root,padding=6);outer.pack(fill='both',expand=True)
        self.browser=PhotosBrowser(outer,self.compress_media,embedded=True,cache_dir=self.data,controller=self);self.status=self.browser.status
        self.settings_window=None
        saved=self.browser.saved_preferences.get('compression',{})
        if not isinstance(saved,dict):saved={}
        for key,(_,default,choices) in self.SETTING_CHOICES.items():getattr(self,key).set(saved.get(key) if saved.get(key) in choices else default)
        self.settingsbtn=ttk.Button(self.browser.heading,text='Settings…',command=self.show_settings);self.settingsbtn.pack(side='right',padx=4)
        self.convertbtn=self.browser.copybtn
        self.viewbtn=ttk.Button(self.browser.controls,text='Play compressed',command=self.view_converted);self.viewbtn.pack(side='left',padx=4)
        self.uploadbtn=ttk.Button(self.browser.controls,text='Replace in Photos…',command=self.replace_selected);self.uploadbtn.pack(side='right',padx=4)
        activity_row=ttk.Frame(self.browser.frame);activity_row.pack(fill='x',before=self.browser.controls,pady=(3,0))
        self.stopbtn=ttk.Button(activity_row,text='Stop compression',command=self.stop_conversion);self.stopbtn.pack(side='right',padx=2)
        self.pausebtn=ttk.Button(activity_row,text='Pause',command=self.pause);self.pausebtn.pack(side='right',padx=2)
        activity_label=ttk.Label(activity_row,textvariable=self.activity,wraplength=850);activity_label.pack(side='left',fill='x',expand=True)
        activity_row.bind('<Configure>',lambda event:activity_label.configure(wraplength=max(100,event.width-260)))
        menu=self.browser.more_menu;menu.add_separator()
        menu.add_command(label='Show in Photos',command=self.show_photos);self.show_photos_index=menu.index('end')
        menu.add_command(label='Show converted in Photos',command=lambda:self.show_photos(True));self.show_converted_photos_index=menu.index('end')
        for label,fn in [('Video details…',self.details),('Check Photos status',self.check_photos),('Retry / review…',self.retry),('Show original in Finder',lambda:self.reveal(False)),('Show converted in Finder',lambda:self.reveal(True))]:menu.add_command(label=label,command=fn)
        self.browser.tree.configure(displaycolumns=('name','conversion','photos','duration','size','pixels'))
        self.browser.tree.heading('conversion',text='Status');self.browser.tree.heading('photos',text='Library size reduction')
        self.browser.gallery.status=None
        self.load_queue()
        for error in clean_storage(self.data,list(self.items.values())):self.logger.warning('Startup cleanup: %s',error)
        self.ui_ready=True;self.add_files(files);self.refresh(True)
        self.photos_service=PhotosService(self)
        self.root.protocol('WM_DELETE_WINDOW',self.close);self.poll_after=self.root.after(100,self.poll)
        self.configure_menu()
        self.root.after_idle(lambda:self.browser.scan(automatic=True))
    def configure_menu(self):
        """Replace Tk's demonstration menus and route native macOS actions."""
        self.root.tk.call('tk','appname','CamPresso')
        self.menubar=tk.Menu(self.root,tearoff=False)
        mac=sys.platform=='darwin';modifier='Command' if mac else 'Control'
        if mac:
            application=tk.Menu(self.menubar,name='apple',tearoff=False)
            application.add_command(label='About CamPresso',command=self.show_about)
            self.menubar.add_cascade(label='CamPresso',menu=application)
            self.root.createcommand('tkAboutDialog',self.show_about)
            self.root.createcommand('tk::mac::ShowHelp',self.show_help)
            self.root.createcommand('tk::mac::ShowPreferences',self.show_settings)
            self.root.createcommand('tk::mac::Quit',self.close)
        file_menu=tk.Menu(self.menubar,tearoff=False)
        file_menu.add_command(label='Close Window',accelerator=modifier+'+W',command=self.close_window)
        if not mac:
            file_menu.add_command(label='Settings…',command=self.show_settings)
            file_menu.add_command(label='Quit CamPresso',command=self.close)
        self.menubar.add_cascade(label='File',menu=file_menu)
        edit_menu=tk.Menu(self.menubar,tearoff=False)
        for label,key in [('Undo','Z'),('Redo','Shift+Z'),('Cut','X'),('Copy','C'),('Paste','V'),('Select All','A')]:
            edit_menu.add_command(label=label,accelerator=modifier+'+'+key,command=lambda action=label.replace(' ',''):self.edit_action(action))
        self.menubar.add_cascade(label='Edit',menu=edit_menu)
        if mac:
            window_menu=tk.Menu(self.menubar,name='window',tearoff=False)
            self.menubar.add_cascade(label='Window',menu=window_menu)
        help_menu=tk.Menu(self.menubar,name='help',tearoff=False)
        help_menu.add_command(label='CamPresso User Guide',command=self.show_help)
        help_menu.add_command(label='Permissions help…',command=self.browser.permissions_help)
        if not mac:help_menu.add_command(label='About CamPresso',command=self.show_about)
        self.menubar.add_cascade(label='Help',menu=help_menu)
        # macOS recognizes .apple/.window/.help only when the menu is attached.
        self.root.configure(menu=self.menubar)
        self.root.bind_all('<'+modifier+'-w>',self.close_window)
    def edit_action(self,action):
        widget=self.root.focus_get()
        if widget is not None:widget.event_generate('<<'+action+'>>')
    def close_window(self,event=None):
        widget=self.root.focus_get();window=widget.winfo_toplevel() if widget is not None else self.root
        if window==self.root:self.close()
        else:window.destroy()
        return 'break'
    def show_settings(self):
        if self.closing:return
        self.root.deiconify()
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.deiconify();self.settings_window.lift();self.settings_window.focus_set();return
        window=tk.Toplevel(self.root);self.settings_window=window;window.title('CamPresso — Settings');window.transient(self.root);window.resizable(True,False)
        window.protocol('WM_DELETE_WINDOW',self.close_settings)
        content=ttk.Frame(window,padding=16);content.pack(fill='both',expand=True)
        library=ttk.LabelFrame(content,text='Photos library',padding=10);library.pack(fill='x')
        self.settings_library=tk.StringVar(value=self.browser.library.get())
        self.settings_library_entry=ttk.Entry(library,textvariable=self.settings_library,width=62);self.settings_library_entry.pack(side='left',fill='x',expand=True)
        self.settings_choose=ttk.Button(library,text='Choose…',command=self.choose_settings_library);self.settings_choose.pack(side='left',padx=(8,0))
        ttk.Label(content,text='The saved library loads automatically when CamPresso opens.\nCompression and replacement require the System Photo Library.',wraplength=560).pack(anchor='w',pady=(6,12))
        compression=ttk.LabelFrame(content,text='Conversion settings',padding=10);compression.pack(fill='x')
        self.settings_drafts={}
        for index,(key,(label,_,choices)) in enumerate(self.SETTING_CHOICES.items()):
            var=tk.StringVar(value=getattr(self,key).get());self.settings_drafts[key]=var
            ttk.Label(compression,text=label).grid(row=index,column=0,sticky='w',padx=(0,16),pady=3)
            ttk.Combobox(compression,textvariable=var,values=choices,state='readonly',width=16).grid(row=index,column=1,sticky='ew',pady=3)
        compression.columnconfigure(1,weight=1)
        ttk.Label(content,text='Compress uses these settings. Save to remember them across launches.\nVideos already queued keep their existing settings.\nTemporary videos are cleared on close. HDR videos are skipped.',wraplength=560).pack(anchor='w',pady=(8,4))
        self.settings_notice=tk.StringVar();ttk.Label(content,textvariable=self.settings_notice,wraplength=560).pack(anchor='w',pady=4)
        actions=ttk.Frame(content);actions.pack(fill='x',pady=(8,0))
        self.settings_save=ttk.Button(actions,text='Save',command=self.save_settings);self.settings_save.pack(side='right')
        ttk.Button(actions,text='Cancel',command=self.close_settings).pack(side='right',padx=8)
        window.bind('<Escape>',lambda event:self.close_settings());self.update_settings_state();window.focus_set()
    def close_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():self.settings_window.destroy()
        self.settings_window=None
    def library_change_blocked(self):
        return bool(self.closing or self.active or self.browser.busy or self.navigating_photos or self.batch or self.replacements or self.browser.download_queue or any(job.get('compression_pending') for job in self.items.values()))
    def update_settings_state(self):
        blocked=self.library_change_blocked()
        self.browser.scanbtn.configure(state='disabled' if blocked else 'normal')
        if self.settings_window is None or not self.settings_window.winfo_exists():return
        for control in (self.settings_library_entry,self.settings_choose):control.configure(state='disabled' if blocked else 'normal')
        self.settings_save.configure(state='disabled' if self.closing else 'normal')
        self.settings_notice.set('Finish or stop queued work before changing the library.' if blocked else '')
    def choose_settings_library(self):
        if self.library_change_blocked():return
        current=Path(self.settings_library.get()).expanduser()
        initial=current.parent if current.parent.is_dir() else Path.home()/'Pictures'
        path=filedialog.askdirectory(parent=self.settings_window,title='Select your .photoslibrary bundle or the folder containing it',initialdir=initial)
        if path:self.settings_library.set(path)
    def save_settings(self):
        if self.closing:return
        # An idle window may have become busy while its chooser was open.
        changed=self.settings_library.get().strip()!=self.browser.library.get().strip()
        if changed and self.library_change_blocked():
            self.settings_notice.set('Finish or stop queued work before changing the library.');return
        try:
            path=self.browser.library.get().strip()
            if changed:
                if not self.settings_library.get().strip():raise ValueError('Choose a Photos library before saving.')
                path=library_key(resolve_library(self.settings_library.get().strip()))
            compression={key:var.get() for key,var in self.settings_drafts.items()}
            if any(value not in self.SETTING_CHOICES[key][2] for key,value in compression.items()):raise ValueError('Choose a listed compression setting.')
            preferences={**self.browser.saved_preferences,'library':path,'compression':compression}
            self.browser.preferences.write(preferences)
        except (OSError,ValueError) as error:
            messagebox.showerror('Could not save settings',str(error),parent=self.settings_window);return
        self.browser.saved_preferences=preferences
        for key,value in compression.items():getattr(self,key).set(value)
        switch=changed and library_key(path)!=library_key(self.browser.actual_library)
        self.browser.library.set(path);self.close_settings()
        if switch:
            self.browser.catalog_rows=[];self.browser.catalog_loaded=False;self.browser.actual_library=path;self.browser.page=0;self.refresh(True)
        if not self.library_change_blocked() and (changed or not self.browser.catalog_loaded):self.browser.scan()
    def show_about(self):
        messagebox.showinfo('About CamPresso','CamPresso\n\nCompress → Review → Replace\n\nReview smaller videos before replacing their originals in Apple Photos.',parent=self.root)
    def show_help(self):
        url='https://github.com/SilvioGiancola/CamPresso/blob/main/docs/USER_GUIDE.md'
        try:
            if not webbrowser.open(url):raise OSError('No browser could be opened.')
        except (OSError,webbrowser.Error) as error:
            messagebox.showerror('CamPresso Help',f'Could not open the user guide: {error}\n\n{url}',parent=self.root)
    def settings(self):return dict(codec=self.codec.get(),size=self.size.get(),quality=self.quality.get(),container=self.container.get(),folder=str(self.data/'Workflow'/'Converted'),replace=False,require_smaller=True)
    def find_job(self,media):
        for identifier,job in self.items.items():
            if job.get('archived'):continue
            if job.get('media'):
                if asset_id(media.uuid) in {asset_id(job['media'].uuid),asset_id(job.get('new_uuid'))} and library_key(job.get('library'))==library_key(self.browser.actual_library):return identifier,job
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
            if copy and path and Path(path)==Path(copy):return None
            return path if path and Path(path).is_file() else None
        except OSError:return None
    def record_media(self,identifier,job):
        source=None if job.get('replacement_complete') else self.source_path(job)
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
                job['media']=replace(job['media'],favorite=latest.favorite,duration=latest.duration)
            elif job.get('replacement_complete'):
                latest=next((row for row in rows if asset_id(row.uuid)==asset_id(job.get('new_uuid'))),None)
                if latest:job['media']=replace(job['media'],favorite=latest.favorite,thumbnail=latest.thumbnail)
            if job.get('archived'):continue
            row=self.record_media(identifier,job);key=asset_id(row.uuid)
            if key not in positions:positions[key]=len(merged);merged.append(row)
            elif job.get('original_removed'):merged[positions[key]]=row
        return merged
    def media_status(self,media):
        _,job=self.find_job(media)
        return workflow_status(job,self.browser.transfers.get(self.browser.transfer_key(media))),savings(job)
    def has_output(self,job):
        try:return bool(job and job.get('converted') and job.get('expected') and file_matches(job.get('output'),job.get('output_fingerprint')))
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
        if not self.ui_ready or self.closing:return
        self.update_settings_state()
        selected=self.browser.selected();idle=not self.active and not self.browser.busy and not self.closing
        ready_selected=bool(selected) and all(row.kind=='Video' and not self.is_external(row) for row in selected)
        queued=set(self.batch)
        if self.current:queued.add(self.current)
        pending=any(identifier not in queued and not (job or {}).get('compression_pending') and can_compress(job) for identifier,job in map(self.find_job,selected))
        queue_ready=not self.closing and self.browser.task in (None,'download') and (not self.active or self.converting())
        self.convertbtn.configure(text='Compress',state='normal' if queue_ready and ready_selected and pending else 'disabled')
        _,job=self.selected_job();ready=self.has_output(job);review=bool(job and not job.get('retry_safe',True))
        self.viewbtn.configure(text='Play' if (job or {}).get('replacement_complete') else 'Play compressed',state='normal' if not self.closing and (ready or idle and self.can_fetch_converted(job)) else 'disabled')
        replace_ready=bool(selected) and all(reviewable(candidate) for _,candidate in map(self.find_job,selected))
        self.uploadbtn.configure(text='Check Photos' if review else 'Replace in Photos…',state='normal' if idle and (review or replace_ready) else 'disabled')
        if len(selected)==1 and (job or {}).get('original_removed'):self.browser.previewbtn.configure(state='disabled')
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
            conversion,reduction=self.media_status(selected[0]);_,job=self.find_job(selected[0])
            text=f'{selected[0].name}\n{conversion}'
            if reduction:text+=f' · Library size reduction: {reduction}'
            if job and conversion in ('Skipped','Error','Needs attention'):text+=' · '+job.get('detail','')[:160]
            self.status.set(text)
        self.update_actions()
    def new_record(self,identifier,**values):
        return dict(id=identifier,run=True,state='Waiting',detail='',progress='',operation=None,output=None,converted=False,expected=None,output_fingerprint=None,backup=None,backup_fingerprint=None,new_uuid=None,verified=False,original_removed=False,converted_removed=False,retry_safe=True,uploaded_ids=[],**values)
    def ensure_job(self,media):
        identifier,job=self.find_job(media)
        if job:
            if job.get('media'):
                job['media']=replace(job['media'],favorite=media.favorite)
                ready=self.browser.available_media(media)
                if ready and not job.get('original_removed') and asset_id(media.uuid)==asset_id(job['media'].uuid):job['media']=replace(job['media'],path=ready.path,local=True)
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
    def compress_media(self,media,library,settings_override=None):
        """Prepare each original, then feed the independent conversion queue."""
        if self.closing or not self.browser.can_download():return False
        try:require_system_library(library)
        except Exception as error:
            messagebox.showerror('System Photo Library required',str(error),parent=self.root);return False
        if not self.batch and not self.active:self.pause_requested=False
        targets={};settings=dict(self.settings(),**(settings_override or {}));generation=self.compression_generation
        for row in media:
            if row.kind!='Video' or self.is_external(row):continue
            identifier,job=self.ensure_job(row)
            if not can_compress(job) or job.get('compression_pending') or identifier in [self.current,*self.batch]:continue
            if job.get('converted'):
                if job.get('output') and Path(job['output']).exists():job.setdefault('retained_outputs',[]).append(job['output'])
                job.update(converted=False,output=None,output_fingerprint=None,expected=None,result_size=None)
            job.update(workflow_managed=True,compression_pending=True,state='Queued',detail='',queued_settings=dict(settings),replacement_approval=None)
            targets[row.uuid]=identifier
        if not targets:self.status.set('Select videos that still need compression. Existing results are ready to review.');return False
        if not self.save_queue():
            for identifier in targets.values():self.items[identifier].update(compression_pending=False,state='Error',detail='Could not save workflow.')
            self.refresh(True);return False
        def ready(row,path):
            identifier=targets[row.uuid];job=self.items[identifier];stat=Path(path).stat()
            job.update(media=replace(job['media'],path=path,local=True),backup=path,backup_fingerprint=[stat.st_size,stat.st_mtime_ns],source_size=stat.st_size,compression_pending=False)
            if generation!=self.compression_generation or self.closing:
                job['state']='Cancelled'
            else:self.convert_media([job['media']],library,settings_override=settings,start=not self.pause_requested)
            self.refresh();self.save_queue()
        def failed(key,error):
            job=self.items[targets[key[1]]]
            job.update(compression_pending=False,state='Cancelled' if isinstance(error,ScanCancelled) else 'Error',detail=str(error) or 'Download cancelled.')
            self.refresh();self.save_queue()
        rows=[self.browser.available_media(row) or row for row in media if row.uuid in targets]
        if not self.browser.queue_downloads(rows,library,on_ready=ready,on_error=failed):
            for identifier in targets.values():self.items[identifier].update(compression_pending=False,state='Cancelled')
            self.refresh(True);self.save_queue();return False
        self.refresh(True);return True
    def convert_media(self,media,library,settings_override=None,start=True):
        if self.closing or self.browser.task not in (None,'download') or self.active and not self.converting():return
        batch=[]
        for row in media:
            if row.kind!='Video' or not self.browser.available_media(row):continue
            identifier,job=self.ensure_job(row)
            if identifier not in [self.current,*self.batch,*batch] and not job.get('converted') and job.get('retry_safe',True):batch.append(identifier)
        if not batch:return
        settings=dict(self.settings(),**(settings_override or {}))
        if not settings.get('folder','').strip():messagebox.showerror('Output folder','Choose an output folder in Settings.');return
        previous={identifier:dict(self.items[identifier]) for identifier in batch}
        for identifier in batch:
            job=self.items[identifier]
            options=dict(settings,folder=str(self.data/'Workflow'/'Converted'/identifier),require_smaller=True)
            job.update(state='Queued for conversion',progress='',queued_settings=options)
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
    def start_worker(self,target,name):
        """Keep worker lifetime separate from UI state and completion events."""
        self.workers={thread for thread in self.workers if thread.is_alive()}
        thread=threading.Thread(target=target,name=name,daemon=True)
        self.workers.add(thread);thread.start()
    def set_active(self,active):
        self.active=active
        if self.closing:return
        if self.browser.choose is not None:self.browser.choose.configure(state='disabled' if self.library_change_blocked() else 'normal')
        self.browser.update_preview_button();self.update_actions()
    def refresh(self,rebuild=False):
        if self.closing:return
        for job in self.items.values():
            if job.get('media') and job.get('backup') and library_key(job.get('library'))==library_key(self.browser.actual_library):
                self.browser.local_copies[(self.browser.actual_library,job['media'].uuid)]=(job['backup'],job.get('backup_fingerprint'))
        if rebuild:self.browser.rebuild_catalog()
        else:self.browser.refresh_workflow()
        self.update_actions()
    def next(self):
        if not self.batch or self.pause_requested or self.runner.cancel.is_set():
            for identifier in self.batch:self.items[identifier]['state']='Paused'
            self.current=None;self.set_active(False);self.save_queue();self.status.set('Compression paused.' if self.pause_requested else 'Compressed videos are ready to review.');return
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
                self.events.put(('converted',identifier,dict(output=output,detail=detail,**{key:work.get(key) for key in ('backup','backup_fingerprint','expected','output_fingerprint','source_size','result_size')})))
            except Exception as error:self.failure_event(identifier,error,work)
        self.start_worker(worker,'video-conversion')
    def failure_event(self,identifier,error,work):
        self.logger.exception('Video action failed: %s',work['name'])
        self.events.put(('failed',identifier,dict(error=str(error),output=getattr(error,'output',None) or work.get('output'),journal=getattr(error,'journal',None) or work.get('journal'),retry_safe=getattr(error,'retry_safe',work.get('operation')!='replace'),cancelled=isinstance(error,Cancelled),skipped=isinstance(error,Skipped),**{key:work.get(key) for key in ('backup','backup_fingerprint','expected','output_fingerprint','source_size','result_size')})))
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
        if job.get('output') and Path(job['output']).exists() and not file_matches(job['output'],job.get('output_fingerprint')):
            job.setdefault('retained_outputs',[]).append(job['output'])
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
        self.start_worker(worker,'converted-download')
    def send_photos(self):
        self.replace_selected()
    def replace_selected(self):
        if self.active or self.browser.busy or self.closing:return
        _,single=self.selected_job()
        if single and not single.get('retry_safe',True):self.check_photos();return
        candidates=[(identifier,job) for identifier,job in map(self.find_job,self.browser.selected()) if reviewable(job)]
        if not candidates or len(candidates)!=len(self.browser.selected()):
            self.refresh();self.status.set('Select unchanged compressed results with a measured size reduction. Changed or missing files must be compressed and reviewed again.');return
        try:
            for _,job in candidates:require_system_library(job.get('library'))
        except Exception as error:messagebox.showerror('Photos',str(error),parent=self.root);return
        reduction=sum(job['source_size']-job['result_size'] for _,job in candidates)
        text=(f'Replace {len(candidates)} video(s) in Photos?\n\n'
              f'Library size reduction: {human_size(reduction)}. This is not immediately reclaimed disk space.\n\n'
              'The smaller copies will be added and checked before the originals move to Recently Deleted. '
              'Deletion also affects your devices using iCloud Photos. Originals normally remain recoverable for 30 days; permanent deletion is manual.\n\n'
              'Dates, location, favorites, hidden status and writable regular albums are preserved. '
              'Faces, edit history and some Photos metadata may not transfer.\n\n'
              'Temporary local copies will be cleared when this app closes.')
        if not messagebox.askokcancel('Replace reviewed videos',text,parent=self.root):return
        for identifier,job in candidates:
            job['replacement_approval']=dict(output=job['output'],fingerprint=list(job['output_fingerprint']),expected=dict(job['expected']),original_uuid=job['media'].uuid,new_uuid=job.get('new_uuid'))
            job['state']='Queued for replacement'
        if not self.save_queue():
            for _,job in candidates:job.update(replacement_approval=None,state='Ready to review')
            return
        self.replacements=[identifier for identifier,_ in candidates];self.next_replacement()
    def next_replacement(self):
        if self.closing or not self.replacements:return
        identifier=self.replacements.pop(0);job=self.items[identifier]
        if not reviewable(job):
            job.update(state='Error',detail='The reviewed result is no longer available. Review it again.',replacement_approval=None)
            self.refresh();self.save_queue();self.next_replacement();return
        self.photo_action(identifier,'replace')
    def check_photos(self):
        identifier,job=self.selected_job()
        if not job:
            selected=self.browser.selected()
            if len(selected)!=1 or self.is_external(selected[0]):return
            identifier,job=self.ensure_job(selected[0])
        if not self.active and not self.browser.busy and not self.closing:self.photo_action(identifier,'inspect')
    def photo_action(self,identifier,operation):
        job=self.items[identifier]
        try:self.runner=JobRunner(self.data/'Photos work')
        except Exception as error:
            for pending in [identifier,*self.replacements]:self.items[pending].update(replacement_approval=None,state='Ready to review')
            self.replacements=[];self.save_queue();self.refresh();messagebox.showerror('Photos',str(error));return
        prior=dict(job)
        if operation!='inspect':job['review_operation']=operation
        job.update(operation=operation,state={'replace':'Replacing','inspect':'Checking Photos'}[operation],progress='…',journal=str(self.runner.workspace/'journals'/(uuid.uuid4().hex+'.json')),retry_safe=operation=='inspect' and job.get('retry_safe',True))
        self.current=identifier;self.set_active(True);self.refresh()
        if not self.save_queue():
            self.items[identifier]=prior;self.current=None;self.replacements=[];self.set_active(False);self.refresh();return
        work=dict(job);runner=self.runner
        def worker():
            try:
                if operation=='replace':result=runner.replace_reviewed(work,lambda text:self.events.put(('progress',identifier,'Replacing',None,text)))
                else:result=runner.actions(dict(action='inspect',library=work.get('library'),uuid=work['media'].uuid if work.get('media') else None,new_uuid=work.get('new_uuid'),expected=work.get('expected'),output=work.get('output'),backup=work.get('backup'),journal=work['journal']))
                self.events.put(('photo_done',identifier,operation,result,{key:work.get(key) for key in ('backup','backup_fingerprint','source_size')}))
            except Exception as error:self.failure_event(identifier,error,work)
        self.start_worker(worker,'photos-action')
    def poll(self):
        if self.closed or self.cleanup_started:return
        if self.poll_after:self.root.after_cancel(self.poll_after)
        self.poll_after=None
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
                if self.closing and kind=='progress':continue
                job=self.items[identifier];operation=job.get('operation')
                if kind=='progress':
                    job.update(state=event[2],progress=f'{event[3]:.0f}%' if event[3] is not None else '…')
                    if event[4]:job['detail']=event[4]
                    self.status.set(f'{job["name"]}: {event[4] or event[2]}')
                    if operation=='service_download':
                        key=(self.browser.actual_library,job['media'].uuid)
                        self.browser.transfers[key]=('↓ Preparing '+job['progress'],None);self.browser.refresh_downloads(key)
                elif kind=='converted':
                    job.update(event[2]);job.update(converted=True,state='Ready to review',progress='100%',operation=None,retry_safe=True,converted_name=Path(job['output']).name,local_output_cleared=False)
                elif kind=='converted_downloaded':
                    job.update(output=event[2],output_fingerprint=event[3],result_size=event[3][0],converted=True,local_output_cleared=False,operation=None,state='Done' if job.get('replacement_complete') else 'Ready to review',progress='')
                    self.current=None;self.set_active(False)
                    if not self.closing:
                        try:subprocess.Popen(['open',event[2]])
                        except OSError as error:self.status.set(f'Could not play compressed video: {error}')
                elif kind=='photo_done':
                    result=event[3];job.update(event[4])
                    if operation=='replace':
                        if job.get('new_uuid'):job['last_new_uuid']=job['new_uuid']
                        job.update(new_uuid=result['new_uuid'],verified=True,converted_removed=False,original_removed=True,replacement_complete=True)
                        if result['new_uuid'] not in job.setdefault('uploaded_ids',[]):job['uploaded_ids'].append(result['new_uuid'])
                    elif operation=='inspect':
                        if job.get('media'):job['original_removed']=not result['original_present']
                        if result['converted_present']:job.update(verified=True,converted_removed=False)
                        elif job.get('new_uuid'):job.update(last_new_uuid=job['new_uuid'],new_uuid=None,verified=False,replacement_complete=False,converted_removed=job.get('verified',False) or job.get('converted_removed',False))
                    uncertain=operation=='inspect' and job.get('review_operation') in ('upload','replace') and not result.get('new_uuid')
                    complete=bool(not uncertain and job.get('original_removed') and job.get('verified') and job.get('new_uuid'))
                    job.update(operation=None,retry_safe=not uncertain,replacement_complete=complete,replacement_approval=None,state='Needs review' if uncertain else 'Done' if complete else 'Ready to review' if job.get('converted') else 'Ready',progress='')
                    if not uncertain:job.pop('review_operation',None)
                    job['detail']=f'Photos: {result.get("phase","Status checked")}\nJournal: {job["journal"]}'
                    if uncertain:job['detail']+='\nThe interrupted upload has no recorded asset ID. Inspect Photos before adding this file again.'
                    self.status.set(job['detail']);self.current=None;self.set_active(False)
                elif kind=='failed':
                    metadata=event[2];cancelled=metadata.pop('cancelled');skipped=metadata.pop('skipped',False);error=metadata.pop('error')
                    if operation=='inspect':metadata['retry_safe']=False
                    job.update(metadata);job.update(operation=None,replacement_approval=None,state='Needs review' if not job.get('retry_safe',True) else 'Cancelled' if cancelled else 'Skipped' if skipped else 'Error',detail=error,progress='')
                    if job.get('output') and job.get('expected'):job['converted']=True
                    self.recover_receipt(job)
                    if not job.get('retry_safe',True):
                        self.pause_requested=True
                        for pending in self.replacements:self.items[pending].update(replacement_approval=None,state='Ready to review')
                        self.replacements=[]
                    if operation!='convert':self.current=None;self.set_active(False)
                    self.status.set(f'{job["name"]}: {error}')
                self.refresh(rebuild=kind!='progress')
                if kind!='progress':self.save_queue()
                if operation=='convert' and kind in ('converted','failed'):self.next()
                elif operation=='replace' and kind in ('photo_done','failed') and job.get('retry_safe',True):self.next_replacement()
        except queue.Empty:pass
        except Exception:self.logger.exception('Could not process a workflow event')
        finally:
            # A failed UI update must not strand completion events or shutdown.
            if not self.closed and not self.cleanup_started:self.poll_after=self.root.after(100,self.poll)
        if not self.closing:self.photos_service.tick()
    def pause(self):
        if self.converting():self.pause_requested=True;self.update_actions();self.status.set('Will pause conversion after the current video. Downloads continue.');return
        if self.active or not self.batch or self.closing or self.browser.task not in (None,'download'):return
        try:self.runner=JobRunner(self.data/'Temporary work')
        except Exception as error:messagebox.showerror('Conversion',str(error));return
        self.pause_requested=False;self.set_active(True);self.next()
    def clear_conversion_queue(self):
        for identifier in self.batch:self.items[identifier].update(state='Cancelled',queued_settings=None)
        self.batch=[]
    def stop_conversion(self):
        if self.closing:return
        self.compression_generation+=1;self.pause_requested=True;self.clear_conversion_queue()
        if self.converting():self.runner.stop()
        self.refresh();self.save_queue();self.status.set('Stopping conversion. Downloads continue.')
    def stop(self):
        self.compression_generation+=1;self.pause_requested=True;self.clear_conversion_queue();self.browser.cancel_downloads();self.navigation_cancel.set()
        for job in self.items.values():
            if job.get('compression_pending'):job.update(compression_pending=False,state='Cancelled',queued_settings=None)
        for identifier in self.replacements:self.items[identifier].update(replacement_approval=None,state='Ready to review')
        self.replacements=[]
        if self.runner:self.runner.stop()
    def save_queue(self):
        rows=[]
        for identifier,job in self.items.items():
            transfer=self.browser.transfers.get((job.get('library'),job['media'].uuid)) if job.get('media') else None
            row=dict(job,id=identifier,phase=workflow_status(job,transfer));row['media']=asdict(job['media']) if job.get('media') else None;rows.append(row)
        try:self.workflow.write({'version':3,'jobs':rows});return True
        except (OSError,ValueError,TypeError) as error:self.logger.exception('Could not save workflow');self.status.set(f'Could not save workflow: {error}');return False
    def load_queue(self):
        rows=self.workflow.read().get('jobs',[])
        if not isinstance(rows,list):return
        for row in rows:
            try:
                if not isinstance(row,dict) or not isinstance(row.get('name'),str):continue
                job=dict(row);job['media']=Media(**row['media']) if row.get('media') else None;identifier=job.get('id') or uuid.uuid4().hex;job['id']=identifier
                for key,value in [('converted_removed',False),('original_removed',False),('new_uuid',None),('verified',False),('converted',False),('uploaded_ids',[])]:job.setdefault(key,value)
                # Queued approvals are never replayed after restarting.
                job['replacement_approval']=None
                if job.get('compression_pending'):job.update(compression_pending=False,state='Cancelled')
                if job.get('state') in ('Queued','Queued for conversion','Paused','Queued for replacement'):job.update(state='Ready to review' if job.get('converted') else 'Cancelled',queued_settings=None)
                job['source_size']=job.get('source_size') or (job.get('backup_fingerprint') or [None])[0] or (job['media'].size if job.get('media') else None)
                job['result_size']=job.get('result_size') or (job.get('output_fingerprint') or [None])[0]
                if job.get('operation'):
                    if job['operation'] not in ('convert','inspect','fetch_converted','service_download'):job['review_operation']=job['operation']
                    job.update(state='Cancelled' if job['operation'] in ('convert','fetch_converted','service_download') else 'Needs review',retry_safe=job['operation'] in ('convert','fetch_converted','service_download'),operation=None,progress='')
                if not job.get('retry_safe',True) and not job.get('new_uuid') and not job.get('review_operation') and row.get('operation')!='inspect':job['review_operation']='upload'
                self.recover_receipt(job)
                job['replacement_complete']=bool(job.get('original_removed') and job.get('verified') and job.get('new_uuid') and job.get('retry_safe',True))
                self.items[identifier]=job
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
            if receipt.get('backup'):job.update(backup=receipt['backup'],backup_fingerprint=receipt.get('backup_fingerprint'))
            if receipt.get('new_uuid'):job['new_uuid']=receipt['new_uuid']
            if phase=='Original moved to Recently Deleted':
                if receipt.get('backup'):job.update(backup=receipt['backup'],backup_fingerprint=receipt.get('backup_fingerprint'))
                job.update(original_removed=True,retry_safe=True,state='Original removed',operation=None)
                if receipt.get('action') in ('finish','remove'):job['verified']=True
            elif phase=='Converted moved to Recently Deleted':job.update(converted_removed=True,verified=False,retry_safe=True,state='Converted',operation=None)
            elif phase=='Import verified' and not receipt.get('remove') and receipt.get('expected')==job.get('expected'):job.update(verified=True,converted_removed=False,retry_safe=True,state='In Photos',operation=None)
            elif phase=='Photos inspected' and all(isinstance(receipt.get(key),bool) for key in ('original_present','converted_present')):
                if media:job['original_removed']=not receipt['original_present']
                if receipt['converted_present'] and receipt.get('expected')==job.get('expected'):job.update(verified=True,converted_removed=False)
                elif job.get('new_uuid'):job.update(last_new_uuid=job['new_uuid'],new_uuid=None,verified=False,converted_removed=job.get('verified',False) or job.get('converted_removed',False))
                if job.get('review_operation') not in ('upload','replace') or receipt.get('new_uuid'):job.update(retry_safe=True,state='Original removed' if job.get('original_removed') else 'In Photos' if job.get('verified') else 'Converted' if job.get('converted') else 'Downloaded',operation=None)
            job['replacement_complete']=bool(job.get('original_removed') and job.get('verified') and job.get('new_uuid') and job.get('retry_safe',True))
            if job.get('retry_safe',True):job.pop('review_operation',None)
        except (OSError,ValueError,TypeError):pass
    def retry(self):
        if self.active or self.browser.busy:return
        identifier,job=self.selected_job()
        if not job:return
        if not job.get('retry_safe',True):self.status.set('Checking the interrupted Photos action.');self.check_photos();return
        if not job.get('converted'):job.update(state='Waiting',progress='');self.save_queue();self.refresh();self.start()
        else:self.status.set('Review the compressed video, then choose Replace in Photos. Use Play compressed to download an existing Photos copy.')
    def details(self,identifier=None):
        if identifier is None:identifier,job=self.selected_job()
        else:job=self.items.get(identifier)
        if not job:
            selected=self.browser.selected()
            if len(selected)==1:messagebox.showinfo('Video details',selected[0].detail(),parent=self.root)
            return
        media=self.record_media(identifier,job);conversion,photos=self.media_status(media)
        converted_path=job.get('output') or ('Local copy cleared on close; download from Photos to play' if job.get('local_output_cleared') and job.get('verified') else 'Not converted')
        original='Removed from Photos' if job.get('original_removed') else 'In Photos' if job.get('media') else 'External input'
        compressed='In Photos (iCloud synchronization not confirmed)' if job.get('verified') else 'Removed from Photos' if job.get('converted_removed') else 'Not added to Photos'
        body=f'{job["name"]}\n{conversion}\nLibrary size reduction: {photos or "Not measured"}\nOriginal: {original}\nCompressed: {compressed}\nDownload: {self.browser.download_status(media)[0]}\n\n{job.get("detail","")}\n\nLocal original: {self.source_path(job) or "Not downloaded"}\nLocal compressed: {converted_path}\nPhotos original ID: {job["media"].uuid if job.get("media") else "External file"}\nPhotos compressed ID: {job.get("new_uuid") or job.get("last_new_uuid") or "Not added"}\nJournal: {job.get("journal") or "Not recorded"}\nLog: {self.logfile}'
        recovery=not job.get('retry_safe',True) or job.get('original_removed') and not job.get('replacement_complete')
        if recovery:
            retained=0
            for path in {job.get('backup'),job.get('output')} - {None}:
                try:retained+=Path(path).stat().st_size
                except OSError:pass
            body+=f'\nRecovery copies: {human_size(retained)} retained because a Photos operation is unresolved or its replacement is missing.'
        for previous in job.get('previous_conversions',[]):
            old=self.items.get(previous,{})
            body+=f'\nPrevious conversion: {old.get("output") or "Local copy cleared"} · Photos ID: {old.get("new_uuid") or old.get("last_new_uuid") or "Not uploaded"}'
        for path in job.get('retained_outputs',[]):body+=f'\nRetained previous or changed file (never cleaned automatically): {path}'
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
        self.start_worker(worker,'photos-navigation')
    def close(self):
        if self.closing or self.closed or self.close_prompt_open:return
        running=self.active or self.browser.busy or self.navigating_photos or bool(self.batch or self.replacements or self.browser.download_queue) or any(job.get('compression_pending') for job in self.items.values()) or self.workers_running()
        pending=any(self.has_output(job) and not job.get('verified') and job.get('retry_safe',True) for job in self.items.values())
        if running or pending:
            text=('Downloads and conversions will be stopped, queued work will be cancelled, and CamPresso will close.\n\n' if running else 'Unreviewed compressed videos will be discarded and CamPresso will close.\n\n')
            text+='Temporary working files will be cleared. Recovery copies for unresolved Photos operations will be kept.'
            if self.active and self.current in self.items and self.items[self.current].get('operation')=='replace':text+='\n\nAn approved Photos change already in progress will finish before the app closes.'
            text+='\n\nStop and close?' if running else '\n\nDiscard and close?'
            self.close_prompt_open=True
            try:confirmed=messagebox.askyesno('Stop and close CamPresso?' if running else 'Close CamPresso?',text,parent=self.root)
            finally:self.close_prompt_open=False
            if not confirmed:return
        self.begin_close()
    def workers_running(self):
        # Cancelled catalog queries only read metadata and publish queue events.
        # They can finish as daemon threads without delaying GUI shutdown.
        return any(thread.is_alive() and not (self.closing and thread.name=='photos-service-selection') for thread in self.workers) or self.browser.workers_running()
    def begin_close(self):
        self.closing=True;self.browser.closing=True;self.stop()
        self.browser.gallery.dispose()
        self.status.set('Stopping downloads and conversions before closing…')
        # Native dialogs run a nested event loop. If Close was chosen while a
        # poll callback was inside a dialog, keep draining completion events.
        if self.poll_after is None:self.poll_after=self.root.after(0,self.poll)
        if self.browser.poll_after is None:self.browser.poll_after=self.root.after(0,self.browser.poll)
        self.wait_close()
    def wait_close(self):
        if self.closed or self.cleanup_started:return
        if self.workers_running() or not self.events.empty() or not self.browser.events.empty():
            if self.active and self.current in self.items and self.items[self.current].get('operation')=='replace':self.status.set('Finishing the approved Photos change before closing…')
            else:self.status.set('Stopping downloads and conversions before closing…')
            self.root.after(100,self.wait_close);return
        # Terminal events are drained and workers are gone. Stale UI busy flags
        # from an earlier callback failure must not keep the app open forever.
        for job in self.items.values():
            operation=job.get('operation')
            if operation in ('convert','fetch_converted','service_download'):job.update(operation=None,state='Cancelled',progress='')
            elif operation in ('replace','inspect'):
                job.update(operation=None,retry_safe=False,state='Needs review',progress='');self.recover_receipt(job)
        self.active=False;self.current=None;self.navigating_photos=False;self.browser.busy=False;self.browser.task=None
        self.destroy()
    def destroy(self):
        if self.closed or self.cleanup_started:return
        if self.workers_running() or self.active or self.browser.busy or self.navigating_photos:
            self.begin_close();return
        self.cleanup_started=True;self.closing=True;self.browser.closing=True;self.set_active(False)
        self.status.set('Clearing temporary local storage…');self.save_queue()
        if self.poll_after:self.root.after_cancel(self.poll_after);self.poll_after=None
        self.browser.dispose()
        self.cleanup_events=queue.Queue()
        def worker():
            try:
                self.browser.gallery.dispose(wait=True)
                errors=clean_storage(self.data,list(self.items.values()),delete_unsent=True)
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
            # Cleanup failures are logged and retained for the next launch;
            # an extra modal dialog must not block a confirmed close.
        self.closed=True;self.storage_lock.close();self.root.destroy()

def main(argv=None):
    """Launch the GUI, optionally using a separate application data directory."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,help='Use a separate directory for preferences, workflow and temporary files.')
    parser.add_argument('videos',nargs='*',help='Optional external video paths.')
    options=parser.parse_args(argv)
    try:App(data=options.data_dir,files=options.videos).root.mainloop()
    except RuntimeError as error:
        messagebox.showerror('CamPresso',str(error));return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
