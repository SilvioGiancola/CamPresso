"""One sequential job pipeline for Photos originals and external files."""
import json, sys, subprocess, threading, uuid, math
from dataclasses import replace
from pathlib import Path
from .photos_download import fetch_video
from .photos_catalog import ScanCancelled, ensure_outside_library
from .engine import Converter, Cancelled
from .workflow_logic import file_matches, require_system_library

class JobFailure(RuntimeError):
    """Failure metadata for the GUI, without inferring recovery from message text."""
    def __init__(self,message,output=None,journal=None,retry_safe=True):
        super().__init__(message)
        self.output=output;self.journal=journal;self.retry_safe=retry_safe

class JobRunner:
    def __init__(self,workspace):
        self.workspace=Path(workspace).expanduser().resolve()
        if any(p.lower().endswith('.photoslibrary') for p in self.workspace.parts):raise RuntimeError('Choose an output folder outside the Photos library bundle.')
        self.workspace.mkdir(parents=True,exist_ok=True)
        self.cancel=threading.Event();self.converter=None
    def stop(self):
        self.cancel.set()
        if self.converter:self.converter.stop()
    def actions(self,request):
        request=dict(request);request['journal']=request.get('journal') or str(self.workspace/'journals'/(uuid.uuid4().hex+'.json'))
        def failure(message):
            return JobFailure(message+'\nJournal: '+request['journal'],journal=request['journal'],retry_safe=request.get('action') in ('check','verify','inspect'))
        # Let an in-flight Photos transaction finish; do not kill while library writes.
        try:
            p=subprocess.run([sys.executable,'-m','video_queue.photos_actions'],input=json.dumps(request,allow_nan=False),capture_output=True,text=True,errors='replace')
        except (OSError,ValueError) as error:raise failure('Photos helper could not start: '+str(error)) from error
        try:
            result=json.loads(p.stdout.strip().splitlines()[-1])
            if not isinstance(result,dict):raise ValueError('Expected a Photos result object')
        except (ValueError,IndexError) as error:
            raise failure('Photos helper failed: '+(p.stderr or p.stdout)[-4000:]) from error
        if p.returncode or result.get('error'):raise failure(str(result.get('error') or 'Photos rejected the change'))
        if request.get('action') in ('check','inspect') and result.get('checked') is not True:
            raise failure('Photos helper did not confirm access.')
        if request.get('action')=='inspect' and any(not isinstance(result.get(key),bool) for key in ('original_present','converted_present')):raise failure('Photos helper did not confirm asset presence.')
        if request.get('action') in ('finish','remove','verify'):
            removing=bool(request.get('remove') or request.get('action')=='remove')
            expected_phase='Original moved to Recently Deleted' if removing else 'Import verified'
            if not result.get('new_uuid') or result.get('phase')!=expected_phase or result.get('removed') is not removing:
                raise failure('Photos helper did not confirm the requested import/removal outcome.')
        if request.get('action') in ('delete_original','delete_converted'):
            converted=request['action']=='delete_converted'
            phase='Converted moved to Recently Deleted' if converted else 'Original moved to Recently Deleted'
            identifier=request.get('new_uuid') if converted else request.get('uuid')
            removed_id=result.get('removed_uuid')
            same=isinstance(removed_id,str) and isinstance(identifier,str) and removed_id.split('/')[0].upper()==identifier.split('/')[0].upper()
            if result.get('phase')!=phase or not same or result.get('converted_removed') is not converted or result.get('removed') is not (not converted):raise failure('Photos did not confirm removal of the selected version.')
        result['journal']=request['journal']
        return result
    def delete_version(self,job,converted=False):
        """Keep a verified independent local copy and delete exactly one Photos ID."""
        if self.cancel.is_set():raise Cancelled()
        media=job.get('media');self.converter=Converter()
        if converted:
            if not job.get('new_uuid') or not job.get('verified') or job.get('converted_removed'):raise RuntimeError('No verified converted copy is present in Photos.')
            local=Path(job.get('output') or '');fingerprint=job.get('output_fingerprint')
        else:
            if not media or job.get('original_removed'):raise RuntimeError('No original Photos asset is available for removal.')
            if not media.uuid or Path(media.uuid).name!=media.uuid or media.uuid in ('.','..'):raise RuntimeError('Invalid original Photos asset identifier.')
            local=Path(job.get('backup') or '')
            if not local.is_file() or job.get('backup_fingerprint')!=[local.stat().st_size,local.stat().st_mtime_ns]:
                if not media.path or not Path(media.path).is_file():raise RuntimeError('Download the original before removing it from Photos.')
                try:local=Path(fetch_video(media,self.workspace/'Original backups'/media.uuid,job['library'],self.cancel))
                except ScanCancelled:raise Cancelled()
                stat=local.stat();job.update(backup=str(local),backup_fingerprint=[stat.st_size,stat.st_mtime_ns])
            fingerprint=job.get('backup_fingerprint')
        if not local.is_file() or local.is_symlink():raise RuntimeError('A local independent video copy is required.')
        if any(part.lower().endswith('.photoslibrary') for part in local.resolve().parts):raise RuntimeError('Keep the local video copy outside Photos.')
        stat=local.stat()
        if fingerprint!=[stat.st_size,stat.st_mtime_ns] or not stat.st_size:raise RuntimeError('The local video copy changed. Photos asset kept.')
        expected=self.expected_video(local)
        if converted and expected!=job.get('expected'):raise RuntimeError('Converted video no longer matches the verified copy.')
        if self.cancel.is_set():raise Cancelled()
        return self.actions(dict(action='delete_converted' if converted else 'delete_original',library=job.get('library'),
            uuid=media.uuid if media else None,new_uuid=job.get('new_uuid'),output=job.get('output'),backup=job.get('backup'),
            expected=expected,output_fingerprint=job.get('output_fingerprint'),backup_fingerprint=job.get('backup_fingerprint'),journal=job.get('journal')))
    def expected_video(self,path):
        info=self.converter.probe(path)
        video=next(s for s in info['streams'] if s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic'))
        expected=dict(duration=float(info['format']['duration']),width=video['width'],height=video['height'])
        if not math.isfinite(expected['duration']) or expected['duration']<=0 or any(not isinstance(expected[k],int) or expected[k]<=0 for k in ('width','height')):
            raise RuntimeError('Converted video has invalid duration or dimensions.')
        return expected
    def publish(self,job,remove=False,verify=False):
        """Use the existing converted file; never encode again or duplicate an upload."""
        if self.cancel.is_set():raise Cancelled()
        output=Path(job.get('output') or '')
        if not output.is_file() or not output.stat().st_size:raise RuntimeError('Converted video is missing. Convert it again before sending to Photos.')
        if any(part.lower().endswith('.photoslibrary') for part in output.resolve().parts):raise RuntimeError('Keep the converted file outside the Photos library.')
        stat=output.stat();fingerprint=job.get('output_fingerprint')
        if not fingerprint or fingerprint!=[stat.st_size,stat.st_mtime_ns]:raise RuntimeError('Converted video changed since verification. Convert it again before sending to Photos.')
        if not job.get('expected'):raise RuntimeError('Convert and verify the video before changing Photos.')
        if remove and not job.get('media'):raise RuntimeError('Only Photos originals can be removed here.')
        if remove:
            backup=Path(job.get('backup') or '')
            if not backup.is_file() or not backup.stat().st_size:raise RuntimeError('Original backup is missing. Original kept.')
            if backup.is_symlink():raise RuntimeError('An independent original backup is required. Original kept.')
            ensure_outside_library(backup,job['library'])
            backup_stat=backup.stat()
            if job.get('backup_fingerprint')!=[backup_stat.st_size,backup_stat.st_mtime_ns]:raise RuntimeError('Original backup changed after conversion. Original kept.')
            if stat.st_size>=backup.stat().st_size:raise RuntimeError('Converted video is not smaller. Original kept.')
        self.converter=Converter();actual=self.expected_video(output);expected=job['expected']
        if actual!=expected:raise RuntimeError('Converted video no longer matches its verified duration and dimensions.')
        if self.cancel.is_set():raise Cancelled()
        action='verify' if verify else 'remove' if remove and job.get('new_uuid') else 'finish'
        if action=='finish' and job.get('new_uuid') and not job.get('converted_removed'):raise RuntimeError('This video already has a Photos asset. Use Check Photos to verify it.')
        media=job.get('media')
        request=dict(action=action,library=job.get('library'),uuid=media.uuid if media else None,
            new_uuid=job.get('new_uuid'),output=str(output),backup=job.get('backup'),remove=remove,
            expected=expected,output_fingerprint=job.get('output_fingerprint'),backup_fingerprint=job.get('backup_fingerprint'),journal=job.get('journal'))
        if remove:request['original_expected']=self.expected_video(backup)
        if job.get('original_removed') and action=='finish':request['uuid']=None
        if job.get('converted_removed') and action=='finish':request['new_uuid']=None
        return self.actions(request)
    def replace_reviewed(self,job,report=lambda text:None):
        """Replace only the exact output approved in the review UI."""
        try:
            require_system_library(job.get('library'))
            if self.cancel.is_set():raise Cancelled()
            approval=job.get('replacement_approval') or {}
            media=job.get('media')
            if not media or job.get('original_removed'):raise RuntimeError('The original is no longer available for replacement.')
            if any(approval.get(key)!=value for key,value in (
                ('output',job.get('output')),('fingerprint',job.get('output_fingerprint')),
                ('expected',job.get('expected')),('original_uuid',media.uuid),('new_uuid',job.get('new_uuid')))):
                raise RuntimeError('The reviewed video changed. Review and approve it again.')
            if not file_matches(job.get('output'),approval.get('fingerprint')):
                raise RuntimeError('The compressed file changed. Compress and review it again.')
            if not file_matches(job.get('backup'),job.get('backup_fingerprint')):
                report('Preparing original for replacement')
                # A stale/changed working copy must not become the backup used
                # to authorize removal. Fetch the exact Photos original again.
                path=fetch_video(replace(media,path=None,local=False),self.workspace/'Original backups'/media.uuid,job['library'],self.cancel,report)
                stat=Path(path).stat();job.update(backup=path,backup_fingerprint=[stat.st_size,stat.st_mtime_ns])
            self.converter=Converter()
            job['source_size']=Path(job['backup']).stat().st_size
            if job['source_size'] <= Path(job['output']).stat().st_size:
                raise RuntimeError('The compressed video is not smaller. Original kept.')
        except (Cancelled, ScanCancelled) as error:
            if isinstance(error,ScanCancelled):error=Cancelled()
            error.retry_safe=True
            raise error
        except Exception as error:raise JobFailure(str(error),retry_safe=True) from error
        # Native transactions journal their intent and placeholder ID before commit.
        # Any ambiguous failure beyond this point must be reconciled before retry.
        try:return self.publish(job,remove=True)
        except JobFailure:raise
        except Cancelled as error:
            error.retry_safe=True;raise
        except Exception as error:raise JobFailure(str(error),retry_safe=True) from error
    def run(self,job,settings,report):
        if self.cancel.is_set():raise Cancelled()
        self.converter=Converter();media=job.get('media');library=job.get('library')
        if job['import']:
            report('Checking Photos access',None)
            self.actions(dict(action='check',library=library,uuid=media.uuid if media else None))
        if media:
            if job['remove'] and not job['import']:raise RuntimeError('Removal requires importing the verified replacement.')
            if not media.uuid or Path(media.uuid).name!=media.uuid or media.uuid in ('.','..'):
                raise RuntimeError('Invalid Photos asset ID. Scan the library again.')
            if job.get('require_download') and (not media.path or not Path(media.path).is_file()):
                raise RuntimeError('Download this video before converting it.')
            originalfolder=self.workspace/'Original backups'/media.uuid
            report('Downloading',None)
            if job.get('workflow_managed') and file_matches(job.get('backup'),job.get('backup_fingerprint')):
                source=job['backup']
                ensure_outside_library(source,library)
            else:
                try:
                    source=fetch_video(media,originalfolder,library,self.cancel,lambda text:report('Downloading',None,text))
                except ScanCancelled:raise Cancelled()
            if self.cancel.is_set():raise Cancelled()
            options=dict(settings,replace=False,folder=settings['folder'])
        else:
            source=job['path'];options=dict(settings,replace=job['replace'])
        if not options.get('folder') and not options['replace']:options['folder']=str(self.workspace/'Converted')
        source_stat=Path(source).stat()
        job.update(backup=str(source),backup_fingerprint=[source_stat.st_size,source_stat.st_mtime_ns],source_size=source_stat.st_size)
        output,detail=self.converter.convert(source,options,lambda state,p:report('Converted / verified' if state=='Completed' else state,p*.9 if job['import'] else p))
        stat=Path(output).stat()
        job.update(output=output,output_fingerprint=[stat.st_size,stat.st_mtime_ns],result_size=stat.st_size)
        import_attempted=False
        try:
            expected=self.expected_video(output);stat=Path(output).stat()
            backup_stat=Path(source).stat()
            job.update(backup=str(source),backup_fingerprint=[backup_stat.st_size,backup_stat.st_mtime_ns],expected=expected,output_fingerprint=[stat.st_size,stat.st_mtime_ns],result_size=stat.st_size)
            if job['import']:
                if self.cancel.is_set():
                    raise Cancelled('Stopped before Photos import. Converted file kept at '+output+'; Photos original kept.')
                report('Adding to Photos',None)
                import_attempted=True
                result=self.actions(dict(action='finish',library=library,uuid=media.uuid if media else None,output=output,backup=source,remove=job['remove'],expected=expected))
                detail+='\nPhotos: '+result['phase']+'\nNew asset ID: '+result['new_uuid']
                if media:detail+='\nOriginal backup: '+source
            report('Completed',100)
        except Cancelled as error:
            error.output=output;error.retry_safe=not job['replace'];raise
        except Exception as error:
            message=str(error)+'\nConverted file retained: '+output
            if import_attempted:message+='\nCheck Photos and the journal before retrying; an import may already exist.'
            elif job['replace']:message+='\nThe external file was already replaced; review its backup before re-adding this job.'
            raise JobFailure(message,output,getattr(error,'journal',None),retry_safe=not import_attempted and not job['replace']) from error
        return output,detail
