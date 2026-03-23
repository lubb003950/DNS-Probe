# DNS 探测系统部署指南

本文档以当前仓库中的 `scripts/deploy.sh`、`scripts/set-db-password.sh`、`scripts/pack_on_server.sh` 为准，适用于使用 `systemd` 的 Linux 服务器。

默认部署目录：

- `/opt/dns_probe`

默认服务用户：

- `root`

如果需要自定义部署目录，可以在执行时覆盖 `DEPLOY_DIR`：

```bash
DEPLOY_DIR=/srv/dns_probe bash scripts/deploy.sh
```

## 1. 当前部署脚本实际会做什么

`scripts/deploy.sh` 现在是一个可重复执行的部署/升级脚本，主要行为如下：

1. 检查是否以 `root` 运行
2. 检查 `openssl` 和 Python 3.11+
3. 使用 `root:root` 作为服务运行账号
4. 创建部署目录、数据目录、日志目录
5. 将当前项目同步到部署目录
6. 安装依赖
7. 安装项目包本身 `pip install -e`
8. 首次部署时生成：
   - `/etc/dns-probe.key`
   - `/etc/dns-probe.env`
   - `/usr/local/bin/dns-probe-start`
9. 如果 MySQL 就绪，尝试执行 `scripts/init_db.py`
10. 写入 systemd unit 和 logrotate 配置
11. `enable` 并尝试重启：
   - `dns-probe-api`
   - `dns-probe-agent`

几个容易误解的点：

- `deploy.sh` 不会交互输入数据库密码
- 数据库密码加密写入动作由 `scripts/set-db-password.sh` 单独负责
- 脚本内部虽然定义了 `.venv` 路径，但当前并不会创建正式虚拟环境；依赖是直接安装到选中的 Python 解释器里
- 如果机器上没有 `rsync`，脚本会退回到 `cp -a`，这时不会自动删除部署目录里的旧文件
- 如果数据库初始化失败，部署会继续完成，并提示你后续手动补跑

## 2. 前置条件

部署前请确认：

| 项目 | 要求 |
| --- | --- |
| 系统 | Linux x86_64，支持 `systemd` |
| 权限 | `root` |
| Python | 3.11+ |
| OpenSSL | 已安装，命令 `openssl` 可用 |
| 数据库 | MySQL 或 MariaDB，库和账号已创建 |
| 建议工具 | `rsync`、`mysqladmin` |
| 网络 | 在线安装依赖时可访问 PyPI；离线部署需提前准备 `wheels/` |

常用检查命令：

```bash
python3.11 --version
openssl version
mysqladmin ping
```

如果缺少常用工具：

```bash
yum install -y python3.11 python3.11-pip
yum install -y openssl
yum install -y rsync
```

## 3. 准备数据库

示例：

```sql
CREATE DATABASE dns_probe CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'dns_probe_user'@'%' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON dns_probe.* TO 'dns_probe_user'@'%';
FLUSH PRIVILEGES;
```

默认连接信息：

- Host: `127.0.0.1`
- Port: `3306`
- Database: `dns_probe`
- User: `dns_probe_user`

## 4. 首次部署

### 4.1 上传代码并执行部署

将项目上传到服务器上的任意临时目录，然后在项目根目录执行：

```bash
cd /root/dns_probe
chmod +x scripts/deploy.sh
bash scripts/deploy.sh
```

如需自定义部署目录：

```bash
cd /root/dns_probe
DEPLOY_DIR=/srv/dns_probe bash scripts/deploy.sh
```

### 4.2 首次部署后生成的关键文件

部署完成后，通常会生成：

```text
/etc/dns-probe.key
/etc/dns-probe.env
/usr/local/bin/dns-probe-start
/etc/systemd/system/dns-probe-api.service
/etc/systemd/system/dns-probe-agent.service
/etc/logrotate.d/dns-probe
```

默认 `/etc/dns-probe.env` 模板大致如下：

```bash
DNS_PROBE_DATABASE_URL=mysql+pymysql://dns_probe_user@127.0.0.1:3306/dns_probe
# DNS_PROBE_DB_PASSWORD_ENC=
DNS_PROBE_AGENT_NAME=local-agent
# DNS_PROBE_AGENT_TOKEN=

DNS_PROBE_AGENT_AUTH_ENABLED=true
DNS_PROBE_PULL_INTERVAL=30
DNS_PROBE_WORKERS=30
DNS_PROBE_RECORD_RETENTION_DAYS=30
```

### 4.3 配置数据库密码

部署脚本默认只会写入不带密码的数据库 URL。之后有两种方式：

#### 方式 A：推荐，使用加密密码

保留：

```bash
DNS_PROBE_DATABASE_URL=mysql+pymysql://dns_probe_user@127.0.0.1:3306/dns_probe
```

然后执行：

```bash
bash /opt/dns_probe/scripts/set-db-password.sh
systemctl restart dns-probe-api dns-probe-agent
```

脚本会交互输入两次数据库密码，并把加密后的密文写入：

```bash
DNS_PROBE_DB_PASSWORD_ENC="..."
```

运行时由 `/usr/local/bin/dns-probe-start` 解密并导出 `DNS_PROBE_DB_PASSWORD`。

#### 方式 B：直接写完整数据库 URL

也可以直接在 `/etc/dns-probe.env` 中写入带密码的 URL：

```bash
DNS_PROBE_DATABASE_URL=mysql+pymysql://dns_probe_user:your_password@127.0.0.1:3306/dns_probe
```

修改后同样需要重启服务：

```bash
systemctl restart dns-probe-api dns-probe-agent
```

### 4.4 如果部署时没有自动建表

`deploy.sh` 会在 MySQL 就绪后尝试运行 `scripts/init_db.py`。如果当时数据库未就绪，或者密码尚未配置，初始化可能被跳过或失败。

这时可以：

1. 配好数据库后重新执行一次 `bash scripts/deploy.sh`
2. 或手动补跑：

```bash
/usr/local/bin/dns-probe-start /usr/bin/python3.11 /opt/dns_probe/scripts/init_db.py
```

请把 `/usr/bin/python3.11` 替换成机器上实际被 `deploy.sh` 选中的 Python 路径。

## 5. 部署完成后的接入步骤

### 5.1 访问 Web

默认访问地址：

```text
http://服务器IP:8000
```

常用页面：

- 节点管理：`/nodes`
- 任务管理：`/tasks`
- Dashboard：`/dashboard`

### 5.2 接入探测节点 Agent

当前系统默认开启 Agent Token 认证，推荐流程如下：

1. 先在 Web 页面 `/nodes` 预注册节点
2. 系统会生成该节点的：
   - `DNS_PROBE_AGENT_NAME`
   - `DNS_PROBE_AGENT_IP`
   - `DNS_PROBE_AGENT_TOKEN`
3. 把这三个值写入对应 Agent 机器的 `/etc/dns-probe.env`
4. 重启该 Agent 服务

示例：

```bash
DNS_PROBE_AGENT_NAME=node-a
DNS_PROBE_AGENT_IP=10.0.0.11
DNS_PROBE_AGENT_TOKEN=s3cret-token-a
```

说明：

- 节点创建后初始状态是 `offline`
- Agent 注册成功、心跳正常后会变为 `online`
- 心跳超时后后台任务会自动重新标记为 `offline`
- 如果在 Web 里重置了节点 Token，需要同步更新 Agent 机器上的 `DNS_PROBE_AGENT_TOKEN`

## 6. 离线部署

如果目标服务器无法访问 PyPI，请先在一台有网络的 Linux 机器上准备 `wheels/`。

### 6.1 下载离线依赖

在项目根目录执行：

```bash
bash scripts/pack_on_server.sh
```

脚本会：

- 自动寻找 Python 3.11+
- 创建临时虚拟环境 `.venv_pack_tmp`
- 将 `requirements_deploy.txt` 中的依赖下载到 `wheels/`
- 删除临时虚拟环境

### 6.2 打包并传到目标服务器

```bash
tar -czf dns_probe_offline.tar.gz dns_probe
scp dns_probe_offline.tar.gz root@目标机器:/root/
```

### 6.3 在目标服务器部署

```bash
cd /root
tar -xzf dns_probe_offline.tar.gz
cd dns_probe
bash scripts/deploy.sh
```

只要部署目录下存在非空 `wheels/`，`deploy.sh` 就会自动使用：

```bash
pip install --no-index --find-links=...
```

## 7. 升级与重复部署

当前 `deploy.sh` 支持重复执行，适合升级：

- 服务会继续以 `root` 账号运行
- 已存在的 `/etc/dns-probe.key` 和 `/etc/dns-probe.env` 不会被覆盖
- systemd unit 会重新生成
- 服务会重新重启

建议升级方式：

```bash
cd 新版本项目目录
bash scripts/deploy.sh
```

补充说明：

- 装了 `rsync` 时，会用 `--delete` 同步代码，适合清理旧文件
- 没装 `rsync` 时，会退回 `cp -a`，旧文件可能残留

## 8. 部署后的服务与路径

### 服务名

```bash
dns-probe-api
dns-probe-agent
```

### 重要路径

| 路径 | 说明 |
| --- | --- |
| `/opt/dns_probe` | 默认部署目录 |
| `/opt/dns_probe/data/logs` | 日志目录 |
| `/etc/dns-probe.key` | 数据库密码加密密钥 |
| `/etc/dns-probe.env` | 运行环境变量 |
| `/usr/local/bin/dns-probe-start` | 启动包装脚本，负责加载环境变量并解密数据库密码 |
| `/etc/systemd/system/dns-probe-api.service` | API 服务 |
| `/etc/systemd/system/dns-probe-agent.service` | Agent 服务 |
| `/etc/logrotate.d/dns-probe` | 日志轮转配置 |

### 当前 systemd 启动行为

API：

```bash
python -m uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --workers 2
```

Agent：

```bash
python -m apps.agent.main
```

两个服务都通过 `/usr/local/bin/dns-probe-start` 启动，因此会先加载 `/etc/dns-probe.env`。

systemd 的标准输出和错误输出写入 `journal`，应用本身仍会写文件日志，因此：

- `journalctl -u ...` 适合看启动、停止、重启、权限报错等服务级事件
- `/opt/dns_probe/data/logs/*.log` 适合看应用业务日志
- 不再把 systemd 输出直接追加到 `api.log` / `agent.log`，避免同一条日志在文件里重复出现

默认日志文件：

- `/opt/dns_probe/data/logs/api.log`
- `/opt/dns_probe/data/logs/agent.log`

## 9. 常用运维命令

```bash
systemctl status dns-probe-api dns-probe-agent
systemctl restart dns-probe-api dns-probe-agent
systemctl stop dns-probe-api dns-probe-agent
journalctl -u dns-probe-api -f
journalctl -u dns-probe-agent -f
tail -f /opt/dns_probe/data/logs/api.log
tail -f /opt/dns_probe/data/logs/agent.log
```

修改环境变量后：

```bash
systemctl restart dns-probe-api dns-probe-agent
```

修改数据库密码时：

```bash
bash /opt/dns_probe/scripts/set-db-password.sh
systemctl restart dns-probe-api dns-probe-agent
```

手动清理历史数据：

```bash
python scripts/cleanup_db.py
python scripts/cleanup_db.py --records 7
python scripts/cleanup_db.py --dry-run
```

## 10. 常见问题

### 10.1 `deploy.sh` 成功，但提示数据库认证尚未配置

这是当前脚本的正常提示。继续执行：

```bash
bash /opt/dns_probe/scripts/set-db-password.sh
systemctl restart dns-probe-api dns-probe-agent
```

### 10.2 服务启动后连不上数据库

检查：

- `/etc/dns-probe.env` 中的 `DNS_PROBE_DATABASE_URL` 是否正确
- 如果使用加密密码，`DNS_PROBE_DB_PASSWORD_ENC` 是否已写入
- `/etc/dns-probe.key` 是否与当前密文匹配
- 修改配置后是否已重启服务

### 10.3 部署时没有自动建表

常见原因：

- MySQL 当时未启动
- `mysqladmin` 不可用
- 数据库认证尚未配置完成

处理方式：

```bash
bash scripts/deploy.sh
```

或手动执行：

```bash
/usr/local/bin/dns-probe-start /usr/bin/python3.11 /opt/dns_probe/scripts/init_db.py
```

### 10.4 旧代码没有被清理

如果系统没有安装 `rsync`，脚本会改用 `cp -a`，不会删除部署目录中的多余旧文件。建议安装 `rsync` 后重新部署。

### 10.5 在线安装依赖失败

请改用离线部署流程，先执行：

```bash
bash scripts/pack_on_server.sh
```

再携带 `wheels/` 一起部署。

### 10.6 旧机器之前是用 `dns-probe` 账号运行

当前版本约定统一使用 `root` 运行 API 和 Agent。

如果旧机器的 systemd unit 里仍然是：

```ini
User=dns-probe
Group=dns-probe
```

重新执行一次：

```bash
bash scripts/deploy.sh
```

即可把 unit 重新生成为 `root` 版本。

如果你是手动改 unit，也别忘了同步处理：

```bash
chown -R root:root /opt/dns_probe/data
systemctl daemon-reload
systemctl restart dns-probe-api dns-probe-agent
```

## 11. 与旧版部署方式相比的变化

- `deploy.sh` 不再交互录入数据库密码
- 数据库密码配置拆分到 `scripts/set-db-password.sh`
- 当前部署不会创建正式虚拟环境
- 当前部署统一使用 `root` 账号运行服务
- Agent Token 认证默认开启，节点建议先在 Web 中预注册后再接入
