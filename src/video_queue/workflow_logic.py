"""Display and eligibility rules for Compress → Review → Replace.

The saved native journal remains the authority for Photos changes. These rules
never infer a successful replacement from conversion alone.
"""
from pathlib import Path


def file_matches(path, fingerprint):
    try:
        target = Path(path)
        stat = target.stat()
        return not target.is_symlink() and stat.st_size > 0 and [stat.st_size, stat.st_mtime_ns] == fingerprint
    except (OSError, TypeError, ValueError):
        return False


def human_size(value):
    if value is None:
        return 'Unknown'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1000 or unit == 'TB':
            return f'{value:,.1f} {unit}'
        value /= 1000


def savings(job):
    if not job:
        return ''
    before, after = job.get('source_size'), job.get('result_size')
    if not before or not after:
        return ''
    reduction=f'{(before-after)*100/before:.0f}% smaller' if after<before else 'no reduction'
    return f'{human_size(before)} → {human_size(after)} · {reduction}'


def reviewable(job):
    return bool(job and job.get('media') and job.get('converted') and job.get('expected')
                and not job.get('original_removed') and job.get('retry_safe', True)
                and (job.get('source_size') or 0) > (job.get('result_size') or 0) > 0
                and file_matches(job.get('output'), job.get('output_fingerprint')))


def can_compress(job):
    if not job:
        return True
    if job.get('original_removed') or not job.get('retry_safe', True):
        return False
    return not job.get('converted') or (not job.get('verified') and not job.get('new_uuid')
                                       and (not file_matches(job.get('output'), job.get('output_fingerprint'))
                                            or bool(job.get('source_size') and job.get('result_size') and job['result_size']>=job['source_size'])))


def status(job, transfer=None):
    if job and job.get('operation') == 'replace':
        return 'Replacing'
    if job and job.get('operation') == 'inspect':
        return 'Needs attention · checking Photos'
    if job and not job.get('retry_safe', True):
        return 'Needs attention'
    if job and job.get('operation') == 'convert':
        return 'Converting ' + job.get('progress', '…')
    if job and job.get('operation') == 'fetch_converted':
        return 'Downloading ' + job.get('progress', '…')
    if job and job.get('replacement_complete'):
        return 'Done'
    if job and job.get('original_removed'):
        return 'Needs attention'
    if transfer:
        label = transfer[0]
        if 'Queued' in label:
            return 'Queued'
        # The download queue reports actual bytes / PhotoKit percentages.
        import re
        percent = re.search(r'\d+%', label)
        return 'Downloading ' + (percent.group() if percent else '…')
    if job:
        state = job.get('state')
        if state in ('Failed', 'Error', 'Cancelled', 'Skipped', 'Paused'):
            return 'Error' if state == 'Failed' else state
        if job.get('compression_pending') or state == 'Queued for conversion':
            return 'Queued'
        if state == 'Queued for replacement':
            return 'Queued · replacement'
        if job.get('converted'):
            if job.get('source_size') and job.get('result_size') and job['result_size']>=job['source_size']:
                return 'Skipped · no size reduction'
            if file_matches(job.get('output'), job.get('output_fingerprint')) or job.get('verified'):
                return 'Ready to review'
            return 'Error · result changed or missing'
    return 'Ready'


def require_system_library(library):
    from osxphotos.utils import get_system_library_path
    system = get_system_library_path()
    if not library or not system or Path(library).expanduser().resolve() != Path(system).expanduser().resolve():
        raise RuntimeError('Choose your System Photo Library before compressing or replacing videos. '
                           'In Photos → Settings → General, check which library is the System Photo Library, '
                           'then select it here. No videos have been changed.')
