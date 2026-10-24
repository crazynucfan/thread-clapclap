"""Check saved-firmware upload behavior without accessing a serial device."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import project


class RollbackTests(unittest.TestCase):
    def upload(self, directory, corrupted=False):
        backup=directory/'saved firmware';backup.mkdir()
        image=backup/'ThreadClapClap.ino.bin';image.write_bytes(b'firmware fixture, not audio')
        digest=hashlib.sha256(image.read_bytes()).hexdigest()
        (backup/'build-manifest.json').write_text(json.dumps({'firmware_sha256':digest}))
        if corrupted:image.write_bytes(b'changed firmware fixture')
        argv=['project.py','--cli','fake-arduino-cli','flash','--port','TEST_NO_DEVICE','--input-dir',str(backup)]
        installed=json.dumps({'platforms':[{'id':'esp32:esp32','installed_version':project.CORE}]})
        error=io.StringIO()
        with patch.object(sys,'argv',argv),patch.object(project.shutil,'which',return_value='fake-arduino-cli'), \
                patch.object(project.subprocess,'check_output',return_value=installed), \
                patch.object(project,'run') as upload,contextlib.redirect_stderr(error):
            try:
                project.main();code=0
            except SystemExit as failure:
                code=failure.code
        return backup.resolve(),code,error.getvalue(),upload.call_args_list

    def test_rollback_upload_uses_explicit_saved_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            backup,code,error,calls=self.upload(Path(temporary))
        self.assertEqual(code,0,error)
        self.assertEqual(len(calls),1)
        args=calls[0].args[0]
        self.assertEqual(args[args.index('--input-dir')+1],backup)
        self.assertEqual(args[args.index('--port')+1],'TEST_NO_DEVICE')
        self.assertNotIn('--erase-all',args)

    def test_changed_rollback_image_is_rejected_before_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            _,code,error,calls=self.upload(Path(temporary),corrupted=True)
        self.assertNotEqual(code,0)
        self.assertIn('firmware differs from its build manifest',error)
        self.assertEqual(calls,[])


if __name__=='__main__':unittest.main()
