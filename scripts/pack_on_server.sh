#!/bin/bash
# ============================================================
# 【推荐】在服务器上（有网络时）执行此脚本，提前下载所有依赖包
# 之后断网也能完整安装，不会缺包。
#
# 用法（在项目根目录执行）：
#   bash scripts/pack_on_server.sh
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
WHEELS_DIR="$PROJECT_DIR/wheels"

python_env() {
    env -u PYTHONHOME -u PYTHONPATH "$@"
}

resolve_python_bin() {
    local candidate="${1:-}"
    [[ -n "$candidate" ]] || return 1
    if [[ "$candidate" == */* ]]; then
        [[ -x "$candidate" ]] || return 1
        printf '%s\n' "$candidate"
    else
        command -v "$candidate" 2>/dev/null
    fi
}

python_is_supported() {
    python_env "$1" -S -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >/dev/null 2>&1
}

python_version() {
    python_env "$1" -S -c "import sys; print('%d.%d' % sys.version_info[:2])"
}

echo "======================================================"
echo " 下载离线依赖包（将保存到 wheels/ 目录）"
echo "======================================================"

# 找到可用的 Python 3.11+
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -n "$PYTHON_BIN" ]]; then
    PY_ABS="$(resolve_python_bin "$PYTHON_BIN" || true)"
    if [[ -z "$PY_ABS" ]]; then
        echo "✗ PYTHON_BIN=$PYTHON_BIN 不存在或不可执行"
        exit 1
    fi
    if ! python_is_supported "$PY_ABS"; then
        echo "✗ PYTHON_BIN=$PY_ABS 不是可正常启动的 Python 3.11+"
        exit 1
    fi
    PYTHON_BIN="$PY_ABS"
    PY_VER=$(python_version "$PYTHON_BIN")
else
    for PY in python3.11 python3.12 python3; do
        PY_ABS="$(resolve_python_bin "$PY" || true)"
        if [[ -n "$PY_ABS" ]] && python_is_supported "$PY_ABS"; then
            PYTHON_BIN="$PY_ABS"
            PY_VER=$(python_version "$PY_ABS")
            break
        fi
    done
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "✗ 未找到可正常启动的 Python 3.11+，请先安装或清理 PYTHONHOME/PYTHONPATH：yum install -y python3.11"
    exit 1
fi
echo "  使用 Python: $PYTHON_BIN ($PY_VER)"

# 创建临时 venv 用于下载（不污染正式环境）
TMP_VENV="$PROJECT_DIR/.venv_pack_tmp"
python_env "$PYTHON_BIN" -m venv "$TMP_VENV"
source "$TMP_VENV/bin/activate"
pip install --upgrade pip -q

# 下载所有依赖（含传递依赖，原生 Linux 平台，100% 可靠）
mkdir -p "$WHEELS_DIR"
echo "  正在下载依赖包到 wheels/ ..."
pip download \
    -r "$PROJECT_DIR/requirements_deploy.txt" \
    -d "$WHEELS_DIR"

# 清理临时 venv
deactivate
rm -rf "$TMP_VENV"

PKG_COUNT=$(ls "$WHEELS_DIR" | wc -l)
echo ""
echo "======================================================"
echo "  ✓ 下载完成，共 $PKG_COUNT 个包"
echo "  保存位置：$WHEELS_DIR"
echo ""
echo "  后续步骤："
echo "  1. 将整个项目（含 wheels/ 目录）打包备份"
echo "     tar -czf dns_probe_offline.tar.gz -C /opt dns_probe"
echo "  2. 断网后运行正式部署脚本："
echo "     bash scripts/deploy.sh"
echo "======================================================"
