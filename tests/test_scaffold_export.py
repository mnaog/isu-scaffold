import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class ExportTest(unittest.TestCase):
    def test_pinned_generic_export_drift_and_dirty_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, target = base / 'source', base / 'target'
            for p in (source, target):
                p.mkdir()
                subprocess.run(['git', 'init', '-q', str(p)], check=True)
                for k,v in [('user.name','Fixture'),('user.email','fixture@example.invalid')]:
                    subprocess.run(['git','-C',str(p),'config',k,v],check=True)
            subprocess.run(['git','-C',str(source),'remote','add','origin','https://example.invalid/scaffold'],check=True)
            (source/'scripts').mkdir();(source/'config').mkdir()
            for name in ['sync-scaffold.py','check-phase0.py']:
                shutil.copyfile(ROOT/'scripts'/name,source/'scripts'/name)
            (source/'config/scaffold-export.json').write_text(json.dumps({'files':['scripts/check-phase0.py']}))
            subprocess.run(['git','-C',str(source),'add','.'],check=True)
            subprocess.run(['git','-C',str(source),'commit','-qm','fixture'],check=True)
            command=['python3',str(source/'scripts/sync-scaffold.py'),str(target)]
            self.assertEqual(subprocess.run(command,capture_output=True).returncode,1)
            self.assertEqual(subprocess.run(command+['--apply'],capture_output=True).returncode,0)
            self.assertEqual(subprocess.run(command,capture_output=True).returncode,0)
            subprocess.run(['git','-C',str(target),'add','.'],check=True)
            subprocess.run(['git','-C',str(target),'commit','-qm','export'],check=True)
            (target/'scripts/check-phase0.py').write_text('local edits')
            result=subprocess.run(command+['--apply'],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('local changes',result.stderr)
            self.assertEqual((target/'scripts/check-phase0.py').read_text(),'local edits')
