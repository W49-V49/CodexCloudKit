from pathlib import Path
import tempfile
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch

from cloudkit import profiles
from cloudkit.profiles import install_profile, _install_memory


class ProfileOwnershipTests(unittest.TestCase):
    """File admission is independent of the optional native runtime prerequisite."""
    def setUp(self):
        process = patch.object(profiles.subprocess, 'run', side_effect=AssertionError('Ownership rejection must precede dependency installation'))
        process.start()
        self.addCleanup(process.stop)

    def test_existing_environment_is_preserved_without_installing(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / 'runtime'
            environment = runtime / 'memory/.venv'
            environment.mkdir(parents=True)
            marker = environment / 'user-file'
            marker.write_text('User environment\n')
            with self.assertRaisesRegex(ValueError, 'preserved'):
                _install_memory(runtime)
            self.assertEqual(marker.read_text(), 'User environment\n')
            self.assertFalse((runtime / 'memory/backend.py').exists())

    def test_existing_customized_service_file_is_preserved_atomically(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / 'runtime'
            service = runtime / 'memory'
            service.mkdir(parents=True)
            (service / 'backend.py').write_text('Custom backend\n')
            with self.assertRaisesRegex(ValueError, 'customization'):
                _install_memory(runtime)
            self.assertEqual((service / 'backend.py').read_text(), 'Custom backend\n')
            self.assertFalse((service / 'requirements.lock').exists())

    def test_linked_receipt_is_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary);runtime = root / 'runtime';service = runtime / 'memory'
            service.mkdir(parents=True);target = root / 'custom-receipt.json'
            target.write_text('{}\n');(service / 'installation.json').symlink_to(target)
            with self.assertRaisesRegex(ValueError, 'user-managed link'):
                _install_memory(runtime)
            self.assertEqual(target.read_text(), '{}\n')
            self.assertFalse((service / 'backend.py').exists())


class MemoryRuntimeRequirementTests(unittest.TestCase):
    def test_unsupported_runtime_is_rejected_before_writing_or_installing(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / 'not-created'
            with patch.object(profiles, 'sys', SimpleNamespace(version_info=(3, 11), executable=sys.executable)), \
                 patch.object(profiles, '_install_memory') as installer, \
                 patch.object(profiles.subprocess, 'run') as process:
                with self.assertRaisesRegex(ValueError, 'memory profile requires Python 3.12'):
                    install_profile('memory', runtime)
            installer.assert_not_called()
            process.assert_not_called()
            self.assertFalse(runtime.exists())

    def test_current_interpreter_has_explicit_memory_requirement(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / 'runtime'
            # This is prerequisite routing only; the native service is tested separately on 3.12.
            with patch.object(profiles, '_install_memory', return_value={'runtime_prerequisite_admitted': True}) as installer:
                if sys.version_info < (3, 12):
                    with self.assertRaisesRegex(ValueError, 'memory profile requires Python 3.12'):
                        install_profile('memory', runtime)
                    installer.assert_not_called()
                    self.assertFalse(runtime.exists())
                else:
                    result = install_profile('memory', runtime)
                    self.assertTrue(result['runtime_prerequisite_admitted'])
                    installer.assert_called_once_with(runtime.resolve())


if __name__ == '__main__':
    unittest.main()
