from pathlib import Path
import tempfile
import unittest
import sys

from cloudkit.profiles import install_profile


@unittest.skipUnless(sys.version_info >= (3, 12), 'The optional memory profile requires Python 3.12')
class ProfileOwnershipTests(unittest.TestCase):
    def test_existing_environment_is_preserved_without_installing(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / 'runtime'
            environment = runtime / 'memory/.venv'
            environment.mkdir(parents=True)
            marker = environment / 'user-file'
            marker.write_text('User environment\n')
            with self.assertRaisesRegex(ValueError, 'preserved'):
                install_profile('memory', runtime)
            self.assertEqual(marker.read_text(), 'User environment\n')
            self.assertFalse((runtime / 'memory/backend.py').exists())

    def test_existing_customized_service_file_is_preserved_atomically(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / 'runtime'
            service = runtime / 'memory'
            service.mkdir(parents=True)
            (service / 'backend.py').write_text('Custom backend\n')
            with self.assertRaisesRegex(ValueError, 'customization'):
                install_profile('memory', runtime)
            self.assertEqual((service / 'backend.py').read_text(), 'Custom backend\n')
            self.assertFalse((service / 'requirements.lock').exists())

    def test_linked_receipt_is_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary);runtime = root / 'runtime';service = runtime / 'memory'
            service.mkdir(parents=True);target = root / 'custom-receipt.json'
            target.write_text('{}\n');(service / 'installation.json').symlink_to(target)
            with self.assertRaisesRegex(ValueError, 'user-managed link'):
                install_profile('memory', runtime)
            self.assertEqual(target.read_text(), '{}\n')
            self.assertFalse((service / 'backend.py').exists())


if __name__ == '__main__':
    unittest.main()
