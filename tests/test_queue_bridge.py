"""Standard-library contracts for the optional queue bridge."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    from cloudkit.runtime import queue_bridge
except ImportError:
    import queue_bridge


class OptionalQueueContracts(unittest.TestCase):
    def test_import_does_not_require_optional_packages(self):
        module_dir = str(Path(queue_bridge.__file__).parent)
        code = 'import sys;sys.path.insert(0,sys.argv[1]);import queue_bridge;print("loaded-without-site-packages")'
        result = subprocess.run([sys.executable, '-I', '-S', '-c', code, module_dir], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), 'loaded-without-site-packages')

    def test_inspection_without_optional_packages_preserves_git_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary);remote = root/'control.git';checkout = root/'control'
            subprocess.run(['git','init','--bare','-q',str(remote)],check=True)
            subprocess.run(['git','clone','-q',str(remote),str(checkout)],check=True,capture_output=True)
            subprocess.run(['git','-C',str(checkout),'switch','-c','automation/state'],check=True,capture_output=True)
            state = {'schema_version':1,'status':'IDLE','execution_route':'chatgpt-private-runner',
                     'autonomy_tasks':{'goal':{'status':'READY','outbox':{'event_id':'one','status':'PENDING'},'processed_events':[]}}}
            (checkout/'STATE.json').write_text(json.dumps(state))
            subprocess.run(['git','-C',str(checkout),'add','STATE.json'],check=True)
            subprocess.run(['git','-C',str(checkout),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                            'commit','-qm','control'],check=True)
            subprocess.run(['git','-C',str(checkout),'push','-q','origin','automation/state'],check=True)
            before = subprocess.check_output(['git','-C',str(checkout),'rev-parse','HEAD'])
            bootstrap = 'import sys,runpy;sys.path.insert(0,sys.argv[1]);sys.argv=sys.argv[2:];runpy.run_path(sys.argv[0],run_name="__main__")'
            result = subprocess.run([sys.executable,'-I','-S','-c',bootstrap,str(Path(queue_bridge.__file__).parent),str(Path(queue_bridge.__file__)),
                '--repository',str(checkout),'inspect','--task-id','goal'],check=True,capture_output=True,text=True)
            value = json.loads(result.stdout)
            self.assertFalse(value['redis']['available'])
            self.assertEqual(value['canonical_state'],'Git')
            self.assertEqual(value['outbox']['event_id'],'one')
            self.assertEqual(subprocess.check_output(['git','-C',str(checkout),'rev-parse','HEAD']),before)


if __name__ == '__main__':
    unittest.main()
