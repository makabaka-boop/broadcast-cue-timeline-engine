# 直播播控提示点排期服务（cue-control）

纯后端服务。彩排后某些提示点（cue point）需要推迟播出时，本服务接收一组
带最早释放时刻（`release`）、可选死线（`latest`）的提示点和形如
「后者时刻 ≥ 前者时刻 + min_gap」的关系，计算**同时满足全部下界的逐点最早
时刻**：

- 分支重新汇合时自动取所有前驱给出的最晚时刻；
- 结果只与规则有关，与点、关系的录入顺序无关；
- 含总间隔为正的有向环时，结论是 `positive_cycle`，不会无限推演；
- 无正权环但有提示点无法满足 `latest` 时，结论是 `deadline_exceeded`。

非法模板不会落库；失败的推演不产生任何记录，也不损伤既有数据；成功结果按
提示点 ID 升序返回，保存在挂载卷的 SQLite 中，容器重启后仍可查询。

## 技术栈

- Python 3.11 + FastAPI + Uvicorn
- SQLite（WAL 模式，位于挂载卷 `/data/cue_control.db`）
- Docker Compose（仅绑定本机回环地址 `127.0.0.1:8000`）
- pytest 锁定分支汇合、乱序、延误传播、零间隔环、正权环等行为

## 快速开始

```bash
docker compose up --build -d
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

数据保存在 Docker 命名卷 `cue-data` 中，重启容器后模板与成功结果仍在。

本地无 Docker 时可直接运行：

```bash
pip install -r requirements.txt
CUE_DB_PATH=./data/cue_control.db uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 数据模型（普通 JSON）

模板：

```json
{
  "points": [
    {"id": "A", "release": 0},
    {"id": "B", "release": 100, "latest": 1000},
    {"id": 7, "release": 5}
  ],
  "relations": [
    {"from": "A", "to": "B", "min_gap": 30},
    {"from": 7, "to": "A", "min_gap": 0}
  ]
}
```

约束（服务端严格校验，违反任一条返回 `invalid_template`，且不落库）：

| 项 | 规则 |
| --- | --- |
| `points` | 必填，1 ~ 300 个；`id` 唯一，非空字符串或整数（长度 ≤ 128），数字 `1` 与字符串 `"1"` 视为同一个 ID |
| `release` | 每点必填，整数，0 ≤ release ≤ 10⁹ |
| `latest` | 可选，整数，且 latest ≥ release |
| `relations` | 可选，至多 3000 条 |
| `from` / `to` | 必须引用已存在的提示点 ID |
| `min_gap` | 整数，0 ≤ min_gap ≤ 10⁶，含义：`time[to] ≥ time[from] + min_gap` |
| 其它字段 | 一律拒绝（点/关系/顶层均如此） |

推演请求体（可整体省略）：

```json
{ "delays": { "A": 50, "B": 120 } }
```

- `delays` 的 key 为提示点 ID（JSON 对象键恒为字符串，数字 ID 用其十进制
  规范写法，例如 `"7"`）；
- 只允许覆盖部分提示点；每个 delay 必须是不小于该点原 `release` 的整数，
  否则返回 `invalid_delay`。

## 算法说明

对每个点 `i` 求最小可行时刻 `t[i]`，满足

```
t[i] ≥ release[i]（或 delay 覆盖值）
t[to] ≥ t[from] + min_gap
```

这是以隐含源点（到每个点有一条权为 release 的边）为起点的**最长路**问题。
实现采用最长路形式的 Bellman-Ford 松弛闭包（`app/engine.py`）：

1. `t[i]` 初始化为各点 release/delay；
2. 反复扫描全部关系做松弛；一轮无变化即收敛到最早时刻；
3. 若第 N 轮（N = 点数）仍能松弛，说明存在**总权为正的有向环**，并沿前驱
   链还原环上的点，返回 `positive_cycle`；
4. 收敛后再检查 `latest`，任一点最早时刻超过死线则返回 `deadline_exceeded`。

全程只对不等式做松弛，**不枚举任何候选时间**。Python 整数无溢出，最大可能
值约为 10⁹ + 3000·10⁶。零间隔环（含自环、所有边权为 0 的环）合法：环上各点
取其中最大的下界。

## 接口约定

所有错误响应使用同一结构与**稳定代码**（不会随实现细节漂移）：

```json
{ "error": { "code": "positive_cycle", "detail": "…", "...": "附加字段" } }
```

| HTTP | code | 场景 |
| --- | --- | --- |
| 400 | `invalid_template` | 模板不合法（结构、范围、未知端点、重复 ID 等） |
| 400 | `invalid_delay` | delay 覆盖非法（未知点、小于 release、非整数等） |
| 400 | `invalid_json` | 请求体不是合法 JSON |
| 404 | `not_found` | 模板或推演结果不存在 |
| 405 | `method_not_allowed` | HTTP 方法不允许 |
| 422 | `positive_cycle` | 存在总间隔为正的有向环，任何排期都不可满足（附 `cycle`） |
| 422 | `deadline_exceeded` | 无正权环但最早时刻超过 `latest`（附 `violations`） |
| 500 | `internal_error` | 未预期的服务器错误 |

### 1) 登记模板

`POST /templates` → `201 Created`

```bash
curl -s -X POST http://127.0.0.1:8000/templates \
  -H 'content-type: application/json' \
  -d '{
    "points": [
      {"id": "A", "release": 0},
      {"id": "B", "release": 0},
      {"id": "C", "release": 0},
      {"id": "M", "release": 0, "latest": 50}
    ],
    "relations": [
      {"from": "A", "to": "B", "min_gap": 10},
      {"from": "A", "to": "C", "min_gap": 5},
      {"from": "B", "to": "M", "min_gap": 0},
      {"from": "C", "to": "M", "min_gap": 0}
    ]
  }'
```

```json
{ "id": "f3c1…", "template": { "...回显的合法模板..." } }
```

### 2) 读取模板

`GET /templates/{template_id}` → `200`，不存在返回 404 `not_found`。

### 3) 延误推演

`POST /templates/{template_id}/inferences` → `201 Created`

```bash
curl -s -X POST http://127.0.0.1:8000/templates/f3c1…/inferences \
  -H 'content-type: application/json' \
  -d '{"delays": {"B": 20}}'
```

成功（`results` 按 ID 升序：数字 ID 按数值在前，字符串 ID 按字典序在后）：

```json
{
  "id": "1743c958…",
  "template_id": "f3c1…",
  "delays": {"B": 20},
  "status": "ok",
  "results": [
    {"id": "A", "time": 0},
    {"id": "B", "time": 20},
    {"id": "C", "time": 5},
    {"id": "M", "time": 20}
  ]
}
```

注意分支汇合：B 被推迟到 20 后，`M = max(B+0, C+0) = max(20, 5) = 20`，
与关系录入顺序无关。

不可实现示例（`B` 推迟到 100，导致 `M` 超过 `latest=50`）：

```bash
curl -i -X POST http://127.0.0.1:8000/templates/f3c1…/inferences \
  -H 'content-type: application/json' \
  -d '{"delays": {"B": 100}}'
# HTTP/1.1 422 Unprocessable Entity
# {"error":{"code":"deadline_exceeded",
#   "detail":"one or more cue points cannot start at or before 'latest'",
#   "violations":[{"id":"M","earliest":100,"latest":50}]}}
```

正权环示例（`x → y → x` 总间隔 2 > 0）：

```bash
curl -i -X POST http://127.0.0.1:8000/templates/<环模板>/inferences \
  -H 'content-type: application/json' -d '{}'
# HTTP/1.1 422
# {"error":{"code":"positive_cycle",
#   "detail":"the rules contain a directed cycle with strictly positive total min_gap; no schedule can satisfy it",
#   "cycle":["x","y"]}}
```

零间隔环合法：环上各点取最大下界。

### 4) 查询推演结果

`GET /inferences/{inference_id}` → `200`（返回与推演成功时相同的结果，
容器重启后仍可取回）；不存在返回 404 `not_found`。

### 健康检查

`GET /health` → `{"status":"ok"}`

## 持久化与隔离保证

- 只有通过完整校验的模板才会 `INSERT`；校验失败不触碰数据库。
- 推演先在内存中计算，**成功后才开启事务写入**；返回
  `positive_cycle` / `deadline_exceeded` / `invalid_delay` 时数据库完全
  不变（测试直接核对记录数与既有记录）。
- 所有写操作经进程内串行锁 + SQLite 立即事务完成，无半写状态。
- 数据库文件位于 Compose 命名卷（容器内 `/data`），重建容器不丢数据。

## 测试

```bash
pip install -r requirements-dev.txt
pytest
```

覆盖点（`tests/`）：

- 分支重新汇合取最晚前驱（含前驱自身被 delay 覆盖的情形）；
- 打乱点与关系顺序，20 个随机排列得到同一张表；接口层再验一次；
- 延误沿整链/多路径传播；
- 零间隔环（环 + 零权自环）在 release 处收敛；
- 正权环（正权自环、嵌入的 2-环）返回 `positive_cycle`，且正权环检测
  优先于死线检查，算法必然终止；
- 无环但超死线返回 `deadline_exceeded`，恰好等于 latest 视为合法；
- 非法模板不落库、失败推演无记录且不损伤既有数据；
- 结果按 ID 升序；重启应用（同一份 SQLite 文件）后模板与结果均可取回。

## 目录结构

```
app/
  engine.py       # 最早时刻求解（最长路松弛 + 正权环/死线判定）
  validation.py   # 模板与 delay 覆盖的严格校验
  scheduling.py   # 编排：校验后的模板 -> 最早时刻结果/稳定错误
  db.py           # SQLite 访问（模板表、推演结果表）
  errors.py       # 稳定错误码与异常处理
  main.py         # FastAPI 路由
tests/            # pytest
Dockerfile
docker-compose.yml
requirements.txt / requirements-dev.txt
```
