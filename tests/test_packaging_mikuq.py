"""Exercise the real MikuQ copy blocks without running PyInstaller or a release build."""
import base64
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("enabled", [True, False])
def test_mikuq_release_assets(tmp_path, enabled):
    shell = shutil.which("powershell.exe")
    source = ROOT / "models/野餐式MikuQ"
    if not shell or not source.exists():
        pytest.skip("Requires Windows PowerShell and the local MikuQ source assets")
    # Parse with Windows PowerShell 5.1 as used by the documented build command.
    # Execute only the asset validation, hint and copy blocks from the build script.
    script = r'''
    $ErrorActionPreference = 'Stop'
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $env:MIKU_TEST_ROOT 'packaging/build_windows.ps1'), [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
    $defaults = [scriptblock]::Create($ast.ParamBlock.Extent.Text + '; [bool]$BundleMikuQ')
    if (-not (& $defaults)) { throw 'MikuQ must be included by default' }
    if (& $defaults -BundleMikuQ:$false) { throw 'MikuQ opt-out does not work' }
    $projectRoot = $env:MIKU_TEST_ROOT
    $packagingRoot = Join-Path $projectRoot 'packaging'
    $distDir = $env:MIKU_TEST_DEST
    $mikuSource = Join-Path $projectRoot 'models/野餐式MikuQ'
    $BundleMikuQ = $env:MIKU_TEST_ENABLED -eq '1'
    $modelHint = ''
    $blocks = @($ast.EndBlock.Statements | Where-Object {
        $_ -is [System.Management.Automation.Language.IfStatementAst] -and
        $_.Clauses[0].Item1.Extent.Text -eq '$BundleMikuQ'
    })
    if ($blocks.Count -ne 3) { throw 'Expected validation, hint and copy blocks' }
    # Dynamic blocks have no script filename; supply the real packaging directory.
    foreach ($block in $blocks) {
        . ([scriptblock]::Create($block.Extent.Text.Replace('$PSScriptRoot', '$packagingRoot')))
    }
    '''
    env = {**os.environ, "MIKU_TEST_ROOT": str(ROOT), "MIKU_TEST_DEST": str(tmp_path),
           "MIKU_TEST_ENABLED": "1" if enabled else "0"}
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    if not enabled:
        assert not list(tmp_path.iterdir())
        return
    destination = tmp_path / "models/野餐式MikuQ"
    expected = {p.relative_to(source) for p in source.rglob("*") if p.is_file()
                and (p.name in {"野餐式MikuQ-1.1.pmx", "Readme.txt"}
                     or p.relative_to(source).parts[0] == "texture")}
    assert {p.relative_to(destination) for p in destination.rglob("*") if p.is_file()} == expected
    for relative in expected:
        assert hashlib.sha256((destination / relative).read_bytes()).digest() == hashlib.sha256(
            (source / relative).read_bytes()).digest()
    credit = tmp_path / "借物表.md"
    assert credit.read_bytes() == (ROOT / "packaging/借物表.md").read_bytes()
    assert "miratsu/新生帝国野餐队队长" in credit.read_text(encoding="utf-8")
