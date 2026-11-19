"""Build/install the package; explicit upgrades require exact existing-file pins."""

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
    receipt = {"domain": DOMAIN, "control_enabled": False, "control_capable": True, "version": json.loads((package / "manifest.json").read_text())["version"], "sha256": sha256(content).hexdigest(), "files": hashes}
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


def component_hashes(config_dir: Path) -> dict[str, str]:
    """Inspect only this domain, refusing links and unexpected foreign files."""
    component = config_dir / "custom_components" / DOMAIN
    if not component.is_dir() or any(p.is_symlink() for p in (config_dir, component.parent, component)):
        raise ValueError("Expected a real existing component directory")
    return _component_tree(component)


def _component_tree(component: Path) -> dict[str, str]:
    result = {}
    for path in component.rglob("*"):
        if path.is_symlink():
            raise ValueError("Component contains a symlink")
        if path.is_file() and "__pycache__" not in path.relative_to(component).parts:
            result[PREFIX + path.relative_to(component).as_posix()] = sha256(path.read_bytes()).hexdigest()
    return result


def upgrade(content: bytes, config_dir: Path, expected_sha256: str, expected_before: dict[str, str], proof_dir: Path) -> dict[str, object]:
    """Replace exactly the pinned component with reversible directory renames.

    No HA services, source execution, helpers or storage are accessed. The caller
    must separately verify idle authority and arrange the authorized HA restart.
    A previously written proof directory forbids a duplicate automatic attempt.
    """
    if proof_dir.exists() or proof_dir.is_symlink():
        raise FileExistsError("An upgrade receipt/staging directory already exists")
    if not expected_before or any(not key.startswith(PREFIX) or not re.fullmatch(r"[0-9a-f]{64}", value) for key, value in expected_before.items()):
        raise ValueError("An independently verified complete baseline is required")
    root = config_dir.resolve(strict=True)
    original_proof = proof_dir.absolute()
    proof = proof_dir.resolve()
    if not proof.is_relative_to(root) or proof.is_relative_to(root / "custom_components") or any(p.is_symlink() for p in original_proof.parents):
        raise ValueError("Proof directory must be a separate non-symlink path inside config")
    if component_hashes(config_dir) != expected_before:
        raise ValueError("Existing component drift; nothing replaced")
    proof.mkdir(parents=True)
    destination = config_dir / "custom_components" / DOMAIN
    backup = proof / "component_before"
    receipt: dict[str, object] = {"phase": "preparing", "archive_sha256": expected_sha256, "before": dict(expected_before), "after": None, "control_enabled": False, "ha_restarted": False}
    receipt_path = proof / "upgrade_receipt.json"
    def save() -> None:
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    save()
    try:
        candidate_config = proof / "candidate_config"
        candidate_config.mkdir()
        (candidate_config / "configuration.yaml").touch()
        staged = install(content, candidate_config, expected_sha256)
        candidate = candidate_config / "custom_components" / DOMAIN
        receipt.update(phase="prepared", after=staged["files"])
        save()
        # Recheck after staging, immediately before the first mutation of old code.
        if component_hashes(config_dir) != expected_before:
            raise ValueError("Component changed during preparation")
        destination.rename(backup)
    except Exception as exc:
        receipt.update(phase="failed_before_rename", error_type=type(exc).__name__)
        save()
        raise
    try:
        if _component_tree(backup) != expected_before:
            raise ValueError("Component changed at rename; restore without installing")
        candidate.rename(destination)
        if component_hashes(config_dir) != staged["files"]:
            raise ValueError("Upgrade readback differs from reviewed archive")
        receipt["phase"] = "installed_restart_pending"
        save()
    except Exception as exc:
        if not destination.exists() and not destination.is_symlink():
            backup.rename(destination)
            receipt["phase"] = "rolled_back"
        else:
            try:
                owned_candidate = component_hashes(config_dir) == staged["files"]
            except (ValueError, OSError):
                owned_candidate = False
            if owned_candidate:
                destination.rename(proof / "failed_candidate")
                backup.rename(destination)
                receipt["phase"] = "rolled_back"
            else:
                receipt["phase"] = "blocked_foreign_change_backup_retained"
        receipt["error_type"] = type(exc).__name__
        save()
        raise
    return receipt


def main() -> None:
    """Build locally; installation is called explicitly through the allowed route."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist") / f"adaptive_charge_preview-{json.loads((Path(__file__).resolve().parents[1] / PREFIX / 'manifest.json').read_text())['version']}.zip")
    parser.add_argument("--install", type=Path, help="Install this reviewed integration ZIP (default control off)")
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
