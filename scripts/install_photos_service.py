"""Build the Photos-only Automator Service without executing the workflow."""
import argparse, plistlib, uuid
from importlib.resources import files
from pathlib import Path

NAME='CamPresso — Compress to 1080p'

def build(destination):
    destination=Path(destination).expanduser()
    if destination.suffix!='.workflow':raise ValueError('Choose a .workflow bundle destination.')
    contents=destination/'Contents';resources=contents/'Resources';resources.mkdir(parents=True,exist_ok=True)
    source=files('campresso').joinpath('resources/photos_service.applescript').read_text(encoding='utf-8')
    service=dict(NSMenuItem={'default':NAME},NSMessage='runWorkflowAsService',NSRequiredContext={'NSApplicationIdentifier':'com.apple.Photos'},NSReturnTypes=[],NSSendTypes=[],NSServiceDescription='Compress the selected videos to 1080p, add copies for review in CamPresso, and keep originals until you decide.',NSTimeout='120000')
    info=dict(CFBundleIdentifier='local.silvio.campresso.convert1080service',CFBundleName=NAME,CFBundleVersion='4',NSServices=[service])
    accepts=dict(Container='List',Optional=True,Types=['com.apple.applescript.object'])
    provides=dict(Container='List',Types=['com.apple.applescript.object'])
    action=dict(AMAccepts=accepts,AMActionVersion='1.0.2',AMParameterProperties={'source':{}},AMProvides=provides,AMRequiredResources=[],ActionBundlePath='/System/Library/Automator/Run AppleScript.action',ActionName='Run AppleScript',ActionParameters={'source':source},Application=['Automator'],BundleIdentifier='com.apple.Automator.RunScript',CFBundleVersion='1.0.2',CanShowSelectedItemsWhenRun=False,CanShowWhenRun=True,Category=['AMCategoryUtilities'],InputUUID=str(uuid.uuid4()).upper(),OutputUUID=str(uuid.uuid4()).upper(),UUID=str(uuid.uuid4()).upper(),UnlocalizedApplications=['Automator'],isViewVisible=True,ShowWhenRun=False)
    action['Class Name']='RunScriptAction'
    application='/System/Applications/Photos.app'
    metadata=dict(applicationBundleID='com.apple.Photos',applicationBundleIDs=['com.apple.Photos'],applicationPath=application,applicationPaths=[application],inputTypeIdentifier='com.apple.Automator.nothing',outputTypeIdentifier='com.apple.Automator.nothing',processesInput=0,serviceApplicationBundleID='com.apple.Photos',serviceApplicationPath=application,serviceApplicationName='Photos',serviceInputTypeIdentifier='com.apple.Automator.nothing',serviceOutputTypeIdentifier='com.apple.Automator.nothing',serviceProcessesInput=0,useAutomaticInputType=False,workflowTypeIdentifier='com.apple.Automator.servicesMenu')
    workflow=dict(AMDocumentVersion='2',AMApplicationBuild='523',AMApplicationVersion='2.10',actions=[dict(action=action,isViewVisible=True)],connectors={},workflowMetaData=metadata)
    (contents/'Info.plist').write_bytes(plistlib.dumps(info,sort_keys=False))
    (resources/'document.wflow').write_bytes(plistlib.dumps(workflow,sort_keys=False))
    return destination

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('destination',nargs='?',default=str(Path.home()/'Library'/'Services'/(NAME+'.workflow')))
    print(build(parser.parse_args().destination))
