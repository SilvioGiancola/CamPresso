"""Photos catalog UI; all GUI work stays on the main thread."""
import csv, queue, threading, subprocess, errno, logging, re
from pathlib import Path
import tkinter as tk
from dataclasses import replace
from tkinter import ttk, filedialog, messagebox
from photos_catalog import scan_library, view, format_size, ScanCancelled
from photos_download import fetch_video
from photos_previews import PreviewStore, ThumbnailStore
from photos_gallery import ThumbnailGallery
from state_store import StateFile

class PhotosBrowser:
    PAGE_SIZE=500
    def __init__(self,parent,on_queue,embedded=False,cache_dir=None,controller=None):
        self.embedded=embedded
        self.on_queue=on_queue;self.rows=[];self.filtered=[];self.events=queue.Queue()
        self.controller=controller;self.catalog_rows=[]
        self.busy=False;self.task=None;self.cancel=threading.Event();self.page=0;self.sort='size';self.desc=True
        self.download_queue=[];self.download_keys=set();self.download_callbacks={};self.play_requests=set();self.download_names={};self.download_count=0;self.download_errors=[]
        self.actual_library='';self.closed=False;self.closing=False;self.debounce=None
        cache_dir=Path(cache_dir) if cache_dir is not None else Path.home()/'Library'/'Application Support'/'Video Queue'
        self.previews=PreviewStore(cache_dir/'Previews')
        self.thumbnails=ThumbnailStore(cache_dir/'Thumbnails',self.previews)
        self.preferences=StateFile(cache_dir/'preferences.json')
        self.saved_preferences=self.preferences.read();self.preference_after=None
        self.transfers={};self.downloading=None;self.local_copies={}
        self.viewmode=tk.StringVar(value='Thumbnails')
        self.sort_labels={'Original size':'size','Resolution':'pixels','Filename':'name','Date taken':'date'}
        self.sort_label=tk.StringVar(value='Original size');self.reverse=tk.BooleanVar(value=True)
        if embedded:
            self.win=ttk.Frame(parent);self.win.pack(fill='both',expand=True)
        else:
            self.win=tk.Toplevel(parent);self.win.title('Video Queue — Photos Library');self.win.geometry('1120x720');self.win.minsize(900,600)
            self.win.protocol('WM_DELETE_WINDOW',self.close)
        frame=ttk.Frame(self.win,padding=8);frame.pack(fill='both',expand=True);self.frame=frame
        heading=ttk.Frame(frame);heading.pack(fill='x',pady=(0,4));self.heading=heading
        ttk.Label(heading,text='Photos Library',font=('Helvetica',16,'bold')).pack(side='left')
        more=ttk.Menubutton(heading,text='More ▾');more.pack(side='right')
        menu=tk.Menu(more,tearoff=False);menu.add_command(label='Save list as CSV…',command=self.save_csv)
        menu.add_command(label='Permissions help…',command=self.permissions_help);more.configure(menu=menu)
        self.more_menu=menu
        remembered=self.saved_preferences.get('library','')
        if 'library' not in self.saved_preferences:
            try:
                from osxphotos.utils import get_last_library_path
                remembered=get_last_library_path() or ''
            except Exception:logging.getLogger('videoqueue').debug('Could not read the last-opened library preference',exc_info=True)
        self.library=tk.StringVar(value=remembered if isinstance(remembered,str) else '')
        if isinstance(remembered,str) and remembered.lower().endswith('.photoslibrary'):self.actual_library=str(Path(remembered).expanduser().resolve())
        self.library.trace_add('write',self.remember_library)
        self.kind=tk.StringVar(value='All');self.search=tk.StringVar()
        top=ttk.Frame(frame);top.pack(fill='x')
        ttk.Label(top,text='Library:').pack(side='left')
        ttk.Entry(top,textvariable=self.library).pack(side='left',fill='x',expand=True,padx=4)
        self.choose=ttk.Button(top,text='Choose…',command=self.choose_library);self.choose.pack(side='left',padx=2)
        self.scanbtn=ttk.Button(top,text='Scan',command=self.scan);self.scanbtn.pack(side='left',padx=2)
        self.cancelbtn=ttk.Button(top,text='Cancel',command=lambda:self.cancel.set(),state='disabled');self.cancelbtn.pack(side='left')
        filters=ttk.Frame(frame);filters.pack(fill='x',pady=4)
        combo=ttk.Combobox(filters,textvariable=self.kind,values=['All','Photos','Videos'],state='readonly',width=8);combo.pack(side='left')
        combo.bind('<<ComboboxSelected>>',lambda e:self.apply())
        ttk.Label(filters,text='Search').pack(side='left',padx=(6,0))
        ttk.Entry(filters,textvariable=self.search).pack(side='left',fill='x',expand=True,padx=4)
        self.advanced_open=False
        self.filter_toggle=ttk.Button(filters,text='Filters ▸',command=self.toggle_filters);self.filter_toggle.pack(side='left')
        self.search.trace_add('write',self.search_changed)
        self.minimum={}
        advanced_holder=ttk.Frame(frame);advanced_holder.pack(fill='x')
        self.advanced=ttk.Frame(advanced_holder)
        minima=ttk.Frame(self.advanced);minima.pack(fill='x',pady=2)
        for index,(key,label) in enumerate([('min_mb','Min MiB'),('min_mp','Min MP'),('min_width','Min width'),('min_height','Min height')]):
            var=tk.StringVar();self.minimum[key]=var
            if embedded:
                ttk.Label(minima,text=label).grid(row=index//2,column=(index%2)*2,sticky='w',padx=(0,6),pady=2)
                ttk.Entry(minima,textvariable=var,width=8).grid(row=index//2,column=(index%2)*2+1,sticky='ew',padx=(0,12),pady=2)
            else:
                ttk.Label(minima,text=label).pack(side='left',padx=(8,3));ttk.Entry(minima,textvariable=var,width=8).pack(side='left')
        if embedded:
            for column in (1,3):minima.columnconfigure(column,weight=1)
            filter_actions=ttk.Frame(self.advanced);filter_actions.pack(fill='x',pady=(0,4))
        else:filter_actions=minima
        ttk.Button(filter_actions,text='Apply filters',command=self.apply).pack(side='left',padx=(0,8))
        ttk.Button(filter_actions,text='Reset filters',command=self.reset).pack(side='left')
        views=ttk.Frame(frame);views.pack(fill='x',pady=(0,2))
        for label in ('Thumbnails','List'):
            ttk.Radiobutton(views,text=label,value=label,variable=self.viewmode,command=self.change_view).pack(side='left')
        ttk.Label(views,text='Sort').pack(side='left',padx=(8,4))
        sortbox=ttk.Combobox(views,textvariable=self.sort_label,values=list(self.sort_labels),state='readonly',width=13);sortbox.pack(side='left')
        sortbox.bind('<<ComboboxSelected>>',lambda event:self.sort_gallery())
        ttk.Checkbutton(views,text='Descending',variable=self.reverse,command=self.sort_gallery).pack(side='left',padx=4)
        holder=ttk.Frame(frame);holder.pack(fill='both',expand=True,pady=4)
        self.columns=[('name','Filename',260),('download','Download',150),('conversion','Conversion',165),('photos','Photos',270),('kind','Media',60),('size','Original size ↕',110),('pixels','Resolution ↕',130),('date','Taken',175)]
        self.tree=ttk.Treeview(holder,columns=[k for k,_,_ in self.columns],show='headings',selectmode='extended')
        for key,label,width in self.columns:
            self.tree.heading(key,text=label,command=(lambda:None) if key in ('download','conversion','photos') else lambda k=key:self.order(k));self.tree.column(key,width=width,minwidth=45,stretch=key=='name')
        self.tree.grid(row=0,column=0,sticky='nsew');holder.rowconfigure(0,weight=1);holder.columnconfigure(0,weight=1)
        self.sy=ttk.Scrollbar(holder,orient='vertical',command=self.tree.yview);self.sy.grid(row=0,column=1,sticky='ns')
        self.sx=ttk.Scrollbar(holder,orient='horizontal',command=self.tree.xview);self.sx.grid(row=1,column=0,sticky='ew');self.tree.configure(yscrollcommand=self.sy.set,xscrollcommand=self.sx.set)
        self.tree.bind('<<TreeviewSelect>>',self.details)
        self.tree.bind('<Double-1>',lambda event:self.preview_selected())
        self.gallery=ThumbnailGallery(holder,self.thumbnails,self.events,self.details,self.preview_selected,status=self.download_status,workflow=self.workflow_status)
        self.gallery.grid(row=0,column=0,columnspan=2,rowspan=2,sticky='nsew')
        self.tree.grid_remove();self.sy.grid_remove();self.sx.grid_remove()
        pagination=ttk.Frame(frame);pagination.pack(fill='x')
        ttk.Button(pagination,text='‹',width=3,command=lambda:self.turn(-1)).pack(side='left')
        ttk.Button(pagination,text='›',width=3,command=lambda:self.turn(1)).pack(side='left')
        self.count=tk.StringVar(value='No library scanned.');ttk.Label(pagination,textvariable=self.count).pack(side='left',padx=6)
        ttk.Button(pagination,text='Select page',command=self.select_page).pack(side='right')
        controls=ttk.Frame(frame);controls.pack(fill='x',pady=(4,2));self.controls=controls
        self.previewbtn=ttk.Button(controls,text='Download & view',command=self.preview_selected,state='disabled');self.previewbtn.pack(side='left')
        self.copybtn=ttk.Button(controls,text='Send to conversion →' if embedded else 'Copy selected → queue',command=self.enqueue_selected if embedded else self.copy_selected,state='disabled');self.copybtn.pack(side='right')
        if controller:self.copybtn.configure(text='Convert selected')
        self.status=tk.StringVar(value='Scan the library · Download and play · Convert · Send to Photos.\nThe library path is remembered when you reopen.')
        status_area=ttk.Frame(frame,height=38);status_area.pack(fill='x',pady=(2,0));status_area.pack_propagate(False)
        self.status_label=ttk.Label(status_area,textvariable=self.status,wraplength=650);self.status_label.pack(fill='x',anchor='nw')
        def wrap_text(event):
            self.status_label.configure(wraplength=max(1,event.width-16))
        frame.bind('<Configure>',wrap_text)
        if embedded:
            self.kind.set('Videos')
            self.tree.configure(height=7)
        self.poll_after=self.win.after(100,self.poll)

    def workflow_status(self,media):
        return self.controller.media_status(media) if self.controller else ('Not converted','Not uploaded')
    def rebuild_catalog(self,preserve=True):
        selected={row.uuid for row in self.selected()} if preserve else set()
        self.rows=self.controller.merge_catalog(self.catalog_rows,self.actual_library) if self.controller else list(self.catalog_rows)
        try:self.filtered=view(self.rows,kind=self.kind.get(),search=self.search.get(),sort=self.sort,descending=self.desc,**self.values())
        except ValueError as error:self.status.set(str(error));return
        pages=max(1,(len(self.filtered)+self.PAGE_SIZE-1)//self.PAGE_SIZE);self.page=min(self.page,pages-1)
        self.render()
        items=[str(index) for index,row in enumerate(self.filtered) if row.uuid in selected and self.page*self.PAGE_SIZE<=index<(self.page+1)*self.PAGE_SIZE]
        if self.viewmode.get()=='List':self.tree.selection_set(items)
        else:self.gallery.selection_set(items)
    def refresh_workflow(self):
        self.gallery.redraw()
        for iid in self.tree.get_children():
            row=self.filtered[int(iid)];conversion,photos=self.workflow_status(row)
            self.tree.set(iid,'conversion',conversion);self.tree.set(iid,'photos',photos)
        self.update_preview_button()

    def remember_library(self,*args):
        if self.preference_after:self.win.after_cancel(self.preference_after)
        self.preference_after=self.win.after(400,self.save_library)
    def save_library(self):
        if self.preference_after:self.win.after_cancel(self.preference_after)
        self.preference_after=None
        self.saved_preferences['library']=self.library.get().strip()
        try:self.preferences.write(self.saved_preferences)
        except OSError as error:
            logging.getLogger('videoqueue').warning('Could not remember Photos library: %s',error)
            self.status.set(f'Could not save library preference: {error}')
    def transfer_key(self,media):return (self.actual_library,media.uuid)
    def available_media(self,media):
        if self.controller and self.controller.is_external(media):
            try:return media if media.path and Path(media.path).is_file() and Path(media.path).stat().st_size>0 else None
            except OSError:return None
        ready=self.previews.media_for_queue(media,self.actual_library)
        try:
            copy=self.local_copies.get(self.transfer_key(media))
            if copy and Path(copy[0]).is_file():
                backup,fingerprint=copy;stat=Path(backup).stat()
                if stat.st_size>0 and [stat.st_size,stat.st_mtime_ns]==fingerprint:return replace(media,path=backup,local=True)
            return ready if ready.path and Path(ready.path).is_file() and Path(ready.path).stat().st_size>0 else None
        except OSError:return None
    def download_status(self,media):
        transfer=self.transfers.get(self.transfer_key(media))
        if transfer:return transfer[0],'#a46700'
        return ('● Downloaded','#24764b') if self.available_media(media) else ('○ Not downloaded','#56616d')
    def refresh_downloads(self,key=None):
        self.gallery.redraw()
        for iid in self.tree.get_children():
            media=self.filtered[int(iid)]
            if key is None or self.transfer_key(media)==key:self.tree.set(iid,'download',self.download_status(media)[0])
        self.update_preview_button()
        if self.controller:self.controller.update_actions()

    def toggle_filters(self):
        self.advanced_open=not self.advanced_open
        if self.advanced_open:self.advanced.pack(fill='x')
        else:self.advanced.pack_forget()
        self.update_filter_toggle()
    def update_filter_toggle(self):
        count=sum(bool(var.get().strip()) for var in self.minimum.values())
        self.filter_toggle.configure(text='Filters'+(f' ({count})' if count else '')+(' ▾' if self.advanced_open else ' ▸'))

    def permissions_help(self):
        app=Path.home()/'Applications'/'Video Queue.app'
        messagebox.showinfo('Photos library access',
            f'The app is installed here:\n{app}\n\n'
            '1. Open System Settings → Privacy & Security → Full Disk Access.\n'
            '2. Click +. In the picker, press Command-Shift-G.\n'
            '3. Type ~/Applications and press Return.\n'
            '4. Select Video Queue.app and enable its switch.\n'
            '5. Quit and reopen Video Queue.\n\n'
            'A “database not found” error can also mean the wrong library folder was selected. '
            'Choose the .photoslibrary bundle, or leave Library blank.',parent=self.win)
        if app.exists():subprocess.Popen(['open','-R',str(app)])

    def choose_library(self):
        if self.busy or self.closing or self.controller and self.controller.active:return
        path=filedialog.askdirectory(parent=self.win,title='Select your .photoslibrary bundle or the folder containing it',initialdir=Path.home()/'Pictures')
        if path:self.library.set(path);self.save_library()
    def set_busy(self,busy,task=None):
        self.busy=busy;self.task=(task or self.task or 'scan') if busy else None
        blocked=busy or self.closing or self.controller and self.controller.active
        for b in (self.choose,self.scanbtn):b.configure(state='disabled' if blocked else 'normal')
        self.cancelbtn.configure(text='Cancel downloads' if self.task=='download' else 'Cancel',state='normal' if busy else 'disabled')
        self.update_preview_button()
    def can_download(self):
        return not self.closing and self.task in (None,'download') and not (self.task=='download' and self.cancel.is_set()) and (not self.controller or self.controller.allow_background_download())
    def scan(self):
        if self.busy or self.closing or self.controller and self.controller.active:return
        path=self.library.get().strip()
        if path and not Path(path).expanduser().is_dir():messagebox.showerror('Library not found','Select a .photoslibrary folder, or leave the path blank.',parent=self.win);return
        self.save_library()
        self.cancel=threading.Event();self.set_busy(True,'scan');self.status.set('Reading Photos library metadata… large libraries may take a while.')
        def worker():
            try:
                rows,actual=scan_library(path or None,lambda done,total:self.events.put(('progress',f'Reading metadata: {done:,} / {total:,}')),self.cancel)
                self.events.put(('scanned',rows,actual))
            except ScanCancelled:self.events.put(('error','Scan cancelled. Previous list kept.'))
            except Exception as e:
                msg=str(e)
                if isinstance(e,PermissionError) or isinstance(e,OSError) and e.errno in (errno.EACCES,errno.EPERM):
                    msg+='\nAccess was denied. Open Permissions help for the app location and Full Disk Access instructions.'
                self.events.put(('error',msg))
        threading.Thread(target=worker,daemon=True).start()
    def values(self):
        out={}
        for key,var in self.minimum.items():
            try:value=float(var.get().strip() or 0)
            except ValueError:raise ValueError('Filter values must be numbers.')
            if value<0 or not value<float('inf'):raise ValueError('Filter values must be finite, non-negative numbers.')
            out[key]=value
        return out
    def apply(self):
        self.update_filter_toggle()
        try:self.filtered=view(self.rows,kind=self.kind.get(),search=self.search.get(),sort=self.sort,descending=self.desc,**self.values())
        except ValueError as e:self.status.set(str(e));return
        self.page=0;self.render()
    def reset(self):
        self.kind.set('All');self.search.set('')
        for var in self.minimum.values():var.set('')
        self.apply()
    def search_changed(self,*args):
        if self.debounce:self.win.after_cancel(self.debounce)
        self.debounce=self.win.after(250,self.apply)
    def order(self,key):
        self.desc=not self.desc if key==self.sort else key in ('size','pixels','width','height')
        self.sort=key;self.reverse.set(self.desc)
        self.sort_label.set(next((label for label,value in self.sort_labels.items() if value==key),key))
        self.apply()
    def sort_gallery(self):
        self.sort=self.sort_labels.get(self.sort_label.get(),self.sort);self.desc=self.reverse.get();self.apply()
    def change_view(self):
        if self.viewmode.get()=='List':
            selection=self.gallery.selection();self.gallery.grid_remove()
            for widget in (self.tree,self.sy,self.sx):widget.grid()
            self.tree.selection_set(selection)
        else:
            selection=self.tree.selection()
            for widget in (self.tree,self.sy,self.sx):widget.grid_remove()
            self.gallery.grid();self.gallery.selection_set(selection)
        self.details()
    def select_page(self):
        if self.viewmode.get()=='List':self.tree.selection_set(self.tree.get_children())
        else:self.gallery.selection_set(iid for iid,_ in self.gallery.rows)
    def render(self,preserve=False):
        selection=self.tree.selection() if preserve else ()
        self.tree.delete(*self.tree.get_children())
        start=self.page*self.PAGE_SIZE
        rows=[(str(index),r) for index,r in enumerate(self.filtered[start:start+self.PAGE_SIZE],start)]
        for iid,r in rows:
            resolution=f'{r.width} × {r.height}' if r.width and r.height else 'Unknown'
            conversion,photos=self.workflow_status(r)
            name=('♥ ' if r.favorite else '')+r.name
            self.tree.insert('', 'end',iid=iid,values=(name,self.download_status(r)[0],conversion,photos,r.kind,format_size(r.size),resolution,r.date))
        self.tree.selection_set(selection)
        self.gallery.set_rows([(iid,self.available_media(row) or row) for iid,row in rows],self.actual_library,preserve=preserve)
        pages=max(1,(len(self.filtered)+self.PAGE_SIZE-1)//self.PAGE_SIZE)
        self.count.set(f'{len(self.filtered):,}/{len(self.rows):,} assets · {self.page+1}/{pages}')
        self.update_preview_button()
    def turn(self,step):
        page=self.page+step
        if 0<=page<max(1,(len(self.filtered)+self.PAGE_SIZE-1)//self.PAGE_SIZE):self.page=page;self.render()
    def selected(self):
        selection=self.tree.selection() if self.viewmode.get()=='List' else self.gallery.selection()
        return [self.filtered[int(i)] for i in selection if 0<=int(i)<len(self.filtered)]
    def update_preview_button(self):
        selected=self.selected();valid=bool(selected) and all(row.kind=='Video' for row in selected)
        cached=self.previews.cached_path(selected[0],self.actual_library) if valid and len(selected)==1 else None
        available=len(selected)==1 and self.available_media(selected[0]) if valid else None
        label='Download selected' if len(selected)>1 else 'Play original' if cached or available else 'Download & play original'
        if self.task=='download' and not cached and not available:label='Queue downloads' if len(selected)>1 else 'Queue download & play'
        self.previewbtn.configure(text=label,state='normal' if valid and self.can_download() else 'disabled')
        ready=self.task in (None,'download') and bool(selected) and all(r.kind=='Video' and self.available_media(r) for r in selected)
        self.copybtn.configure(state='normal' if ready and not self.closing else 'disabled')
        if self.controller:self.controller.update_actions()
    def details(self,event=None):
        selected=self.selected()
        self.update_preview_button()
        if selected and not self.busy:
            r=selected[0]
            ready=bool(self.previews.cached_path(r,self.actual_library))
            self.status.set(f'{len(selected)} selected · {r.name}\n'+('Downloaded preview ready to play.' if ready else 'Local original available.' if r.local else 'iCloud original will download when you preview or convert.'))
        if self.controller:self.controller.selection_detail()
    def preview_selected(self):
        if not self.can_download():return
        selected=self.selected()
        if not selected or any(row.kind!='Video' for row in selected):self.status.set('Select videos to download or view.');return
        media=[self.available_media(row) or row for row in selected];library=self.actual_library
        if len(media)==1 and media[0].path and Path(media[0].path).is_file() and not any(part.lower().endswith('.photoslibrary') for part in Path(media[0].path).resolve().parts):
            try:subprocess.Popen(['open',media[0].path])
            except OSError as error:self.status.set(f'Could not open video player: {error}')
            return
        self.queue_downloads(media,library,play=len(media)==1)
    def queue_downloads(self,media,library,play=False,on_ready=None,on_error=None):
        if not self.can_download():return False
        starting=not self.busy
        if starting:
            self.cancel=threading.Event();self.download_count=0;self.download_errors=[];self.set_busy(True,'download')
        for row in media:
            key=(library,row.uuid)
            if on_ready or on_error:self.download_callbacks.setdefault(key,[]).append((on_ready,on_error))
            if play:self.play_requests.add(key)
            if key in self.download_keys:continue
            external=bool(self.controller and self.controller.is_external(row))
            self.download_queue.append((key,row,library,external));self.download_keys.add(key);self.download_names[key]=row.name
            self.transfers[key]=('↓ Queued for download',None)
        self.refresh_downloads()
        if starting:self.start_next_download()
        return True
    def download_callbacks_done(self,key,row=None,path=None,error=None):
        for ready,failed in self.download_callbacks.pop(key,[]):
            try:
                if error is None and ready:ready(row,path)
                elif error is not None and failed:failed(key,error)
            except Exception as callback_error:
                logging.getLogger('videoqueue').exception('Download completion callback failed: %s',key)
                if error is None and failed:
                    try:failed(key,callback_error)
                    except Exception:logging.getLogger('videoqueue').exception('Download failure callback failed: %s',key)
    def start_next_download(self):
        if self.cancel.is_set():
            for key,_,_,_ in self.download_queue:
                self.download_callbacks_done(key,error=ScanCancelled());self.transfers.pop(key,None);self.download_keys.discard(key);self.play_requests.discard(key)
            self.download_queue=[]
        if not self.download_queue:
            self.events.put(('downloads_finished',self.download_count,list(self.download_errors),self.cancel.is_set()));return
        key,row,library,external=self.download_queue.pop(0);cancel=self.cancel
        self.downloading=key;self.transfers[key]=('↓ Downloading…',None);self.refresh_downloads(key)
        def worker():
            try:
                path=row.path if external else self.previews.prepare(row,library,cancel,lambda text:self.events.put(('download_progress',key,text)))
                self.events.put(('download_saved',key,row,path))
            except ScanCancelled:self.events.put(('download_failed',key,row,None,True))
            except Exception as error:
                logging.getLogger('videoqueue').exception('Video download failed: %s (%s)',row.name,row.uuid)
                self.events.put(('download_failed',key,row,str(error),False))
        threading.Thread(target=worker,daemon=True).start()
    def enqueue_selected(self):
        selected=[r for r in self.selected() if r.kind=='Video']
        if not selected:self.status.set('Select videos first.');return
        if self.task not in (None,'download') or self.closing:return
        ready=[self.available_media(r) for r in selected]
        if any(r is None for r in ready):self.status.set('Download the selected videos before sending them to conversion.');return
        self.on_queue(ready,self.actual_library)
        if not self.controller:self.status.set(f'{len(selected)} downloaded videos sent to conversion.\nConvert first; Send to Photos and Remove original are separate actions.')

    def copy_selected(self):
        if self.busy:return
        selected=[r for r in self.selected() if r.kind=='Video']
        if not selected:self.status.set('Select videos first. Photos cannot enter the video conversion queue.');return
        dest=filedialog.askdirectory(parent=self.win,title='Choose folder for downloaded/exported originals')
        if not dest:return
        self.cancel=threading.Event();self.set_busy(True);self.status.set('Preparing selected originals (downloads cloud videos when needed)…')
        library=self.actual_library
        def worker():
            copied=[];errors=[]
            for n,r in enumerate(selected,1):
                if self.cancel.is_set():break
                self.events.put(('progress',f'Copying {n} / {len(selected)}: {r.name}'))
                try:
                    path=fetch_video(r,dest,library,self.cancel,lambda text:self.events.put(('progress',f'{n}/{len(selected)} · {r.name}: {text}')));copied.append(path)
                except ScanCancelled:break
                except Exception as e:
                    logging.getLogger('videoqueue').exception('Photos download/copy failed: %s (%s)',r.name,r.uuid)
                    errors.append(f'{r.name}: {e}')
            self.events.put(('copied',copied,errors,self.cancel.is_set()))
        threading.Thread(target=worker,daemon=True).start()
    def save_csv(self):
        if not self.filtered:self.status.set('No results to save.');return
        target=filedialog.asksaveasfilename(parent=self.win,title='Save current filtered list',defaultextension='.csv',initialfile='Photos-Catalog.csv')
        if not target:return
        try:
            with open(target,'w',newline='',encoding='utf-8-sig') as f:
                fields=['uuid','name','kind','size_bytes','width','height','megapixels','date','local','live','edited']
                writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
                for r in self.filtered:
                    # Neutralize spreadsheet formula prefixes in user-controlled text.
                    safe=lambda s: "'"+s if isinstance(s,str) and s.startswith(('=','+','-','@','\t','\r')) else s
                    writer.writerow(dict(uuid=safe(r.uuid),name=safe(r.name),kind=r.kind,size_bytes=r.size,width=r.width,height=r.height,megapixels=r.pixels/1e6 if r.pixels else None,date=r.date,local=r.local,live=r.live,edited=r.edited))
            self.status.set(f'Saved {len(self.filtered):,} rows to {target}')
        except OSError as e:messagebox.showerror('Could not save',str(e),parent=self.win)
    def poll(self):
        if self.closed:return
        self.win.after_cancel(self.poll_after)
        try:
            while True:
                e=self.events.get_nowait()
                if e[0]=='progress':self.status.set(e[1])
                elif e[0]=='download_started':
                    self.downloading=e[1];self.transfers[e[1]]=('↓ Downloading…',None)
                    self.status.set(f'Downloading: {e[2]}');self.refresh_downloads(e[1])
                elif e[0]=='download_progress':
                    percent=re.search(r'(\d+(?:\.\d+)?)%',e[2])
                    elapsed=re.search(r'(\d+)s',e[2])
                    phase='Downloading' if 'Downloading' in e[2] else 'Copying'
                    label=f'↓ {phase} {float(percent.group(1)):.0f}%' if percent else '↓ Downloading'+(f' {elapsed.group(1)}s' if elapsed else '…')
                    if not percent and 'Checking' in e[2]:label='↓ Checking download…'
                    self.transfers[e[1]]=(label,None);self.status.set(e[2]);self.refresh_downloads(e[1])
                elif e[0]=='download_saved':
                    self.download_count+=1;self.transfers.pop(e[1],None);self.downloading=None;self.download_keys.discard(e[1]);self.refresh_downloads(e[1])
                    self.download_callbacks_done(e[1],row=e[2],path=e[3])
                    if e[1] in self.play_requests and not self.closing and not self.cancel.is_set():
                        try:subprocess.Popen(['open',e[3]])
                        except OSError as error:self.status.set(f'Could not open video player: {error}')
                    self.play_requests.discard(e[1]);self.start_next_download()
                elif e[0]=='download_failed':
                    self.transfers.pop(e[1],None);self.downloading=None;self.download_keys.discard(e[1]);self.play_requests.discard(e[1])
                    error=ScanCancelled() if e[4] else RuntimeError(e[3])
                    if not e[4]:self.download_errors.append(f'{e[2].name}: {e[3]}')
                    self.download_callbacks_done(e[1],error=error);self.refresh_downloads(e[1]);self.start_next_download()
                elif e[0]=='downloads_finished':
                    self.transfers.clear();self.downloading=None;self.download_keys.clear();self.download_names.clear();self.play_requests.clear();self.set_busy(False)
                    if not self.closing:self.render(preserve=True)
                    self.status.set(f'{e[1]} videos ready. Play original or Convert selected.'+(' Download stopped.' if e[3] else '')+(' Some downloads failed; successful videos remain available.' if e[2] else ''))
                    if e[2] and not self.closing:messagebox.showerror('Download issues','\n\n'.join(e[2][:8])+(f'\n\n{len(e[2])-8} more issues are recorded in the log.' if len(e[2])>8 else ''),parent=self.win)
                elif e[0]=='scanned':
                    self.catalog_rows=e[1];self.actual_library=e[2];self.library.set(e[2]);self.set_busy(False)
                    if self.controller:self.controller.refresh(True)
                    else:self.rebuild_catalog()
                    self.status.set(f'{len(self.rows):,} videos/assets · {Path(e[2]).name}\nSelect a video to play, convert, send to Photos or remove a version.')
                elif e[0]=='copied':
                    self.on_queue(e[1]);self.set_busy(False)
                    self.status.set(f'{len(e[1])} original videos downloaded/copied and queued. Photos originals unchanged.'+(' Download/copy cancelled.' if e[3] else '')+(' Issues: '+'; '.join(e[2]) if e[2] else ''))
                    if e[2]:messagebox.showerror('Some selected videos could not be downloaded', '\n\n'.join(e[2]),parent=self.win)
                elif e[0]=='thumbnail':self.gallery.accept_thumbnail(*e[1:])
                elif e[0]=='error':
                    self.transfers.pop(self.downloading,None);self.downloading=None
                    self.set_busy(False);self.status.set(e[1])
                    self.refresh_downloads()
                    if not self.closing:messagebox.showerror('Photos Library',e[1],parent=self.win)
        except queue.Empty:pass
        if not self.closed:self.poll_after=self.win.after(100,self.poll)
    def close(self):
        if self.closing or self.closed:return
        self.closing=True
        if self.busy:
            self.cancel.set();self.status.set('Stopping background task before closing…');self.win.after(100,self.wait_close)
        else:self.dispose();self.win.destroy()
    def wait_close(self):
        if self.busy:self.win.after(100,self.wait_close)
        else:self.dispose();self.win.destroy()
    def dispose(self):
        if self.closed:return
        self.closed=True
        if self.preference_after:self.win.after_cancel(self.preference_after);self.preference_after=None
        self.save_library()
        self.cancel.set();self.gallery.dispose()
        self.win.after_cancel(self.poll_after)
        if self.debounce:self.win.after_cancel(self.debounce);self.debounce=None
