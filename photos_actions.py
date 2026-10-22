"""Public PhotoKit helper: verify selected assets and retained local copies.

Runs in a separate process. Only the System Photo Library is modified. Each
request is journalled; deletion requires a verified local copy and an explicit
choice of version. Import failures never trigger deletion.
"""
import json, math, os, sys, tempfile, threading, time
from pathlib import Path

class Actions:
    def __init__(self,library,on_import_created=None):
        from osxphotos.utils import get_system_library_path
        import Photos
        from Foundation import NSRunLoop, NSDate, NSURL
        self.P=Photos;self.loop=NSRunLoop.currentRunLoop();self.Date=NSDate;self.URL=NSURL
        self.on_import_created=on_import_created
        system=get_system_library_path()
        if not system or library and Path(system).expanduser().resolve()!=Path(library).expanduser().resolve():
            raise RuntimeError('Select the System Photo Library for iCloud downloads, sending videos to Photos, or removing either version. Local videos in other libraries can be converted and kept locally.')
        self.lib=Photos.PHPhotoLibrary.sharedPhotoLibrary()
        status=Photos.PHPhotoLibrary.authorizationStatusForAccessLevel_(Photos.PHAccessLevelReadWrite)
        if status==Photos.PHAuthorizationStatusNotDetermined:
            done=threading.Event();answer=[]
            def authorized(status):
                answer.append(status)
                done.set()
            Photos.PHPhotoLibrary.requestAuthorizationForAccessLevel_handler_(Photos.PHAccessLevelReadWrite,authorized)
            self.wait(done,180);status=answer[0]
        if status!=Photos.PHAuthorizationStatusAuthorized:
            raise RuntimeError('Full Photos access is required to download, inspect, import or remove assets. Allow access in System Settings → Privacy & Security → Photos.')
    def wait(self,event,timeout):
        deadline=time.monotonic()+timeout
        while not event.is_set():
            if time.monotonic()>deadline:raise RuntimeError('Photos operation timed out. Inspect Photos before retrying.')
            self.loop.runUntilDate_(self.Date.dateWithTimeIntervalSinceNow_(.1))
    def change(self,block):
        event=threading.Event();result=[];block_errors=[]
        # Native void blocks must return None. Never let a Python exception cross
        # the Objective-C callback boundary: PyObjC can terminate the helper.
        def changes():
            try:
                block()
            except Exception as error:
                block_errors.append(error)
        def completed(success,error):
            result.append((success,error))
            event.set()
        self.lib.performChanges_completionHandler_(changes,completed)
        self.wait(event,600)
        if block_errors:
            raise RuntimeError('Photos change request failed: '+str(block_errors[0])+'. Check Photos and the journal before retrying.') from block_errors[0]
        if not result[0][0]:raise RuntimeError(str(result[0][1]) if result[0][1] is not None else 'Photos rejected the change')
    def find(self,uuid,missing_ok=False):
        # Public API: exact UUID matching, never filename or fuzzy matches.
        options=self.P.PHFetchOptions.alloc().init();options.setIncludeHiddenAssets_(True)
        assets=(self.P.PHAsset.fetchAssetsWithLocalIdentifiers_options_([uuid],options) if '/' in uuid
                else self.P.PHAsset.fetchAssetsWithOptions_(options))
        matches=[assets.objectAtIndex_(i) for i in range(assets.count()) if str(assets.objectAtIndex_(i).localIdentifier()).split('/')[0].upper()==uuid.split('/')[0].upper()]
        if not matches and missing_ok:return None
        if len(matches)!=1:raise RuntimeError('The exact original Photos asset was not found uniquely. Original kept.')
        return matches[0]
    def check(self,uuid):
        old=self.find(uuid)
        if old.mediaType()!=self.P.PHAssetMediaTypeVideo:raise RuntimeError('Selected Photos asset is not a video.')
        return old
    def import_video(self,path,old=None):
        placeholders=[]
        albums=[]
        if old:
            fetch=self.P.PHAssetCollection.fetchAssetCollectionsContainingAsset_withType_options_(old,self.P.PHAssetCollectionTypeAlbum,None)
            for i in range(fetch.count()):
                album=fetch.objectAtIndex_(i)
                if album.canPerformEditOperation_(self.P.PHCollectionEditOperationAddContent):albums.append(album)
        def block():
            request=self.P.PHAssetChangeRequest.creationRequestForAssetFromVideoAtFileURL_(self.URL.fileURLWithPath_(str(path)))
            if request is None:raise RuntimeError('Photos could not create a video import request')
            placeholder=request.placeholderForCreatedAsset()
            if placeholder is None:raise RuntimeError('Photos did not return an imported asset placeholder')
            identifier=str(placeholder.localIdentifier());placeholders.append(identifier)
            if self.on_import_created:self.on_import_created(identifier)
            if old:
                request.setCreationDate_(old.creationDate());request.setLocation_(old.location())
                request.setFavorite_(old.isFavorite());request.setHidden_(old.isHidden())
            for album in albums:
                change=self.P.PHAssetCollectionChangeRequest.changeRequestForAssetCollection_(album)
                if change:change.addAssets_([placeholder])
        self.change(block)
        if not placeholders:raise RuntimeError('Photos did not return an imported asset ID')
        return self.find(placeholders[0])
    def remove(self,old):
        def block():
            self.P.PHAssetChangeRequest.deleteAssets_([old])
        self.change(block)

def workflow(request,adapter,journal,checkpoint=lambda:None):
    """Dependency-injected safety checks also exercised on Linux."""
    if request.get('action') not in ('check','finish','remove','verify','inspect','delete_original','delete_converted'):raise RuntimeError('Unknown Photos action.')
    if request['action']=='inspect':
        original=adapter.find(request['uuid'],missing_ok=True) if request.get('uuid') else None
        converted=adapter.find(request['new_uuid'],missing_ok=True) if request.get('new_uuid') else None
        if original and original.mediaType()!=adapter.P.PHAssetMediaTypeVideo:raise RuntimeError('Recorded original is not a video.')
        if converted:verify_asset(converted,request['expected'],adapter)
        journal.update(phase='Photos inspected',original_present=bool(original),converted_present=bool(converted));checkpoint()
        return dict(checked=True,original_present=bool(original),converted_present=bool(converted),new_uuid=request.get('new_uuid'))
    if request['action'] in ('delete_original','delete_converted'):
        converted=request['action']=='delete_converted'
        identifier=request.get('new_uuid') if converted else request.get('uuid')
        if not identifier:raise RuntimeError('No recorded Photos asset is available for removal.')
        if converted and request.get('uuid') and identifier.split('/')[0].upper()==request['uuid'].split('/')[0].upper():raise RuntimeError('Original and converted identifiers must be different.')
        local=Path(request['output'] if converted else request['backup'])
        if not local.is_file() or not local.stat().st_size or local.is_symlink() or any(part.lower().endswith('.photoslibrary') for part in local.resolve().parts):raise RuntimeError('Keep an independent local video copy before removing it from Photos.')
        check_file(local,request.get('output_fingerprint') if converted else request.get('backup_fingerprint'),'Local video copy')
        asset=adapter.check(identifier);verify_asset(asset,request['expected'],adapter)
        journal['phase']='Converted removal pending' if converted else 'Original removal pending';checkpoint()
        check_file(local,request.get('output_fingerprint') if converted else request.get('backup_fingerprint'),'Local video copy')
        adapter.remove(asset)
        journal['phase']='Converted moved to Recently Deleted' if converted else 'Original moved to Recently Deleted'
        journal['removed_uuid']=str(asset.localIdentifier());checkpoint()
        return dict(new_uuid=request.get('new_uuid'),removed=not converted,converted_removed=converted,removed_uuid=journal['removed_uuid'],phase=journal['phase'])
    if request['action']=='verify':
        imported=adapter.check(request['new_uuid'])
        verify_asset(imported,request['expected'],adapter)
        journal.update(new_uuid=str(imported.localIdentifier()),phase='Import verified');checkpoint()
        return {'new_uuid':journal['new_uuid'],'removed':False,'phase':'Import verified'}
    old=adapter.check(request['uuid']) if request.get('uuid') else None
    journal['original_uuid']=request.get('uuid');journal['phase']='Original checked'
    if request['action']=='check':return {'checked':True}
    expected=request['expected']
    duration=float(expected['duration'])
    if not math.isfinite(duration) or duration<=0 or any(not isinstance(expected[k],int) or expected[k]<=0 for k in ('width','height')):
        raise RuntimeError('Invalid expected video duration or dimensions. Original kept.')
    output=Path(request['output'])
    if not output.is_file() or output.stat().st_size==0:raise RuntimeError('Converted file is missing or empty. Original kept.')
    check_file(output,request.get('output_fingerprint'),'Converted file')
    removing=request.get('remove') or request['action']=='remove'
    if removing:
        backup=Path(request['backup'])
        if not old or not backup.is_file() or backup.stat().st_size==0:raise RuntimeError('Original backup is missing. Original kept.')
        if backup.is_symlink() or any(part.lower().endswith('.photoslibrary') for part in backup.resolve().parts):raise RuntimeError('An independent backup outside Photos is required. Original kept.')
        check_file(backup,request.get('backup_fingerprint'),'Original backup')
        if output.stat().st_size>=backup.stat().st_size:raise RuntimeError('Converted video is not smaller. Original kept.')
    if request['action']=='remove':
        if not request.get('new_uuid') or request['new_uuid'].split('/')[0].upper()==request['uuid'].split('/')[0].upper():
            raise RuntimeError('A separate verified converted asset is required before removal.')
        imported=adapter.check(request['new_uuid'])
    else:
        # Persist intent before native work; a process crash cannot erase this stage.
        journal['phase']='Import pending';checkpoint()
        imported=adapter.import_video(output,old)
    journal['new_uuid']=str(imported.localIdentifier());journal['phase']='Imported';checkpoint()
    verify_asset(imported,expected,adapter)
    journal['phase']='Import verified';checkpoint()
    if removing:
        # User chose removal for this exact UUID. Photos may show native confirmation.
        check_file(output,request.get('output_fingerprint'),'Converted file')
        check_file(backup,request.get('backup_fingerprint'),'Original backup')
        journal['phase']='Removal pending';checkpoint()
        adapter.remove(old);journal['phase']='Original moved to Recently Deleted'
        checkpoint()
    return {'new_uuid':journal['new_uuid'],'removed':bool(removing),'phase':journal['phase']}

def check_file(path,fingerprint,label):
    if fingerprint is not None:
        stat=path.stat()
        if [stat.st_size,stat.st_mtime_ns]!=fingerprint:raise RuntimeError(label+' changed since verification. Original kept.')

def verify_asset(imported,expected,adapter):
    duration=float(expected['duration'])
    if not math.isfinite(duration) or duration<=0 or any(not isinstance(expected[k],int) or expected[k]<=0 for k in ('width','height')):
        raise RuntimeError('Invalid expected video duration or dimensions. Original kept.')
    if imported.mediaType()!=adapter.P.PHAssetMediaTypeVideo:raise RuntimeError('Recorded Photos asset is not a video. Asset kept.')
    imported_duration=float(imported.duration())
    if not math.isfinite(imported_duration) or imported_duration<=0 or abs(imported_duration-duration)>max(.5,duration*.005):raise RuntimeError('Photos video duration differs from the local copy. Asset kept.')
    if sorted((imported.pixelWidth(),imported.pixelHeight()))!=sorted((expected['width'],expected['height'])):raise RuntimeError('Photos video dimensions differ from the local copy. Asset kept.')

def save_journal(target,journal):
    """Publish a complete, flushed journal rather than truncating its old contents."""
    target=Path(target).expanduser().resolve()
    if any(part.lower().endswith('.photoslibrary') for part in target.parts):
        raise RuntimeError('Keep the recovery journal outside the Photos library bundle.')
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+target.name+'-',dir=target.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as output:
            json.dump(journal,output,indent=2,allow_nan=False)
            output.flush();os.fsync(output.fileno())
        os.replace(name,target)
    finally:
        Path(name).unlink(missing_ok=True)

def main():
    journal={};target=None
    def checkpoint():save_journal(target,journal)
    def created(identifier):
        journal['new_uuid']=identifier
        checkpoint()
    try:
        request=json.load(sys.stdin);target=Path(request['journal'])
        journal.update({key:request.get(key) for key in ('action','library','uuid','new_uuid','output','backup','remove','expected','output_fingerprint','backup_fingerprint')})
        journal['phase']='Checking Photos access';checkpoint()
        if request.get('action') not in ('check','finish','remove','verify','inspect','delete_original','delete_converted'):raise RuntimeError('Unknown Photos action.')
        result=workflow(request,Actions(request.get('library'),created),journal,checkpoint)
        checkpoint()
        print(json.dumps(result))
    except Exception as e:
        journal['error']=str(e)
        if target is not None:
            try:checkpoint()
            except Exception as write_error:journal['journal_error']=str(write_error)
        print(json.dumps({'error':str(e),'journal':journal}));sys.exit(1)

if __name__=='__main__':main()
