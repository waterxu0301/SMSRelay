# SMS 接力 — IPC 接口说明

> 面向开发者。描述 GUI 主进程与监听核心子进程之间的 stdio JSON Lines 协议。
> 全部命令、字段、错误码均整理自源码：`relaycore/ipc/`（server.py、events.py、handlers_*.py）、`relaycore/poll_loop.py`、`relaycore/dao/state.py`、`desktop/ipc_client.py`、`desktop/supervisor.py`。

## 1. 协议总览

- **传输**：子进程的 **stdin/stdout**，UTF-8 编码的 **JSON Lines**（每行一个 JSON 对象，`\n` 结尾）；子进程 stderr 被 GUI 丢弃（`DEVNULL`）。
- **协议版本**：`proto: 1`。所有请求与响应必须携带 `"proto": 1`，事件同样携带。
- **方向**：
  - GUI → 核心：请求（核心逐行读取 stdin，**串行处理**）；
  - 核心 → GUI：响应（与请求一一配对）+ 事件推送（异步）。
- **进程拉起方式**：
  - 源码态：`<python> -u -m relaycore.main`（cwd 须为 `sms-relay/`）；
  - py2app 打包态：`<SMSRelay.app/Contents/MacOS/CFBundleExecutable> -u --relaycore-child`（复用 bundle 二进制，入口经 `desktop/app.py` 分发）。

## 2. 消息格式

### 2.1 请求（GUI → 核心）

```json
{"proto": 1, "id": "7", "cmd": "get_status", "params": {}}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `proto` | int | 必须为 `1`，否则 `BAD_REQUEST` |
| `id` | any（非 null） | 请求标识；GUI 实现为自增整数的字符串（`"1"`、`"2"`…）。核心原样回显为 `rid` |
| `cmd` | string | 命令名；未注册 → `UNKNOWN_CMD` |
| `params` | object | 可选，缺省视为 `{}` |

### 2.2 响应（核心 → GUI）

成功：

```json
{"proto": 1, "rid": "7", "ok": true, ...命令自有字段}
```

失败：

```json
{"proto": 1, "rid": "7", "ok": false,
 "error": {"code": "VALIDATION", "message": "…", "fields": ["url"]}}
```

`fields` 仅部分错误码携带（字符串数组，标识出错的字段名）。非法 JSON / 缺 `id` 时响应的 `rid` 为 `null`。

**特例**：`test_push` 真实投递（`dry_run=false`）的响应中，投递结果字段 `ok` 会**覆盖外层协议位**——投递失败时响应形如 `{"proto":1,"rid":"7","ok":false,"dry_run":false,"reason":"…"}`，此时 `ok=false` 表示**投递失败**而非协议错误，失败原因在 `reason` 字段且**没有** `error` 对象。消费方以 `"reason" in resp and "error" not in resp` 区分。

### 2.3 事件推送（核心 → GUI，无 rid）

```json
{"proto": 1, "evt": "status", "listening": true, "…": "…"}
```

客户端区分规则：消息含 `rid` 且不含 `evt` → 响应；含 `evt` → 事件。事件与响应共享 stdout，可能交错。

## 3. rid 配对与超时

- GUI 侧 `IpcClient`（`desktop/ipc_client.py`）为每个请求生成自增 id，写入 pending 表；读线程按 `rid` 唤醒等待者，**天然支持乱序响应**；
- 同步 `request()` 超时 **5 秒**，超时返回本地合成的 `{"ok": false, "error": {"code": "TIMEOUT"}}`；另有本地合成 `UNAVAILABLE`（核心进程不可用时 GUI 直接回调，不经网络）；
- 子进程退出（stdout EOF）时，所有挂起请求被唤醒并得到 TIMEOUT/空响应；
- GUI 一切回调经 `root.after(0, fn)` 派发回 Tk 主线程执行。

## 4. 错误码总表

| 错误码 | 来源 | 含义 |
| --- | --- | --- |
| `BAD_REQUEST` | 核心 | 非法 JSON / 非 JSON 对象 / 缺 `id` / `proto != 1` |
| `UNKNOWN_CMD` | 核心 | 命令名未注册 |
| `INTERNAL` | 核心 | 处理器抛出未捕获异常（兜底，不崩溃主循环） |
| `VALIDATION` | 核心 | 业务校验失败，通常携带 `fields` |
| `OUT_OF_WINDOW` | 核心 | `repush` 目标不在内存缓冲（>100 条或已重启） |
| `TARGET_DISABLED` | 核心 | `test_push` 的目标处于停用状态 |
| `TIMEOUT` | GUI 本地 | 请求 5 秒无响应 |
| `UNAVAILABLE` | GUI 本地 | 核心子进程未运行/未就绪 |

## 5. 命令清单（共 22 条）

注册分布：control 5 + config 2 + targets 4 + push 4 + query 7。核心就绪时 `child_ready` 事件的 `capabilities.commands` 返回按字母排序的命令全集，可据此做能力协商。

### 5.1 状态与轮询控制（handlers_control.py，5 条）

#### get_status
- **入参**：无
- **出参**（来自 `PollLoop.status()`）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `listening` | bool | 监听是否运行（change fix-poll-loop-silent-death：语义为「用户意图 running 为真 **且轮询线程真实存活**」；线程若因异常死亡则上报 false，不再以意图标志误报「监听中」） |
| `degraded` | bool | chat.db 缺失降级标志 |
| `interval` | float | 当前轮询间隔（秒） |
| `watermark` | int | 水位线（chat.db ROWID；读取失败为 -1） |
| `processed_count` | int | 本次监听累计处理条数 |
| `buffered_codes` | int | 内存缓冲验证码条数（≤100） |
| `uptime_s` | float | 本次监听运行秒数 |

- **错误**：`INTERNAL`

#### start
- **入参**：无
- **出参**：`listening: true`；同时推送一条 `status` 事件
- **行为**：启动轮询线程（幂等；启动时清除 degraded）。自愈：若 `running` 已为真但轮询线程已死，再次 start 会重建线程
- **错误**：`INTERNAL`

#### stop
- **入参**：无
- **出参**：`listening: false`；同时推送 `status` 事件
- **行为**：停止轮询（幂等；**不会导致子进程退出**）
- **错误**：`INTERNAL`

#### scan_now
- **入参**：无
- **出参**：
  - 扫描进行中：`busy: true`
  - 正常：`busy: false, processed: <int>`（本轮处理的消息条数，含被过滤的），并推送 `scan_result` 事件
- **行为**：立即强制扫描一次（`scan_once(force=True)`，持锁防并发）
- **错误**：`INTERNAL`

#### check_db
- **入参**：无
- **出参**：`readable: <bool>`, `reason: <string>`
- **reason 取值**：`ok`（可读）/ `locked`（可读但被占用，readable=true）/ `authorization`（TCC 拒绝，readable=false）/ `missing`（chat.db 不存在）/ `missing_fda`（其他不可读，按未授权处理）
- **用途**：授权引导页探测；核心启动/重连（child_ready）时由 GUI 主动调用
- **错误**：`INTERNAL`

### 5.2 配置热加载与过滤（handlers_config.py，2 条）

#### reload_config
- **入参**：无
- **出参**：`config: <dict>`（config 表全部键值，如 `interval`、`max_age`、`sender_filter`、`sender_whitelist`、`keyword_filter`、`keyword_whitelist`、`silent_start`、`silent_end` 等）
- **行为**：ConfigRepo 直读 SQLite 无缓存，**保存即热生效**，本命令主要用于读取/刷新；同时推送 `status` 事件
- **错误**：`INTERNAL`

#### save_filters
- **入参**（params 中仅以下白名单键生效，其余静默忽略）：

| 键 | 校验规则 |
| --- | --- |
| `interval` | 数值 ≥ 1（秒） |
| `max_age` | 数值 > 0（秒） |
| `filter_mode` | 无特殊校验，原样存储 |
| `sender_filter` / `keyword_filter` | 须为 `"0"` 或 `"1"` |
| `sender_whitelist` / `keyword_whitelist` | 逗号分隔名单；启用对应过滤时不能为空（含存量） |
| `silent_start` / `silent_end` | `HH:MM` 格式；须成对（一个非空则两个都非空，含存量合并判断） |

- **出参**：`config: <dict>`（保存后的全量配置）
- **错误**：`VALIDATION`（`fields` 可能含 `interval`/`max_age`/`silent_period`/`sender_whitelist`/`keyword_whitelist` 及各键名）
- **行为**：逐键写入 config 表后立即生效

### 5.3 投递目标 CRUD（handlers_targets.py，4 条）

目标序列化统一格式（**回执不含明文 URL/密钥**）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 32 位 hex UUID |
| `name` | string | 名称（唯一） |
| `platform` | string | `feishu` / `dingtalk` / `wecom` |
| `url_masked` | string | 打码地址：前 18 字符 + `****` + 尾 4 字符（长度 ≤12 时整体 `****`） |
| `enabled` | bool | 启用状态 |
| `has_secret` | bool | 是否配置了 Keychain 密钥回指（`secret_account` 非空） |

#### list_targets
- **入参**：无
- **出参**：`targets: [<上述序列化格式>]`（按创建时间升序，含停用目标）
- **错误**：`INTERNAL`

#### save_target
- **入参**：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 否 | **缺省 = 新增**；携带 = 编辑（name/url/secret_hint） |
| `name` | 是 | 非空；唯一（重名 → `VALIDATION`） |
| `platform` | 新增必填 | `feishu`/`dingtalk`/`wecom`（编辑不可改） |
| `url` | 是 | 完整明文 webhook（http/https + host）；编辑修改须重传完整地址 |
| `enabled` | 否 | 默认 true；仅新增时生效（编辑走 set_target_enabled） |
| `secret_hint` | 否 | 密钥尾 4 位掩码提示。**明文 secret 永不经 IPC**：GUI 直写 Keychain 后仅回传 hint |

- **出参**：`target: <序列化目标>`
- **错误**：`VALIDATION`（`fields`：`name`/`platform`/`url`；重名时 `fields=["name"]`）
- **副作用**：推送 `status` 事件（目标变更同步状态栏）；轮询每轮直读 DB，新目标下一轮即生效

#### delete_target
- **入参**：`id`
- **出参**：`deleted: <id>`
- **行为**：删除目标并**级联删除 `secret_refs` 引用行**；Keychain 条目由 GUI 侧另行删除（`security delete-generic-password`）
- **错误**：`VALIDATION`（缺 id / 目标不存在，`fields=["id"]`）

#### set_target_enabled
- **入参**：`id`, `enabled: <bool>`
- **出参**：`target: <序列化目标>`
- **错误**：`VALIDATION`（缺 id / 不存在）
- **副作用**：推送 `status` 事件

### 5.4 邮件配置与测试推送（handlers_push.py，4 条）

#### get_email
- **入参**：无
- **出参**：`email: {enabled: bool, smtp_host, smtp_port, security, sender, recipients: [..]}`；无配置行时仅 `{enabled: false}`。**授权码永远不出现在 IPC**
- **错误**：`INTERNAL`

#### save_email
- **入参**：

| 字段 | 校验 |
| --- | --- |
| `smtp_host` | 字符串；`enabled=true` 时必填 |
| `smtp_port` | 整数，缺省 465 |
| `security` | `ssl` / `starttls` / `none`（存库时 `none` → NULL） |
| `sender` | `enabled=true` 时必填 |
| `recipients` | 数组或逗号分隔字符串；`enabled=true` 时非空 |
| `enabled` | bool，缺省 false |

- **出参**：`email: <同 get_email>`
- **错误**：`VALIDATION`（`fields`：`smtp_host`/`smtp_port`/`security`/`sender`/`recipients`）
- **授权码边界**：不入参；GUI 直写 Keychain `email.authcode`

#### set_email_enabled
- **入参**：`enabled: <bool>`
- **出参**：`email: <同 get_email>`
- **行为**：启用前校验已配置 SMTP（host + recipients），否则 `VALIDATION`
- **错误**：`VALIDATION`（`fields=["smtp_host"]`）

#### test_push
- **入参**：`target_id`（必填）, `dry_run`（bool，缺省 true）
- **dry_run=true（零外发）出参**：`dry_run: true, platform: <string>, url_masked: <string>, content: <string>`（渲染预览，占位码 `******`，结构为「程序标识 + 示例原文」直连的单行；示例原文中的验证码同样掩码）

  示例 content（标头 + 原文直连的单行结构）：

  ```
  【SMS接力】（示例）由水滴发送，登陆验证码：******。时间: 2026-10-29 16:30:05
  ```
- **dry_run=false（真实投递占位码 000000）出参**：`dry_run: false, ok: <bool 投递结果>, reason: <失败原因|null>`（注意 2.2 节特例：`ok` 覆盖协议位，无 `error` 对象；真实测试投递不携带短信原文，故为「【SMS接力】验证码 000000」单行格式）
- **错误**：`VALIDATION`（目标不存在 `fields=["target_id"]`；未知平台）、`TARGET_DISABLED`
- **行为**：真实测试**不计入统计、不触发告警**；写一条 `TEST_PUSH` 运行日志

### 5.5 重推与统计日志（handlers_query.py，7 条）

#### repush
- **入参**：`rowid`（int，必填；非整数 → `VALIDATION`）
- **出参**：`results: {<target_id>: {ok: bool, reason: string|null}}`（向当前全部启用目标重投）
- **行为**：从内存环形缓冲（最近 100 条）取码重投；写 `REPUSH` 运行日志
- **错误**：`VALIDATION`、`OUT_OF_WINDOW`（缓冲无此消息：超 100 条或子进程重启过）

#### get_stats
- **入参**：`days`（int，缺省 7；**夹取到 1..7**）
- **出参**：`stats: [{target_id, total: int, ok: int, rate: float(4位小数), degraded: bool(rate<0.995)}]`（基于 `delivery_log` 按目标聚合）
- **错误**：`INTERNAL`

#### get_logs
- **入参**：`level`（可选：`DEBUG`/`INFO`/`WARN`/`ERROR`，其他值 → `VALIDATION`）, `days`（缺省 7，夹取 1..7）
- **出参**：`logs: [{id, level, code, message, detail, created_at(unix秒)}]`（**按 id 降序**，新→旧；GUI 取前 200 条后反转显示）
- **错误**：`VALIDATION`（`fields=["level"]`）

#### clear_logs
- **入参**：`confirmed: true`（**必须显式为 true**，否则 `VALIDATION`）
- **出参**：`cleared: true`
- **行为**：清空 `delivery_log`/`run_log`/`alert_log` 三表全部数据（二次确认由 GUI 前置完成）

#### export_logs
- **入参**：无
- **出参**：`lines: [<string>]`（近 7 天 run_log 逐行文本，**时间正序**，格式 `YYYY-MM-DD HH:MM:SS [LEVEL] code message`，已脱敏）
- **错误**：`INTERNAL`

#### set_autostart
- **入参**：`enabled: <bool>`
- **出参**：`ok: true, autostart: <bool>`
- **行为**：**核心侧仅记运行日志留痕**（`AUTOSTART`），真正的 SMAppService 注册/注销在 GUI 进程内执行
- **错误**：`INTERNAL`

#### list_buffered
- **入参**：无
- **出参**：`buffered: [{rowid: int, sender_label: string, code: string, ts: unix秒}]`（内存缓冲全部条目；`code` 为 6 位验证码明文，**仅经 IPC 内存展示，不落日志/文件**）
- **错误**：`INTERNAL`

## 6. 事件推送

核心启动即发送 `child_ready`，此后各类事件异步推送（线程安全，锁保护 stdout；stdout 写失败即 GUI 退出 → 静默停发，核心继续运行）：

| 事件 | 载荷字段 | 触发时机 |
| --- | --- | --- |
| `child_ready` | `capabilities: {commands: [<22 条命令名，字母序>]}` | 子进程启动、`serve_forever` 入口（每次重连成功都会重发） |
| `status` | 同 `get_status` 出参 | start/stop、配置/目标变更、扫描推进水位线、降级等状态变化 |
| `log` | `level, code, ts(unix秒), message` | 运行日志（轮询跳过、水位线失败、测试推送等） |
| `scan_result` | `processed: int` | `scan_now` 完成时 |
| `alert` | `type: "alert", time: "YYYY-MM-DD HH:MM:SS", summary: <脱敏摘要>, targets: [<目标名>]` | 全目标投递失败告警（10 分钟按 event_key 去重） |

**协议预留**：`relaycore/ipc/events.py` 头注还声明了 `delivery` 与 `error` 事件类型（`EventEmitter.delivery()` 便捷方法已定义），但当前核心代码**并不发射**这两类事件——投递结果落 `delivery_log`/`stats_daily` 后经 `get_stats`/`get_logs` 查询。对接方不应依赖它们。

示例（真实报文形态）：

```json
{"proto": 1, "evt": "child_ready", "capabilities": {"commands": ["check_db", "clear_logs", ...]}}
{"proto": 1, "evt": "log", "level": "INFO", "code": "CORE_START", "ts": 1790000000, "message": "监听核心已启动"}
{"proto": 1, "evt": "alert", "type": "alert", "time": "2026-10-29 10:00:00", "summary": "投递失败告警：xx — 网络异常", "targets": ["xx"]}
```

## 7. EOF 与重连策略（desktop/supervisor.py）

- 子进程侧：stdin EOF（GUI 退出）→ 停止轮询与清理线程、关闭 DB 连接后退出（exit 0）；stdout 写失败（BrokenPipe）→ 标记 broken、静默停发并退出服务循环。
- GUI 侧守护（Supervisor）：
  1. 子进程异常退出（非 GUI 主动 shutdown）→ 立即置状态「核心重建中」；
  2. **退避序列 1s → 2s → 4s，自动重启最多 3 次**（第 n 次退出按 `BACKOFF_SEQUENCE[min(n-1, 2)]` 延迟重拉）；
  3. 第 4 次退出 → 状态「监听不可用」，停止自动重启，等待用户点击状态栏「手动重试」（`manual_retry` 重置计数后重新拉起）；
  4. 收到 `child_ready` → 重启计数清零、状态恢复「核心运行中」（发生过崩溃会提示重连成功）；
  5. GUI 正常退出流程：`request("stop", timeout=2s)` → `terminate()` → `wait(3s)` 超时则 `kill()`。

## 8. 安全与脱敏边界

- **密钥不经 IPC**：钉钉加签 secret 与 SMTP 授权码仅由 GUI 直写 Keychain（service `com.zero-reconn.sms-relay`，账户 `target.<uuid>` / `email.authcode`）；IPC 只传尾 4 位 `secret_hint`；核心子进程对 Keychain **只读**。
- **URL 打码**：所有回执中的 webhook 地址均为打码形式（前 18 + `****` + 尾 4）；修改需重传完整明文。
- **验证码边界**：验证码明文仅出现在 `list_buffered` 回执（内存展示）；日志入库与导出文本经 `redact()` 脱敏（URL token 段、query 参数 token、6 位数字码）。
- **通知边界**：系统通知正文经 `redact_text()` 处理，不含 6 位验证码。
