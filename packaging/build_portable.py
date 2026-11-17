"""Build and verify a portable ZIP using the current Windows Python environment."""

import hashlib
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Build this Windows release on Windows.")
    # Resolve/cache precisely the models used by this application before bundling.
    sys.path.insert(0, str(ROOT))
    from rok_automation.vision.account_label import _get_ocr_engine
    _get_ocr_engine()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                    str(ROOT / "packaging" / "ROKauto.spec")], cwd=ROOT, check=True)
    folder = ROOT / "dist" / "ROKauto"
    env = dict(os.environ, ROK_STATE_DIR=str(ROOT / "build" / "smoke-state"),
               LOCALAPPDATA=str(ROOT / "build" / "smoke-user"))
    subprocess.run([str(folder / "ROKauto-worker.exe"), "--self-test"],
                   cwd=ROOT / "build", env=env, check=True, timeout=120)
    subprocess.run([str(folder / "ROKauto-worker.exe"), "--help"],
                   cwd=ROOT / "build", env=env, check=True, timeout=30,
                   stdout=subprocess.DEVNULL)
    shutil.copy2(ROOT / "packaging" / "README-portable.txt", folder / "README.txt")
    versions = sorted(f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()
                      if d.metadata.get("Name"))
    (folder / "build-environment.txt").write_text("\n".join(versions) + "\n", encoding="utf-8")
    archive = Path(shutil.make_archive(str(ROOT / "dist" / "ROKauto-windows-x64"),
                                       "zip", ROOT / "dist", "ROKauto"))
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"Portable release: {archive} ({archive.stat().st_size / 1024 / 1024:.1f} MiB)")


if __name__ == "__main__":
    main()
