"""Read one original PhotoKit resource, emitting real download progress."""
import json, math, re, signal, sys, threading, time
from pathlib import Path
from photos_actions import Actions
from photos_catalog import ensure_outside_library

def main():
    cancelled=threading.Event();done=threading.Event();lock=threading.Lock();errors=[]
    signal.signal(signal.SIGTERM,lambda *_:cancelled.set())
    signal.signal(signal.SIGINT,lambda *_:cancelled.set())
    manager=None;request_id=None;output=None;stopping=False
    def emit(packet):
        print(json.dumps(packet),flush=True)
    try:
        request=json.load(sys.stdin);api=Actions(request['library'])
        asset=api.check(request['uuid'])
        resources=api.P.PHAssetResource.assetResourcesForAsset_(asset)
        originals=[resource for resource in resources if resource.type()==api.P.PHAssetResourceTypeVideo]
        if len(originals)!=1:raise RuntimeError('Photos did not provide one original video resource.')
        resource=originals[0]
        directory=ensure_outside_library(request['destination'],request['library'])
        directory.mkdir(parents=True,exist_ok=True)
        name=re.sub(r'[/\\\x00-\x1f]','_',str(resource.originalFilename())).strip()
        if not name or name in ('.','..'):raise RuntimeError('Photos returned an invalid original filename.')
        target=directory/name
        options=api.P.PHAssetResourceRequestOptions.alloc().init();options.setNetworkAccessAllowed_(True)
        last=[-1]
        def progress(fraction):
            # Every native void callback returns None and traps Python errors.
            try:
                value=float(fraction)
                if math.isfinite(value):
                    percent=max(0,min(100,int(value*100)))
                    with lock:
                        if not stopping and percent!=last[0]:
                            emit({'event':'progress','percent':percent});last[0]=percent
            except Exception as error:
                with lock:errors.append(str(error));done.set()
        def received(data):
            with lock:
                if stopping or errors:return
                try:output.write(data)
                except Exception as error:errors.append(str(error));done.set()
        def completed(error):
            with lock:
                if error is not None:errors.append(str(error))
                done.set()
        options.setProgressHandler_(progress)
        manager=api.P.PHAssetResourceManager.defaultManager()
        output=target.open('xb')
        request_id=manager.requestDataForAssetResource_options_dataReceivedHandler_completionHandler_(resource,options,received,completed)
        deadline=time.monotonic()+1800
        while not done.is_set():
            if cancelled.is_set():raise RuntimeError('Download cancelled.')
            if time.monotonic()>deadline:raise RuntimeError('Photos download timed out.')
            api.loop.runUntilDate_(api.Date.dateWithTimeIntervalSinceNow_(.1))
        with lock:
            stopping=True;output.flush();output.close();output=None
            if errors:raise RuntimeError('; '.join(errors))
        if cancelled.is_set():raise RuntimeError('Download cancelled.')
        if not target.stat().st_size:raise RuntimeError('Photos returned an empty video.')
        emit({'event':'complete','filename':name})
    except Exception as error:
        with lock:stopping=True
        if manager is not None and request_id is not None:manager.cancelDataRequest_(request_id)
        emit({'event':'error','error':str(error)});return 1
    finally:
        with lock:
            stopping=True
            if output is not None:output.close()
    return 0

if __name__=='__main__':sys.exit(main())
