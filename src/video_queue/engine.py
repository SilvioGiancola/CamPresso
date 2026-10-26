"""Video Queue conversion engine. Originals are never touched until verification."""
import json, math, os, queue, shutil, subprocess, tempfile, threading, uuid
from pathlib import Path
from .quicktime_tracks import restore_track_dates, metadata_mismatches

class Cancelled(Exception): pass

class Skipped(RuntimeError):
    """A valid input that this compression workflow deliberately leaves alone."""
    pass

class Converter:
    def __init__(self):
        self.cancel = threading.Event()
        self.proc = None
        self.lock = threading.Lock()
        self.tools = {t: shutil.which(t) for t in ('ffmpeg', 'ffprobe', 'exiftool')}
        missing = [t for t, p in self.tools.items() if not p]
        if missing: raise RuntimeError('Missing tools: ' + ', '.join(missing))

    def stop(self):
        # The run loop checks this event and reaps its child. Never wait for a
        # subprocess lock on the GUI thread while confirming shutdown.
        self.cancel.set()

    def run(self, args, progress=None, duration=0):
        if self.cancel.is_set(): raise Cancelled()
        with tempfile.TemporaryFile(mode='w+') as err:
            with self.lock:
                self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=err, text=True)
                proc = self.proc
                if self.cancel.is_set(): proc.terminate()
            messages=queue.Queue();lines=[]
            def read_output():
                try:
                    for line in proc.stdout:messages.put(line)
                finally:messages.put(None)
            reader=threading.Thread(target=read_output,daemon=True);reader.start()
            try:
                while True:
                    if self.cancel.is_set():raise Cancelled()
                    try:line=messages.get(timeout=.1)
                    except queue.Empty:continue
                    if line is None:break
                    if progress and line.startswith('out_time_us='):
                        try:value=float(line.split('=')[1])
                        except ValueError:continue
                        if math.isfinite(value):progress(min(99, value / 1e6 / max(duration, .01) * 100))
                    elif not progress:lines.append(line)
                # A tool can close stdout before exiting. Keep cancellation usable.
                while proc.poll() is None:
                    if self.cancel.wait(.1):raise Cancelled()
                if self.cancel.is_set():raise Cancelled()
                if proc.returncode:
                    err.seek(0)
                    raise RuntimeError((err.read() + '\n' + ''.join(lines))[-6000:].strip() or f'Command failed ({proc.returncode})')
                return ''.join(lines)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
                reader.join(timeout=2)
                proc.stdout.close()
                with self.lock:self.proc=None

    def probe(self, path):
        return json.loads(self.run([self.tools['ffprobe'], '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))

    def metadata(self, path):
        tags = ['TrackCreateDate', 'MediaCreateDate', 'ModifyDate', 'TrackModifyDate', 'MediaModifyDate', 'CreateDate', 'CreationDate', 'DateTimeOriginal', 'GPSCoordinates', 'Title', 'Description', 'Comment', 'Artist', 'Copyright']
        data = json.loads(self.run([self.tools['exiftool'], '-j', '-G1', '-s', *['-' + t for t in tags], str(path)]))[0]
        return {k: v for k, v in data.items() if k != 'SourceFile'}

    def convert(self, source, settings, report):
        phase='Reading original'
        def staged_report(state, progress):
            nonlocal phase
            phase=state
            report(state,progress)
        try:
            return self._convert(source,settings,staged_report)
        except (Cancelled, Skipped):raise
        except Exception as exc:
            raise RuntimeError(f'Failed during: {phase}\n\n{exc}\n\nOriginal file was not replaced.') from exc

    def _convert(self, source, settings, report):
        source = Path(source).expanduser().absolute()
        if source.is_symlink(): raise RuntimeError('Please select the actual file, not a symbolic link.')
        source=source.resolve()
        if any(p.lower().endswith('.photoslibrary') for p in source.parts):
            raise RuntimeError('Export from Photos first. Files inside a Photos library cannot be processed.')
        if settings['replace'] and source.suffix.lower() not in ('.mp4', '.mov', '.m4v'):
            raise RuntimeError('Replacement supports MP4, MOV and M4V only. Choose New file for this format.')
        initial = source.stat()
        identity = (initial.st_ino, initial.st_size, initial.st_mtime_ns)
        info = self.probe(source)
        video = next((s for s in info['streams'] if s['codec_type'] == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
        if not video: raise RuntimeError('No video stream found.')
        if video.get('color_transfer') in ('smpte2084', 'arib-std-b67'):
            raise Skipped('HDR video is not supported yet. Original kept in Photos.')
        if video.get('sample_aspect_ratio') not in (None, 'N/A', '0:1', '1:1'):
            raise Skipped('Anamorphic video is not supported. Original kept.')
        duration = float(info['format'].get('duration', 0))
        if not math.isfinite(duration) or duration<=0:raise RuntimeError('Video duration is missing or invalid. Original kept.')
        before = self.metadata(source)
        extension = source.suffix.lower() if settings['replace'] else '.' + settings['container'].lower()
        folder = Path(settings['folder']).expanduser().resolve() if settings.get('folder') and not settings['replace'] else source.parent
        if any(p.lower().endswith('.photoslibrary') for p in folder.parts):raise RuntimeError('Choose an output folder outside the Photos library bundle.')
        if extension not in ('.mp4','.mov','.m4v'):raise RuntimeError('Choose MP4 or MOV as the output container.')
        codec = settings['codec']
        if codec not in ('HEVC','H.264'):raise RuntimeError('Choose HEVC or H.264 as the video codec.')
        size = int(settings['size'])
        # Bound the long/short sides: landscape 1920x1080, portrait 1080x1920. Never upscale.
        longside = {720:1280, 1080:1920, 2160:3840}[size]
        vf = (f"scale=w='if(gte(iw,ih),min(iw,{longside}),min(iw,{size}))':"
              f"h='if(gte(iw,ih),min(ih,{size}),min(ih,{longside}))':"
              'force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1')
        crf = {'High':20, 'Balanced':24, 'Small':28}[settings['quality']]
        if codec == 'H.264': crf -= 2
        folder.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(folder).free < initial.st_size + 256 * 1024**2:
            raise RuntimeError('Not enough free space for compression. Free local storage and retry; original kept.')
        fd, tempname = tempfile.mkstemp(prefix='.videoqueue-', suffix=extension, dir=folder)
        os.close(fd)
        temp = Path(tempname)
        try:
            report('Converting', 0)
            args = [self.tools['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-y', '-nostdin', '-i', str(source),
                    '-map', f"0:{video['index']}", '-map', '0:a?', '-map_metadata', '0', '-map_chapters', '0',
                    '-vf', vf, '-c:v', 'libx265' if codec == 'HEVC' else 'libx264', '-crf', str(crf), '-preset', 'medium', '-pix_fmt', 'yuv420p']
            if codec == 'HEVC': args += ['-tag:v', 'hvc1']
            args += ['-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', '-progress', 'pipe:1', str(temp)]
            self.run(args, lambda p: report('Converting', p * .90), duration)
            report('Copying metadata', 92)
            self.run([self.tools['exiftool'], '-overwrite_original', '-TagsFromFile', str(source), '-All:All',
                      '--Rotation', '--Orientation', '--ImageWidth', '--ImageHeight', str(temp)])
            report('Restoring movie / track dates', 94)
            outinfo = self.probe(temp)
            source_groups=set();mapping={}
            if 'mov' in info.get('format',{}).get('format_name','').split(','):
                kept=[video]+[s for s in info['streams'] if s['codec_type']=='audio']
                outputstreams=[s for s in outinfo['streams'] if s['codec_type']=='audio' or (s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic'))]
                source_groups,mapping=restore_track_dates(source,temp,kept,outputstreams)
            report('Checking metadata', 95)
            after = self.metadata(temp)
            mismatches = metadata_mismatches(before,after,source_groups,mapping)
            if mismatches: raise RuntimeError('Metadata verification failed: ' + '\n'.join(mismatches) + '. Original kept.')
            outinfo = self.probe(temp)
            outputduration=float(outinfo['format'].get('duration',0))
            if not math.isfinite(outputduration) or outputduration<=0 or abs(outputduration - duration) > max(.5, duration * .005):
                raise RuntimeError('Duration verification failed. Original kept.')
            if len([s for s in outinfo['streams'] if s['codec_type'] == 'audio']) != len([s for s in info['streams'] if s['codec_type'] == 'audio']):
                raise RuntimeError('Audio verification failed. Original kept.')
            report('Verifying playback', 96)
            self.run([self.tools['ffmpeg'], '-v', 'error', '-xerror', '-nostdin', '-i', str(temp), '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'])
            report('Preserving file dates', 98)
            os.utime(temp, ns=(initial.st_atime_ns, initial.st_mtime_ns))
            shutil.copymode(source, temp)
            if os.sys.platform == 'darwin':
                self.run([self.tools['exiftool'], '-overwrite_original', '-TagsFromFile', str(source), '-FileCreateDate', str(temp)])
            os.utime(temp, ns=(initial.st_atime_ns, initial.st_mtime_ns))
            if self.cancel.is_set(): raise Cancelled()
            current = source.stat()
            if (current.st_ino, current.st_size, current.st_mtime_ns) != identity:
                raise RuntimeError('Original changed during conversion. Replacement cancelled.')
            report('Saving result', 99)
            outputsize = temp.stat().st_size
            if settings.get('require_smaller') and outputsize >= initial.st_size:
                raise Skipped('The compressed video is not smaller. Original kept in Photos.')
            if settings['replace']:
                if outputsize >= initial.st_size:
                    raise RuntimeError('Converted file is not smaller. Original kept; try Small quality.')
                backupdir = source.parent / 'Video Queue Originals'
                backupdir.mkdir(exist_ok=True)
                backup = backupdir / (source.stem + '-' + uuid.uuid4().hex[:10] + source.suffix)
                # A hard link retains the complete original, without copying it.
                os.link(source, backup)
                os.replace(temp, source)
                output = source
                detail = f'Backup: {backup}'
            else:
                name = source.stem + f'_{size}p_{codec.replace(".", "")}'
                index = 0
                while True:
                    output = folder / (name + (f'_{index}' if index else '') + extension)
                    try:
                        os.link(temp, output) # Atomic publish; never overwrite an existing file.
                        break
                    except FileExistsError: index += 1
                temp.unlink()
                detail = 'New file created'
            saved = (initial.st_size-outputsize)/1024**2
            report('Completed', 100)
            return str(output), f'{detail}\nSize: {outputsize/1024**2:.1f} MB; change: {saved:+.1f} MB saved'
        finally:
            temp.unlink(missing_ok=True)
