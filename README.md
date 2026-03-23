# DNS 探测系统

基于 `requeriment/DNS探测_UI融合版.md` 实现的分布式 DNS 探测平台。

系统由两部分组成：

- API Server：提供 Web UI、REST API、任务编排、告警评估与展示
- Probe Agent：部署在各探测节点上，定时拉取任务、执行 DNS 查询并上报结果

## 当前能力

- Web UI 管理探测任务、DNS 服务器、探测节点、告警事件
- 多节点 Agent 自动探测并回传原始记录
- 支持失败率与连续失败告警，并可推送到云智慧事件中心
- Dashboard 提供总体成功率、失败率、状态分布、热点失败域名等视图
- 任务详情页支持按时间、节点、状态筛选，并展示每次拨测的耗时曲线

## 最近同步到文档的行为

- 节点需要先在 Web 页面预注册，系统会生成 `Agent Token`
- Agent 认证默认开启，Agent 必须携带正确的 Token 才能注册、心跳、拉取任务、回传记录
- 节点心跳超时后会被自动标记为 `offline`
- 在任务配置页中，`可用节点` 只显示 `enabled=true` 且当前 `online` 的节点
- 如果历史任务已经绑定了 `offline` 节点，编辑任务时仍会保留在 `已选节点` 中，不会被静默丢失
- 删除任务时会一并清理该任务关联的探测记录和告警记录，避免残留脏数据
- 任务详情页中的趋势图已改为“每次拨测耗时曲线”，并按“节点 + DNS”组合分别绘制曲线
- Web 页面支持重置节点 Token，旧 Token 会失效

## 本地开发

### 环境要求

- Python 3.11+
- 建议安装 `dig`
- Windows 下如果没有 `dig`，Agent 会自动退回使用 `nslookup`
- 默认数据库为 MySQL，可通过环境变量覆盖为其他 SQLAlchemy 兼容连接串

### 安装

```bash
pip install -e .
```

### 关键环境变量

常用配置见 `packages/core/config.py`，其中最关键的是：

| 变量名 | 说明 |
| --- | --- |
| `DNS_PROBE_DATABASE_URL` | 数据库连接串 |
| `DNS_PROBE_AGENT_NAME` | Agent 节点名称 |
| `DNS_PROBE_AGENT_IP` | Agent 节点 IP |
| `DNS_PROBE_AGENT_TOKEN` | Agent 认证 Token |
| `DNS_PROBE_AGENT_AUTH_ENABLED` | 是否启用 Agent Token 认证 |

### 初始化数据库

```bash
python scripts/init_db.py
```

如需导入示例数据：

```bash
python scripts/seed_demo.py
```

### 启动服务

启动 API：

```bash
python -m uvicorn apps.api.main:app --reload
```

启动 Agent：

```bash
python -m apps.agent.main
```

访问地址：

- Web UI：<http://127.0.0.1:8000>
- 节点管理页：<http://127.0.0.1:8000/nodes>
- 任务管理页：<http://127.0.0.1:8000/tasks>

### Agent 配置流程

1. 在 Web 页面 `/nodes` 中创建探测节点
2. 复制页面生成的 Agent 环境变量片段
3. 在 Agent 所在机器上写入环境变量
4. 启动 Agent

示例：

```bash
DNS_PROBE_AGENT_NAME=node-a
DNS_PROBE_AGENT_IP=10.0.0.11
DNS_PROBE_AGENT_TOKEN=s3cret-token-a
```

## 运行时行为说明

### 节点状态

- 节点创建后初始状态为 `offline`
- Agent 注册成功或心跳成功后会变为 `online`
- 如果连续多个心跳周期未上报，后台任务会自动将节点标记回 `offline`

### 任务下发

- 任务可以绑定指定探测节点
- 如果任务未选择节点，则表示可下发给所有可执行节点
- 任务编辑页中的 `可用节点` 仅展示当前在线节点
- 已经绑定到任务上的离线节点会保留在 `已选节点` 中，方便人工调整

### 任务详情页图表

- 支持按时间范围、节点、状态筛选
- 趋势图展示单次拨测延迟，而不是按小时失败率
- 每条曲线对应一个“节点 + DNS”组合，便于对比不同节点或不同 DNS 的响应耗时

### 删除行为

- 删除任务时，会同时删除该任务关联的：
  - `ProbeRecord`
  - `AlertEvent`
  - 任务与节点、DNS 的关联关系

## 云主机部署

完整步骤见 [DEPLOY.md](DEPLOY.md)。

如果只新增远端探测节点，不部署 API / Web / 数据库，可直接参考 [AGENT_MIN_DEPLOY.md](AGENT_MIN_DEPLOY.md)。

简要流程：

1. 安装 Python 3.11+ 与 MySQL，并创建数据库
2. 配置数据库连接与 Agent 环境变量
3. 上传项目到目标目录，例如 `/opt/dns_probe`
4. 执行一键部署脚本
5. 先在 Web 页面创建节点，再将生成的配置下发到 Agent 机器
6. 启动 API 与 Agent 服务

一键部署示例：

```bash
cd /opt/dns_probe
bash scripts/deploy.sh
```

无外网环境下，可先在有网机器执行：

```bash
bash scripts/pack_on_server.sh
```

生成 `wheels/` 后再打包传到目标机器执行部署。

## 部署后的目录结构

执行 `deploy.sh` 后，典型目录结构如下：

```text
/opt/dns_probe/
├── apps/
│   ├── agent/
│   ├── api/
│   └── web/
├── packages/
│   ├── alerts/
│   ├── core/
│   └── db/
├── scripts/
├── data/logs/
├── wheels/
├── pyproject.toml
├── requirements_deploy.txt
├── README.md
└── DEPLOY.md
```

系统目录中还会生成：

| 路径 | 说明 |
| --- | --- |
| `/etc/dns-probe.env` | 环境变量文件 |
| `/etc/systemd/system/dns-probe-api.service` | API 服务 unit |
| `/etc/systemd/system/dns-probe-agent.service` | Agent 服务 unit |
| `/etc/logrotate.d/dns-probe` | 日志轮转配置 |

## 脚本说明

| 脚本 | 作用 |
| --- | --- |
| `scripts/init_db.py` | 初始化数据库并执行必要迁移 |
| `scripts/deploy.sh` | 一键部署 API、Agent、systemd、logrotate |
| `scripts/run_dev.py` | 本地开发时启动 API 服务 |
| `scripts/seed_demo.py` | 导入演示数据 |
| `scripts/cleanup_db.py` | 手动清理过期探测记录与老告警 |
| `scripts/pack_on_server.sh` | 打包离线 wheel 依赖 |
| `scripts/logrotate_dns_probe.conf` | logrotate 配置模板 |

## 相关路径

- Web 路由：`apps/api/routers/web.py`
- API 路由：`apps/api/routers/`
- Web 模板：`apps/web/templates/`
- 数据模型：`packages/db/models.py`
- 告警逻辑：`packages/alerts/rules.py`
- 配置项：`packages/core/config.py`
