"""Durable native-workflow records and explicit migration from the Tk workflow."""

import hashlib
import json
import re
from pathlib import Path
import shutil
import uuid

from .state_store import StateFile
from .workflow_logic import file_matches


def asset_key(identifier):
    return str(identifier or '').split('/')[0].upper()


def generated_ids(job):
    legacy = job.get('legacy_record') or {}
    identifiers = [job.get('result_id'), legacy.get('new_uuid'), legacy.get('last_new_uuid')]
    identifiers += legacy.get('uploaded_ids') or []
    for previous in legacy.get('previous_conversions') or []:
        if isinstance(previous, dict):
            identifiers += [previous.get('new_uuid'), previous.get('last_new_uuid')]
    return {asset_key(value) for value in identifiers if isinstance(value, str) and value}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_strict(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(result, dict):
            raise ValueError('expected a JSON object')
        return result
    except (OSError, ValueError) as error:
        raise RuntimeError(f'Cannot read {path.name}. The original file has been kept. '
                           'Restore a backup before processing more videos.') from error


class NativeStore:
    VERSION = 4

    def __init__(self, data):
        self.data = Path(data).expanduser().resolve()
        self.file = StateFile(self.data / 'workflow.json')
        value = read_strict(self.file.path)
        version = value.get('version', 3)
        if version not in (3, 4):
            raise RuntimeError(f'Unsupported workflow version {version}; no data was changed.')
        if not isinstance(value.get('jobs', []), list):
            raise RuntimeError('The saved workflow has an invalid jobs list. No data was changed.')
        self.legacy = value.get('legacy', [])
        self.jobs = {}
        if version == 3 and value:
            backup = self.data / 'Migrations' / ('workflow-v3-' + uuid.uuid4().hex + '.json')
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.file.path, backup)
            preferences = self.data / 'preferences.json'
            if preferences.exists():
                preference_backup = backup.with_name(backup.name.replace('workflow-', 'preferences-', 1))
                shutil.copy2(preferences, preference_backup)
                with preference_backup.open('rb') as stream:
                    import os
                    os.fsync(stream.fileno())
            with backup.open('rb') as stream:
                import os
                os.fsync(stream.fileno())
            directory = os.open(backup.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        for record in value.get('jobs', []):
            if not isinstance(record, dict):
                raise RuntimeError('Invalid workflow record; restore a backup before continuing.')
            if version == 3:
                record = self.migrate(record)
                if record is None:
                    continue
            if not isinstance(record.get('id'), str) or not record.get('source_uuid'):
                raise RuntimeError('A workflow record is missing its identity. No records were discarded.')
            if not re.fullmatch(r'[A-Za-z0-9-]{1,80}', record['id']):
                raise RuntimeError('A workflow record has an unsafe identity. No records were discarded.')
            if record.get('generation') and not re.fullmatch(r'[a-f0-9]{32}', record['generation']):
                raise RuntimeError('A workflow record has an invalid generation. No records were discarded.')
            record['approval'] = None
            if record.get('state') in ('Queued', 'Downloading', 'Prepared', 'Converting', 'Verifying', 'Awaiting save'):
                record.update(state='Paused', detail='Compression interrupted. Resume when ready.')
            elif record.get('state') in ('Saving to Photos', 'Applying decision'):
                record.update(state='Needs attention', detail='Inspect the recorded Photos operation before retrying.')
            record.setdefault('generation', uuid.uuid4().hex)
            record.setdefault('operations', [])
            if record['id'] in self.jobs:
                raise RuntimeError('Duplicate workflow identity. Restore a backup before continuing.')
            self.jobs[record['id']] = record
        # Reading a valid old state never implicitly starts an import or deletion.
        if version == 3 and value:
            self.save()

    def migrate(self, old):
        media = old.get('media')
        if not isinstance(media, dict) or not media.get('uuid'):
            self.legacy.append(old)
            return None
        complete = bool(old.get('original_removed') and old.get('verified') and old.get('new_uuid'))
        operation = old.get('operation')
        unsafe = (not old.get('retry_safe', True) or bool(operation and operation not in ('convert', 'fetch_converted', 'service_download'))
                  or bool(old.get('original_removed') and not complete))
        local = file_matches(old.get('output'), old.get('output_fingerprint'))
        state = ('Needs attention' if unsafe else 'Completed' if complete else
                 'Ready to review' if old.get('verified') and old.get('new_uuid') else
                 'Awaiting import' if local and old.get('converted') else 'Ready')
        return dict(
            id=old.get('id') or uuid.uuid4().hex, source_uuid=asset_key(media['uuid']),
            original_id=media['uuid'], result_id=old.get('new_uuid'), library=old.get('library'),
            media=media, name=old['name'], generation=uuid.uuid4().hex,
            state=state, detail=old.get('detail', ''), progress=None,
            source_size=old.get('source_size') or media.get('size'),
            result_size=old.get('result_size') or (old.get('output_fingerprint') or [None])[0],
            expected=old.get('expected'), original_version=None, result_version=None,
            decision='keep' if complete else None, approval=None, operations=[],
            output=old.get('output') if local else None,
            output_fingerprint=old.get('output_fingerprint') if local else None,
            output_sha256=digest(old['output']) if local else None,
            legacy_journal=old.get('journal'), legacy_record=old,
            excluded=complete, warnings=[],
        )

    def save(self):
        self.file.write(dict(version=self.VERSION, jobs=list(self.jobs.values()), legacy=self.legacy))

    def find(self, identifier, library):
        return next((job for job in self.jobs.values()
                     if asset_key(job['source_uuid']) == asset_key(identifier)
                     and job.get('library') == library), None)

    def create(self, media, library, settings):
        job = dict(id=uuid.uuid4().hex, source_uuid=asset_key(media['uuid']),
                   original_id=media['uuid'], result_id=None, media=dict(media), library=library,
                   name=media['name'], generation=uuid.uuid4().hex, state='Queued',
                   progress=None, detail='', source_size=media.get('size'), result_size=None,
                   expected=None, original_version=None, result_version=None, decision=None,
                   approval=None, operations=[], output=None, output_sha256=None,
                   profile=dict(settings), warnings=[], excluded=False)
        self.jobs[job['id']] = job
        return job
