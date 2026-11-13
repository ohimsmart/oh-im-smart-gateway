"""bootstrap.sh 判斷「已經裝過」的條件：新舊兩個 origin 名稱都走 update.sh，其他 repo 不碰。"""
import subprocess
from pathlib import Path

import pytest

BOOTSTRAP = Path(__file__).resolve().parents[4] / "scripts" / "bootstrap.sh"


def _checkout(tmp_path: Path, origin: str) -> Path:
    d = tmp_path / "hub"
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "update.sh").write_text(f'#!/bin/bash\ntouch "{tmp_path}/updated"\n')
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    subprocess.run(["git", "-C", str(d), "remote", "add", "origin", origin], check=True)
    return d


def _run(tmp_path: Path, d: Path):
    # start_new_session：沒有控制終端，run_with_tty 直接執行，不讀 /dev/tty
    return subprocess.run(["bash", str(BOOTSTRAP)], env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                          "XIAOZHI_HUB_DIR": str(d)}, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, start_new_session=True, timeout=60)


@pytest.mark.parametrize("origin", ["https://github.com/ohimsmart/oh-im-smart-gateway.git",
                                    "https://github.com/justhalfbit/xiaozhi-hub-server.git"])
def test_existing_checkout_is_updated(tmp_path, origin):
    r = _run(tmp_path, _checkout(tmp_path, origin))
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "updated").exists()


def test_other_repository_is_left_alone(tmp_path):
    r = _run(tmp_path, _checkout(tmp_path, "https://github.com/someone/else.git"))
    assert r.returncode != 0
    assert "不是本项目" in r.stderr
    assert not (tmp_path / "updated").exists()
