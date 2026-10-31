"""Start an empty workflow without replaying or changing any Photos operation."""

from copy import deepcopy
import hashlib
import json
import logging
from pathlib import Path
import re

from .history import cleanup_preview, delete_preview_files, protected, references, uncertain
from .native_store import asset_key, generated_ids, read_strict


def start_fresh(store, history):
    """Publish safety records before clearing jobs; deleting media comes last."""
    jobs = list(store.jobs.values())
    legacy = store.legacy
    if not isinstance(legacy, list) or any(not isinstance(row, dict) for row in legacy):
        raise RuntimeError('Invalid legacy records; no session state was cleared.')
    prior = [entry for entry in history.previous if not entry.get('session_archived')]
    if not jobs and not legacy and not prior:
        return

    records = deepcopy(jobs + legacy)
    for job in records:
        if not isinstance(job.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', job['id']):
            job['id'] = hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()
        job['approval'] = None
        # Only IDs in matching app journals are used, never a guessed filename.
        operation = job.get('operation')
        operation_id = operation.get('id') if isinstance(operation, dict) else None
        if isinstance(operation_id, str) and re.fullmatch(r'[a-f0-9]{32}', operation_id):
            path = history.data/'NativeJournals'/f'{operation_id}.json'
            if path.is_file() and path.resolve() == path and not path.is_symlink():
                try:
                    receipt = read_strict(path)
                except RuntimeError:
                    logging.exception('Unreadable native journal retained; source remains protected')
                    receipt = {}
                if (receipt.get('job_id') == job['id'] and receipt.get('generation') == job.get('generation')
                        and receipt.get('library') == job.get('library')
                        and asset_key(receipt.get('original_id')) == asset_key(job.get('original_id'))):
                    if receipt.get('result_id'):
                        job['result_id'] = receipt['result_id']
        if not uncertain(job) and not generated_ids(job) and job.get('state') != 'Completed':
            job.update(state='Cancelled', operation=None, retry_safe=True)

    # Previous-installation records become a quiet archive too. Current records
    # take precedence if a previously restored pair was decided this session.
    history.save_links([entry['job'] for entry in prior if not entry.get('restored')] + records)

    record_ids = {job['id'] for job in records}
    guards = [dict(entry['job'], state='Needs attention') for entry in history.previous
              if not entry.get('restored') and not
              (Path(entry['source_root']) == history.data and entry['job'].get('id') in record_ids)]
    plan = cleanup_preview(history.data, records + guards)
    entries = {entry['id']: deepcopy(entry) for entry in history.previous}
    for entry in entries.values():
        entry['session_archived'] = True
        entry['job']['approval'] = None
    for job in records:
        folder = history.data/'NativeWork'/job['id']
        retained = any(str(value) in plan['retained'] for value, _ in references(job))
        if not (protected(job) or retained or folder.exists()):
            continue
        entry_id = hashlib.sha256((str(history.data)+'|'+job['id']).encode()).hexdigest()
        operation = job.get('operation')
        operation_id = operation.get('id') if isinstance(operation, dict) else None
        journal = (str(history.data/'NativeJournals'/f'{operation_id}.json')
                   if isinstance(operation_id, str) and re.fullmatch(r'[a-f0-9]{32}', operation_id) else job.get('legacy_journal'))
        entries[entry_id] = dict(id=entry_id, source_root=str(history.data), journal=journal,
                                job=job, restored=False, workflow_version=store.VERSION,
                                session_archived=True)
    history.previous = list(entries.values())
    history.save_previous()
    history.retain_files(plan['retained'])
    pending = read_strict(history.pending_file.path).get('files', {})
    history.pending_file.write(dict(version=1, files={**pending, **plan['files']}))

    # All associations/archive/cleanup intent are fsynced before this commit.
    store.jobs.clear(); store.legacy = []; store.save()
    delete_preview_files(history.data, plan)
    remaining = {path: stamp for path, stamp in {**pending, **plan['files']}.items() if Path(path).exists()}
    history.pending_file.write(dict(version=1, files=remaining))


def discard_old_requests(data, started_at, launch_request=None):
    """Keep an explicit new Service invocation, never an old queued request."""
    directory = Path(data)/'Requests'
    if not directory.is_dir() or directory.is_symlink():
        return
    for path in directory.glob('*.json'):
        try:
            if path.is_symlink() or not re.fullmatch(r'[a-f0-9]{32}', path.stem):
                continue
            if path.stem == launch_request or path.stat().st_mtime >= started_at:
                continue
            path.unlink()
        except OSError:
            # The consumer also checks session age, so failed removals never
            # turn into queued work later in this session.
            logging.exception('Old Service request retained but will not run')
