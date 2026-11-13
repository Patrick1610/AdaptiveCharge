"""Build/install only the isolated preview package; never replace an existing tree."""

from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import stat
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

DOMAIN = "adaptive_charge_preview"
PREFIX = f"custom_components/{DOMAIN}/"
MAX_BYTES = 2_000_000
SUFFIXES = {".py", ".json", ".yaml", ".png"}


def build(source_root: Path, output: Path) -> dict[str, object]:
    """Produce deterministic bytes and external hash binding for review/install."""
    package = source_root / "custom_components" / DOMAIN
    files = sorted(p for p in package.rglob("*") if p.is_file() and p.suffix in SUFFIXES and "__pycache__" not in p.parts)
    hashes = {}
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        for path in files:
            relative = PREFIX + path.relative_to(package).as_posix()
            content = path.read_bytes()
            item = ZipInfo(relative, (2026, 1, 1, 0, 0, 0))
            item.compress_type = ZIP_DEFLATED
            item.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(item, content)
            hashes[relative] = sha256(content).hexdigest()
    content = stream.getvalue()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    receipt = {"domain": DOMAIN, "control_enabled": False, "sha256": sha256(content).hexdigest(), "files": hashes}
    output.with_suffix(".json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def install(content: bytes, config_dir: Path, expected_sha256: str) -> dict[str, object]:
    """Validate the entire archive before exclusively creating preview-only files.

    The caller supplies an independently reviewed archive digest. No code from
    the archive is executed. On I/O failure, only exact newly written own files
    are removed; any concurrent foreign change is left intact.
    """
    if sha256(content).hexdigest() != expected_sha256:
        raise ValueError("Archive SHA256 mismatch")
    if not config_dir.is_dir() or not (config_dir / "configuration.yaml").is_file():
        raise ValueError("Select an existing Home Assistant configuration directory")
    parent = config_dir / "custom_components"
    destination = parent / DOMAIN
    if config_dir.is_symlink() or parent.is_symlink() or destination.exists() or destination.is_symlink():
        raise FileExistsError("Refusing to overwrite existing or symlinked installation")
    files: dict[PurePosixPath, bytes] = {}
    total = 0
    with ZipFile(BytesIO(content)) as archive:
        for item in archive.infolist():
            name = PurePosixPath(item.filename)
            mode = item.external_attr >> 16
            if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", item.filename) or not item.filename.startswith(PREFIX) or ".." in name.parts or name.is_absolute() or item.is_dir() or stat.S_ISLNK(mode) or str(name) != item.filename or name.suffix not in SUFFIXES:
                raise ValueError("Unexpected archive path/type")
            relative = PurePosixPath(*name.parts[2:])
            if relative in files or len(files) >= 80:
                raise ValueError("Duplicate/excess archive members")
            total += item.file_size
            if total > MAX_BYTES:
                raise ValueError("Archive too large")
            data = archive.read(item)
            if name.suffix == ".py":
                compile(data, item.filename, "exec")  # Syntax check only.
            files[relative] = data
    manifest = json.loads(files.get(PurePosixPath("manifest.json"), b"{}"))
    if manifest.get("domain") != DOMAIN or PurePosixPath("__init__.py") not in files:
        raise ValueError("Wrong/missing preview manifest")
    parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()  # Exclusive creation: never replace an existing directory.
    written: dict[Path, bytes] = {}
    try:
        for relative, data in files.items():
            path = destination.joinpath(*relative.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            if any(p.is_symlink() for p in path.parents if destination in (p, *p.parents)):
                raise ValueError("Symlink encountered during installation")
            with path.open("xb") as handle:
                handle.write(data)
            written[path] = data
    except Exception:
        for path, data in written.items():
            if path.is_file() and not path.is_symlink() and path.read_bytes() == data:
                path.unlink()
        for folder in sorted(destination.rglob("*"), reverse=True):
            if folder.is_dir() and not folder.is_symlink():
                try:
                    folder.rmdir()
                except OSError:
                    pass
        try:
            destination.rmdir()
        except OSError:
            pass
        raise
    return {"domain": DOMAIN, "control_enabled": False, "sha256": expected_sha256, "files": {str(p.relative_to(config_dir)): sha256(data).hexdigest() for p, data in written.items()}}


def main() -> None:
    """Build locally; installation is called explicitly through the allowed route."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist/adaptive_charge_preview-5.0.0b1.zip"))
    parser.add_argument("--install", type=Path, help="Install this reviewed preview-only ZIP")
    parser.add_argument("--config", type=Path, help="Existing HA config directory")
    parser.add_argument("--sha256", help="Independently verified ZIP digest")
    args = parser.parse_args()
    if args.install:
        if not args.config or not args.sha256:
            parser.error("--install requires --config and --sha256")
        print(json.dumps(install(args.install.read_bytes(), args.config, args.sha256)))
        return
    if args.config or args.sha256:
        parser.error("--config and --sha256 require --install")
    receipt = build(Path(__file__).resolve().parents[1], args.output)
    print(json.dumps({"archive": str(args.output), "sha256": receipt["sha256"], "files": len(receipt["files"])}))


if __name__ == "__main__":
    main()
