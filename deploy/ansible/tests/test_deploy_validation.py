import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class DeployValidationTests(unittest.TestCase):
    def run_playbook(self,play='tests/deploy-validate.yml',**changes):
        values={'kai_release_archive':'/private/tmp/reviewed.tar.gz','kai_release_sha256':'a'*64}
        values.update(changes)
        return subprocess.run([str(ROOT/'.venv/bin/ansible-playbook'),'-i','localhost,',play,'--extra-vars',json.dumps(values)],
          cwd=ROOT,capture_output=True,text=True,env=dict(os.environ,ANSIBLE_HOME=str(ROOT/'.cache/ansible'),ANSIBLE_NOCOLOR='1'))

    def test_valid_inputs_have_no_host_mutations(self):
        result=self.run_playbook();self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('changed=0',result.stdout)

    def test_unreviewed_source_invalid_profiles_and_production_are_refused(self):
        for changes in [{'kai_release_archive':'relative.tar.gz'},{'kai_release_sha256':'bad'},
                        {'kai_deploy_profiles':['documents']},{'kai_deploy_profiles':'admin'},
                        {'kai_deploy_profiles':['admin','admin']},{'kai_deploy_environment':'production'}]:
            with self.subTest(changes=changes):
                result=self.run_playbook(**changes);self.assertNotEqual(result.returncode,0);self.assertIn('changed=0',result.stdout)

    def test_main_role_refuses_local_target_without_mutations(self):
        result=self.run_playbook(play='tests/deploy-local-denial.yml')
        self.assertNotEqual(result.returncode,0);self.assertIn('never localhost or a container',result.stdout);self.assertIn('changed=0',result.stdout)

    def test_engine_and_compose_floors_are_rechecked_without_a_daemon(self):
        for engine,compose,valid in [('28.0.0','v2.24.4',True),('27.5.0','v2.24.4',False),('28.0.0','v2.23.0',False)]:
            result=self.run_playbook(play='tests/deploy-engine-guard.yml',kai_deploy_engine_version={'stdout':engine},kai_deploy_compose_version={'stdout':compose})
            self.assertEqual(result.returncode==0,valid,result.stdout+result.stderr)

    def test_reuse_requires_matching_receipt_and_immutable_root_owned_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt=Path(directory)/'.kai-complete';receipt.write_text('a'*64+'\n')
            def state(mode='0755',marker=True):
                return {'results':[{'stat':{'exists':True,'isdir':True,'uid':0,'mode':mode}},
                    {'stat':{'exists':marker,'isreg':marker,'uid':0,'mode':'0644'}},{'stat':{'exists':False}}]}
            good=self.run_playbook(play='tests/deploy-release-guard.yml',kai_deploy_release=directory,kai_deploy_existing=state())
            self.assertEqual(good.returncode,0,good.stdout+good.stderr)
            for value in [state(mode='0777'),state(marker=False)]:
                result=self.run_playbook(play='tests/deploy-release-guard.yml',kai_deploy_release=directory,kai_deploy_existing=value)
                self.assertNotEqual(result.returncode,0)
            receipt.write_text('wrong\n')
            bad=self.run_playbook(play='tests/deploy-release-guard.yml',kai_deploy_release=directory,kai_deploy_existing=state())
            self.assertNotEqual(bad.returncode,0);self.assertIn('does not match',bad.stdout)
