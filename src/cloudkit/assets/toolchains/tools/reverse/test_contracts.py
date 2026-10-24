"""Fast admission tests; do not download assets or require native reverse tools."""
import importlib.util,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('reverse_tools_contract',Path(__file__).with_name('reverse_tools.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class Contracts(unittest.TestCase):
    def test_old_json_cannot_make_a_failed_postscript_succeed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);binary=root/'fixture';binary.write_bytes(b'fixture bytes')
            output=root/'output';output.mkdir()
            stale={'program':'fixture','functions':[{'name':'main','c':'stale data'}]}
            old=output/'decompilation.json';old.write_text(json.dumps(stale))
            def fake_run(*args,**kwargs):return subprocess.CompletedProcess(args[0],0,'{}','')
            with patch.object(module.subprocess,'run',side_effect=fake_run),patch.object(module,'java_environment',return_value={}):
                with self.assertRaisesRegex(RuntimeError,'Ghidra decompilation failed'):
                    module.analyze(binary,output,'main')
            self.assertEqual(json.loads(old.read_text()),stale)

    def test_user_owned_launcher_is_preserved_before_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime=Path(temporary)/'runtime';(runtime/'bin').mkdir(parents=True)
            launcher=runtime/'bin/reverse-tools';launcher.write_text('user-owned launcher\n')
            result=subprocess.run([sys.executable,str(ROOT/'environment/reverse/install.py'),'--runtime',str(runtime)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0);self.assertIn('user-owned file preserved',result.stderr)
            self.assertEqual(launcher.read_text(),'user-owned launcher\n')
            self.assertFalse(list((runtime/'downloads').iterdir()))

    def test_runtime_directory_symlink_cannot_redirect_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);outside=root/'outside';outside.mkdir();runtime=root/'runtime';runtime.mkdir()
            (runtime/'bin').symlink_to(outside,target_is_directory=True)
            result=subprocess.run([sys.executable,str(ROOT/'environment/reverse/install.py'),'--runtime',str(runtime)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(list(outside.iterdir()),[])

if __name__=='__main__':unittest.main(verbosity=2)
