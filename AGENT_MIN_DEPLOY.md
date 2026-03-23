# Agent 节点最小部署手册

本文档只适用于“单独部署探测 Agent 节点”的场景，即：

- API Server 已经部署完成并可访问
- 服务端数据库已经由 API 侧准备好
- 当前机器只负责执行 DNS 探测任务，不承担 Web/API/数据库职责

## 1. 先回答核心问题

Agent 节点最小部署时，不需要本地部署数据库。

原因很直接：

- Agent 进程只做 4 件事：注册、心跳、拉取任务、回传结果
- 这些动作全部通过 HTTP 调用 API 完成
- 数据库连接和建表逻辑在 API 服务端，不在 Agent 主流程里

所以：

- `Agent-only` 节点：不需要 MySQL / MariaDB / PostgreSQL
- `API + Agent` 同机部署：数据库仍然是必需的，因为 API 要写库

注意：当前仓库里的 `scripts/deploy.sh` 是“整套系统部署脚本”，会同时部署 API 和 Agent，并尝试检查 MySQL、执行 `init_db.py`。如果你的目标只是新增一个远端 Agent 节点，不建议直接使用这个脚本。

## 2. 最小依赖

推荐环境：

- Linux x86_64
- Python 3.11+
- `dig` 命令可用

可选兼容：

- 如果没有 `dig`，代码会自动退回到 `nslookup`

Agent 节点至少要满足下面两类网络连通性：

- 能访问 API 服务的 `http://<API_HOST>:<API_PORT>`
- 能访问要探测的 DNS 服务器，通常是 53 端口

## 3. 部署前准备

### 3.1 在服务端 Web 页面预注册节点

进入节点管理页：

- `/nodes`

新增节点后，页面会生成该节点对应的配置片段，至少包含：

```bash
DNS_PROBE_AGENT_NAME="agent-shanghai-01"
DNS_PROBE_AGENT_IP="10.0.0.21"
DNS_PROBE_AGENT_TOKEN="your-token"
```

这一步是必需的。当前 API 默认要求：

- 节点必须先预注册
- Agent 必须带正确 Token 才能注册、心跳、拉任务、上报结果

### 3.2 准备代码

把项目代码放到 Agent 机器，例如：

```bash
/opt/dns_probe
```

如果是离线环境，也需要把项目源码和依赖包一起带到目标机器。

## 4. 安装步骤

### 4.1 安装系统依赖

以常见 Linux 发行版为例，确保以下命令可用：

```bash
python3.11 --version
dig -v
```

如果没有 `dig`，也可以检查：

```bash
nslookup
```

### 4.2 安装 Python 依赖

在项目根目录执行：

```bash
cd /opt/dns_probe
python3.11 -m pip install -r requirements_deploy.txt
python3.11 -m pip install -e .
```

说明：

- 这里不需要执行 `scripts/init_db.py`
- 这里不需要执行 `scripts/set-db-password.sh`
- Agent-only 节点通常也不需要配置 `DNS_PROBE_DATABASE_URL`

## 5. Agent 最小配置

建议单独创建一个环境变量文件，例如：

```bash
/etc/dns-probe-agent.env
```

内容示例：

```bash
DNS_PROBE_API_HOST=10.0.0.5
DNS_PROBE_API_PORT=8000

DNS_PROBE_AGENT_NAME=agent-shanghai-01
DNS_PROBE_AGENT_IP=10.0.0.21
DNS_PROBE_AGENT_TOKEN=your-token

DNS_PROBE_AGENT_AUTH_ENABLED=true
DNS_PROBE_PULL_INTERVAL=30
DNS_PROBE_HEARTBEAT_INTERVAL=30
DNS_PROBE_WORKERS=30
```

最关键的是前 5 个变量：

- `DNS_PROBE_API_HOST`: API 服务地址
- `DNS_PROBE_API_PORT`: API 服务端口
- `DNS_PROBE_AGENT_NAME`: 必须和 Web 里预注册的名称一致
- `DNS_PROBE_AGENT_IP`: 节点对外上报的 IP
- `DNS_PROBE_AGENT_TOKEN`: 节点 Token

注意：

- 如果不配置 `DNS_PROBE_API_HOST`，代码默认值是 `127.0.0.1`
- 这只适合同机部署 API + Agent
- 对于远端独立 Agent，这通常会导致它错误地去连本机 `127.0.0.1:8000`

## 6. 启动方式

### 6.1 直接前台启动

```bash
cd /opt/dns_probe
set -a
source /etc/dns-probe-agent.env
set +a
python3.11 -m apps.agent.main
```

### 6.2 使用 systemd 启动

新建：

```bash
/etc/systemd/system/dns-probe-agent.service
```

内容示例：

```ini
[Unit]
Description=DNS Probe Agent
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/dns_probe
EnvironmentFile=/etc/dns-probe-agent.env
ExecStart=python3.11 -m apps.agent.main
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

加载并启动：

```bash
systemctl daemon-reload
systemctl enable --now dns-probe-agent
systemctl status dns-probe-agent
```

## 7. 启动后如何验证

### 7.1 看本机日志

前台启动时直接看终端输出；systemd 启动时执行：

```bash
journalctl -u dns-probe-agent -f
```

正常情况下应看到：

- 节点注册成功
- 周期性心跳成功
- 能正常拉取任务

### 7.2 看服务端页面

进入：

- `/nodes`

检查该节点是否：

- 状态变成 `online`
- 当前 IP 已更新
- 最近心跳时间持续刷新

### 7.3 看任务是否执行

如果该节点被分配了任务，启动后应能看到：

- Agent 周期性拉取任务
- 任务详情页出现该节点的探测记录

## 8. 最小排障清单

### 8.1 节点一直注册失败

优先检查：

- `DNS_PROBE_API_HOST` 是否写成了远端 API 地址
- `DNS_PROBE_API_PORT` 是否正确
- Agent 机器是否能访问 API 端口
- 节点是否已经在 `/nodes` 预注册
- `DNS_PROBE_AGENT_NAME` 是否和服务端一致
- `DNS_PROBE_AGENT_TOKEN` 是否是最新值

### 8.2 服务端返回 401 / 403

通常表示鉴权或预注册不匹配，重点检查：

- Token 是否填错
- 节点名称是否填错
- Web 里是否重置过 Token

如果 Web 里重置过 Token，需要同步更新 Agent 机器上的 `DNS_PROBE_AGENT_TOKEN`。

### 8.3 Agent 进程启动了，但没有探测结果

优先检查：

- 是否真的给该节点分配了任务
- 任务是否启用
- 目标 DNS 服务器是否可达
- 本机是否有 `dig` 或 `nslookup`

### 8.4 误配了数据库参数怎么办

单独 Agent 节点即使带了 `DNS_PROBE_DATABASE_URL`，通常也不会因为主流程直接访问数据库而受益。

建议做法是：

- 不配数据库参数
- 不执行数据库初始化脚本
- 把数据库能力保留在 API 服务端

## 9. 最小部署结论

新增一个 Agent 节点，最少只需要：

1. 一台能访问 API 和目标 DNS 的机器
2. Python 3.11+
3. `dig` 或 `nslookup`
4. 项目代码和 Python 依赖
5. 5 个关键配置：`DNS_PROBE_API_HOST`、`DNS_PROBE_API_PORT`、`DNS_PROBE_AGENT_NAME`、`DNS_PROBE_AGENT_IP`、`DNS_PROBE_AGENT_TOKEN`

不需要：

- 本地数据库
- 建表脚本
- 数据库密码加密脚本
- 整套 API/Web 部署
