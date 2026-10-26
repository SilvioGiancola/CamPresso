"""Exercise native callback contracts without changing a real Photos library."""
import unittest
from types import SimpleNamespace, ModuleType
from unittest.mock import patch
from video_queue.photos_actions import Actions

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.status=0;self.success=True;self.error=None;self.returns=[]
        test=self
        class Library:
            @staticmethod
            def sharedPhotoLibrary():return Library()
            @staticmethod
            def authorizationStatusForAccessLevel_(level):return test.status
            @staticmethod
            def requestAuthorizationForAccessLevel_handler_(level,handler):
                test.assertIsNone(handler(3))
            def performChanges_completionHandler_(self,block,completion):
                test.assertIsNone(block())
                test.assertIsNone(completion(test.success,test.error))
        photos=ModuleType('Photos')
        photos.PHPhotoLibrary=Library
        photos.PHAccessLevelReadWrite=2;photos.PHAuthorizationStatusNotDetermined=0;photos.PHAuthorizationStatusAuthorized=3
        photos.PHAssetChangeRequest=SimpleNamespace(deleteAssets_=lambda assets:'ignored native result')
        osxphotos=ModuleType('osxphotos');osxphotos.__path__=[]
        utils=ModuleType('osxphotos.utils');utils.get_system_library_path=lambda:'/System.photoslibrary'
        foundation=ModuleType('Foundation')
        foundation.NSRunLoop=SimpleNamespace(currentRunLoop=lambda:SimpleNamespace())
        foundation.NSDate=SimpleNamespace();foundation.NSURL=SimpleNamespace(fileURLWithPath_=lambda path:path)
        self.modules=patch.dict('sys.modules',{'Photos':photos,'Foundation':foundation,'osxphotos':osxphotos,'osxphotos.utils':utils})
        self.modules.start();self.addCleanup(self.modules.stop)
        self.actions=Actions('/System.photoslibrary')
    def test_permission_handler_returns_none(self):
        # setUp requests permission; the strict stub rejects the old tuple callback.
        self.assertIsNotNone(self.actions.lib)
    def test_completion_and_change_callbacks_return_none(self):
        self.actions.change(lambda:('discard','this result'))
    def test_change_block_exception_becomes_python_failure(self):
        def broken():raise ValueError('bad request')
        with self.assertRaisesRegex(RuntimeError,'bad request'):self.actions.change(broken)
    def test_native_failure_without_error_has_useful_message(self):
        self.success=False
        with self.assertRaisesRegex(RuntimeError,'Photos rejected'):self.actions.change(lambda:None)
    def test_removal_block_discards_native_return(self):
        self.actions.remove('original')
    def test_placeholder_is_recorded_before_commit_finishes(self):
        placeholder=SimpleNamespace(localIdentifier=lambda:'NEW/L0/001')
        request=SimpleNamespace(placeholderForCreatedAsset=lambda:placeholder)
        self.actions.P.PHAssetChangeRequest.creationRequestForAssetFromVideoAtFileURL_=lambda url:request
        recorded=[];self.actions.on_import_created=recorded.append
        def find(identifier):
            self.assertEqual(recorded,['NEW/L0/001'])
            return placeholder
        self.actions.find=find
        self.assertIs(self.actions.import_video('/converted.mp4'),placeholder)
    def test_non_system_library_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'System Photo Library'):Actions('/Other.photoslibrary')

if __name__=='__main__':unittest.main()
