FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai

# ============ Layer 1: 基础工具 (变动少，缓存稳定) ============
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb x11vnc wget gnupg ca-certificates git \
    fonts-wqy-zenhei fonts-wqy-microhei \
    locales python3 python3-pip curl && \
    locale-gen zh_CN.UTF-8 && \
    rm -rf /var/lib/apt/lists/*

ENV LANG=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    LC_ALL=zh_CN.UTF-8

# ============ Layer 2: Chrome (变动少) ============
RUN wget -q -O /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && \
    apt-get update && \
    apt-get install -y --no-install-recommends /tmp/google-chrome.deb && \
    rm /tmp/google-chrome.deb && \
    rm -rf /var/lib/apt/lists/*

# ============ Layer 3: noVNC (变动少) ============
RUN git clone --depth=1 https://github.com/novnc/noVNC.git /opt/novnc && \
    git clone --depth=1 https://github.com/novnc/websockify /opt/novnc/utils/websockify && \
    ln -s /opt/novnc/vnc.html /opt/novnc/index.html

# ============ Layer 4: Playwright + Chromium (下载大，放前面缓存) ============
RUN pip3 install --no-cache-dir playwright && \
    python3 -m playwright install chromium

# ============ Layer 5: 中文输入法 (可能调整，放后面) ============
RUN apt-get update && apt-get install -y --no-install-recommends \
    fcitx fcitx-googlepinyin fcitx-frontend-all \
    dbus dbus-x11 im-config autocutsel && \
    rm -rf /var/lib/apt/lists/*

ENV GTK_IM_MODULE=fcitx \
    QT_IM_MODULE=fcitx \
    XMODIFIERS=@im=fcitx

# 配置 fcitx 默认使用 Google 拼音
RUN mkdir -p /root/.config/fcitx && \
    printf '[Profile]\nIMName=googlepinyin\nEnabledIMList=fcitx-keyboard-us:True,googlepinyin:True,pinyin:False,shuangpin:False\nPreeditStringInClientWindow=False\n' > /root/.config/fcitx/profile

# ============ Layer 6: 启动脚本 (最常变动，放最后) ============
COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 6080 5900 9222

CMD ["/start.sh"]
