"""Synthetic archive regression tests; real upstream rendering has separate proof."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

SOURCE=Path(__file__).resolve().parents[2]/'environment/design/install.py'

class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='design-install-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        spec=importlib.util.spec_from_file_location('design_install_fixture',SOURCE)
        self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        self.module.RUNTIME=self.root/'runtime'
        self.module.DEST=self.module.RUNTIME/'diagram-design'/'fixture'
        content=b'fixture skill\n';archive=self.module.RUNTIME/'downloads/fixture.tar.gz';archive.parent.mkdir(parents=True)
        with tarfile.open(archive,'w:gz') as tar:
            entry=tarfile.TarInfo('upstream/skills/diagram-design/SKILL.md');entry.size=len(content);tar.addfile(entry,io.BytesIO(content))
        self.module.PIN={'source_commit':'fixture','source_url':'https://api.github.com/unused-fixture','archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'version':'fixture','skill_relative_path':'skills/diagram-design','file_sha256':{'skills/diagram-design/SKILL.md':hashlib.sha256(content).hexdigest()}}
    def run_install(self):
        with contextlib.redirect_stdout(io.StringIO()):self.module.main()
    def test_fresh_extract_and_repeat_without_replacing_pristine_source(self):
        self.run_install();path=self.module.DEST/'skills/diagram-design/SKILL.md';original=path.stat().st_mtime_ns
        self.run_install();self.assertEqual(path.stat().st_mtime_ns,original)
        self.assertTrue(json.loads((self.module.RUNTIME/'installation.json').read_text())['unchanged_source_reused'])
    def test_modified_skill_is_preserved_and_rejected(self):
        self.run_install();path=self.module.DEST/'skills/diagram-design/SKILL.md';path.write_text('custom skill')
        with self.assertRaisesRegex(ValueError,'differs'):self.run_install()
        self.assertEqual(path.read_text(),'custom skill')
    def test_modified_launcher_is_preserved_and_rejected(self):
        self.run_install();path=self.module.RUNTIME/'bin/design-tools';path.write_text('custom launcher')
        with self.assertRaisesRegex(ValueError,'custom design launcher'):self.run_install()
        self.assertEqual(path.read_text(),'custom launcher')
    def test_corrupt_download_is_not_extracted(self):
        archive=self.module.RUNTIME/'downloads/fixture.tar.gz';archive.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'integrity mismatch'):self.run_install()
        self.assertFalse(self.module.DEST.exists())
    def test_source_parent_link_is_preserved_and_rejected(self):
        self.module.DEST.parent.mkdir();self.module.DEST.symlink_to(self.root,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlinks'):self.run_install()
        self.assertTrue(self.module.DEST.is_symlink())

if __name__=='__main__':unittest.main(verbosity=2)
