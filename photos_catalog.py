"""Read-only Photos catalog and safe copies outside the library.

Only osxphotos.PhotosDB query APIs are used. No Photos database writes or
in-library media edits occur. Live Photos are single photo assets, not videos.
"""
from dataclasses import dataclass
from pathlib import Path
import os, re, shutil, tempfile, threading

class ScanCancelled(Exception): pass

@dataclass(frozen=True)
class Media:
    uuid: str
    name: str
    kind: str
    size: int | None
    width: int | None
    height: int | None
    date: str
    path: str | None
    local: bool
    live: bool = False
    edited: bool = False
    thumbnail: str | None = None
    favorite: bool = False

    @property
    def pixels(self):
        return self.width*self.height if self.width and self.height else None

    def detail(self):
        return (f'{"♥ " if self.favorite else ""}{self.name} · {self.kind}' + (' · Favorite in Photos' if self.favorite else '') + (' · Live Photo' if self.live else '') +
                (' · Edited in Photos (original listed)' if self.edited else '') +
                f'\nOriginal: {format_size(self.size)} · {self.width or "?"} × {self.height or "?"} · {self.date or "Date unknown"}' +
                f'\n{"Local original available" if self.local else "Original not local — select Download & view to preview it"}' +
                f'\nAsset ID: {self.uuid}')

def positive_int(value):
    try:
        result=int(value)
        return result if result>0 else None
    except (ValueError, TypeError):return None

def media_from_photo(photo):
    """Use database-reported original size; do not stat every iCloud asset."""
    path=photo.path
    try:
        thumbnail=next((str(p) for p in getattr(photo,'path_derivatives',[]) if Path(p).suffix.lower() in ('.jpeg','.jpg','.png','.heic','.tif','.tiff')),None)
    except OSError:thumbnail=None
    # osxphotos path is None when original is missing (per its documented API).
    return Media(uuid=str(photo.uuid), name=photo.original_filename or photo.filename or str(photo.uuid),
                 kind='Video' if photo.ismovie else 'Photo', size=positive_int(photo.original_filesize),
                 width=positive_int(photo.original_width), height=positive_int(photo.original_height),
                 date=photo.date.isoformat(timespec='seconds') if photo.date else '',
                 path=str(path) if path else None, local=bool(path),
                 live=bool(photo.live_photo), edited=bool(photo.hasadjustments),thumbnail=thumbnail,
                 favorite=bool(getattr(photo,'favorite',False)))

def resolve_library(library):
    if not library:return None
    path=Path(library).expanduser()
    if not path.is_dir():raise ValueError('Library folder does not exist: '+str(path))
    if path.name.lower().endswith('.photoslibrary'):return str(path)
    candidates=sorted(p for p in path.iterdir() if p.is_dir() and p.name.lower().endswith('.photoslibrary'))
    if len(candidates)==1:return str(candidates[0])
    if len(candidates)>1:raise ValueError('Several Photos libraries were found. Enter the full path of one .photoslibrary folder:\n'+'\n'.join(str(p) for p in candidates))
    raise ValueError('Choose the .photoslibrary bundle itself, not the Pictures folder. No Photos libraries were found in '+str(path))

def scan_library(library=None, progress=lambda *a:None, cancel=None, db_factory=None):
    cancel=cancel or threading.Event()
    if db_factory is None:
        try:import osxphotos
        except ImportError as e:raise RuntimeError('Photos browsing requires the updated installer (osxphotos).') from e
        db_factory=osxphotos.PhotosDB
        library=resolve_library(library)
    if cancel.is_set():raise ScanCancelled()
    db=db_factory(str(Path(library).expanduser()) if library else None)
    if cancel.is_set():raise ScanCancelled()
    rows=[]; photos=db.photos(images=True, movies=True, intrash=False)
    total=len(photos)
    for index,photo in enumerate(photos,1):
        if cancel.is_set():raise ScanCancelled()
        rows.append(media_from_photo(photo))
        if index%250==0 or index==total:progress(index,total)
    return rows,str(db.library_path)

def view(rows, kind='All', search='', min_mb=0, min_mp=0, min_width=0, min_height=0,
         sort='size', descending=True):
    text=search.casefold().strip()
    selected=[]
    for r in rows:
        if kind=='Photos' and r.kind!='Photo':continue
        if kind=='Videos' and r.kind!='Video':continue
        if text and text not in (r.name+' '+r.date+' '+r.uuid).casefold():continue
        if min_mb and (r.size is None or r.size<min_mb*1024**2):continue
        if min_mp and (r.pixels is None or r.pixels<min_mp*1e6):continue
        if min_width and (r.width is None or r.width<min_width):continue
        if min_height and (r.height is None or r.height<min_height):continue
        selected.append(r)
    key=lambda r: getattr(r,sort)
    known=[r for r in selected if key(r) is not None]
    unknown=[r for r in selected if key(r) is None]
    # Numeric values remain numeric; unknown values are last in either direction.
    return sorted(known,key=key,reverse=descending)+unknown

def format_size(size):return f'{size/1024**2:,.1f} MiB' if size is not None else 'Unknown'

def ensure_outside_library(destination, library):
    dest=Path(destination).expanduser().resolve()
    lib=Path(library).expanduser().resolve()
    if dest==lib or lib in dest.parents or any(p.lower().endswith('.photoslibrary') for p in dest.parts):
        raise RuntimeError('Choose a folder outside your Photos library.')
    return dest

def copy_video(media, destination, library, cancel=None, report=None):
    """Copy bytes, not hard links, from a managed or referenced original.

    Temp + exclusive publish prevents collisions and cancellation leaves no output.
    The caller can subsequently queue the independent copy for compression.
    """
    if media.kind!='Video':raise RuntimeError('Only videos can enter the conversion queue.')
    if not media.path:raise RuntimeError('Original is not local. Download/export it from Photos first.')
    source=Path(media.path)
    if not source.is_file():raise RuntimeError('Original is no longer local. Download/export it in Photos and scan again.')
    cancel=cancel or threading.Event()
    if cancel.is_set():raise ScanCancelled()
    dest=ensure_outside_library(destination,library);dest.mkdir(parents=True,exist_ok=True)
    before=source.stat()
    filename=re.sub(r'[/\\\x00-\x1f]', '_', media.name).strip() or 'video'
    if filename in ('.','..'):filename='video'
    name=Path(filename)
    # The copied file has the actual source container, even if a display filename was renamed.
    suffix=source.suffix or name.suffix
    fd,tmp=tempfile.mkstemp(prefix='.videoqueue-copy-',suffix=suffix,dir=dest)
    tmp=Path(tmp)
    try:
        copied=0;last_percent=-1
        with os.fdopen(fd,'wb') as output, source.open('rb') as inp:
            while True:
                if cancel.is_set():raise ScanCancelled()
                chunk=inp.read(4*1024**2)
                if not chunk:break
                output.write(chunk)
                copied+=len(chunk)
                percent=min(99,int(copied*100/before.st_size)) if before.st_size else 0
                if report and percent!=last_percent:report(f'Copying original: {percent}%');last_percent=percent
        after=source.stat()
        if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
            raise RuntimeError('Original changed during copying. Please scan and retry.')
        shutil.copystat(source,tmp)
        if cancel.is_set():raise ScanCancelled()
        n=0
        while True:
            target=dest/(name.stem+(f'_{n}' if n else '')+suffix)
            try:os.link(tmp,target);break
            except FileExistsError:n+=1
        if report:report('Copying original: 100%')
        return str(target)
    finally:tmp.unlink(missing_ok=True)
