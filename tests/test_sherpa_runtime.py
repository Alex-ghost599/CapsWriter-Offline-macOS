from pathlib import Path
import base64
import hashlib
from importlib.metadata import PathDistribution
from types import SimpleNamespace

import pytest

from core.server.engines.sherpa_runtime import ensure_sherpa_onnx_runtime
from core.server.engines import sherpa_runtime


def test_macos_sherpa_runtime_is_resolvable():
    target = ensure_sherpa_onnx_runtime()

    if target is not None:
        assert isinstance(target, Path)
        assert target.exists()
        assert target.resolve().name.startswith("libonnxruntime.")


@pytest.fixture
def installed_runtime(tmp_path, monkeypatch):
    sherpa = tmp_path / 'sherpa_onnx'
    ort = tmp_path / 'onnxruntime'
    (sherpa / 'lib').mkdir(parents=True)
    (ort / 'capi').mkdir(parents=True)
    source = ort / 'capi/libonnxruntime.1.27.0.dylib'
    source.write_bytes(b'onnxruntime wheel library')
    target = sherpa / 'lib' / source.name
    target.write_bytes(b'sherpa core wheel library')
    dist_info = tmp_path / 'sherpa_onnx_core-1.13.4.dist-info'
    dist_info.mkdir()
    dist_info.joinpath('METADATA').write_text('Name: sherpa-onnx-core\nVersion: 1.13.4\n')
    digest = base64.urlsafe_b64encode(hashlib.sha256(target.read_bytes()).digest()).decode().rstrip('=')
    dist_info.joinpath('RECORD').write_text(
        f'sherpa_onnx/lib/{target.name},sha256={digest},{target.stat().st_size}\n'
    )
    monkeypatch.setattr(sherpa_runtime.sys, 'platform', 'darwin')
    monkeypatch.setattr(sherpa_runtime, 'find_spec', lambda name: SimpleNamespace(origin=str(tmp_path / name / '__init__.py')))
    monkeypatch.setattr(sherpa_runtime, 'version', lambda name: '1.27.0')
    # Real wheel-style metadata and files; only package discovery is redirected.
    monkeypatch.setattr(sherpa_runtime, 'distribution', lambda name: PathDistribution(dist_info), raising=False)
    return source, target, dist_info


def test_accepts_verified_library_from_core_wheel(installed_runtime):
    _, target, _ = installed_runtime
    before = target.read_bytes()
    assert ensure_sherpa_onnx_runtime() == target
    assert not target.is_symlink()
    assert target.read_bytes() == before


def test_rejects_modified_core_library(installed_runtime):
    _, target, _ = installed_runtime
    target.write_bytes(b'corrupted library')
    with pytest.raises(RuntimeError, match='Unexpected ONNX Runtime library'):
        ensure_sherpa_onnx_runtime()


def test_rejects_library_not_owned_by_core_wheel(installed_runtime):
    _, _, dist_info = installed_runtime
    dist_info.joinpath('RECORD').write_text('')
    with pytest.raises(RuntimeError, match='Unexpected ONNX Runtime library'):
        ensure_sherpa_onnx_runtime()


def test_creates_legacy_runtime_symlink_when_library_is_absent(installed_runtime):
    source, target, _ = installed_runtime
    target.unlink()
    assert ensure_sherpa_onnx_runtime() == target
    assert target.is_symlink()
    assert target.resolve() == source.resolve()
