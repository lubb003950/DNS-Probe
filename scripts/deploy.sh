#!/bin/bash
# DNS Probe deploy/upgrade script
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(dirname "$SCRIPT_DIR")"

DEPLOY_DIR="${DEPLOY_DIR:-/opt/dns_probe}"
SERVICE_USER="root"
SERVICE_GROUP="root"
VENV_DIR="$DEPLOY_DIR/.venv"
DATA_DIR="$DEPLOY_DIR/data"
LOG_DIR="$DATA_DIR/logs"
KEY_FILE="/etc/dns-probe.key"
ENV_FILE="/etc/dns-probe.env"
LOGROTATE_DEST="/etc/logrotate.d/dns-probe"
WRAPPER_SCRIPT="/usr/local/bin/dns-probe-start"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

db_auth_configured() {
    if [[ -n "${DNS_PROBE_DB_PASSWORD_ENC:-}" ]]; then
        return 0
    fi
    [[ "${DNS_PROBE_DATABASE_URL:-}" =~ ://[^@/]+:[^@/]*@ ]]
}

echo "============================================================"
echo " DNS Probe deploy / upgrade"
echo " Target dir: $DEPLOY_DIR"
echo "============================================================"
echo ""

[[ "$(id -u)" -eq 0 ]] || die "Please run this script as root."
command -v openssl >/dev/null 2>&1 || die "openssl not found. Install it first."

PYTHON_BIN=""
PY_VER=""
for PY in python3.12 python3.11 python3; do
    if command -v "$PY" >/dev/null 2>&1; then
        _abs="$(command -v "$PY")"
        if "$_abs" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
            PYTHON_BIN="$_abs"
            PY_VER="$("$_abs" -c "import sys; print('%d.%d' % sys.version_info[:2])")"
            break
        fi
    fi
done
[[ -n "$PYTHON_BIN" ]] || die "Python 3.11+ not found."
info "Python: $PYTHON_BIN ($PY_VER)"
info "Service account: $SERVICE_USER:$SERVICE_GROUP"

info "Creating directories..."
mkdir -p "$DEPLOY_DIR" "$DATA_DIR" "$LOG_DIR"

info "Syncing source code to $DEPLOY_DIR ..."
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude='.venv/' \
        --exclude='*.egg-info/' \
        --exclude='dist/' \
        --exclude='build/' \
        --exclude='.git/' \
        --exclude='__pycache__/' \
        --exclude='*.pyc' \
        --exclude='.env' \
        --exclude='.env.*' \
        --exclude='*.log' \
        "$SRC_DIR/" "$DEPLOY_DIR/"
else
    warn "rsync not found, falling back to cp -a. Old deleted files may remain in $DEPLOY_DIR."
    cp -a "$SRC_DIR/." "$DEPLOY_DIR/"
    rm -rf "$DEPLOY_DIR/.git" "$DEPLOY_DIR/dist" "$DEPLOY_DIR/build" 2>/dev/null || true
    find "$DEPLOY_DIR" -maxdepth 3 -name '*.egg-info' -type d \
        -not -path "$VENV_DIR/*" -exec rm -rf {} + 2>/dev/null || true
    find "$DEPLOY_DIR" -name '__pycache__' -type d \
        -not -path "$VENV_DIR/*" -exec rm -rf {} + 2>/dev/null || true
fi

info "Upgrading pip..."
"$PYTHON_BIN" -m pip install --upgrade pip -q

WHEELS_DIR="$DEPLOY_DIR/wheels"
if [[ -d "$WHEELS_DIR" ]] && [[ -n "$(ls -A "$WHEELS_DIR" 2>/dev/null)" ]]; then
    PKG_COUNT="$(ls "$WHEELS_DIR" | wc -l)"
    info "Installing dependencies from offline wheels ($PKG_COUNT packages)..."
    "$PYTHON_BIN" -m pip install --no-index --find-links="$WHEELS_DIR" \
        -r "$DEPLOY_DIR/requirements_deploy.txt" -q
else
    info "Installing dependencies from PyPI..."
    "$PYTHON_BIN" -m pip install -r "$DEPLOY_DIR/requirements_deploy.txt" -q
fi

info "Installing project package..."
"$PYTHON_BIN" -m pip install -e "$DEPLOY_DIR" -q

if [[ ! -f "$KEY_FILE" ]]; then
    info "Generating encryption key: $KEY_FILE"
    openssl rand -base64 48 | tr -d '\n' > "$KEY_FILE"
    chmod 640 "$KEY_FILE"
    chown root:root "$KEY_FILE"
else
    info "Encryption key already exists: $KEY_FILE"
fi

if [[ ! -f "$ENV_FILE" ]]; then
    info "Creating env file: $ENV_FILE"
    cat > "$ENV_FILE" <<'ENVEOF'
# DNS Probe runtime configuration
# Restart services after changes:
# systemctl restart dns-probe-api dns-probe-agent

DNS_PROBE_DATABASE_URL=mysql+pymysql://dns_probe_user@127.0.0.1:3306/dns_probe
# DNS_PROBE_DB_PASSWORD_ENC=
DNS_PROBE_AGENT_NAME=local-agent
# DNS_PROBE_AGENT_TOKEN=

DNS_PROBE_AGENT_AUTH_ENABLED=true
DNS_PROBE_PULL_INTERVAL=30
DNS_PROBE_WORKERS=30
DNS_PROBE_RECORD_RETENTION_DAYS=30
ENVEOF
    chmod 640 "$ENV_FILE"
    chown root:root "$ENV_FILE"
else
    info "Env file already exists: $ENV_FILE"
fi

info "Writing startup wrapper: $WRAPPER_SCRIPT"
cat > "$WRAPPER_SCRIPT" <<WRAPEOF
#!/bin/bash
set -euo pipefail

KEY_FILE="$KEY_FILE"
ENV_FILE="$ENV_FILE"

set -a
source "\$ENV_FILE"
set +a

if [[ -n "\${DNS_PROBE_DB_PASSWORD_ENC:-}" ]]; then
    _key=\$(tr -d '\r\n' < "\$KEY_FILE")
    DNS_PROBE_DB_PASSWORD=\$(
        printf '%s' "\$DNS_PROBE_DB_PASSWORD_ENC" \
        | openssl enc -d -aes-256-cbc -pbkdf2 -iter 10000 -md sha256 \
            -pass "pass:\$_key" -base64 -in - 2>/dev/null || true
    )
    export DNS_PROBE_DB_PASSWORD
fi

exec "\$@"
WRAPEOF
chmod 755 "$WRAPPER_SCRIPT"

info "Checking MySQL readiness..."
MYSQL_READY=false
if command -v mysqladmin >/dev/null 2>&1; then
    for i in $(seq 1 12); do
        if mysqladmin ping --silent 2>/dev/null; then
            MYSQL_READY=true
            break
        fi
        warn "MySQL not ready yet, retrying in 5s ($i/12)..."
        sleep 5
    done
else
    warn "mysqladmin not found, skipping automatic MySQL readiness check."
fi

if $MYSQL_READY; then
    info "Initializing database schema..."
    if (
        set -a
        source "$ENV_FILE" 2>/dev/null || true
        set +a
        if [[ -n "${DNS_PROBE_DB_PASSWORD_ENC:-}" ]]; then
            _key=$(tr -d '\r\n' < "$KEY_FILE")
            DNS_PROBE_DB_PASSWORD=$(
                printf '%s' "$DNS_PROBE_DB_PASSWORD_ENC" \
                | openssl enc -d -aes-256-cbc -pbkdf2 -iter 10000 -md sha256 \
                    -pass "pass:$_key" -base64 -in - 2>/dev/null || true
            )
            export DNS_PROBE_DB_PASSWORD
        fi
        if ! db_auth_configured; then
            warn "No encrypted DB password or password-bearing DNS_PROBE_DATABASE_URL detected."
            warn "init_db may fail if MySQL requires a password."
        fi
        cd "$DEPLOY_DIR"
        "$PYTHON_BIN" scripts/init_db.py
    ); then
        info "Database schema initialized."
    else
        warn "Database schema initialization failed, but deployment will continue."
        warn "If your database needs a password, run: bash $DEPLOY_DIR/scripts/set-db-password.sh"
        warn "Then run manually: $WRAPPER_SCRIPT $PYTHON_BIN $DEPLOY_DIR/scripts/init_db.py"
    fi
else
    warn "MySQL was not ready, skipping init_db."
    warn "Run manually later: $WRAPPER_SCRIPT $PYTHON_BIN $DEPLOY_DIR/scripts/init_db.py"
fi

info "Setting file permissions..."
chown -R root:root "$DEPLOY_DIR"
find "$DEPLOY_DIR" -type d -exec chmod 755 {} \;
find "$DEPLOY_DIR" -type f -exec chmod 644 {} \;
if [[ -d "$DEPLOY_DIR/scripts" ]]; then
    find "$DEPLOY_DIR/scripts" -type f -name '*.sh' -exec chmod 755 {} \;
fi
chown -R root:root "$DATA_DIR"
chmod 750 "$DATA_DIR" "$LOG_DIR"

LOGROTATE_SRC="$DEPLOY_DIR/scripts/logrotate_dns_probe.conf"
if [[ -f "$LOGROTATE_SRC" ]]; then
    sed \
        -e "s|/opt/dns_probe|$DEPLOY_DIR|g" \
        -e "s|create 0644 root root|create 0644 $SERVICE_USER $SERVICE_GROUP|g" \
        "$LOGROTATE_SRC" > "$LOGROTATE_DEST"
    chmod 644 "$LOGROTATE_DEST"
    info "Installed logrotate config: $LOGROTATE_DEST"
else
    warn "Logrotate template not found: $LOGROTATE_SRC"
fi

info "Writing systemd unit files..."
cat > /etc/systemd/system/dns-probe-api.service <<APIEOF
[Unit]
Description=DNS Probe API Server
Documentation=file://$DEPLOY_DIR/CLAUDE.md
After=network.target mysqld.service mariadb.service
Wants=mysqld.service

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$DEPLOY_DIR
ExecStart=$WRAPPER_SCRIPT $PYTHON_BIN -m uvicorn apps.api.main:app \
    --host 0.0.0.0 --port 8000 --workers 2
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal
NoNewPrivileges=yes
ProtectSystem=full
PrivateTmp=yes
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
APIEOF

cat > /etc/systemd/system/dns-probe-agent.service <<AGENTEOF
[Unit]
Description=DNS Probe Agent
Documentation=file://$DEPLOY_DIR/CLAUDE.md
After=network.target dns-probe-api.service
Wants=dns-probe-api.service

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$DEPLOY_DIR
ExecStart=$WRAPPER_SCRIPT $PYTHON_BIN -m apps.agent.main
Restart=on-failure
RestartSec=10s
StandardOutput=journal
StandardError=journal
NoNewPrivileges=yes
ProtectSystem=full
PrivateTmp=yes
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
AGENTEOF

systemctl daemon-reload

info "Enabling services..."
systemctl enable dns-probe-api dns-probe-agent

if ! systemctl restart dns-probe-api; then
    warn "Failed to restart dns-probe-api. Check: journalctl -u dns-probe-api -f"
fi
if ! systemctl restart dns-probe-agent; then
    warn "Failed to restart dns-probe-agent. Check: journalctl -u dns-probe-agent -f"
fi

sleep 3
API_STATUS="$(systemctl is-active dns-probe-api 2>/dev/null || echo "unknown")"
AGENT_STATUS="$(systemctl is-active dns-probe-agent 2>/dev/null || echo "unknown")"

DB_AUTH_SET=false
if grep -q '^DNS_PROBE_DB_PASSWORD_ENC=[^#]' "$ENV_FILE" 2>/dev/null; then
    DB_AUTH_SET=true
elif grep -Eq '^DNS_PROBE_DATABASE_URL=.*://[^@/]+:[^@/]*@' "$ENV_FILE" 2>/dev/null; then
    DB_AUTH_SET=true
fi

echo ""
echo "============================================================"
echo " Deployment complete"
echo "============================================================"
printf "  %-20s %s\n" "dns-probe-api:" "$API_STATUS"
printf "  %-20s %s\n" "dns-probe-agent:" "$AGENT_STATUS"
echo ""
echo "  Deploy dir: $DEPLOY_DIR"
echo "  Log dir:    $LOG_DIR"
echo "  Env file:   $ENV_FILE"
echo ""

if ! $DB_AUTH_SET; then
    echo -e "${YELLOW}  [!] Database auth is not configured yet. Run:${NC}"
    echo "      bash $DEPLOY_DIR/scripts/set-db-password.sh"
    echo "      systemctl restart dns-probe-api dns-probe-agent"
    echo "      $WRAPPER_SCRIPT $PYTHON_BIN $DEPLOY_DIR/scripts/init_db.py"
    echo ""
fi

echo "  Useful commands:"
echo "  systemctl status dns-probe-api dns-probe-agent"
echo "  journalctl -u dns-probe-api -f"
echo "  journalctl -u dns-probe-agent -f"
echo "============================================================"
