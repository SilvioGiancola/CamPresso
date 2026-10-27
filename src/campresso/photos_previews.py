"""Local poster thumbnails and reusable original previews, outside Photos."""
import hashlib, json, os, shutil, tempfile
from dataclasses import replace
from pathlib import Path
from .photos_catalog import ScanCancelled, ensure_outside_library
from .photos_download import fetch_video, validate_video, wait_process

THUMBNAIL_SIZE=(176,100)

def checked_cache_root(path):
    path=Path(path).expanduser().resolve()
    if any(part.lower().endswith('.photoslibrary') for part in path.parts):
        raise RuntimeError('Keep preview downloads and thumbnails outside the Photos library.')
    return path

def asset_key(media,library):
    identity=[str(Path(library).expanduser().resolve()),media.uuid,media.size,media.width,media.height,media.name]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

class PreviewStore:
    def __init__(self,root,fetcher=fetch_video,validator=validate_video):
        self.root=checked_cache_root(root);self.fetcher=fetcher;self.validator=validator
        self.entries={}
    def entry(self,media,library):
        if not library:raise RuntimeError('Scan a Photos library before previewing a video.')
        ensure_outside_library(self.root,library)
        entry=self.root/asset_key(media,library)
        if entry.is_symlink():raise RuntimeError('Preview cache folders must not be symbolic links.')
        return entry
    def cached_path(self,media,library):
        if not library:return None
        try:
            entry=self.entry(media,library);manifest=entry/'preview.json'
            data=self.entries.get(str(entry))
            if data is None:
                data=json.loads(manifest.read_text());self.entries[str(entry)]=data
            filename=data['filename']
            if not isinstance(filename,str) or Path(filename).name!=filename or filename in ('.','..'):return None
            path=entry/filename
            if path.is_symlink() or path.resolve().parent!=entry.resolve():return None
            if path.is_file():
                stat=path.stat()
                if stat.st_size==data['size'] and data['size']>0 and ('mtime_ns' not in data or stat.st_mtime_ns==data['mtime_ns']):return str(path.resolve())
        except (OSError,ValueError,TypeError,KeyError,RuntimeError):pass
        return None
    def media_for_queue(self,media,library):
        path=self.cached_path(media,library)
        return replace(media,path=path,local=True) if path else media
    def validate(self,path,media,cancel):
        if self.validator is validate_video:self.validator(path,media,cancel=cancel)
        else:self.validator(path,media)
    def prepare(self,media,library,cancel,report=lambda message:None):
        if media.kind!='Video':raise RuntimeError('Select a video to download and play.')
        if cancel.is_set():raise ScanCancelled()
        cached=self.cached_path(media,library)
        if cached:
            report('Checking downloaded video…')
            try:self.validate(cached,media,cancel)
            except (RuntimeError,ValueError):
                # Keep the old video, but stop reusing an unreadable preview.
                entry=self.entry(media,library);self.entries.pop(str(entry),None)
                (entry/'preview.json').unlink(missing_ok=True)
                report('Downloaded video could not be read. Preparing a fresh copy…')
            else:
                if cancel.is_set():raise ScanCancelled()
                return cached
        if cancel.is_set():raise ScanCancelled()
        entry=self.entry(media,library);entry.mkdir(parents=True,exist_ok=True)
        path=None;temporary=None
        try:
            fetched=Path(self.fetcher(media,entry,library,cancel,report))
            if fetched.is_symlink() or fetched.resolve().parent!=entry.resolve():raise RuntimeError('Preview download returned a file outside its cache folder.')
            fetched=fetched.resolve()
            path=fetched
            report('Checking video before playback…');self.validate(path,media,cancel)
            if cancel.is_set():raise ScanCancelled()
            stat=path.stat()
            data={'filename':path.name,'size':stat.st_size,'mtime_ns':stat.st_mtime_ns,'uuid':media.uuid,'library':str(library)}
            fd,name=tempfile.mkstemp(prefix='.preview-',dir=entry);temporary=Path(name)
            with os.fdopen(fd,'w') as output:
                json.dump(data,output);output.flush();os.fsync(output.fileno())
            os.replace(temporary,entry/'preview.json');temporary=None
            self.entries[str(entry)]=data
            return str(path)
        except Exception:
            if path is not None:path.unlink(missing_ok=True)
            raise
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)

class ThumbnailStore:
    def __init__(self,root,previews,runner=wait_process):
        self.root=checked_cache_root(root);self.previews=previews;self.runner=runner
    def image(self,media,library,cancel):
        if cancel.is_set():raise ScanCancelled()
        if library:ensure_outside_library(self.root,library)
        poster=Path(media.thumbnail) if media.thumbnail else None
        source=poster if poster and poster.is_file() else None
        if source is None:
            path=self.previews.cached_path(media,library) or media.path
            source=Path(path) if path and Path(path).is_file() else None
        if source is None:return None
        stat=source.stat()
        fingerprint=[str(source.resolve()),stat.st_size,stat.st_mtime_ns,THUMBNAIL_SIZE]
        key=hashlib.sha256(json.dumps(fingerprint).encode()).hexdigest()
        target=self.root/(key+'.png')
        if target.is_file() and target.stat().st_size:return str(target)
        self.root.mkdir(parents=True,exist_ok=True)
        width,height=THUMBNAIL_SIZE
        fd,name=tempfile.mkstemp(prefix='.thumb-',suffix='.png',dir=self.root);os.close(fd)
        temporary=Path(name)
        try:
            ffmpeg=shutil.which('ffmpeg')
            if not ffmpeg:raise RuntimeError('FFmpeg is required for video thumbnails.')
            filters=f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x18202b,setsar=1'
            args=[ffmpeg,'-v','error','-nostdin','-y','-i',str(source),'-map','0:v:0','-frames:v','1','-vf',filters,'-c:v','png','-threads','1',str(temporary)]
            self.runner(args,cancel,lambda message:None,timeout=30)
            if cancel.is_set():raise ScanCancelled()
            if not temporary.stat().st_size:raise RuntimeError('No poster image was available.')
            os.replace(temporary,target)
            return str(target)
        finally:temporary.unlink(missing_ok=True)
