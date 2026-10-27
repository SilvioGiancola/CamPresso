"""History cleanup and previous-installation recovery; never writes to Photos."""

from copy import deepcopy
from contextlib import contextmanager, ExitStack
import hashlib
from pathlib import Path
import uuid

from .native_store import asset_key, generated_ids, read_strict
from .state_store import StateFile
from .storage_cleanup import StorageLock
from .workflow_logic import file_matches

LEGACY_NAME = 'Video Queue'
LEGACY_BUNDLE_ID = 'local.silvio.videoqueue'
LEGACY_SERVICE_ID = LEGACY_BUNDLE_ID + '.convert1080service'
REMOVABLE = {'Completed', 'Cancelled', 'Skipped', 'Error', 'Ready'}
MEDIA_ROOTS = ('NativeWork', 'Workflow', 'Photos work', 'Temporary work',
               'Converted previews', 'Previews', 'Thumbnails')
MEDIA_SUFFIXES = {'.mp4', '.mov', '.m4v', '.jpg', '.jpeg', '.png', '.heic', '.tiff'}


def protected(job):
    """Unfinished decisions and ambiguous writes are never ordinary history."""
    return (job.get('state') not in REMOVABLE or bool(job.get('operation'))
            or not job.get('retry_safe', True)
            or bool((job.get('result_id') or job.get('new_uuid')) and job.get('state') != 'Completed')
            or bool(job.get('original_removed') and not
                    (job.get('decision') == 'keep' or job.get('replacement_complete'))))


def uncertain(job):
    operation = job.get('operation')
    native_write = bool(operation)
    if isinstance(operation, str):
        native_write = operation not in ('convert', 'fetch_converted', 'service_download', '')
    return (job.get('state') in ('Needs attention', 'Saving to Photos', 'Applying decision')
            or not job.get('retry_safe', True) or native_write
            or bool(job.get('original_removed') and not
                    (job.get('decision') == 'keep' or job.get('replacement_complete'))))


def identity(job):
    source = job.get('source_uuid') or (job.get('media') or {}).get('uuid')
    if not source or not job.get('library'):
        return None
    results = sorted(generated_ids(job) | {asset_key(job.get('new_uuid'))} - {''})
    if not results and not protected(job):
        return None
    library = str(Path(job['library']).expanduser().resolve())
    return dict(id=hashlib.sha256((library+'|'+asset_key(source)).encode()).hexdigest(),
                library=library, source_id=asset_key(source), result_id=job.get('result_id') or job.get('new_uuid'),
                generated_ids=results, decision=job.get('decision') or
                ('keep' if job.get('original_removed') else None), pending=protected(job))


def merge_links(existing, jobs):
    links = {link['id']: link for link in existing}
    for job in jobs:
        link = identity(job)
        if link:
            previous = links.get(link['id'], {})
            link['generated_ids'] = sorted(set(previous.get('generated_ids', [])) | set(link['generated_ids']))
            links[link['id']] = link
    return list(links.values())


def references(job):
    records = [job, job.get('legacy_record') or {}]
    for record in records:
        for field in ('output', 'backup'):
            if record.get(field):
                yield record[field], record.get(field+'_fingerprint')
        for path in record.get('retained_outputs') or []:
            if path:
                yield path, None


def safe_path(value, data):
    path = Path(value).expanduser().absolute()
    if (any(part.lower().endswith('.photoslibrary') for part in path.parts)
            or any(parent.is_symlink() for parent in (path, *path.parents))
            or path.resolve() != path
            or not any(path.is_relative_to(data/name) for name in MEDIA_ROOTS)):
        return None
    return path


def source_paths(jobs):
    return {Path(path).expanduser().resolve() for job in jobs
            for record in (job, job.get('legacy_record') or {})
            for path in (record.get('path'), (record.get('media') or {}).get('path')) if path}


def cleanup_preview(data, jobs):
    """Snapshot only proven app-owned disposable files; unknown files stay."""
    data = Path(data).resolve()
    jobs = list(jobs)
    removable = [job for job in jobs if not protected(job)]
    keep = [job for job in jobs if protected(job)]
    protected_paths, inputs = set(), set()
    protected_directories = []
    for job in jobs:
        for record in (job, job.get('legacy_record') or {}):
            for value in (record.get('path'), (record.get('media') or {}).get('path')):
                if value:
                    inputs.add(Path(value).expanduser().resolve())
    for job in keep:
        for value, _ in references(job):
            protected_paths.add(Path(value).expanduser().resolve())
        if job.get('id'):
            protected_directories.append(data/'NativeWork'/job['id'])
    candidates = {}
    retained = []
    for job in removable:
        for value, fingerprint in references(job):
            path = safe_path(value, data)
            if not path or not fingerprint or not file_matches(path, fingerprint):
                if Path(value).is_file():
                    retained.append(str(value))
                continue
            candidates[str(path)] = list(fingerprint)
    # Preview/thumbnail caches are wholly app-owned. Other roots need a tracked
    # fingerprint; do not guess that an unfamiliar movie is disposable.
    for root in (data/'Previews', data/'Thumbnails', data/'Converted previews'):
        if not root.is_dir() or root.is_symlink():
            continue
        for path in root.rglob('*'):
            if path.suffix.lower() in MEDIA_SUFFIXES and path.is_file() and safe_path(path, data):
                stat = path.stat()
                candidates[str(path)] = [stat.st_size, stat.st_mtime_ns]
    disposable = {}
    for value, stamp in candidates.items():
        try:
            if (Path(value).resolve() not in protected_paths | inputs
                    and not any(Path(value).is_relative_to(root) for root in protected_directories)
                    and Path(value).stat().st_nlink == 1):
                disposable[value] = stamp
            else:
                retained.append(value)
        except OSError:
            retained.append(value)
    candidates = disposable
    return dict(job_ids=[job['id'] for job in removable], files=candidates,
                bytes=sum(stamp[0] for stamp in candidates.values()), protected_count=len(keep),
                retained=sorted(set(retained)))


def delete_preview_files(data, preview):
    failures = []
    removed_bytes = 0
    for value, stamp in preview['files'].items():
        path = safe_path(value, Path(data).resolve())
        try:
            if not path:
                failures.append(value+': unexpected path')
            elif not path.exists():
                continue
            elif not file_matches(path, stamp) or path.stat().st_nlink != 1:
                failures.append(value+': file changed; retained')
            else:
                path.unlink()
                removed_bytes += stamp[0]
        except OSError as error:
            failures.append(value+': '+str(error))
    return dict(removed_bytes=removed_bytes, retained=failures)


class History:
    def __init__(self, data):
        self.data = Path(data).resolve()
        self.links_file = StateFile(self.data/'asset_links.json')
        self.previous_file = StateFile(self.data/'PreviousWork.json')
        self.pending_file = StateFile(self.data/'cleanup_pending.json')
        self.retained_file = StateFile(self.data/'retained_files.json')
        self.links = read_strict(self.links_file.path).get('links', [])
        self.previous = read_strict(self.previous_file.path).get('entries', [])
        if not isinstance(self.links, list) or not isinstance(self.previous, list):
            raise RuntimeError('Invalid history/recovery manifest. No files were removed.')
        self.preview = None

    @contextmanager
    def lock_previous(self):
        roots = {self.data.parent/LEGACY_NAME}
        roots.update(Path(entry['source_root']) for entry in self.previous)
        with ExitStack() as stack:
            for root in sorted(roots, key=str):
                if not root.is_dir() or root == self.data:
                    continue
                if root.resolve() != root or any(part.lower().endswith('.photoslibrary') for part in root.parts):
                    raise RuntimeError('Unexpected previous storage path; cleanup was not started.')
                if any((root/name).is_symlink() for name in ('workflow.json', 'campresso_cleanup_pending.json', '.app.lock')):
                    raise RuntimeError('Previous state contains a symbolic link; cleanup was not started.')
                try:
                    lock = StorageLock(root)
                except RuntimeError as error:
                    raise RuntimeError('Close the previous app before cleaning history or inspecting previous work.') from error
                stack.callback(lock.close)
            yield

    def save_links(self, jobs):
        self.links = merge_links(self.links, jobs)
        self.links_file.write(dict(version=1, links=self.links))

    def save_previous(self):
        self.previous_file.write(dict(version=1, entries=self.previous))

    def has_uncertain_previous(self):
        return any(not entry.get('restored') and uncertain(entry['job']) for entry in self.previous)

    def preview_cleanup(self, jobs):
        jobs = list(jobs)
        preview = cleanup_preview(self.data, jobs)
        # Failed removals remain explicitly tracked, without restoring history.
        pending = read_strict(self.pending_file.path)
        blocked = {Path(value).expanduser().resolve() for job in jobs if protected(job) for value, _ in references(job)}
        for job in jobs:
            for record in (job, job.get('legacy_record') or {}):
                for path in (record.get('path'), (record.get('media') or {}).get('path')):
                    if path:
                        blocked.add(Path(path).expanduser().resolve())
        for value, stamp in pending.get('files', {}).items():
            if (value not in preview['files'] and safe_path(value, self.data) and file_matches(value, stamp)
                    and Path(value).resolve() not in blocked
                    and not any(Path(value).is_relative_to(self.data/'NativeWork'/job['id']) for job in jobs if protected(job))):
                preview['files'][value] = stamp
                preview['bytes'] += stamp[0]
        preview['token'] = uuid.uuid4().hex
        preview['previous'] = self.previous_cleanup_previews(jobs)
        preview['bytes'] += sum(value['bytes'] for value in preview['previous'].values())
        for value in preview['previous'].values():
            preview['retained'] += value['retained']
        preview['protected_count'] += sum(not item.get('restored') for item in self.previous)
        self.preview = preview
        return dict(token=preview['token'], records=len(preview['job_ids']) + sum(
                    len(value['old_job_ids']) for value in preview['previous'].values()), bytes=preview['bytes'],
                    protected_count=preview['protected_count'], retained_count=len(preview['retained']))

    def previous_cleanup_previews(self, jobs):
        """A resolved archived pair can finally release its old recovery bytes."""
        groups = {}
        for entry in self.previous:
            if not entry.get('restored'):
                continue
            old = entry['job']
            proof = next((job for job in jobs if job.get('previous_work_id') == entry['id']
                          and job.get('state') == 'Completed'), None)
            if not proof:
                old_link = identity(old)
                proof = next((link for link in self.links if old_link and link['id'] == old_link['id']
                              and not link.get('pending') and link.get('decision') in ('keep', 'revert')), None)
            if not proof:
                continue
            resolved = dict(old, state='Completed', decision=proof.get('decision'), operation=None,
                            retry_safe=True, replacement_complete=True)
            groups.setdefault(entry['source_root'], []).append(resolved)
        plans = {}
        roots = {entry['source_root'] for entry in self.previous}
        legacy = self.data.parent/LEGACY_NAME
        if legacy.is_dir():
            roots.add(str(legacy))
        for root in roots:
            root = Path(root)
            value = read_strict(root/'workflow.json')
            if value and value.get('version') not in (3, 4):
                continue
            records = value.get('jobs', [])
            resolved = groups.get(str(root), [])
            replacements = {job['id']: job for job in resolved}
            for job in records:
                replacements.setdefault(job['id'], job)
            plan = cleanup_preview(root, replacements.values())
            pending = read_strict(root/'campresso_cleanup_pending.json').get('files', {})
            blocked = {Path(path).resolve() for job in replacements.values() if protected(job)
                       for path, _ in references(job)}
            inputs = source_paths(replacements.values())
            for path, stamp in pending.items():
                if (path not in plan['files'] and safe_path(path, root) and file_matches(path, stamp)
                        and Path(path).resolve() not in blocked | inputs
                        and not any(Path(path).is_relative_to(root/'NativeWork'/job['id'])
                                    for job in replacements.values() if protected(job))):
                    plan['files'][path] = stamp
                    plan['bytes'] += stamp[0]
            if plan['files'] or plan['retained'] or any(job['id'] in plan['job_ids'] for job in records):
                plan['old_job_ids'] = [job['id'] for job in records if job['id'] in plan['job_ids']]
                path = root/'workflow.json'
                stat = path.stat() if path.exists() else None
                plan['workflow_fingerprint'] = [stat.st_size, stat.st_mtime_ns] if stat else None
                plans[str(root)] = plan
        return plans

    def retain_files(self, values):
        paths = set(read_strict(self.retained_file.path).get('paths', [])) | set(values)
        self.retained_file.write(dict(version=1, paths=sorted(paths)))

    def retained_summaries(self):
        paths = set(read_strict(self.retained_file.path).get('paths', []))
        paths.update(read_strict(self.pending_file.path).get('files', {}))
        roots = {Path(entry['source_root']) for entry in self.previous} | {self.data.parent/LEGACY_NAME}
        for root in roots:
            paths.update(read_strict(root/'campresso_cleanup_pending.json').get('files', {}))
        rows = []
        for value in sorted(paths):
            try:
                size = Path(value).lstat().st_size
            except OSError:
                continue
            rows.append(dict(id=value, path=value, bytes=size,
                             reason='Changed, external or unverified file; retained for inspection.'))
        return rows

    def summaries(self):
        result = []
        for entry in self.previous:
            if entry.get('restored'):
                continue
            job = entry['job']
            paths = {value for value, _ in references(job)}
            size = 0
            for value in paths:
                try:
                    size += Path(value).stat().st_size
                except OSError:
                    pass
            result.append(dict(id=entry['id'], name=job.get('name', 'Previous video'),
                               state=job.get('state', 'Needs attention'), library=job.get('library'), bytes=size,
                               reason='Unfinished review or Photos operation; retained until inspected.'))
        return result


def capture_previous(data, legacy):
    """Called by the locked installer. No preference import and no Photos API."""
    history = History(data)
    value = read_strict(Path(legacy)/'workflow.json')
    if value and value.get('version') not in (3, 4):
        raise RuntimeError('Unsupported previous workflow; inspect it before installing.')
    jobs = value.get('jobs', [])
    if not isinstance(jobs, list) or any(not isinstance(job, dict) or not job.get('id') for job in jobs):
        raise RuntimeError('Previous workflow is malformed. It has been retained unchanged.')
    # Classify old completed replacements without invoking a migrating store.
    jobs = deepcopy(jobs)
    legacy_rows = value.get('legacy', [])
    if not isinstance(legacy_rows, list) or any(not isinstance(row, dict) for row in legacy_rows):
        raise RuntimeError('Previous legacy records are malformed. No history was cleared.')
    for record in legacy_rows:
        record = deepcopy(record)
        record.setdefault('id', hashlib.sha256(repr(sorted(record.items())).encode()).hexdigest())
        jobs.append(record)
    for job in jobs:
        if value.get('version') == 3 and job.get('original_removed') and job.get('verified') and job.get('new_uuid'):
            job.update(state='Completed', decision='keep', replacement_complete=True)
    history.save_links(jobs)
    known = {entry['id'] for entry in history.previous}
    for job in jobs:
        if not protected(job):
            continue
        entry_id = hashlib.sha256((str(Path(legacy).resolve())+'|'+job['id']).encode()).hexdigest()
        if entry_id in known:
            continue
        operation = job.get('operation')
        journal = (str(Path(legacy)/'NativeJournals'/(operation['id']+'.json'))
                   if isinstance(operation, dict) and operation.get('id') else job.get('legacy_journal') or job.get('journal'))
        history.previous.append(dict(id=entry_id, source_root=str(Path(legacy).resolve()),
                                     journal=journal, job=job, restored=False, workflow_version=value.get('version')))
    history.save_previous()
    history.retain_files(cleanup_preview(legacy, jobs)['retained'])
    return jobs


def clear_previous_history(legacy, jobs):
    """Archive must have been fsynced first. Recovery stays at its old paths."""
    legacy = Path(legacy).resolve()
    pending_file = StateFile(legacy/'campresso_cleanup_pending.json')
    preview = cleanup_preview(legacy, jobs)
    pending = read_strict(pending_file.path)
    preview['files'] = {**pending.get('files', {}), **preview['files']}
    pending_file.write(dict(version=1, files=preview['files']))
    StateFile(legacy/'workflow.json').write(dict(version=4, jobs=[job for job in jobs if protected(job)]))
    outcome = delete_preview_files(legacy, preview)
    remaining = {path: stamp for path, stamp in preview['files'].items() if Path(path).exists()}
    pending_file.write(dict(version=1, files=remaining))
    return outcome
