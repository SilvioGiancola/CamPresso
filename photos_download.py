"""Download exactly one selected Photos original through OSXPhotos/PhotoKit.

Runs in a cancellable subprocess; downloads publish outside Photos only after
validation. Library scanning itself never initiates any downloads.
"""
import os, subprocess, sys, tempfile, time, json, queue, threading, math, shutil
from pathlib import Path
from dataclasses import replace
from photos_catalog import ScanCancelled, copy_video, ensure_outside_library

VIDEO_SUFFIXES={'.mov','.mp4','.m4v','.avi','.mkv','.mts','.m2ts','.mpg','.mpeg','.3gp','.3g2','.webm','.m2v'}

def command(media,library,stage,executable=None):
    if media.kind!='Video' or not media.uuid.strip():raise RuntimeError('Select a video with a valid Photos asset ID.')
    cli=Path(executable) if executable else Path(sys.executable).parent/'osxphotos'
    if not cli.is_file():raise RuntimeError('Photos download tool is missing. Run the updated installer.')
    return [str(cli),'export','--library',str(library),'--uuid',media.uuid,
            '--download-missing','--use-photos-export','--use-photokit',
            '--skip-edited','--skip-live','--skip-raw','--skip-bursts',str(stage)]

def wait_process(args,cancel,report,timeout=1800,popen=subprocess.Popen):
    """Capture diagnostics without pipe deadlocks and stop helper on cancellation."""
    started=time.monotonic();proc=None
    with tempfile.TemporaryFile(mode='w+') as log:
        try:
            proc=popen(args,stdout=log,stderr=subprocess.STDOUT,text=True)
            last=-1
            while proc.poll() is None:
                if cancel.is_set():raise ScanCancelled()
                elapsed=int(time.monotonic()-started)
                if elapsed>timeout:raise RuntimeError('Download timed out. Check iCloud connectivity and retry.')
                if elapsed//2!=last:
                    report(f'Downloading/exporting original… {elapsed}s. Accept the Photos-access prompt if macOS shows one.')
                    last=elapsed//2
                cancel.wait(.15)
            if cancel.is_set():raise ScanCancelled()
            log.seek(0);details=log.read()[-6000:]
            if proc.returncode:
                raise RuntimeError('Photos could not download/export the selected original.\n'+details+
                    '\nAllow Photos access when prompted, ensure this asset is in the accessible Photos library, and check your internet connection.')
            return details
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()

def validate_video(path,media,ffprobe=None):
    probe=ffprobe or str(Path('/opt/homebrew/bin/ffprobe') if Path('/opt/homebrew/bin/ffprobe').exists() else 'ffprobe')
    result=subprocess.run([probe,'-v','error','-show_streams','-of','json',str(path)],capture_output=True,text=True,timeout=60)
    if result.returncode:raise RuntimeError('Downloaded file could not be read: '+result.stderr[-1500:])
    data=json.loads(result.stdout)
    main=next((s for s in data['streams'] if s.get('codec_type')=='video' and not s.get('disposition',{}).get('attached_pic')),None)
    if main is None:raise RuntimeError('Photos export did not contain a playable video.')
    if media.width and media.height and sorted((main['width'],main['height']))!=sorted((media.width,media.height)):
        raise RuntimeError('Downloaded dimensions differ from the listed original. Scan again; the file was not queued.')

def resource_download(media,library,stage,cancel,report):
    """Keep native callbacks in a subprocess; cancellation stops its data request."""
    process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('photos_resource.py'))],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace')
    events=queue.Queue();diagnostics=[];complete=False;failure=None;started=time.monotonic();last=-1;reported=False
    def read():
        try:
            for line in process.stdout:events.put(line)
        finally:events.put(None)
    reader=threading.Thread(target=read,daemon=True)
    try:
        reader.start()
        process.stdin.write(json.dumps(dict(uuid=media.uuid,library=str(library),destination=str(stage)))+'\n');process.stdin.close()
        ended=False
        while not ended or process.poll() is None:
            if cancel.is_set():raise ScanCancelled()
            elapsed=int(time.monotonic()-started)
            if elapsed>1800:raise RuntimeError('Photos download timed out.')
            if not reported and elapsed//2!=last:
                report(f'Downloading original… {elapsed}s. Allow Photos access if prompted.');last=elapsed//2
            try:line=events.get(timeout=.1)
            except queue.Empty:continue
            if line is None:ended=True;continue
            try:packet=json.loads(line)
            except ValueError:diagnostics.append(line[-1000:]);diagnostics=diagnostics[-8:];continue
            if not isinstance(packet,dict):continue
            if packet.get('event')=='progress':
                percent=packet.get('percent')
                if isinstance(percent,(int,float)) and math.isfinite(percent) and 0<=percent<=100:
                    reported=True;report(f'Downloading original: {percent:.0f}%')
            elif packet.get('event')=='complete':complete=True
            elif packet.get('event')=='error':failure=packet.get('error')
        if cancel.is_set():raise ScanCancelled()
        if process.returncode or not complete:raise RuntimeError('Photos download failed: '+str(failure or ''.join(diagnostics) or 'No complete original received.'))
        return 'Original resource downloaded.'
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        reader.join(timeout=1)
        if process.stdout:process.stdout.close()
        if process.stdin and not process.stdin.closed:process.stdin.close()

def fetch_video(media,destination,library,cancel,report=lambda s:None,runner=wait_process,validator=validate_video,cli=None):
    if media.kind!='Video':raise RuntimeError('Only videos can enter the conversion queue.')
    if media.path and Path(media.path).is_file():
        report('Copying local original…')
        return copy_video(media,destination,library,cancel,report)
    if cancel.is_set():raise ScanCancelled()
    dest=ensure_outside_library(destination,library);dest.mkdir(parents=True,exist_ok=True)
    if media.size and shutil.disk_usage(dest).free < media.size*2 + 256*1024**2:
        raise RuntimeError('Not enough local storage to download and save this original. Free space and retry.')
    with tempfile.TemporaryDirectory(prefix='.videoqueue-download-',dir=dest) as temp:
        stage=Path(temp)
        if runner is wait_process and cli is None:details=resource_download(media,library,stage,cancel,report)
        else:
            args=command(media,library,stage,cli)
            details=runner(args,cancel,report)
        if cancel.is_set():raise ScanCancelled()
        candidates=[p for p in stage.rglob('*') if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES and not p.is_symlink()]
        if len(candidates)!=1:
            raise RuntimeError(f'Expected one original video, received {len(candidates)}.\n{details}')
        candidate=candidates[0]
        if not candidate.stat().st_size:raise RuntimeError('Photos returned an empty original file.')
        report('Checking downloaded original…')
        validator(candidate,media)
        downloaded=replace(media,path=str(candidate),local=True)
        # A full independent copy, exclusive naming, and cancellation checks.
        # Naming preserves the real exported container suffix.
        report('Saving downloaded original outside Photos…')
        return copy_video(downloaded,dest,library,cancel,report)
