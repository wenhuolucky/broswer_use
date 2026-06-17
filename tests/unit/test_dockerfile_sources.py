from pathlib import Path


def test_default_apt_mirror_uses_https_ubuntu_base_url():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "ARG APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/ubuntu" in dockerfile
    assert "http://mirrors.tuna.tsinghua.edu.cn/ubuntu noble" not in dockerfile
