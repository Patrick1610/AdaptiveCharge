"""No-overwrite and path confinement of the separately distributed preview."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from tools.preview_bundle import DOMAIN, build, install


def test_hacs_has_only_the_readonly_preview_domain():
    root = Path(__file__).resolve().parents[2]
    manifests = list((root / "custom_components").glob("*/manifest.json"))
    assert [p.parent.name for p in manifests] == [DOMAIN]
    assert json.loads(manifests[0].read_text())["domain"] == DOMAIN


def test_deterministic_bundle_and_nonoverwriting_install(tmp_path):
    root=Path(__file__).resolve().parents[2]
    a,b=tmp_path/"a.zip",tmp_path/"b.zip"
    assert build(root,a)==build(root,b)
    config=tmp_path/"config"
    config.mkdir()
    (config/"configuration.yaml").touch()
    legacy=config/"custom_components/adaptive_charge"
    legacy.mkdir(parents=True)
    (legacy/"__init__.py").write_text("existing")
    content=a.read_bytes()
    with ZipFile(BytesIO(content)) as archive:
        assert archive.read("custom_components/adaptive_charge_preview/brand/icon.png") == (root / "custom_components/adaptive_charge_preview/brand/icon.png").read_bytes()
    receipt=install(content,config,sha256(content).hexdigest())
    assert receipt["control_enabled"] is False
    assert (legacy/"__init__.py").read_text()=="existing"
    with pytest.raises(FileExistsError):
        install(content,config,sha256(content).hexdigest())
    assert (legacy/"__init__.py").read_text()=="existing"


@pytest.mark.parametrize("name",["custom_components/adaptive_charge/__init__.py","../configuration.yaml","/config/configuration.yaml","custom_components/adaptive_charge_preview/../../automations.yaml",r"custom_components/adaptive_charge_preview/..\..\configuration.yaml"])
def test_unexpected_paths_rejected_before_writing(tmp_path,name):
    stream=BytesIO()
    with ZipFile(stream,"w") as archive:
        archive.writestr(name,"not allowed")
    content=stream.getvalue()
    (tmp_path/"configuration.yaml").touch()
    with pytest.raises(ValueError):
        install(content,tmp_path,sha256(content).hexdigest())
    assert not (tmp_path/"custom_components").exists()


def test_nonexistent_config_root_is_not_created(tmp_path):
    archive=tmp_path/"preview.zip"
    build(Path(__file__).resolve().parents[2],archive)
    content=archive.read_bytes()
    missing=tmp_path/"misspelled-config"
    with pytest.raises(ValueError,match="existing Home Assistant"):
        install(content,missing,sha256(content).hexdigest())
    assert not missing.exists()


def test_digest_mismatch_writes_nothing(tmp_path):
    with pytest.raises(ValueError,match="SHA256"):
        install(b"invalid",tmp_path,"0"*64)
    assert not (tmp_path/"custom_components").exists()
