import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile


SPEC = importlib.util.spec_from_file_location("package_script", Path(__file__).parents[1] / "scripts/package.py")
package_script = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package_script)


class PackageTests(unittest.TestCase):
    def make_project(self, root: Path):
        (root / "eightbitdo").mkdir()
        (root / "eightbitdo/resources").mkdir()
        (root / "eightbitdo/resources/__init__.py").write_text("")
        (root / "eightbitdo/resources/install-udev-rules.sh").write_text("#!/bin/bash\nexit 0\n")
        (root / "dist").mkdir()
        (root / "scripts").mkdir()
        (root / "scripts/install-udev-rules.sh").write_text("#!/bin/bash\nexit 0\n")
        (root / "package.json").write_text(json.dumps({"name": "8bitdo-deck"}))
        (root / "plugin.json").write_text(json.dumps({"name": "8BitDo Deck"}))
        (root / "eightbitdo/__init__.py").write_text("")
        (root / "main.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
            "import eightbitdo\n"
            "class Plugin:\n    service = object()\n"
        )
        (root / "dist/index.js").write_text("export default () => ({});\n")

    def test_archive_is_deterministic_and_allowlisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_project(root)
            (root / "eightbitdo/private.bin").write_bytes(b"firmware")
            (root / "dist/index.js.map").write_text("private source paths")
            (root / "secret.json").write_text("secret")
            first = root / "out/first.zip"
            second = root / "out/second.zip"
            self.assertEqual(package_script.build_package(root, first), package_script.build_package(root, second))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(set(archive.namelist()), {
                    f"8bitdo-deck/{name}" for name in package_script.REQUIRED_FILES
                })
                for entry in archive.infolist():
                    self.assertEqual(entry.date_time, package_script.FIXED_TIMESTAMP)

    def test_missing_frontend_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_project(root)
            (root / "dist/index.js").unlink()
            with self.assertRaisesRegex(ValueError, "Missing"):
                package_script.package_files(root)
            (root / "dist/index.js").symlink_to(root / "main.py")
            with self.assertRaisesRegex(ValueError, "unsafe"):
                package_script.package_files(root)

    def test_broken_backend_fails_without_replacing_existing_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_project(root)
            destination = root / "out/plugin.zip"
            package_script.build_package(root, destination)
            original = destination.read_bytes()
            (root / "main.py").write_text("import eightbitdo\n")
            with self.assertRaises(subprocess.CalledProcessError):
                package_script.build_package(root, destination)
            self.assertEqual(destination.read_bytes(), original)

    def test_bundled_api_requires_its_source_and_license_materials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_project(root)
            (root / "package.json").write_text(json.dumps({
                "name": "8bitdo-deck", "dependencies": {"@decky/api": "1.1.3"},
            }))
            with self.assertRaisesRegex(ValueError, "Missing"):
                package_script.package_files(root)

    def test_missing_permission_helper_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_project(root)
            (root / "eightbitdo/resources/install-udev-rules.sh").unlink()
            with self.assertRaisesRegex(ValueError, "Missing"):
                package_script.package_files(root)


if __name__ == "__main__":
    unittest.main()
