"""Locate one exact asset in Photos through its public scripting dictionary."""
import re, subprocess, threading, time, uuid

SCRIPT='''on run arguments
    set targetIdentifier to item 1 of arguments
    with timeout of 25 seconds
        tell application id "com.apple.Photos"
            if not (exists media item id targetIdentifier) then error "This item was not found in the open Photos library. Open the library used in CamPresso and try again. The item may have been removed from Photos."
            activate
            spotlight (media item id targetIdentifier)
        end tell
    end timeout
end run
'''

def show_in_photos(identifier,cancel=None):
    cancel=cancel or threading.Event()
    if not isinstance(identifier,str):raise ValueError('No Photos asset is selected.')
    parts=identifier.split('/',1);base=str(uuid.UUID(parts[0])).upper()
    suffix='/'+parts[1] if len(parts)>1 else '/L0/001'
    if not re.fullmatch(r'/L\d+/\d+',suffix):raise ValueError('Invalid Photos asset identifier.')
    if cancel.is_set():return
    process=subprocess.Popen(['/usr/bin/osascript','-e',SCRIPT,'--',base+suffix],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace')
    try:
        deadline=time.monotonic()+30
        while True:
            if cancel.is_set():return
            if time.monotonic()>deadline:raise RuntimeError('Photos did not respond. Open Photos and allow Automation access if macOS requests it, then try again.')
            try:_,error=process.communicate(timeout=.2);break
            except subprocess.TimeoutExpired:continue
        if process.returncode:raise RuntimeError(error.strip() or 'Photos could not show the selected item.')
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.communicate(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.communicate()
        if process.stdout:process.stdout.close()
        if process.stderr:process.stderr.close()
