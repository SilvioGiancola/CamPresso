"""Verify native encoder output using the existing cancellable FFmpeg runner."""

import math
from pathlib import Path

from .engine import Converter, Skipped


def verify_output(path, expected, source_size, cancel, report):
    converter = Converter()
    converter.cancel = cancel
    info = converter.probe(path)
    video = next((stream for stream in info['streams'] if stream.get('codec_type') == 'video'
                  and not stream.get('disposition', {}).get('attached_pic')), None)
    if not video:
        raise RuntimeError('The compressed file has no video stream.')
    duration = float(info['format'].get('duration', 0))
    reference = expected['duration']
    if not math.isfinite(duration) or abs(duration-reference) > max(.5, reference*.005):
        raise RuntimeError('The compressed duration differs from the rendered Photos video.')
    if (video['width'], video['height']) != (expected['width'], expected['height']):
        raise RuntimeError('The compressed dimensions do not match the selected profile.')
    numerator, _, denominator = str(video.get('avg_frame_rate', '0/1')).partition('/')
    fps = float(numerator) / float(denominator or 1) if float(denominator or 1) else 0
    if not fps or abs(fps-expected.get('fps', fps)) > max(1, expected.get('fps', fps)*.02):
        raise RuntimeError('The compressed frame timing does not match the rendered Photos video.')
    if expected.get('hdr'):
        transfer = video.get('color_transfer')
        wanted = 'arib-std-b67' if expected['transfer'] == 'HLG' else 'smpte2084'
        if transfer != wanted or video.get('color_primaries') != 'bt2020' or '10' not in video.get('pix_fmt', ''):
            raise RuntimeError('HDR verification failed: transfer, color primaries or 10-bit depth was lost.')
        if any('DOVI' in str(item).upper() or 'DOLBY' in str(item).upper()
               for item in video.get('side_data_list', [])):
            raise RuntimeError('Unexpected Dolby Vision metadata remained after HDR conversion.')
    audio = [stream for stream in info['streams'] if stream.get('codec_type') == 'audio']
    if len(audio) != expected.get('audio_count', len(audio)):
        raise RuntimeError('Compressed audio track verification failed.')
    report('Checking complete playback', None)
    converter.run([converter.tools['ffmpeg'], '-v', 'error', '-xerror', '-threads', '2',
                   '-filter_threads', '1', '-i', str(path),
                   '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'])
    size = Path(path).stat().st_size
    if not source_size:
        raise Skipped('Original stored size is unknown. No copy was added to Photos.')
    if size >= source_size:
        raise Skipped('The result is not smaller. Original kept; no copy was added to Photos.')
    return size
