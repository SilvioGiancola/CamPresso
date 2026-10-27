"""A scrollable thumbnail grid; native Tk images stay on the GUI thread."""
import math, threading
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import ttk
from .photos_catalog import ScanCancelled, format_size, format_duration

class ThumbnailGallery(ttk.Frame):
    TILE_WIDTH=216;TILE_HEIGHT=182;GAP=8
    def __init__(self,parent,store,events,on_select,on_play,status=None,workflow=None):
        super().__init__(parent)
        self.store=store;self.events=events;self.on_select=on_select;self.on_play=on_play
        self.status=status
        self.workflow=workflow
        self.rows=[];self.library='';self.selected_ids=set();self.anchor=None
        self.generation=0;self.cancel=threading.Event();self.closed=False
        self.results={};self.images={};self.requested=set();self.image_items={}
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='video-thumbnail')
        self.canvas=tk.Canvas(self,background='#f1f3f5',highlightthickness=0,height=240,yscrollincrement=32,takefocus=True)
        self.canvas.grid(row=0,column=0,sticky='nsew')
        scrollbar=ttk.Scrollbar(self,orient='vertical',command=self.scroll);scrollbar.grid(row=0,column=1,sticky='ns')
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.rowconfigure(0,weight=1);self.columnconfigure(0,weight=1)
        self.canvas.bind('<Configure>',lambda event:self.redraw())
        self.canvas.bind('<MouseWheel>',self.wheel)
        self.canvas.bind('<Button-1>',self.click)
        self.canvas.bind('<Double-1>',lambda event:self.on_play())
        self.canvas.bind('<Return>',lambda event:self.on_play())
        for key in ('Left','Right','Up','Down'):self.canvas.bind('<'+key+'>',self.move)
    def selection(self):return tuple(iid for iid,_ in self.rows if iid in self.selected_ids)
    def selection_set(self,items):
        self.selected_ids=set(map(str,items))&{iid for iid,_ in self.rows}
        self.redraw();self.on_select()
    def set_rows(self,rows,library,preserve=False):
        self.cancel.set();self.cancel=threading.Event();self.generation+=1
        selected=set(self.selected_ids) if preserve else set()
        self.rows=list(rows);self.library=library;self.selected_ids=selected&{iid for iid,_ in self.rows}
        self.results={};self.images={};self.requested=set();self.anchor=None
        if not preserve:self.canvas.yview_moveto(0)
        self.redraw()
    def layout(self):
        width=max(1,self.canvas.winfo_width());stride=self.TILE_WIDTH+self.GAP
        columns=max(1,(width+self.GAP-12)//stride)
        margin=max(6,(width-columns*stride+self.GAP)//2)
        height=math.ceil(len(self.rows)/columns)*(self.TILE_HEIGHT+self.GAP)+12
        return columns,margin,max(1,height)
    def redraw(self):
        if self.closed:return
        columns,margin,height=self.layout();self.canvas.configure(scrollregion=(0,0,self.canvas.winfo_width(),height))
        self.canvas.delete('all');self.image_items={}
        if not self.rows:
            self.canvas.create_text(max(130,self.canvas.winfo_width()/2),55,text='Scan a library to see video thumbnails.',fill='#59636e',font=('Helvetica',12))
            return
        stride=self.TILE_HEIGHT+self.GAP;top=self.canvas.canvasy(0)
        first=max(0,int(top//stride)-1)*columns
        last=min(len(self.rows),(int((top+self.canvas.winfo_height())//stride)+2)*columns)
        visible={self.rows[n][0] for n in range(first,last)}
        self.images={iid:image for iid,image in self.images.items() if iid in visible}
        for position in range(first,last):
            iid,media=self.rows[position]
            x=margin+(position%columns)*(self.TILE_WIDTH+self.GAP);y=6+(position//columns)*stride
            selected=iid in self.selected_ids
            self.canvas.create_rectangle(x,y,x+self.TILE_WIDTH,y+self.TILE_HEIGHT,fill='white',outline='#0a84ff' if selected else '#d8dde3',width=2 if selected else 1)
            self.canvas.create_rectangle(x+4,y+5,x+self.TILE_WIDTH-4,y+105,fill='#18202b',outline='')
            image=self.images.get(iid)
            if image is None and self.results.get(iid):
                try:image=tk.PhotoImage(master=self.canvas,file=self.results[iid]);self.images[iid]=image
                except tk.TclError:self.results[iid]=None
            self.image_items[iid]=self.canvas.create_image(x+self.TILE_WIDTH/2,y+55,image=image or '')
            if image is None:self.canvas.create_text(x+self.TILE_WIDTH/2,y+54,text='▶' if media.kind=='Video' else 'Photo',fill='#96a2af',font=('Helvetica',22))
            name=media.name if len(media.name)<=29 else media.name[:26]+'…'
            self.canvas.create_text(x+6,y+115,text=name,anchor='w',fill='#202934',font=('Helvetica',10))
            resolution=f'{media.width}×{media.height}' if media.width and media.height else 'Unknown size'
            self.canvas.create_text(x+6,y+130,text=f'{format_size(media.size)} · {format_duration(media.duration)}' if media.kind=='Video' else f'{format_size(media.size)} · {resolution}',anchor='w',fill='#65717d',font=('Helvetica',9))
            if media.kind=='Video' and self.workflow:
                conversion,photos=self.workflow(media)
                self.canvas.create_text(x+6,y+148,text=conversion,anchor='w',fill='#156da5',font=('Helvetica',9,'bold'))
                self.canvas.create_text(x+6,y+166,text=photos,anchor='w',fill='#59636e',font=('Helvetica',9))
            if media.kind=='Video':self.canvas.create_text(x+self.TILE_WIDTH-10,y+94,text='▶',anchor='e',fill='white',font=('Helvetica',11))
            if media.favorite:
                self.canvas.create_oval(x+8,y+79,x+32,y+103,fill='#18202b',outline='')
                self.canvas.create_text(x+20,y+91,text='♥',fill='#ff667d',font=('Helvetica',16,'bold'))
            if media.kind=='Video' and self.status:
                label,color=self.status(media)
                badge=self.canvas.create_text(x+9,y+18,text=label,anchor='w',fill='white',font=('Helvetica',9,'bold'))
                bounds=self.canvas.bbox(badge)
                background=self.canvas.create_rectangle(bounds[0]-3,bounds[1]-2,bounds[2]+3,bounds[3]+2,fill=color,outline='')
                self.canvas.tag_lower(background,badge)
            if iid not in self.requested:
                self.requested.add(iid)
                self.pool.submit(self.load_thumbnail,self.generation,iid,media,self.library,self.cancel)
    def load_thumbnail(self,generation,iid,media,library,cancel):
        try:path=self.store.image(media,library,cancel)
        except ScanCancelled:return
        except Exception:path=None
        if not cancel.is_set():self.events.put(('thumbnail',generation,iid,path))
    def accept_thumbnail(self,generation,iid,path):
        if self.closed or generation!=self.generation:return
        self.results[iid]=path
        if iid in self.image_items:self.redraw()
    def scroll(self,*args):self.canvas.yview(*args);self.redraw()
    def wheel(self,event):
        delta=event.delta
        units=-int(delta/120) if abs(delta)>=120 else (-1 if delta>0 else 1)
        self.canvas.yview_scroll(units,'units');self.redraw();return 'break'
    def click(self,event):
        self.canvas.focus_set();columns,margin,_=self.layout()
        x=self.canvas.canvasx(event.x)-margin;y=self.canvas.canvasy(event.y)-6
        column=int(x//(self.TILE_WIDTH+self.GAP));row=int(y//(self.TILE_HEIGHT+self.GAP))
        position=row*columns+column
        if x<0 or y<0 or column>=columns or position>=len(self.rows):return
        if x%(self.TILE_WIDTH+self.GAP)>self.TILE_WIDTH or y%(self.TILE_HEIGHT+self.GAP)>self.TILE_HEIGHT:return
        self.select(position,event.state)
    def select(self,position,state=0):
        iid=self.rows[position][0]
        if state&1 and self.anchor is not None:
            first,last=sorted((self.anchor,position));self.selected_ids={item for item,_ in self.rows[first:last+1]}
        elif state&(4|8):
            if iid in self.selected_ids:self.selected_ids.remove(iid)
            else:self.selected_ids.add(iid)
            self.anchor=position
        else:self.selected_ids={iid};self.anchor=position
        self.redraw();self.on_select()
    def move(self,event):
        if not self.rows:return 'break'
        columns,_,height=self.layout();position=self.anchor if self.anchor is not None else 0
        offset={'Left':-1,'Right':1,'Up':-columns,'Down':columns}[event.keysym]
        position=max(0,min(len(self.rows)-1,position+offset));self.select(position,event.state)
        y=(position//columns)*(self.TILE_HEIGHT+self.GAP);top=self.canvas.canvasy(0)
        if y<top or y+self.TILE_HEIGHT>top+self.canvas.winfo_height():self.canvas.yview_moveto(y/height);self.redraw()
        return 'break'
    def dispose(self,wait=False):
        self.closed=True;self.cancel.set();self.pool.shutdown(wait=False,cancel_futures=True)
        if wait:self.pool.shutdown(wait=True,cancel_futures=True)
