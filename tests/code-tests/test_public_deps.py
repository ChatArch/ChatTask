"""Pinned operator-owned public dependency staging for offline guest builds."""
import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chattask.settings import TaskSettings, load_guest_dependencies


class GuestPublicDependencies(unittest.TestCase):
    def test_archive_digest_must_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            archive=root/'deps.tar.gz'
            with tarfile.open(archive,'w:gz') as stream:
                blob=b'public-only-dependency-archive'
                info=tarfile.TarInfo('deps/example.py');info.size=len(blob)
                stream.addfile(info,io.BytesIO(blob))
            settings=TaskSettings(endpoint='http://127.0.0.1:8123',api_key='synthetic',
                                  public_deps_path=str(archive),
                                  public_deps_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertEqual(load_guest_dependencies(settings),archive.read_bytes())
            archive.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'digest'):
                load_guest_dependencies(settings)

    def test_unpinned_or_symlink_archive_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);real=root/'real.tar.gz';real.write_bytes(b'public')
            linked=root/'linked.tar.gz';linked.symlink_to(real)
            with self.assertRaises(ValueError):
                TaskSettings(endpoint='http://127.0.0.1:8123',api_key='synthetic',public_deps_path=str(real))
            settings=TaskSettings(endpoint='http://127.0.0.1:8123',api_key='synthetic',
                                  public_deps_path=str(linked),public_deps_sha256=hashlib.sha256(real.read_bytes()).hexdigest())
            with self.assertRaisesRegex(ValueError,'symlink'):
                load_guest_dependencies(settings)

    def test_absent_archive_keeps_regular_public_network_mode(self):
        settings=TaskSettings(endpoint='http://127.0.0.1:8123',api_key='synthetic')
        self.assertIsNone(load_guest_dependencies(settings))

if __name__=='__main__':unittest.main()
