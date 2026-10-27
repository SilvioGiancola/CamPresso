"""Headless workflow service. Native Photos operations are delegated to the Mac host.

Protocol 1 uses newline-delimited JSON; stdout is reserved for protocol packets.
No GUI imports, PhotoKit writes or automatic destructive decisions occur here.
"""

import argparse
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import logging
from pathlib import Path
import queue
import re
import shutil
import sys
import threading
import time
import uuid

from .engine import Cancelled, Skipped
from .native_store import NativeStore, asset_key, digest, generated_ids, read_strict
from .native_validation import verify_output
from .history import History, delete_preview_files, protected
from .photos_catalog import scan_library, resolve_library
from .state_store import StateFile
from .storage_cleanup import StorageLock
from .workflow_logic import file_matches, require_system_library


PROTOCOL = 1
PRESETS = {'Gentle': 7_500_000, 'Standard': 4_500_000, 'High': 3_000_000, 'Maximum': 1_500_000}
ACTIVE = {'Queued', 'Downloading', 'Prepared', 'Converting', 'Verifying', 'Awaiting save', 'Saving to Photos', 'Applying decision'}


class NativeError(RuntimeError):
    def __init__(self, message, uncertain=False, code='native_error'):
        super().__init__(message)
        self.uncertain, self.code = uncertain, code


class Transport:
    def __init__(self):
        self.lock = threading.Lock()
        self.stream = sys.stdout
        self.pending = {}
        self.pending_lock = threading.Lock()
        self.progress = None

    def send(self, packet):
        packet = dict(protocol=PROTOCOL, **packet)
        with self.lock:
            self.stream.write(json.dumps(packet, allow_nan=False, separators=(',', ':')) + '\n')
            self.stream.flush()

    def native(self, method, params, progress=None, timeout=1800):
        identifier = 'native-' + uuid.uuid4().hex
        answer = queue.Queue(maxsize=1)
        with self.pending_lock:
            self.pending[identifier] = (answer, progress)
        try:
            self.send(dict(type='native.request', id=identifier, method=method, params=params))
            try:
                packet = answer.get(timeout=timeout)
            except queue.Empty as error:
                if method in ('prepare', 'encode'):
                    self.send(dict(type='native.request', id='cancel-' + uuid.uuid4().hex,
                                   method='cancel', params=dict(job_id=params.get('job_id'))))
                raise NativeError('The native operation did not report a final outcome. Inspect recovery before retrying.',
                                  method in ('import', 'decide'), 'timeout') from error
            if 'error' in packet:
                error = packet['error']
                raise NativeError(error.get('message', 'Native operation failed.'), error.get('uncertain', False), error.get('code', 'native_error'))
            return packet.get('result', {})
        finally:
            with self.pending_lock:
                self.pending.pop(identifier, None)

    def receive(self, packet):
        with self.pending_lock:
            pending = self.pending.get(packet.get('id'))
        if not pending:
            return
        answer, progress = pending
        if packet['type'] == 'native.progress':
            if progress:
                progress(packet.get('stage', 'Preparing'), packet.get('percent'))
        elif packet['type'] == 'native.reply':
            try:
                answer.put_nowait(packet)
            except queue.Full:
                pass

    def abort_pending(self):
        with self.pending_lock:
            pending = list(self.pending.values())
        for answer, _ in pending:
            try:
                answer.put_nowait(dict(error=dict(message='The app is closing.', code='cancelled')))
            except queue.Full:
                pass


class WorkflowController:
    def __init__(self, data, transport):
        self.data = Path(data).expanduser().resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.storage_lock = StorageLock(self.data)
        self.transport = transport
        self.lock = threading.RLock()
        self.store = NativeStore(self.data)
        self.history = History(self.data)
        self.cleaning = False
        self.cleanup_revision = None
        self.preferences_file = StateFile(self.data / 'preferences.json')
        self.preferences = read_strict(self.preferences_file.path)
        old = self.preferences.get('compression', {})
        self.preferences.setdefault('preset', {'Balanced': 'Standard', 'High': 'Gentle', 'Small': 'High'}.get(old.get('quality'), 'Standard'))
        self.preferences.setdefault('keep_original_resolution', False)
        self.preferences.setdefault('container', old.get('container', 'MP4'))
        self.preferences.setdefault('codec', old.get('codec', 'HEVC'))
        if old.get('size') not in (None, '1080'):
            self.preferences.setdefault('resolution_override', int(old['size']))
        self.preferences.setdefault('library', '')
        self.library = str(Path(self.preferences['library']).expanduser().resolve()) if self.preferences.get('library') else ''
        self.rows = []
        self.catalog_error = None
        self.scanning = False
        self.paused = False
        self.stopping = False
        self.finished = threading.Event()
        self.decisions = []
        self.executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix='campresso')
        self.lanes = {}
        self.cancellations = {}
        self.revision = 0
        self.write_fault = None
        self.last_progress = {}
        self.last_requests = 0
        self.run_ids = set()
        self.scan_cancel = threading.Event()
        for job in self.store.jobs.values():
            if job['state'] not in ('Needs attention', 'Awaiting import'):
                self.cleanup_job(job, discard=True)
        self.scheduler = threading.Thread(target=self.run, name='workflow-scheduler', daemon=True)
        self.scheduler.start()

    def save(self):
        try:
            self.store.save()
        except (OSError, ValueError) as error:
            self.write_fault = f'Cannot save workflow. Processing paused; recovery files kept. {error}'
            self.paused = True
            raise RuntimeError(self.write_fault) from error

    def settings(self, force1080=False):
        result = {key: self.preferences.get(key) for key in ('preset', 'keep_original_resolution', 'container', 'codec', 'resolution_override')}
        if force1080:
            result.update(keep_original_resolution=False, resolution_override=1080)
        return result

    def assert_writable(self):
        if self.cleaning:
            raise RuntimeError('Wait for history cleanup to finish.')
        if self.write_fault:
            raise RuntimeError(self.write_fault)
        require_system_library(self.library)
        if self.preferences.get('personal_library_confirmed') != self.library:
            raise RuntimeError('In Settings, confirm in Apple Photos that family Shared Photo Library is not configured. Shared or uncertain libraries are browse-only.')
        if not self.preferences.get('import_notice_accepted'):
            raise RuntimeError('Read and accept the compression/review information in Settings first.')

    def estimate(self, media, profile=None):
        profile = profile or self.settings()
        width, height, duration = (media.get(key) for key in ('width', 'height', 'duration'))
        if not width or not height or not duration:
            return None
        cap = profile.get('resolution_override') or (720 if profile.get('preset') == 'Maximum' else 1080)
        long = round(cap * 16/9)
        factor = 1 if profile.get('keep_original_resolution') else min(1, long/max(width, height), cap/min(width, height))
        fps = media.get('fps') or 30
        fps = min(fps, 30 if profile.get('preset') == 'Maximum' else fps if profile.get('preset') == 'Gentle' else 60)
        scale = max(.2, min(4, width*height*factor*factor*fps/(1920*1080*30)))
        bitrate = PRESETS.get(profile.get('preset'), PRESETS['Standard']) * scale
        return int(duration * (bitrate + 192000) / 8 * 1.02)

    def row(self, media, job=None):
        row = dict(media)
        row.update(id=asset_key(media['uuid']), source_uuid=asset_key(media['uuid']), state='Ready',
                   progress=None, detail='', job_id=None, result_id=None,
                   estimated_size=self.estimate(media), source_size=media.get('size'), result_size=None,
                   warnings=[], excluded=False, recovery_bytes=0)
        if job:
            row.update({key: job.get(key) for key in ('state', 'progress', 'detail', 'result_id', 'source_size', 'result_size', 'warnings', 'excluded', 'decision', 'generation')})
            if (job.get('original_version') or {}).get('duration'):
                row['duration'] = job['original_version']['duration']
            row.update(job_id=job['id'], original_id=job.get('original_id'),
                       estimated_size=self.estimate(media, job.get('profile') or self.settings()),
                       recovery_bytes=self.recovery_bytes(job), operation=job.get('operation'),
                       operations=job.get('operations', []), legacy_journal=job.get('legacy_journal'),
                       generated_ids=sorted(generated_ids(job)))
            if row['state'] == 'Prepared':
                row.update(state='Queued', detail='Downloaded; waiting for conversion')
            if row['state'] == 'Awaiting save':
                row.update(state='Saving to Photos', detail='Waiting for the Photos transaction queue')
        return row

    def all_rows(self):
        jobs = [job for job in self.store.jobs.values() if job.get('library') == self.library]
        generated = set().union(*(generated_ids(job) for job in jobs))
        catalog = {asset_key(row['uuid']): row for row in self.rows}
        media = {identifier: row for identifier, row in catalog.items() if identifier not in generated}
        for job in jobs:
            fallback = dict(job['media'])
            if job.get('state') == 'Completed' and job.get('decision') == 'keep':
                replacement = catalog.get(asset_key(job.get('result_id')))
                if replacement:
                    fallback.update({key: replacement.get(key) for key in
                                     ('favorite', 'local', 'date', 'duration', 'thumbnail', 'width', 'height')})
            media.setdefault(job['source_uuid'], fallback)
        rows = [self.row(row, self.store.find(identifier, self.library)) for identifier, row in media.items()]
        active_sources = {job['source_uuid'] for job in jobs}
        for link in self.history.links:
            if link['library'] != self.library or link['source_id'] in active_sources:
                continue
            ids = set(link.get('generated_ids', [])) | {link['source_id']}
            present = next((row for row in rows if row['id'] in ids), None)
            survivor = link.get('result_id') if link.get('decision') == 'keep' else link['source_id']
            candidate = catalog.get(asset_key(survivor)) or (catalog.get(link['source_id']) if link.get('pending') else None)
            rows = [row for row in rows if row['id'] not in ids]
            if candidate:
                present = self.row(candidate)
            if present:
                present.update(state='Previous work' if link.get('pending') else
                               'Already compressed' if link.get('decision') == 'keep' else 'Original kept',
                               excluded=True, detail='History cleared; asset association retained for safety.')
                rows.append(present)
        return rows

    def snapshot(self):
        with self.lock:
            return dict(revision=self.revision, rows=self.all_rows(), preferences=self.preferences,
                        library=self.library, scanning=self.scanning, paused=self.paused,
                        catalog_error=self.catalog_error, fatal_error=self.write_fault,
                        active=sum(job['state'] in ACTIVE for job in self.store.jobs.values()),
                        pending_reviews=sum(job['state'] == 'Ready to review' for job in self.store.jobs.values()),
                        legacy_count=len(self.store.legacy), run_ids=sorted(self.run_ids),
                        previous_work=self.history.summaries(), cleaning=self.cleaning,
                        retained_files=self.history.retained_summaries())

    def publish(self, job=None):
        self.revision += 1
        if job:
            self.transport.send(dict(type='event', event='job', revision=self.revision,
                                     row=self.row(job['media'], job)))
        else:
            self.transport.send(dict(type='event', event='snapshot', data=self.snapshot()))

    def update(self, job, **values):
        with self.lock:
            job.update(values)
            self.save()
            self.publish(job)

    def progress(self, job, stage, percent):
        with self.lock:
            if self.stopping:
                return
            now = time.monotonic()
            if now-self.last_progress.get(job['id'], 0) < .25:
                return
            self.last_progress[job['id']] = now
            percent = max(0, min(100, float(percent))) if isinstance(percent, (float, int)) else None
            job.update(progress=percent, detail=stage)
            self.publish(job)

    def scan(self):
        with self.lock:
            if self.scanning or self.stopping:
                return
            self.scanning = True
            self.catalog_error = None
            self.publish()
        def work():
            try:
                if not self.library:
                    raise RuntimeError('Choose your System Photo Library in Settings.')
                requested_library = self.library
                # Third-party catalog diagnostics must not enter the JSON stream.
                with redirect_stdout(sys.stderr):
                    rows, actual = scan_library(requested_library, cancel=self.scan_cancel, videos_only=True)
                with self.lock:
                    if self.stopping or self.library != requested_library:
                        return
                    self.rows = [asdict(row) for row in rows if row.kind == 'Video']
                    self.library = str(Path(actual).resolve())
                    self.preferences['library'] = self.library
                    self.preferences_file.write(self.preferences)
            except Exception as error:
                with self.lock:
                    self.catalog_error = str(error) + '\nCheck the library path and Full Disk Access for CamPresso.'
                logging.exception('Catalog reading failed')
            finally:
                with self.lock:
                    self.scanning = False
                    if not self.stopping:
                        self.publish()
        threading.Thread(target=work, name='photos-catalog', daemon=True).start()

    def enqueue(self, identifiers, force1080=False):
        with self.lock:
            self.assert_writable()
            wanted = {asset_key(value) for value in identifiers}
            if not wanted or len(wanted) > 10000:
                raise ValueError('Select between 1 and 10,000 videos.')
            if not self.lanes and not any(job['state'] in ACTIVE for job in self.store.jobs.values()):
                self.run_ids.clear()
            self.paused = False
            for row in self.all_rows():
                if row['id'] not in wanted:
                    continue
                if row.get('excluded'):
                    continue
                if row.get('shared_library') or row.get('shared_album'):
                    continue
                job = self.store.find(row['id'], self.library)
                if job and job['state'] not in ('Ready', 'Error', 'Cancelled', 'Paused', 'Waiting for connection', 'Waiting for space', 'Skipped'):
                    continue
                if job and (job.get('result_id') or job.get('excluded')):
                    continue
                if job is None:
                    job = self.store.create(row, self.library, self.settings(force1080))
                else:
                    job.update(state='Queued', progress=None, detail='', generation=uuid.uuid4().hex,
                               profile=self.settings(force1080), approval=None, prepared=None, operation=None,
                               expected=None, result_size=None, warnings=[])
                self.cancellations[job['id']] = threading.Event()
                self.run_ids.add(job['id'])
            self.save()
            self.publish()
        return {}

    def native_params(self, job, **extra):
        return dict(job_id=job['id'], generation=job['generation'], original_id=job['original_id'],
                    result_id=job.get('result_id'), library=job['library'],
                    source_size=job.get('source_size'), **extra)

    def prepare(self, job):
        self.update(job, state='Downloading', progress=None, detail='Preparing the current Photos video')
        prepared = self.transport.native('prepare', self.native_params(job, profile=job['profile']),
                                         lambda stage, value: self.progress(job, stage, value))
        if self.cancellations[job['id']].is_set():
            raise Cancelled()
        self.update(job, state='Prepared', progress=None, prepared=prepared,
                    original_id=prepared['original_id'], original_version=prepared['original_version'])

    def encode(self, job):
        cancel = self.cancellations[job['id']]
        folder = self.work_folder(job)
        output = folder / ('compressed.' + job['profile'].get('container', 'MP4').lower())
        self.update(job, state='Converting', progress=0, detail='Converting', output=str(output))
        result = self.transport.native('encode', self.native_params(job, output=str(output), profile=job['profile']),
                                       lambda stage, value: self.progress(job, stage, value))
        if cancel.is_set():
            raise Cancelled()
        self.update(job, state='Verifying', progress=None, detail='Checking compressed playback')
        size = verify_output(output, result['expected'], job['source_size'], cancel,
                             lambda stage, value: self.progress(job, stage, value))
        stat = output.stat()
        self.update(job, state='Awaiting save', progress=None, expected=result['expected'],
                    result_size=size, output_sha256=digest(output),
                    output_fingerprint=[stat.st_size, stat.st_mtime_ns], warnings=result.get('warnings', []))

    def begin_operation(self, job, action):
        with self.lock:
            operation = uuid.uuid4().hex
            job['operation'] = dict(id=operation, action=action)
            job['operations'].append(dict(id=operation, action=action, generation=job['generation']))
            self.save()
            return operation

    def import_result(self, job):
        self.assert_writable()
        if self.history.has_uncertain_previous():
            raise RuntimeError('Inspect uncertain previous Photos operations before importing more copies.')
        if job['library'] != self.library:
            raise RuntimeError('Open the recorded library before importing this video.')
        if not file_matches(job.get('output'), job.get('output_fingerprint')) or digest(job['output']) != job.get('output_sha256'):
            raise RuntimeError('The compressed file changed. It was not imported.')
        operation = self.begin_operation(job, 'import')
        self.update(job, state='Saving to Photos', progress=None, detail='Adding and verifying the smaller copy')
        result = self.transport.native('import', self.native_params(job, operation_id=operation,
            output=job['output'], sha256=job['output_sha256'], expected=job['expected'],
            original_version=job.get('original_version'), name=job['name']))
        self.update(job, state='Ready to review', detail='Saved in Photos. Compare, then choose which version to keep.',
                    result_id=result['result_id'], original_id=result['original_id'],
                    original_version=result['original_version'], result_version=result['result_version'],
                    operation=None, progress=None)
        self.cleanup_job(job)

    def work_folder(self, job):
        folder = self.data / 'NativeWork' / job['id'] / job['generation']
        if folder.resolve() != folder or any(parent.is_symlink() for parent in (folder, *folder.parents)):
            raise RuntimeError('The temporary folder contains a symbolic link. No media files were changed.')
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def decide(self, job, decision, approval):
        self.assert_writable()
        if self.history.has_uncertain_previous():
            raise RuntimeError('Inspect uncertain previous Photos operations before applying decisions.')
        if job['library'] != self.library or job['media'].get('shared_library') or job['media'].get('shared_album'):
            raise RuntimeError('Only personal videos from the selected System Photo Library can be changed.')
        if approval != job.get('approval') or not approval or approval['generation'] != job['generation']:
            raise RuntimeError('The review changed. Open it again before deciding.')
        operation = self.begin_operation(job, decision)
        self.update(job, state='Applying decision', detail='Keeping compressed video' if decision == 'keep' else 'Keeping original video')
        result = self.transport.native('decide', self.native_params(job, operation_id=operation,
            decision=decision, approval=approval, expected=job['expected']))
        self.update(job, state='Completed', decision=decision, detail='Compressed copy kept' if decision == 'keep' else 'Original kept',
                    operation=None, approval=None, progress=None, excluded=True,
                    result_version=result.get('result_version', job.get('result_version')))
        self.cleanup_job(job)

    def failure(self, job, error):
        logging.exception('Workflow action failed: %s', job['name'], exc_info=error)
        uncertain = isinstance(error, NativeError) and error.uncertain
        if uncertain or job.get('result_id'):
            state = 'Needs attention'
        elif isinstance(error, (Cancelled,)) or self.cancellations.get(job['id'], threading.Event()).is_set():
            state = 'Paused' if self.stopping or self.paused else 'Cancelled'
        elif isinstance(error, Skipped) or isinstance(error, NativeError) and error.code == 'unsupported':
            state = 'Skipped'
        elif isinstance(error, NativeError) and error.code in ('network', 'offline'):
            state = 'Waiting for connection'
        elif 'space' in str(error).lower() or isinstance(error, NativeError) and error.code == 'storage':
            state = 'Waiting for space'
        else:
            state = 'Error'
        self.update(job, state=state, detail=str(error) or 'Processing stopped.', progress=None, approval=None)
        if not uncertain:
            with self.lock:
                job['operation'] = None
                self.save()
        if uncertain:
            with self.lock:
                for identifier, _, _ in self.decisions:
                    self.store.jobs[identifier].update(state='Ready to review', approval=None)
                self.decisions.clear()
                self.save()
                self.publish()
        elif not job.get('result_id'):
            self.cleanup_job(job, discard=True)

    def cleanup_job(self, job, discard=False):
        if job['state'] == 'Needs attention' or job.get('operation'):
            return
        if job['state'] in ('Completed', 'Ready to review'):
            self.cleanup_legacy(job)
        output = job.get('output')
        if not (discard or job.get('result_id')):
            return
        path = Path(output) if output else None
        root = self.data / 'NativeWork'
        try:
            if path and (not path.resolve().is_relative_to(root.resolve()) or path.is_symlink()):
                return  # Migrated external results never belong to this cleaner.
            if path and path.exists():
                if job.get('output_fingerprint') and not file_matches(path, job['output_fingerprint']):
                    job.update(state='Needs attention', detail='A working file changed outside the app. It was retained for inspection.')
                    return
                path.unlink()
            folder = root / job['id']
            if folder.resolve() != folder or folder.is_symlink() or root.is_symlink():
                return
            # Only known application-created rendition names are disposable.
            # Unknown files/symlinks are retained. Empty generation folders go too.
            if folder.is_dir():
                for generation in folder.iterdir():
                    if not generation.is_dir() or generation.is_symlink():
                        continue
                    for filename in ('photos-rendered.mov', 'compressed.mp4', 'compressed.mov'):
                        candidate = generation / filename
                        if candidate.is_file() and not candidate.is_symlink():
                            candidate.unlink()
                    if not any(generation.iterdir()):
                        generation.rmdir()
                if not any(folder.iterdir()):
                    folder.rmdir()
            with self.lock:
                job['output'] = None
                self.save()
        except OSError:
            logging.exception('Could not clear app-owned compressed file')

    def cleanup_legacy(self, job):
        """Only release unchanged, tracked bytes in known old app-owned roots."""
        legacy = job.get('legacy_record') or {}
        roots = [self.data/name for name in ('Workflow', 'Photos work', 'Temporary work', 'Converted previews')]
        for field in ('output', 'backup'):
            value, fingerprint = legacy.get(field), legacy.get(field + '_fingerprint')
            if not value or not fingerprint:
                continue
            path = Path(value).expanduser().absolute()
            inputs = [legacy.get('path'), (legacy.get('media') or {}).get('path')]
            if any(source and Path(source).expanduser().resolve() == path.resolve() for source in inputs):
                continue
            if any(part.lower().endswith('.photoslibrary') for part in path.parts):
                continue
            if path.resolve() != path or any(parent.is_symlink() for parent in (path, *path.parents)):
                continue
            if not any(path.is_relative_to(root) for root in roots):
                continue
            if file_matches(path, fingerprint):
                try:
                    path.unlink()
                    if job.get('output') == value:
                        job['output'] = None
                except OSError:
                    logging.exception('Legacy app-owned media retained')

    def recovery_bytes(self, job):
        if job['state'] not in ('Needs attention', 'Awaiting import'):
            return 0
        paths = [job.get('output'), (job.get('legacy_record') or {}).get('backup')]
        folder = self.data / 'NativeWork' / job['id']
        if folder.is_dir() and not folder.is_symlink():
            paths += [str(path) for path in folder.rglob('*') if path.is_file() and not path.is_symlink()]
        total = 0
        for value in set(paths):
            try:
                if value:
                    total += Path(value).stat().st_size
            except OSError:
                pass
        return total

    def open_review(self, identifier):
        job = self.store.jobs[identifier]
        if job['state'] != 'Ready to review':
            raise RuntimeError('Inspect or finish processing this video before review.')
        try:
            result = self.transport.native('review', self.native_params(job, expected=job['expected']))
        except NativeError as error:
            self.update(job, state='Needs attention', approval=None, detail=str(error))
            raise
        if job.get('original_version') and result['original_version'] != job['original_version']:
            self.update(job, state='Needs attention', detail='The original changed in Photos. Inspect and review again.')
            raise RuntimeError(job['detail'])
        if job.get('result_version') and result['result_version'] != job['result_version']:
            self.update(job, state='Needs attention', detail='The compressed video changed in Photos. Inspect and review again.')
            raise RuntimeError(job['detail'])
        approval = dict(token=uuid.uuid4().hex, generation=job['generation'],
                        original_id=result['original_id'], result_id=result['result_id'],
                        original_version=result['original_version'], result_version=result['result_version'])
        self.update(job, original_id=result['original_id'], original_version=result['original_version'],
                    result_version=result['result_version'], approval=approval)
        return dict(job_id=identifier, name=job['name'], source_size=job['source_size'],
                    result_size=job['result_size'], warnings=job.get('warnings', []), **approval)

    def inspect(self, identifier):
        job = self.store.jobs[identifier]
        result = self.transport.native('inspect', self.native_params(job, expected=job.get('expected'),
            operation=job.get('operation'), legacy_journal=job.get('legacy_journal')))
        if result.get('completed_decision'):
            self.update(job, state='Completed', decision=result['completed_decision'], operation=None, approval=None, excluded=True,
                        result_id=result.get('result_id', job.get('result_id')), detail='Recorded Photos decision confirmed.')
        elif result.get('original_present') and result.get('result_present') and result.get('verified'):
            self.update(job, state='Ready to review', result_id=result['result_id'], original_id=result['original_id'],
                        original_version=result['original_version'], result_version=result['result_version'],
                        operation=None, approval=None, detail='Both Photos versions verified. Review again before deciding.')
        else:
            self.update(job, state='Needs attention', detail=result.get('detail', 'The Photos operation is unresolved. No import or deletion was repeated.'))
        return result

    def import_legacy(self, identifier):
        job = self.store.jobs[identifier]
        self.assert_writable()
        if job['media'].get('shared_library') or job['media'].get('shared_album'):
            raise RuntimeError('Shared videos are browse/play-only.')
        if job['state'] != 'Awaiting import' or job.get('result_id') or not file_matches(job.get('output'), job.get('output_fingerprint')):
            raise RuntimeError('The old local result is missing or changed. Recompress instead.')
        if not job.get('expected') or not job.get('source_size'):
            raise RuntimeError('The old result has no complete verification record. Recompress instead.')
        folder = self.work_folder(job)
        destination = folder / ('compressed' + Path(job['output']).suffix)
        shutil.copy2(job['output'], destination)
        stat = destination.stat()
        self.update(job, output=str(destination), output_fingerprint=[stat.st_size, stat.st_mtime_ns],
                    output_sha256=digest(destination), state='Awaiting save', detail='Adding the existing result for review')
        return {}

    def consume_requests(self):
        if self.stopping or self.scanning or not self.rows or time.monotonic()-self.last_requests < 1:
            return
        self.last_requests = time.monotonic()
        directory = self.data / 'Requests'
        if not directory.is_dir():
            return
        # A Service never supplies deletion approval. Requests wait for setup.
        try:
            self.assert_writable()
        except RuntimeError:
            return
        for path in sorted(directory.glob('*.json')):
            try:
                if path.is_symlink() or path.stat().st_size > 131072 or not re.fullmatch(r'[0-9a-f]{32}', path.stem):
                    raise ValueError('Invalid Service request file.')
                request = read_strict(path)
                from .photos_service import selected_ids
                if request.get('version') != 1 or request.get('action') != 'convert1080':
                    raise ValueError('Unknown Service request.')
                identifiers = selected_ids(request.get('uuids'))
                if not set(identifiers).issubset({asset_key(row['uuid']) for row in self.rows}):
                    raise RuntimeError('Selected Service videos are not in the loaded library. Refresh and retry the Service.')
                self.enqueue(identifiers, force1080=True)
                path.unlink()
            except Exception as error:
                logging.exception('Service request failed')
                if path.exists():
                    path.rename(path.with_suffix('.failed'))
                self.transport.send(dict(type='event', event='notice', message=str(error)))

    def run(self):
        try:
            while True:
                with self.lock:
                    for lane, (identifier, future) in list(self.lanes.items()):
                        if future.done():
                            del self.lanes[lane]
                            try:
                                future.result()
                            except Exception as error:
                                try:
                                    self.failure(self.store.jobs[identifier], error)
                                except Exception:
                                    logging.exception('Could not persist failure')
                    if self.stopping and not self.lanes:
                        break
                    if not self.stopping and not self.write_fault and not self.cleaning:
                        jobs = list(self.store.jobs.values())
                        if 'photos' not in self.lanes:
                            if self.decisions:
                                identifier, decision, approval = self.decisions.pop(0)
                                self.lanes['photos'] = (identifier, self.executor.submit(self.decide, self.store.jobs[identifier], decision, approval))
                            elif not any(job['state'] == 'Needs attention' for job in jobs) and not self.history.has_uncertain_previous():
                                job = next((job for job in jobs if job['state'] == 'Awaiting save'), None)
                                if job:
                                    job['state'] = 'Saving to Photos'
                                    self.lanes['photos'] = (job['id'], self.executor.submit(self.import_result, job))
                        if not self.paused:
                            if 'encode' not in self.lanes and not any(job['state'] in ('Awaiting save', 'Saving to Photos') for job in jobs):
                                job = next((job for job in jobs if job['state'] == 'Prepared'), None)
                                if job:
                                    job['state'] = 'Converting'
                                    self.lanes['encode'] = (job['id'], self.executor.submit(self.encode, job))
                            if 'prepare' not in self.lanes and not any(job['state'] == 'Prepared' for job in jobs):
                                # Only one prepared original may wait ahead of the encoder.
                                job = next((job for job in jobs if job['state'] == 'Queued'), None)
                                if job:
                                    job['state'] = 'Downloading'
                                    self.lanes['prepare'] = (job['id'], self.executor.submit(self.prepare, job))
                self.consume_requests()
                time.sleep(.1)
        except Exception as error:
            logging.exception('Workflow scheduler stopped')
            self.write_fault = str(error)
        finally:
            try:
                if not self.write_fault:
                    for job in self.store.jobs.values():
                        self.cleanup_job(job, discard=job['state'] not in ('Awaiting import', 'Needs attention'))
                self.executor.shutdown(wait=True)
                self.save()
            except Exception:
                logging.exception('Shutdown state/cache cleanup was incomplete; files retained')
            finally:
                self.finished.set()
                if self.stopping:
                    self.transport.send(dict(type='event', event='shutdownReady'))
                else:
                    self.transport.send(dict(type='event', event='fatal', message=self.write_fault or 'Workflow stopped unexpectedly. Reopen to inspect recovery.'))

    def stop(self, closing=False):
        with self.lock:
            self.paused = True
            self.stopping = closing
            if closing:
                self.scan_cancel.set()
            for identifier, _, _ in self.decisions:
                self.store.jobs[identifier].update(state='Ready to review', approval=None)
            for identifier, cancel in self.cancellations.items():
                cancel.set()
                job = self.store.jobs[identifier]
                if job['state'] in ('Queued', 'Prepared', 'Awaiting save'):
                    job.update(state='Paused' if closing else 'Cancelled', detail='Processing stopped.', progress=None)
            self.decisions.clear()
            try:
                self.save()
            except RuntimeError:
                logging.exception('Could not persist stopped work')
            self.publish()
        # Native cancellation only affects reads/encoding, never Photos writes.
        self.transport.send(dict(type='native.request', id='cancel-' + uuid.uuid4().hex, method='cancel', params={}))

    def require_idle_cleanup(self):
        if self.stopping or self.cleaning or self.scanning or self.lanes or self.decisions or any(
                job['state'] in ACTIVE for job in self.store.jobs.values()):
            raise RuntimeError('Stop processing and wait for current work before clearing history.')
        if self.transport.native('storage.status', {}).get('busy'):
            raise RuntimeError('Wait for the current native operation before clearing history.')

    def cleanup_history(self, token):
        with self.lock, self.history.lock_previous():
            self.require_idle_cleanup()
            preview = self.history.preview
            if not preview or token != preview['token'] or self.cleanup_revision != self.revision:
                raise RuntimeError('History changed. Preview cleanup again before confirming.')
            for root, plan in preview['previous'].items():
                if plan['workflow_fingerprint'] and not file_matches(Path(root)/'workflow.json', plan['workflow_fingerprint']):
                    raise RuntimeError('Previous work changed. Preview cleanup again before confirming.')
            self.cleaning = True
            try:
                # Identity associations must survive even if the process stops
                # between ledger publication, history removal and file removal.
                targets = [self.store.jobs[identifier] for identifier in preview['job_ids']]
                if any(protected(job) for job in targets):
                    raise RuntimeError('Some work now needs recovery. Preview cleanup again.')
                self.history.save_links(targets)
                self.history.retain_files(preview['retained'])
                self.history.pending_file.write(dict(version=1, files=preview['files']))
                for root, plan in preview['previous'].items():
                    StateFile(Path(root)/'campresso_cleanup_pending.json').write(dict(version=1, files=plan['files']))
                for identifier in preview['job_ids']:
                    del self.store.jobs[identifier]
                self.run_ids.difference_update(preview['job_ids'])
                self.save()
                outcome = delete_preview_files(self.data, preview)
                old_records = 0
                for root, plan in preview['previous'].items():
                    root = Path(root)
                    previous = read_strict(root/'workflow.json')
                    previous['jobs'] = [job for job in previous.get('jobs', []) if job['id'] not in plan['old_job_ids']]
                    StateFile(root/'workflow.json').write(previous)
                    old_records += len(plan['old_job_ids'])
                    old_outcome = delete_preview_files(root, plan)
                    outcome['removed_bytes'] += old_outcome['removed_bytes']
                    outcome['retained'] += old_outcome['retained']
                    StateFile(root/'campresso_cleanup_pending.json').write(dict(version=1, files={
                        path: stamp for path, stamp in plan['files'].items() if Path(path).exists()}))
                outcome['retained'] += [path+': changed/external/unverified; retained' for path in preview['retained']]
                self.history.pending_file.write(dict(version=1, files={
                    path: stamp for path, stamp in preview['files'].items() if Path(path).exists()}))
                self.history.preview = None
                return dict(records=len(preview['job_ids'])+old_records, **outcome)
            finally:
                self.cleaning = False
                self.publish()

    def restore_previous(self, identifier):
        with self.history.lock_previous():
            return self._restore_previous(identifier)

    def _restore_previous(self, identifier):
        with self.lock:
            self.require_idle_cleanup()
            entry = next((value for value in self.history.previous if value['id'] == identifier), None)
            if not entry or entry.get('restored'):
                raise RuntimeError('This previous record is already restored or unavailable.')
            old = dict(entry['job'])
            if not self.library or str(Path(old.get('library') or '').resolve()) != self.library:
                raise RuntimeError('Select the recorded System Photo Library in Settings before inspecting this record.')
            require_system_library(self.library)
            media = old.get('media') or {}
            old.setdefault('source_uuid', asset_key(media.get('uuid')))
            old.setdefault('original_id', media.get('uuid'))
            old.setdefault('result_id', old.get('new_uuid'))
            old.setdefault('generation', uuid.uuid4().hex)
            if not old.get('original_id') or not old.get('expected'):
                raise RuntimeError('This record needs manual recovery. Its original files and journals have been retained.')
            existing = self.store.find(old['source_uuid'], self.library)
            if existing and existing.get('previous_work_id') == identifier:
                entry['restored'] = True
                self.history.save_previous()
                return dict(message='This previous pair is already restored in the main gallery.')
            if existing or old['id'] in self.store.jobs:
                raise RuntimeError('This source already has current work. Inspect it in the main gallery.')
            self.cleaning = True
        try:
            result = self.transport.native('inspect', self.native_params(old, expected=old['expected'],
                operation=old.get('operation'), previous_work_id=identifier))
            with self.lock:
                if not (result.get('original_present') and result.get('result_present') and result.get('verified')):
                    if result.get('completed_decision'):
                        old.update(state='Completed', decision=result['completed_decision'],
                                   result_id=result.get('result_id', old.get('result_id')),
                                   operation=None, retry_safe=True, replacement_complete=True)
                        self.history.save_links([old])
                        entry['restored'] = True
                        self.history.save_previous()
                        return dict(message='The previous Photos decision was confirmed; its history remains cleared.')
                    raise RuntimeError(result.get('detail', 'The previous Photos operation remains unresolved. No change was repeated.'))
                old.update(state='Ready to review', original_id=result['original_id'], result_id=result['result_id'],
                           previous_work_id=identifier,
                           original_version=result['original_version'], result_version=result['result_version'],
                           approval=None, operation=None, progress=None, retry_safe=True, excluded=False,
                           source_size=old.get('source_size') or media.get('size'),
                           result_size=old.get('result_size') or (old.get('output_fingerprint') or [None])[0],
                           operations=old.get('operations') or [], warnings=old.get('warnings') or [],
                           detail='Previous Photos pair inspected. Review again before deciding.')
                self.store.jobs[old['id']] = old
                self.save()
                entry['restored'] = True
                self.history.save_previous()
                return dict(message='The previous pair is available in Review. No Photos changes were made.')
        finally:
            with self.lock:
                self.cleaning = False
                self.publish()

    def command(self, method, params):
        if method == 'initialize':
            self.publish()
            if self.library:
                self.scan()
            return dict(protocol=PROTOCOL, version=4)
        if method == 'history.previewCleanup':
            with self.lock, self.history.lock_previous():
                self.require_idle_cleanup()
                preview = self.history.preview_cleanup(self.store.jobs.values())
                self.cleanup_revision = self.revision
                return preview
        if method == 'history.clear':
            return self.cleanup_history(params['token'])
        if method == 'previous.inspect':
            return self.restore_previous(params['id'])
        if method == 'catalog.refresh':
            self.scan()
            return {}
        if method == 'preferences.update':
            with self.lock:
                if self.cleaning:
                    raise RuntimeError('Wait for cleanup or previous-work inspection to finish.')
                values = params['values']
                path = values.get('library', self.library).strip()
                if path:
                    path = str(Path(resolve_library(path)).resolve())
                if path != self.library and (self.lanes or any(job['state'] in ACTIVE for job in self.store.jobs.values())):
                    raise RuntimeError('Stop processing before changing the library.')
                if path != self.library and self.scanning:
                    raise RuntimeError('Wait for the current library scan before choosing another library.')
                if values.get('preset') not in PRESETS:
                    raise ValueError('Choose one of the four conversion presets.')
                if values.get('container') not in ('MP4', 'MOV') or values.get('codec') not in ('HEVC', 'H.264'):
                    raise ValueError('Choose a supported codec and container.')
                override = values.get('resolution_override')
                if override not in (None, 360, 480, 720, 1080, 1440, 2160):
                    raise ValueError('Unsupported resolution override.')
                previous = self.library
                self.library = path
                allowed = ('preset', 'keep_original_resolution', 'container', 'codec', 'resolution_override', 'import_notice_accepted')
                self.preferences.update({key: values.get(key) for key in allowed})
                self.preferences.update(library=path, personal_library_confirmed=path if values.get('personal_library_confirmed') else None)
                self.preferences_file.write(self.preferences)
                if previous != path:
                    self.rows = []
                self.publish()
            if previous != path:
                self.scan()
            return {}
        if method == 'jobs.enqueue':
            return self.enqueue(params['ids'])
        if method == 'jobs.stop':
            self.stop()
            return {}
        if method == 'jobs.pause':
            with self.lock:
                self.paused = True
                self.publish()
            return {}
        if method == 'jobs.resume':
            with self.lock:
                self.assert_writable()
                self.paused = False
                ids = [job['source_uuid'] for job in self.store.jobs.values()
                       if job['state'] in ('Paused', 'Waiting for connection', 'Waiting for space', 'Cancelled')]
            return self.enqueue(ids) if ids else {}
        if method == 'review.open':
            return self.open_review(params['job_id'])
        if method == 'review.batch':
            return dict(reviews=[self.open_review(identifier) for identifier in params['job_ids']])
        if method == 'review.decide':
            with self.lock:
                self.assert_writable()
                if self.history.has_uncertain_previous():
                    raise RuntimeError('Inspect uncertain previous Photos operations before applying decisions.')
                if any(job['state'] == 'Needs attention' for job in self.store.jobs.values()):
                    raise RuntimeError('Inspect unresolved Photos operations before applying more decisions.')
                if params['decision'] not in ('keep', 'revert'):
                    raise ValueError('Choose Keep Compressed or Revert.')
                for review in params['reviews']:
                    job = self.store.jobs[review['job_id']]
                    approval = {key: review[key] for key in ('token', 'generation', 'original_id', 'result_id', 'original_version', 'result_version')}
                    if job['state'] != 'Ready to review' or approval != job.get('approval'):
                        raise RuntimeError('One selected comparison changed. Review it again.')
                for review in params['reviews']:
                    job = self.store.jobs[review['job_id']]
                    job['state'] = 'Applying decision'
                    self.decisions.append((job['id'], params['decision'], dict(job['approval'])))
                self.save()
                self.publish()
            return {}
        if method == 'recovery.inspect':
            return self.inspect(params['job_id'])
        if method == 'legacy.import':
            return self.import_legacy(params['job_id'])
        if method == 'photos.show':
            from .photos_navigation import show_in_photos
            show_in_photos(params['id'])
            return {}
        if method == 'shutdown':
            self.stop(closing=True)
            return {}
        raise ValueError('Unknown backend command: ' + method)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path.home()/'Library'/'Application Support'/'CamPresso')
    parser.add_argument('--stdio', action='store_true')
    options = parser.parse_args(argv)
    if any(part.lower().endswith('.photoslibrary') for part in options.data_dir.resolve().parts):
        raise RuntimeError('Application data must be outside the Photos library.')
    options.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=options.data_dir/'conversion.log', level=logging.INFO,
                        format='%(asctime)s %(threadName)s %(message)s')
    transport = Transport()
    try:
        controller = WorkflowController(options.data_dir, transport)
    except Exception as error:
        transport.send(dict(type='event', event='fatal', message=str(error)))
        return 1
    inbox = queue.Queue()
    def read():
        for line in sys.stdin:
            try:
                if len(line) > 32_000_000:
                    raise ValueError('Protocol message is too large.')
                packet = json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                if packet.get('protocol') != PROTOCOL:
                    raise ValueError('The native app and backend protocol versions differ. Reinstall CamPresso.')
                if packet.get('type') in ('native.reply', 'native.progress'):
                    transport.receive(packet)
                else:
                    inbox.put(packet)
            except Exception as error:
                transport.send(dict(type='event', event='notice', message=str(error)))
        inbox.put(None)
    threading.Thread(target=read, name='protocol-reader', daemon=True).start()
    commands = ThreadPoolExecutor(max_workers=3, thread_name_prefix='commands')
    def dispatch(packet):
        try:
            result = controller.command(packet['method'], packet.get('params', {}))
            transport.send(dict(type='reply', id=packet['id'], result=result))
        except Exception as error:
            logging.exception('Backend command failed')
            transport.send(dict(type='reply', id=packet.get('id'), error=dict(message=str(error))))
    try:
        while not controller.finished.is_set():
            try:
                packet = inbox.get(timeout=.1)
            except queue.Empty:
                continue
            if packet is None:
                controller.stop(closing=True)
            elif not controller.stopping:
                commands.submit(dispatch, packet)
    finally:
        transport.abort_pending()
        commands.shutdown(wait=True)
        controller.storage_lock.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
