"""代理模块测试（app/proxy/）。

只验证纯逻辑：账号→IP 分配（最少绑定优先）、持久化、协议覆盖、Playwright
proxy 参数构建、未启用时直连。不启动任何浏览器。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.proxy.assignment import ProxyAssignmentManager
from app.proxy.browser import build_playwright_proxy
from app.proxy.config import load_proxy_config
from app.proxy.provider import ProxyInfo


_YAML_TWO_FIXED = """
defaults:
  protocol: http
  verify_exit_ip: false
ip_pool:
  - provider: fixed_auth
    ip: 1.1.1.1
    port: 2018
    username: u1
    password: p1
    label: A
  - provider: fixed_auth
    ip: 2.2.2.2
    port: 2018
    username: u2
    password: p2
    protocol: socks5
    label: B
"""


def _write_cfg(tmp_path: Path) -> tuple[str, str]:
    cfg = tmp_path / "proxies.yaml"
    cfg.write_text(_YAML_TWO_FIXED, encoding="utf-8")
    return str(cfg), str(tmp_path / "assign.json")


def test_load_config_parses_pool_and_defaults(tmp_path: Path) -> None:
    cfg, _ = _write_cfg(tmp_path)
    conf = load_proxy_config(cfg)
    assert len(conf.ip_pool) == 2
    assert conf.defaults.protocol == "http"
    assert conf.defaults.verify_exit_ip is False
    # 条目级 protocol 覆盖 defaults
    assert conf.ip_pool[1].protocol == "socks5"


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_proxy_config(str(tmp_path / "nope.yaml"))


def test_fixed_auth_missing_fields_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "ip_pool:\n  - provider: fixed_auth\n    ip: 1.1.1.1\n    port: 2018\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_proxy_config(str(bad))


@pytest.mark.asyncio
async def test_assignment_least_loaded_and_persist(tmp_path: Path) -> None:
    cfg, ap = _write_cfg(tmp_path)
    mgr = ProxyAssignmentManager(cfg, ap)

    idxs = []
    for cid in ("c1", "c2", "c3", "c4"):
        await mgr.get_proxy_for_channel(cid)
        idxs.append(mgr.get_ip_index_for(cid))
    # 最少绑定优先 → 均匀交替分布
    assert idxs == [0, 1, 0, 1]

    # 持久化到磁盘
    saved = json.loads(Path(ap).read_text(encoding="utf-8"))
    assert len(saved) == 4

    # 同渠道复用同一 IP
    await mgr.get_proxy_for_channel("c1")
    assert mgr.get_ip_index_for("c1") == 0

    # 重启（新实例读同一文件）后绑定关系不变
    mgr2 = ProxyAssignmentManager(cfg, ap)
    assert mgr2.get_ip_index_for("c3") == 0


@pytest.mark.asyncio
async def test_get_protocol_for_overrides_default(tmp_path: Path) -> None:
    cfg, ap = _write_cfg(tmp_path)
    mgr = ProxyAssignmentManager(cfg, ap)
    # c1 → index 0（http，用 defaults），下一个新渠道 → index 1（socks5，条目覆盖）
    await mgr.get_proxy_for_channel("c1")
    await mgr.get_proxy_for_channel("c2")
    assert mgr.get_protocol_for("c1") == "http"
    assert mgr.get_protocol_for("c2") == "socks5"


def test_build_playwright_proxy_http_carries_auth() -> None:
    info = ProxyInfo(ip="1.1.1.1", http_port=2018, sock_port=2018, username="u", password="p")
    pd = build_playwright_proxy(info, "http")
    assert pd == {"server": "http://1.1.1.1:2018", "username": "u", "password": "p"}


def test_build_playwright_proxy_socks5_drops_auth() -> None:
    # Chromium 不支持 SOCKS5 账密认证：只保留 server，丢弃账密
    info = ProxyInfo(ip="2.2.2.2", http_port=2018, sock_port=2018, username="u", password="p")
    pd = build_playwright_proxy(info, "socks5")
    assert pd == {"server": "socks5://2.2.2.2:2018"}


def test_build_playwright_proxy_none() -> None:
    assert build_playwright_proxy(None) is None
