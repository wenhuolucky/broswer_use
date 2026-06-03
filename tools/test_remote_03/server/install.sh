#!/bin/bash
# ============================================================
#  FRP 服务端一键安装脚本 — 在有公网 IP 的 Linux VPS 上运行
#  用法: bash install.sh
# ============================================================
set -e

FRP_VERSION="0.62.1"
FRP_DIR="/opt/frp"
CONFIG_DIR="/etc/frp"

echo "============================================="
echo "  FRP 服务端安装脚本 v${FRP_VERSION}"
echo "============================================="

# ---- 1. 下载 frp ----
if command -v frps &>/dev/null; then
    echo "[1/5] frps 已存在，跳过下载"
else
    echo "[1/5] 下载 frp ${FRP_VERSION}..."
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  ARCH_TAG="amd64" ;;
        aarch64) ARCH_TAG="arm64" ;;
        *)       echo "不支持的架构: $ARCH"; exit 1 ;;
    esac

    TMPDIR=$(mktemp -d)
    curl -sL "https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_${ARCH_TAG}.tar.gz" -o "${TMPDIR}/frp.tar.gz"
    tar xzf "${TMPDIR}/frp.tar.gz" -C "${TMPDIR}"
    
    mkdir -p "$FRP_DIR"
    cp "${TMPDIR}/frp_${FRP_VERSION}_linux_${ARCH_TAG}/frps" "${FRP_DIR}/frps"
    chmod +x "${FRP_DIR}/frps"
    rm -rf "${TMPDIR}"
    
    echo "[1/5] 下载完成: ${FRP_DIR}/frps"
fi

# ---- 2. 部署配置 ----
echo "[2/5] 部署配置文件..."
mkdir -p "$CONFIG_DIR"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "${SCRIPT_DIR}/frps.toml" ]; then
    cp "${SCRIPT_DIR}/frps.toml" "${CONFIG_DIR}/frps.toml"
    echo "[2/5] 已复制 frps.toml → ${CONFIG_DIR}/frps.toml"
else
    echo "[2/5] 未找到 frps.toml，请手动放到 ${CONFIG_DIR}/frps.toml"
fi

# ---- 3. 创建 systemd 服务 ----
echo "[3/5] 配置 systemd 服务..."
cat > /etc/systemd/system/frps.service << 'EOF'
[Unit]
Description=FRP Server
After=network.target

[Service]
Type=simple
ExecStart=/opt/frp/frps -c /etc/frp/frps.toml
Restart=on-failure
RestartSec=5
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
echo "[3/5] systemd 服务已创建"

# ---- 4. 开放防火墙端口 ----
echo "[4/5] 配置防火墙..."
if command -v ufw &>/dev/null; then
    ufw allow 7000/tcp   # frps 绑定端口
    ufw allow 7500/tcp   # Dashboard
    ufw allow 6801:6803/tcp  # noVNC
    ufw allow 6901:6903/tcp  # CDP
    echo "[4/5] ufw 规则已添加"
elif command -v firewall-cmd &>/dev/null; then
    firewall-cmd --permanent --add-port=7000/tcp
    firewall-cmd --permanent --add-port=7500/tcp
    firewall-cmd --permanent --add-port=6801-6803/tcp
    firewall-cmd --permanent --add-port=6901-6903/tcp
    firewall-cmd --reload
    echo "[4/5] firewalld 规则已添加"
else
    echo "[4/5] 未检测到防火墙工具，请手动开放端口: 7000, 7500, 6801-6803, 6901-6903"
fi

# ---- 5. 启动服务 ----
echo "[5/5] 启动 frps 服务..."
systemctl enable frps
systemctl restart frps

sleep 2
if systemctl is-active --quiet frps; then
    echo ""
    echo "============================================="
    echo "  ✅ frps 服务已启动!"
    echo ""
    echo "  服务端口: 7000"
    echo "  Dashboard: http://$(hostname -I | awk '{print $1}'):7500"
    echo "  配置文件: ${CONFIG_DIR}/frps.toml"
    echo ""
    echo "  常用命令:"
    echo "    systemctl status frps"
    echo "    systemctl restart frps"
    echo "    journalctl -u frps -f"
    echo "============================================="
else
    echo "❌ frps 启动失败，请检查日志: journalctl -u frps -n 50"
    exit 1
fi