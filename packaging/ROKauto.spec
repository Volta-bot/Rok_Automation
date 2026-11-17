from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
ocr_data, ocr_bins, ocr_imports = collect_all("rapidocr")
a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root)],
    binaries=ocr_bins,
    datas=[(str(root / "templates"), "templates")] + ocr_data + copy_metadata("rapidocr"),
    hiddenimports=ocr_imports,
    excludes=["torch", "tensorflow", "paddle", "openvino", "tensorrt", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ROKauto", console=False)
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ROKauto-worker", console=True)
coll = COLLECT(gui, worker, a.binaries, a.datas, name="ROKauto")
