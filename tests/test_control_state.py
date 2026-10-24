"""Real Git transport tests for independently installed memory control helpers."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from cloudkit.runtime import control_state


class ControlTransportTests(unittest.TestCase):
    def test_process_token_is_injected_without_writing_git_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / 'git'
            executable.write_text('#!' + sys.executable + '\nimport json,os\n'
                'print(json.dumps({"header_configured":any(os.environ.get("GIT_CONFIG_KEY_"+str(i))=="http.https://github.com/.extraheader" for i in range(int(os.environ.get("GIT_CONFIG_COUNT","0"))))}))\n')
            executable.chmod(0o755)
            with patch.dict(os.environ, {'PATH': str(root) + os.pathsep + os.environ['PATH'],
                                         'GITHUB_TOKEN': 'synthetic-transport-fixture'}):
                result = json.loads(control_state.command(root, 'version'))
            self.assertTrue(result['header_configured'])
            self.assertFalse((root / '.gitconfig').exists())

    def test_installed_helpers_initialize_publish_and_fetch_private_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary);remote = root / 'control.git';checkout = root / 'control'
            subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True)
            subprocess.run(['git', 'clone', '-q', str(remote), str(checkout)], check=True, capture_output=True)
            sha = 'b' * 40
            supervisor = Path(control_state.__file__).with_name('worker_supervisor.py')
            result = subprocess.run([sys.executable, str(supervisor), '--repository', str(checkout), 'init',
                '--project-id', 'example/control', '--source-sha', sha], text=True, capture_output=True, check=True)
            self.assertTrue(json.loads(result.stdout)['initialized'])
            snapshot = root / 'snapshot.tar.gz'
            # This fixture verifies the control transport contract, not native SQLite recovery.
            with tarfile.open(snapshot, 'w:gz') as bundle:
                for name, value in {'snapshot.json': {'project_id': 'example/project', 'branch': 'main', 'index_generation': 1},
                                    'manifest.json': {'project_id': 'example/project', 'branch': 'main', 'last_indexed_commit': sha}}.items():
                    content = json.dumps(value).encode();member = tarfile.TarInfo(name);member.size = len(content)
                    bundle.addfile(member, io.BytesIO(content))
            status = root / 'status.json'
            status.write_text(json.dumps({'project_id': 'example/project', 'branch': 'main', 'last_indexed_commit': sha,
                                          'active_generation': 1, 'memory_revision': 1, 'pipeline': {'fixture': True}}))
            writer = [sys.executable, str(Path(control_state.__file__)), '--repository', str(checkout),
                      '--project', 'example/project', 'publish', '--snapshot', str(snapshot), '--status', str(status),
                      '--code-sha', sha, '--expected-snapshot-sha256', 'absent']
            published = json.loads(subprocess.check_output(writer, text=True))
            self.assertTrue(published['published'])
            destination = root / 'fresh-directory/snapshot.tar.gz'
            fetched = json.loads(subprocess.check_output([sys.executable, str(Path(control_state.__file__)), '--repository', str(checkout),
                '--project', 'example/project', 'fetch', '--output', str(destination)], text=True))
            self.assertTrue(fetched['fetched'])
            self.assertEqual(fetched['snapshot_sha256'], published['snapshot_sha256'])
            self.assertEqual(destination.read_bytes(), snapshot.read_bytes())
            stale = subprocess.run(writer, text=True, capture_output=True)
            self.assertNotEqual(stale.returncode, 0)


if __name__ == '__main__':
    unittest.main()
