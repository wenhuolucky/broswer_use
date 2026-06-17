from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.streaming.display_pool import Slot


_WWW_CANDIDATES = (
    "/usr/share/kasmvnc/www",
    "/usr/local/share/kasmvnc/www",
    "/usr/share/kasmvncserver/www",
)

# ── KasmVNC 画质配置 ────────────────────────────────────────────
# KasmVNC 默认「允许客户端覆盖服务端编码设置」，导致我们在命令行设的画质/视频模式参数
# 被 noVNC 客户端默认预设覆盖、不生效(表现为画面仍糊)。把这个开关关掉后，命令行的
# -VideoArea/-DynamicQuality* 才真正说了算。该开关只能写在 kasmvnc.yaml 里。
# Xvnc 读 $HOME/.vnc/kasmvnc.yaml，故给 Xvnc 子进程指定一个受控 HOME，把配置写进去。
_KASMVNC_HOME = Path(tempfile.gettempdir()) / "kasmvnc-home"
_OPENBOX_RC = Path(tempfile.gettempdir()) / "kasmvnc-openbox-rc.xml"

_KASMVNC_YAML = """\
runtime_configuration:
  allow_client_to_override_kasm_server_settings: false
"""

# openbox 默认把桌面(根窗口)滚轮绑定到切换工作区，滚动页面时会漏出 "desktop N" 弹窗。
# 这份配置：单工作区、关弹窗、清空桌面/根窗口的鼠标与键盘绑定，避免误触 WM 行为。
#
# 关键：必须显式给出 <focus> 与 <mouse> 的 Client 上下文。KasmVNC 把客户端键盘事件
# 注入到「持有 X 输入焦点的窗口」(鼠标走坐标注入、与焦点无关)。openbox 默认 <mouse>
# 里的 Client「Left → Focus」绑定负责「点哪个窗口就把焦点给它」；若只留 Desktop/Root
# 空上下文，等于删掉了点击聚焦——kiosk 的 Chromium 一旦因 RANDR resize 等失去焦点就再
# 也夺不回来，表现为「鼠标能点、键盘没反应」。这里用 followMouse + underMouse 让指针下
# 的窗口始终持有焦点(单一全屏窗口最稳)，并保留点击聚焦兜底。
_OPENBOX_RC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <focus>
    <focusNew>yes</focusNew>
    <followMouse>yes</followMouse>
    <focusLast>yes</focusLast>
    <underMouse>yes</underMouse>
    <focusDelay>0</focusDelay>
    <raiseOnFocus>no</raiseOnFocus>
  </focus>
  <desktops>
    <number>1</number>
    <popupTime>0</popupTime>
  </desktops>
  <keyboard></keyboard>
  <mouse>
    <context name="Desktop"></context>
    <context name="Root"></context>
    <context name="Client">
      <mousebind button="Left" action="Press">
        <action name="Focus"/>
        <action name="Raise"/>
      </mousebind>
    </context>
  </mouse>
</openbox_config>
"""


def _ensure_kasmvnc_config() -> None:
    """写出 kasmvnc.yaml(受控 HOME 的 ~/.vnc 下)与 openbox 配置文件。幂等、失败不致命。"""
    try:
        vnc_dir = _KASMVNC_HOME / ".vnc"
        vnc_dir.mkdir(parents=True, exist_ok=True)
        (vnc_dir / "kasmvnc.yaml").write_text(_KASMVNC_YAML, encoding="utf-8")
        _OPENBOX_RC.write_text(_OPENBOX_RC_XML, encoding="utf-8")
    except OSError:
        pass


@dataclass(slots=True)
class StreamProcess:
    slot: Slot
    procs: list[asyncio.subprocess.Process] = field(default_factory=list)
    log_path: str | None = None
    _logf: object | None = None

    async def stop(self) -> None:
        for proc in self.procs:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
        for proc in self.procs:
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (TimeoutError, asyncio.TimeoutError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        if self._logf is not None:
            try:
                self._logf.close()
            except Exception:
                pass


def _resolve_www(configured: str | None) -> str | None:
    if configured:
        return configured if Path(configured, "index.html").is_file() else None
    for candidate in _WWW_CANDIDATES:
        if Path(candidate, "index.html").is_file():
            return candidate
    for index_file in Path("/usr/share").glob("kasmvnc*/www/index.html"):
        return str(index_file.parent)
    return None


async def start_stream(
    slot: Slot,
    *,
    kasmvnc_bin: str,
    screen: str,
    www_dir: str | None = None,
) -> StreamProcess:
    parts = screen.split("x")
    if len(parts) == 3:
        geometry = f"{parts[0]}x{parts[1]}"
        depth = parts[2]
    else:
        geometry = screen
        depth = "24"

    stream = StreamProcess(slot=slot)
    display_num = slot.display.lstrip(":")
    stream.log_path = str(Path(tempfile.gettempdir()) / f"kasmvnc-X{display_num}.log")
    stream._logf = open(stream.log_path, "wb")

    # 并发场景下，如果之前的 Xvnc 崩溃残留锁文件，先清理
    await _cleanup_stale_display(display_num)

    args = [
        kasmvnc_bin,
        slot.display,
        "-geometry",
        geometry,
        "-depth",
        depth,
        "-SecurityTypes",
        "None",
        "-DisableBasicAuth",
        "-interface",
        "127.0.0.1",
        "-websocketPort",
        str(slot.web_port),
    ]
    # 画质偏好「保清晰」：头条登录页背景是循环视频，持续运动 ~5s(VideoTime 默认)后会让
    # KasmVNC 进入「视频模式」——视频模式会在服务端把分辨率缩小、再用 4:2:0 抽样的低画质
    # 编码，导致整帧(连带登录框文字)发糊，滚动打断运动才短暂恢复。
    # 根治不是调视频模式画质(那只管编码质量，管不了降分辨率/抽样)，而是直接「不进入视频
    # 模式」：把触发视频模式所需的变化面积阈值 -VideoArea 提到 100%(默认 45%)，背景视频不再
    # 触发，整帧始终走静态通道。再把静态画质上下限抬高保证锐利。数值 0-9，9 最高。
    args.extend([
        "-VideoArea", "100",        # 实质禁用视频模式(默认 45 → 背景视频不再触发降质/降分辨率)
        "-DynamicQualityMin", "8",  # 静态画质地板(默认 7)
        "-DynamicQualityMax", "9",  # 静态画质上限(默认 8)
    ])
    www = _resolve_www(www_dir)
    if www:
        args.extend(["-httpd", www])

    # 写出 kasmvnc.yaml/openbox 配置，并把 HOME 指向受控目录，让 Xvnc 读到我们的
    # kasmvnc.yaml(关闭客户端覆盖，使上面的画质参数生效)。
    _ensure_kasmvnc_config()
    env = {**os.environ, "DISPLAY": slot.display, "HOME": str(_KASMVNC_HOME)}
    proc = await asyncio.create_subprocess_exec(
        *args,
        env=env,
        stdout=stream._logf,
        stderr=stream._logf,
    )
    stream.procs.append(proc)
    if not await _wait_display_ready(proc, display_num, timeout=12.0):
        await stream.stop()
        raise RuntimeError(f"KasmVNC display {slot.display} did not become ready")

    # 在该显示上挂一个轻量窗口管理器(openbox)。
    # KasmVNC 客户端默认会把远端桌面动态 resize 成浏览器视口大小，但有头 Chromium
    # 的窗口是启动时固定的，没有 WM 就跟不上桌面尺寸变化——窗口盖不满桌面，用户只
    # 看到左上角一块；若改用缩放又会糊。openbox 会在桌面(RANDR)尺寸变化时把全屏
    # (--kiosk)的 Chromium 窗口重新铺满，于是桌面=窗口=客户端视口、1:1 清晰且铺满。
    # 没装 openbox 时静默退化为旧行为(可能不铺满)，不致命。
    try:
        wm = await asyncio.create_subprocess_exec(
            "openbox",
            "--config-file",
            str(_OPENBOX_RC),
            env=env,
            stdout=stream._logf,
            stderr=stream._logf,
        )
        stream.procs.append(wm)
    except FileNotFoundError:
        pass

    return stream


async def _wait_display_ready(
    proc: asyncio.subprocess.Process, display_num: str, *, timeout: float
) -> bool:
    sock = Path(f"/tmp/.X11-unix/X{display_num}")
    waited = 0.0
    while waited < timeout:
        if proc.returncode is not None:
            return False
        if sock.exists():
            return True
        await asyncio.sleep(0.2)
        waited += 0.2
    return proc.returncode is None and sock.exists()


async def _cleanup_stale_display(display_num: str) -> None:
    """清理残留的 X display 锁文件和 socket。

    并发场景下，如果 Xvnc 进程崩溃但没清理锁文件，下次启动会报
    "Server is already active for display X"。这个函数检查锁文件是否
    真的对应一个活着的进程，如果是残留的，删除锁文件和 socket。
    """
    lock_file = Path(f"/tmp/.X{display_num}-lock")
    sock_file = Path(f"/tmp/.X11-unix/X{display_num}")

    if lock_file.exists():
        try:
            # 锁文件里存的是进程 PID
            pid_str = lock_file.read_text().strip()
            if pid_str.isdigit():
                pid = int(pid_str)
                # 检查进程是否还在跑
                try:
                    import signal
                    os.kill(pid, 0)  # 不发送信号，只检查进程是否存在
                    # 进程还在跑，不能删除锁文件
                    return False
                except ProcessLookupError:
                    # 进程不在，锁文件是残留的，删除
                    lock_file.unlink()
                except PermissionError:
                    # 没权限检查，保守起见不删除
                    return False
            else:
                # 锁文件格式不对，直接删除
                lock_file.unlink()
        except Exception:
            pass

    if sock_file.exists():
        try:
            sock_file.unlink()
        except Exception:
            pass

    return True
