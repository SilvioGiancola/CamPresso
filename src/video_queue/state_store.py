"""Small atomic JSON files for the library preference and saved workflow."""
import json, os, tempfile
from pathlib import Path

class StateFile:
    def __init__(self,path):
        self.path=Path(path).expanduser().resolve()
        if any(part.lower().endswith('.photoslibrary') for part in self.path.parts):
            raise RuntimeError('Keep application settings outside the Photos library.')
    def read(self):
        try:
            data=json.loads(self.path.read_text(encoding='utf-8'))
            return data if isinstance(data,dict) else {}
        except (OSError,ValueError):return {}
    def write(self,data):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='.'+self.path.name+'-',dir=self.path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as output:
                json.dump(data,output,indent=2,allow_nan=False)
                output.flush();os.fsync(output.fileno())
            os.replace(name,self.path)
        finally:Path(name).unlink(missing_ok=True)
