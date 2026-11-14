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


def upgrade_fixture(tmp_path):
    from tools.preview_bundle import component_hashes
    config=tmp_path/'config'
    config.mkdir(); (config/'configuration.yaml').write_text('original: untouched\n')
    old=config/'custom_components'/DOMAIN
    old.mkdir(parents=True)
    (old/'__init__.py').write_text('OLD = True\n')
    (old/'manifest.json').write_text(json.dumps({'domain':DOMAIN,'version':'5.0.0b1'}))
    pins=component_hashes(config)
    archive=tmp_path/'reviewed.zip'
    receipt=build(Path(__file__).resolve().parents[2],archive)
    return config,old,pins,archive.read_bytes(),receipt['sha256']


def test_explicit_upgrade_retains_exact_backup_and_unrelated_config(tmp_path):
    from tools.preview_bundle import component_hashes,upgrade
    config,old,pins,content,digest=upgrade_fixture(tmp_path)
    proof=config/'own-proof/upgrade'
    receipt=upgrade(content,config,digest,pins,proof)
    assert receipt['phase']=='installed_restart_pending' and receipt['control_enabled'] is False
    assert (proof/'component_before/__init__.py').read_text()=='OLD = True\n'
    assert component_hashes(config)==receipt['after']
    assert (config/'configuration.yaml').read_text()=='original: untouched\n'
    with pytest.raises(FileExistsError): upgrade(content,config,digest,pins,proof)


def test_upgrade_refuses_foreign_file_or_baseline_drift(tmp_path):
    from tools.preview_bundle import upgrade
    config,old,pins,content,digest=upgrade_fixture(tmp_path)
    (old/'foreign.py').write_text('USER = True\n')
    with pytest.raises(ValueError,match='drift'): upgrade(content,config,digest,pins,config/'proof')
    assert (old/'foreign.py').exists() and (old/'__init__.py').read_text()=='OLD = True\n'


def test_upgrade_recovers_old_code_when_second_rename_fails(tmp_path,monkeypatch):
    from tools.preview_bundle import upgrade
    config,old,pins,content,digest=upgrade_fixture(tmp_path)
    real=Path.rename
    def fail_candidate(path,target):
        if path.parent.name=='custom_components' and path.is_relative_to(config/'proof/candidate_config'):
            raise OSError('simulated rename failure')
        return real(path,target)
    monkeypatch.setattr(Path,'rename',fail_candidate)
    with pytest.raises(OSError): upgrade(content,config,digest,pins,config/'proof')
    assert (old/'__init__.py').read_text()=='OLD = True\n'
    assert json.loads((config/'proof/upgrade_receipt.json').read_text())['phase']=='rolled_back'


@pytest.mark.parametrize('escape',['../outside-proof','custom_components/proof'])
def test_upgrade_proof_cannot_escape_config_or_enter_components(tmp_path,escape):
    from tools.preview_bundle import upgrade
    config,old,pins,content,digest=upgrade_fixture(tmp_path)
    with pytest.raises(ValueError,match='Proof directory'): upgrade(content,config,digest,pins,config/escape)
    assert (old/'__init__.py').read_text()=='OLD = True\n'


def test_upgrade_does_not_overwrite_foreign_change_during_failed_install(tmp_path,monkeypatch):
    from tools.preview_bundle import upgrade
    config,old,pins,content,digest=upgrade_fixture(tmp_path)
    real=Path.rename
    def foreign_change(path,target):
        if path.is_relative_to(config/'proof/candidate_config') and path.name==DOMAIN:
            old.mkdir()
            (old/'foreign.py').write_text('USER_CHANGE = True\n')
            raise OSError('foreign writer occupied destination')
        return real(path,target)
    monkeypatch.setattr(Path,'rename',foreign_change)
    with pytest.raises(OSError): upgrade(content,config,digest,pins,config/'proof')
    assert (old/'foreign.py').read_text()=='USER_CHANGE = True\n'
    assert (config/'proof/component_before/__init__.py').read_text()=='OLD = True\n'
    assert json.loads((config/'proof/upgrade_receipt.json').read_text())['phase']=='blocked_foreign_change_backup_retained'


def test_upgrade_staging_failure_records_receipt_and_preserves_original(tmp_path):
    from tools.preview_bundle import upgrade
    config,old,pins,content,digest=upgrade_fixture(tmp_path)
    with pytest.raises(ValueError,match='SHA256'): upgrade(content,config,'0'*64,pins,config/'proof')
    receipt=json.loads((config/'proof/upgrade_receipt.json').read_text())
    assert receipt['phase']=='failed_before_rename' and receipt['error_type']=='ValueError'
    assert (old/'__init__.py').read_text()=='OLD = True\n'


def test_upgrade_first_rename_failure_records_receipt_and_preserves_original(tmp_path,monkeypatch):
    from tools.preview_bundle import upgrade
    config,old,pins,content,digest=upgrade_fixture(tmp_path)
    real=Path.rename
    def fail_first(path,target):
        if path==old: raise OSError('simulated first-rename permission failure')
        return real(path,target)
    monkeypatch.setattr(Path,'rename',fail_first)
    with pytest.raises(OSError): upgrade(content,config,digest,pins,config/'proof')
    receipt=json.loads((config/'proof/upgrade_receipt.json').read_text())
    assert receipt['phase']=='failed_before_rename' and receipt['error_type']=='OSError'
    assert (old/'__init__.py').read_text()=='OLD = True\n'
