# 生产安全与运维加固实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 移除仓库凭据、提供最小权限服务运行配置、可控日志与备份、CI 门禁和可恢复的权威文档。

**Architecture:** 应用只从环境变量读取秘密；生产运行文件作为可审阅模板进入仓库，但安装、账号变更、密码轮换和历史重写保持为独立外部步骤。CI 在推送时执行锁文件、测试和已知凭据扫描。

**Tech Stack:** Python 3.14、uv、pytest、GitHub Actions、systemd、logrotate、mysqldump

## Global Constraints

- 不在仓库中写入任何真实凭据、服务器私钥或业务备份。
- 不自动修改服务器、本机 SSH 配置、Git 远端历史或真实账号。
- systemd、备份和日志配置必须使用专用非 root 用户。
- `data/` 保持不变。

---

### Task 1: 移除仓库凭据和 agents.md 回退

**Files:**
- Modify: `agents.md`
- Modify: `src/work_order_process/config.py`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_config.py`
- Create: `tests/test_repository_security.py`

**Interfaces:**
- Changes: `load_settings()` 只从 `WORKORDER_USERNAME`、`WORKORDER_PASSWORD` 读取认证信息。

- [ ] **Step 1: 写环境变量必填和仓库凭据扫描测试**

```python
def test_settings_do_not_read_credentials_from_agents(monkeypatch, tmp_path):
    monkeypatch.delenv("WORKORDER_USERNAME", raising=False)
    monkeypatch.delenv("WORKORDER_PASSWORD", raising=False)
    monkeypatch.setattr(config, "AGENTS_FILE", tmp_path / "agents.md")
    (tmp_path / "agents.md").write_text('USERNAME = "x"\\nPASSWORD = "y"', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings()

def test_tracked_text_files_do_not_contain_known_credentials():
    tracked = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
    content = "\n".join(
        Path(path).read_text(encoding="utf-8", errors="ignore")
        for path in tracked
        if Path(path).suffix.lower() in {"", ".md", ".py", ".toml", ".yml", ".yaml"}
    )
    forbidden_password = "Bosi_" + "soft2024"
    assert forbidden_password not in content
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-default-groups pytest tests/test_config.py tests/test_repository_security.py -q`

Expected: agents.md 仍被读取且真实凭据仍存在。

- [ ] **Step 3: 删除凭据和回退逻辑**

删除 `_read_agents_defaults`、正则凭据解析及 README 中 agents.md 配置说明。`agents.md` 仅保留项目说明并引用 `.env.example`。

- [ ] **Step 4: 运行安全配置测试**

Run: `uv run --no-default-groups pytest tests/test_config.py tests/test_repository_security.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add agents.md src/work_order_process/config.py .env.example README.md tests/test_config.py tests/test_repository_security.py
git commit -m "security: remove tracked API credentials"
```

### Task 2: 增加 systemd 和日志轮转模板

**Files:**
- Modify: `src/work_order_process/daily_runner.py`
- Create: `deploy/work-order-daily.service`
- Create: `deploy/work-order-daily.logrotate`
- Create: `docs/production_operations.md`
- Test: `tests/test_daily_runner.py`
- Test: `tests/test_deploy_assets.py`

**Interfaces:**
- Produces: `configure_logging() -> None`

- [ ] **Step 1: 写 httpx 日志级别和部署模板测试**

```python
def test_configure_logging_suppresses_httpx_request_logs():
    configure_logging()
    assert logging.getLogger("httpx").level == logging.WARNING

def test_systemd_unit_runs_as_dedicated_user():
    text = Path("deploy/work-order-daily.service").read_text(encoding="utf-8")
    assert "User=workorder" in text
    assert "Restart=on-failure" in text
    assert "EnvironmentFile=/etc/work-order-process/work-order.env" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-default-groups pytest tests/test_daily_runner.py tests/test_deploy_assets.py -q`

Expected: 配置函数和部署文件不存在。

- [ ] **Step 3: 编写部署模板**

systemd 核心内容：

```ini
[Service]
Type=simple
User=workorder
Group=workorder
WorkingDirectory=/opt/work_order_process
EnvironmentFile=/etc/work-order-process/work-order.env
ExecStart=/usr/local/bin/uv run --no-default-groups python -m work_order_process.daily_runner
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
```

logrotate 核心内容：

```text
/var/log/work-order-process/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    create 0640 workorder workorder
}
```

- [ ] **Step 4: 运行部署资产测试**

Run: `uv run --no-default-groups pytest tests/test_daily_runner.py tests/test_deploy_assets.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add src/work_order_process/daily_runner.py deploy docs/production_operations.md tests/test_daily_runner.py tests/test_deploy_assets.py
git commit -m "ops: add managed daily runner configuration"
```

### Task 3: 增加受控 MySQL 备份模板

**Files:**
- Create: `scripts/backup_mysql.sh`
- Create: `deploy/work-order-backup.service`
- Create: `deploy/work-order-backup.timer`
- Modify: `docs/production_operations.md`
- Test: `tests/test_deploy_assets.py`

**Interfaces:**
- Script consumes: `/etc/work-order-process/mysql-backup.cnf`
- Script consumes: `BACKUP_DIR`，默认 `/var/backups/work-order-process`
- Script consumes: `RETENTION_DAYS`，默认 `14`

- [ ] **Step 1: 写备份脚本安全约束测试**

```python
def test_backup_script_uses_defaults_file_and_writes_outside_repo():
    text = Path("scripts/backup_mysql.sh").read_text(encoding="utf-8")
    assert "--defaults-extra-file=/etc/work-order-process/mysql-backup.cnf" in text
    assert "/var/backups/work-order-process" in text
    assert "-mtime +\"$RETENTION_DAYS\"" in text
    assert "-p$" not in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-default-groups pytest tests/test_deploy_assets.py -q`

Expected: 备份文件不存在。

- [ ] **Step 3: 编写备份脚本和 timer**

脚本使用：

```bash
umask 077
mysqldump --defaults-extra-file=/etc/work-order-process/mysql-backup.cnf \
  --single-transaction --routines --triggers work_order_datalake \
  | gzip -c > "$tmp_file"
test -s "$tmp_file"
mv "$tmp_file" "$final_file"
find "$BACKUP_DIR" -type f -name '*.sql.gz' -mtime +"$RETENTION_DAYS" -delete
```

timer 设置 `OnCalendar=*-*-* 01:00:00` 和 `Persistent=true`。

- [ ] **Step 4: 运行部署资产测试**

Run: `uv run --no-default-groups pytest tests/test_deploy_assets.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add scripts/backup_mysql.sh deploy/work-order-backup.service deploy/work-order-backup.timer docs/production_operations.md tests/test_deploy_assets.py
git commit -m "ops: add database backup templates"
```

### Task 4: 增加 CI、仓库整洁和权威文档

**Files:**
- Create: `.github/workflows/test.yml`
- Modify: `.gitignore`
- Modify: `README.md`
- Create: `docs/database_usage.md`
- Test: `tests/test_repository_security.py`

**Interfaces:**
- CI runs on Python 3.14.

- [ ] **Step 1: 写 CI 和文档链接测试**

```python
def test_ci_runs_lock_tests_and_secret_scan():
    text = Path(".github/workflows/test.yml").read_text(encoding="utf-8")
    assert "python-version: '3.14'" in text
    assert "uv lock --check" in text
    assert "pytest -q" in text
    assert "test_repository_security.py" in text

def test_readme_links_existing_documents():
    for path in re.findall(r"`(docs/[^`]+\\.md)`", Path("README.md").read_text(encoding="utf-8")):
        assert Path(path).exists(), path
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-default-groups pytest tests/test_repository_security.py -q`

Expected: CI 不存在，README 仍链接缺失文档。

- [ ] **Step 3: 编写 CI 和文档**

CI 顺序：

```yaml
- uses: astral-sh/setup-uv@v6
- run: uv sync --all-groups --locked
- run: uv lock --check
- run: uv run pytest tests/test_repository_security.py -q
- run: uv run pytest -q
```

`.gitignore` 增加 `logs/`、`backups/`。`docs/database_usage.md` 恢复表关系、快照导入、营收加工、常用查询和运维入口；README 只链接存在文件。

- [ ] **Step 4: 运行文档和完整测试**

Run: `uv run --no-default-groups pytest tests/test_repository_security.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add .github/workflows/test.yml .gitignore README.md docs/database_usage.md tests/test_repository_security.py
git commit -m "ci: verify tests secrets and documentation"
```

### Task 5: 本地总验证和外部操作清单

**Files:**
- Modify: `docs/production_operations.md`

- [ ] **Step 1: 运行全部验证**

Run: `uv sync --all-groups --locked`

Expected: 依赖同步成功。

Run: `uv lock --check`

Expected: 锁文件有效。

Run: `uv run pytest -q`

Expected: 全部通过。

Run: `git diff --check`

Expected: 无输出。

- [ ] **Step 2: 记录但不执行外部操作**

文档必须列出：

```text
1. 轮换 API 密码并更新 /etc/work-order-process/work-order.env
2. 创建 workorder Linux 用户和最小权限 MySQL 用户
3. chmod 0600 环境文件和备份配置
4. 安装、启用 systemd 与 logrotate
5. 确认基础设施备份后再启用项目备份 timer
6. 修复本机 SSH KexAlgorithms
7. 评估并确认是否重写 Git 历史
8. 部署同一提交并进行数据库只读验收
```

- [ ] **Step 3: 提交最终文档更新**

```powershell
git add docs/production_operations.md
git commit -m "docs: add production hardening rollout checklist"
```
