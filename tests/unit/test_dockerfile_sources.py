from pathlib import Path


def test_default_build_does_not_force_tuna_apt_mirror():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "ARG APT_MIRROR=" in dockerfile
    assert "ARG APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/ubuntu" not in dockerfile
    assert 'if [ -n "$APT_MIRROR" ]; then' in dockerfile


def test_compose_exposes_apt_mirror_build_arg():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert "args:" in compose
    assert "APT_MIRROR: ${APT_MIRROR:-}" in compose
    assert "APT_MIRROR=https://mirrors.aliyun.com/ubuntu" in env_example


def test_default_python_package_sources_do_not_force_tuna():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    lockfile = Path("uv.lock").read_text(encoding="utf-8")

    assert "ARG PIP_MIRROR=https://pypi.org/simple" in dockerfile
    assert "PIP_MIRROR: ${PIP_MIRROR:-https://pypi.org/simple}" in compose
    assert "pypi.tuna.tsinghua.edu.cn" not in pyproject
    assert "pypi.tuna.tsinghua.edu.cn" not in lockfile
