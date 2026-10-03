import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent

def module(name, file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

check=module('deployment','check-deployment.py')
draft=module('draft_mysql','configure-draft.py')


class DeploymentRequirementsTests(unittest.TestCase):
    def test_mysql_uses_only_observed_shared_paths(self):
        observed=['/etc/mysql','/etc/mysql/mysql.cnf','/etc/mysql/conf.d','/etc/mysql/debian.cnf']
        items=draft.mysql_config_items('/etc/mysql','app1',observed)
        self.assertEqual({x['remote'] for x in items},{'/etc/mysql/mysql.cnf','/etc/mysql/conf.d'})
        self.assertTrue(all(x['node_group']=='role_mysql' for x in items))
        self.assertEqual(draft.mysql_config_items('/etc/mysql','app1',['/etc/mysql']),[])

    def test_credentials_and_new_sql_cannot_silently_ship_or_be_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);subprocess.run(['git','init','-q',str(repo)],check=True)
            path=repo/'webapp/sql/restore.sql';path.parent.mkdir(parents=True);path.write_text('SELECT 1;')
            subprocess.run(['git','-C',str(repo),'add','.'],check=True)
            manifest={'items':[]}
            with self.assertRaisesRegex(ValueError,'missing from sync'):check.check(repo,manifest)
            manifest['local_only_files']=[{'local':'webapp/sql/restore.sql','reason':'local fixture; never executed on servers'}]
            check.check(repo,manifest)
            manifest['items']=[{'type':'directory','local':'config/mysql','remote':'/etc/mysql'}]
            with self.assertRaisesRegex(ValueError,'wholesale'):check.check(repo,manifest)
            manifest['items']=[{'type':'file','local':'config/mysql/secret','remote':'/etc/mysql/debian.cnf'}]
            with self.assertRaisesRegex(ValueError,'debian.cnf'):check.check(repo,manifest)
