"""Preserve QuickTime movie and track dates across explicit stream remapping.

Only creation/modification fields in mvhd/tkhd/mdhd atoms are written. Encoded
frames, durations, track IDs, dimensions, and rotation remain untouched.
"""
from dataclasses import dataclass
import struct

@dataclass
class Track:
    id: int
    group: str
    dates: dict

# Atom offsets and lengths are validated against the enclosing box.
def boxes(f,start,end):
    pos=start
    while pos<end:
        if end-pos<8:raise ValueError('Incomplete QuickTime atom header')
        f.seek(pos);size,kind=struct.unpack('>I4s',f.read(8));header=8
        if size==1:
            if end-pos<16:raise ValueError('Incomplete extended atom header')
            size=struct.unpack('>Q',f.read(8))[0];header=16
        elif size==0:size=end-pos
        if size<header or pos+size>end:raise ValueError('Invalid QuickTime atom size')
        yield kind,pos+header,pos+size
        pos+=size

def date_field(f,start,end):
    f.seek(start);version=f.read(1)
    if version not in (b'\x00',b'\x01'):raise ValueError('Unsupported QuickTime header version')
    width=4 if version==b'\x00' else 8
    if start+4+width*2>end:raise ValueError('Truncated QuickTime date header')
    f.seek(start+4);raw=f.read(width*2)
    return start+4,width,tuple(int.from_bytes(raw[i:i+width],'big') for i in (0,width))

def movie_dates(path):
    with open(path,'rb') as f:
        f.seek(0,2);length=f.tell()
        for kind,start,end in boxes(f,0,length):
            if kind==b'moov':
                for child,hs,he in boxes(f,start,end):
                    if child==b'mvhd':return date_field(f,hs,he)
    raise ValueError('Missing QuickTime movie header')

def restore_movie_dates(source,output):
    # FFmpeg can populate an unset ModifyDate from CreationDate, and ExifTool's
    # bulk copy can omit zero dates. Preserve the raw values, including zero.
    _,_,values=movie_dates(source);offset,width,_=movie_dates(output)
    if any(value>=1<<(width*8) for value in values):raise ValueError('Original movie timestamp cannot fit output header')
    with open(output,'r+b') as f:
        f.seek(offset);f.write(b''.join(value.to_bytes(width,'big') for value in values));f.flush()
    if movie_dates(output)[2]!=values:raise ValueError('QuickTime movie date verification failed')

def tracks(path):
    result=[]
    with open(path,'rb') as f:
        f.seek(0,2);length=f.tell()
        for kind,start,end in boxes(f,0,length):
            if kind!=b'moov':continue
            for kind,ts,te in boxes(f,start,end):
                if kind!=b'trak':continue
                dates={};trackid=None
                for kind,hs,he in boxes(f,ts,te):
                    if kind==b'tkhd':
                        field=date_field(f,hs,he);dates['tkhd']=field
                        offset,width,_=field
                        if offset+width*2+4>he:raise ValueError('Truncated track ID')
                        f.seek(offset+width*2);trackid=int.from_bytes(f.read(4),'big')
                    elif kind==b'mdia':
                        for kind,ms,me in boxes(f,hs,he):
                            if kind==b'mdhd':dates['mdhd']=date_field(f,ms,me)
                if trackid is None:raise ValueError('Missing QuickTime track ID')
                result.append(Track(trackid,f'Track{len(result)+1}',dates))
    if not result:raise ValueError('No QuickTime tracks found')
    return result

def stream_id(stream):
    value=stream.get('id')
    if value is None:raise ValueError('Cannot identify source/output QuickTime track')
    return int(str(value),16) if str(value).lower().startswith('0x') else int(value)

def restore_track_dates(source,output,source_streams,output_streams):
    """Return all original groups, and source→output groups for retained tracks."""
    restore_movie_dates(source,output)
    src=tracks(source);dst=tracks(output)
    if len(source_streams)!=len(output_streams):
        describe=lambda streams: ', '.join(f"{s.get('codec_type')} index={s.get('index')} id={s.get('id')} codec={s.get('codec_name')}" for s in streams) or '(none)'
        raise ValueError(f'Retained track count differs: expected {len(source_streams)}, found {len(output_streams)}.\nOriginal mapped tracks: {describe(source_streams)}\nOutput media tracks: {describe(output_streams)}')
    if any(a.get('codec_type')!=b.get('codec_type') for a,b in zip(source_streams,output_streams)):
        raise ValueError('Output track types do not match the selected original tracks')
    srcbyid={t.id:t for t in src};dstbyid={t.id:t for t in dst}
    mapping={};checks=[]
    with open(output,'r+b') as f:
        for old,new in zip(source_streams,output_streams):
            a=srcbyid[stream_id(old)];b=dstbyid[stream_id(new)];mapping[a.group]=b.group
            for tag,(_,_,values) in a.dates.items():
                if tag not in b.dates:raise ValueError(f'Missing output {tag} header')
                offset,width,_=b.dates[tag]
                if any(v>=1<<(width*8) for v in values):raise ValueError('Original timestamp cannot fit output header')
                f.seek(offset);f.write(b''.join(v.to_bytes(width,'big') for v in values))
                checks.append((b.id,tag,values))
        f.flush()
    verified={t.id:t for t in tracks(output)}
    for tid,tag,values in checks:
        if verified[tid].dates[tag][2]!=values:raise ValueError('QuickTime track date verification failed')
    return {t.group for t in src},mapping

def metadata_mismatches(before,after,source_groups=(),mapping=None):
    mapping=mapping or {};source_groups=set(source_groups)
    errors=[]
    for key,value in before.items():
        group,sep,tag=key.partition(':')
        target=key
        if group in source_groups:
            if group not in mapping:continue # Track explicitly omitted from conversion.
            target=mapping[group]+':'+tag
        if after.get(target)!=value:
            errors.append(f'{key} → {target}: expected {value!r}, got {after.get(target)!r}')
    return errors
