#!/bin/bash
# DNS 探测系统 — 数据库密码加密工具
# 用法：bash /opt/dns_probe/scripts/set-db-password.sh
# 需要 root 权限；部署后首次设置密码，或后续更换密码时使用

set -e

KEY_FILE="/etc/dns-probe.key"
ENV_FILE="/etc/dns-probe.env"

# ── 前置检查 ──────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    echo "错误：请以 root 身份运行此脚本" >&2
    exit 1
fi

if ! command -v openssl &>/dev/null; then
    echo "错误：未找到 openssl，请先执行 yum install -y openssl" >&2
    exit 1
fi

if [ ! -f "$KEY_FILE" ]; then
    echo "错误：密钥文件 $KEY_FILE 不存在" >&2
    echo "       请先运行 bash scripts/deploy.sh 完成部署" >&2
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "错误：环境变量文件 $ENV_FILE 不存在" >&2
    echo "       请先运行 bash scripts/deploy.sh 完成部署" >&2
    exit 1
fi

# ── 交互输入密码 ──────────────────────────────────────────
echo "======================================"
echo " DNS 探测系统 — 数据库密码设置"
echo "======================================"
echo ""
read -r -s -p "请输入 MySQL 密码: " PASSWORD
echo ""
read -r -s -p "确认密码: " PASSWORD2
echo ""

if [ "$PASSWORD" != "$PASSWORD2" ]; then
    echo "错误：两次输入不一致，未作任何修改" >&2
    exit 1
fi

if [ -z "$PASSWORD" ]; then
    echo "错误：密码不能为空" >&2
    exit 1
fi

# ── 加密（与 start-with-secret.sh 一致：密钥经 tr 去 CR/LF，openssl 使用 -pass env） ──
export OPENSSL_PROBE_KEY
OPENSSL_PROBE_KEY=$(tr -d '\r\n' < "$KEY_FILE")
if [ -z "$OPENSSL_PROBE_KEY" ]; then
    echo "错误：密钥文件 $KEY_FILE 为空" >&2
    exit 1
fi
# 固定迭代与摘要；显式 -in - 适配 OpenSSL 3.x（与 start-with-secret.sh 解密一致）
# tr -d '\n' 保证密文单行，防止 systemd EnvironmentFile 逐行解析时截断
ENC=$(printf '%s' "$PASSWORD" \
    | openssl enc -e -aes-256-cbc -pbkdf2 -iter 10000 -md sha256 -pass env:OPENSSL_PROBE_KEY -base64 -A -in - 2>/dev/null \
    | tr -d '\n')
unset OPENSSL_PROBE_KEY

if [ -z "$ENC" ]; then
    echo "错误：加密失败，请检查 $KEY_FILE 是否损坏" >&2
    exit 1
fi

# ── 写入 env 文件 ──────────────────────────────────────────
# 密文用双引号包裹，避免 systemd 对部分 base64 字符解析异常；密文本身不含 "
SAFE_ENC="$ENC"
if grep -q "^DNS_PROBE_DB_PASSWORD_ENC=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^DNS_PROBE_DB_PASSWORD_ENC=.*|DNS_PROBE_DB_PASSWORD_ENC=\"$SAFE_ENC\"|" "$ENV_FILE"
else
    echo "DNS_PROBE_DB_PASSWORD_ENC=\"$SAFE_ENC\"" >> "$ENV_FILE"
fi

echo ""
echo "✓ 密码已加密并写入 $ENV_FILE"
echo ""
echo "请重启服务使配置生效："
echo "  systemctl restart dns-probe-api"
echo "  systemctl restart dns-probe-agent"
