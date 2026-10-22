"""Remove app-owned media caches after workers stop, preserving recovery files."""
import fcntl, os
from pathlib import Path

class StorageLock:
    """One running app owns the shared workflow and its temporary storage."""
    def __init__(self,data):
        self.handle=(Path(data)/'.app.lock').open('a+')
        try:fcntl.flock(self.handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close();raise RuntimeError('Video Queue is already open. Use its existing window, or close it before reopening.')
    def close(self):
        fcntl.flock(self.handle.fileno(),fcntl.LOCK_UN);self.handle.close()

def clean_storage(data,jobs,delete_unsent=False):
    data=Path(data).expanduser().resolve();errors=[];removed=set();protected=set();outputs=set();roots={data/'Previews',data/'Thumbnails',data/'Converted previews',data/'Temporary work'/'Original backups',data/'Photos work'/'Original backups'}
    def canonical(value):return Path(os.path.abspath(Path(value).expanduser())) if value else None
    def safe_path(path):
        return not any(part.lower().endswith('.photoslibrary') for part in path.parts) and not any(parent.is_symlink() for parent in (path,*path.parents))
    for job in jobs:
        media=job.get('media');backup=canonical(job.get('backup'))
        if media and backup and backup.parent.parent.name=='Original backups' and backup.parent.parent.parent.name in ('Video Queue Work','Photos work','Temporary work'):
            roots.add(backup.parent.parent)
        if not media and job.get('path'):protected.add(canonical(job['path']))
        uncertain=not job.get('retry_safe',True)
        if media and (job.get('original_removed') or uncertain):
            usable=False
            if backup:
                protected.add(backup)
                try:
                    stat=backup.stat();usable=stat.st_size>0 and [stat.st_size,stat.st_mtime_ns]==job.get('backup_fingerprint')
                except OSError:pass
            if not usable and media.path:protected.add(canonical(media.path))
        output=canonical(job.get('output'))
        if output:outputs.add(output)
        if output and (uncertain or (job.get('converted_removed') or not job.get('verified')) and not delete_unsent):protected.add(output)
    def remove_tree(path):
        if path in protected or path in outputs:return
        if not safe_path(path):errors.append(f'Kept unexpected symbolic link or Photos library: {path}');return
        try:
            if path.is_dir():
                for child in path.iterdir():remove_tree(child)
                if not any(path.iterdir()):path.rmdir()
            elif path.is_file():path.unlink();removed.add(path)
        except OSError as error:errors.append(f'{path}: {error}')
    for root in sorted(roots,key=str):
        if root.exists() or root.is_symlink():remove_tree(root)
    for job in jobs:
        output=canonical(job.get('output'))
        if not output or output in protected or not safe_path(output):continue
        # Saved workflow files identify generated outputs; never remove an input
        # or a file replaced/edited since this app verified it.
        media=job.get('media');source=canonical(media.path if media else job.get('path'))
        if source==output:continue
        try:
            if not output.is_file():continue
            if source and source.is_file() and os.path.samefile(output,source):continue
            stat=output.stat()
            if [stat.st_size,stat.st_mtime_ns]!=job.get('output_fingerprint'):continue
            output.unlink();removed.add(output)
        except OSError as error:errors.append(f'{output}: {error}')
    for job in jobs:
        backup=canonical(job.get('backup'));output=canonical(job.get('output'));media=job.get('media')
        if backup in removed:job.update(backup=None,backup_fingerprint=None)
        if media and canonical(media.path) in removed:
            from dataclasses import replace
            job['media']=replace(media,path=None,local=False)
        if output in removed:
            job.update(output=None,output_fingerprint=None,local_output_cleared=True)
            if not job.get('verified'):
                job.update(converted=False,expected=None,state='Waiting',progress='')
        job['cleanup_retained']=bool((canonical(job.get('backup')) in protected and job.get('backup')) or (canonical(job.get('output')) in protected and not job.get('retry_safe',True)))
    return errors
