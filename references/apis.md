# 远端 API 参考

## VNDB Kana API（首选）

基址 `https://api.vndb.org/kana`　·　**免认证**

### 端点

| 端点 | 用途 |
|---|---|
| `POST /vn` | 视觉小说（作品） |
| `POST /release` | 发行版本 |
| `POST /producer` | 制作方（社团/厂商） |
| `POST /character` | 角色 |
| `POST /staff` | 工作人员 |
| `POST /tag` | 标签 |
| `GET /schema` | 枚举值、可用字段、外部站点列表 |
| `GET /stats` | 数据库统计 |
| `GET /authinfo` | 验证 Token（Token 格式 `xxx-xxx-xxx`） |

### 限流

| 项 | 限制 |
|---|---|
| 请求数 | **200 次 / 5 分钟**（≈1.5s 一次） |
| 执行时间 | 每分钟最多 1 秒 |
| 单次请求 | 超 3 秒中止 |
| 超限响应 | `429 Throttled` |

建议节流间隔 **1.8 秒**，留余量。

### 分页与字段

- `results` 默认 10，**最多 100**；`page` 从 1 开始；`more: true` 表示还有
- `count: true` 可只取匹配总数（但计数开销大，非必要不用）
- **`fields` 必须显式列出**，不支持通配符；顶层 `id` 总是默认返回
- 嵌套对象**必须带子字段**，点号与大括号等价：`image.url` ≡ `image{url}`；
  只写父对象名（`titles` / `developers` / `image`）会 HTTP 400

### 常用请求

```bash
# 基础搜索
curl -X POST 'https://api.vndb.org/kana/vn' \
  -H 'Content-Type: application/json' \
  --data '{
    "filters": ["search","=","关键字"],
    "fields": "id,title,alttitle,titles{title,lang,main},aliases,olang,released,developers{id,name,original}",
    "results": 10
  }'
```

```bash
# 排除 18+ 结果（只想要全年龄条目时）
--data '{"filters":["and",["search","=","x"],["lang","!=","zh-hk"]],
        "fields":"id,title,olang,released","results":20}'
```

### 关键字段（`/vn`）

```
id                形如 v17
title             主标题（原文语言）
alttitle          别名
titles            所有标题（含语言标记）
aliases           罗马字等别名
olang             原始语言
released          发售日 "YYYY-MM-DD"
length_minutes    时长（分钟）
platforms         ["win","linux",...]
developers        制作方数组
image.url         封面图 URL
image.sexual      封面 NSFW 标记
tags              标签（需配合 spoiler 等级）
extlinks          外部链接
```

### 关键筛选器

```
["search", "=", "关键字"]           搜索标题/别名/发行版标题
["id", "=", "v17"]
["lang", "=", "zh-hans"]            语言
["platform", "=", "win"]
["released", ">=", "2016-01-01"]
["developer", "=", "p999"]           按制作方
["tag", "=", ["g505", 2, 1.2]]      标签 ID + spoiler 等级 + 最低评分
["and", 条件1, 条件2]
["or", 条件1, 条件2]
```

### 标签 spoiler 等级

| 等级 | 含义 |
|---|---|
| -1 | 全部隐藏（默认不含） |
| 0 | 全部显示 |
| 1 | 排除 Explicit |
| 2 | 只显示 Explicit |

批量取标签时用 `["tag","=",["g####",2,1.0]]` 拿全量。

---

## Bangumi API

基址 `https://api.bgm.tv/v0`　·　**搜索免认证**

### 端点

```
POST /v0/search/subjects      搜索条目
GET  /v0/subjects/{id}        条目详情
GET  /v0/subjects/{id}/image
GET  /v0/subjects/{id}/persons
POST /v0/subjects/{id}/collect  （需认证）
```

### User-Agent（强制）

```
{开发者ID}/{应用名}/{版本} [{平台}] [{项目URL}]
例：mydev/GalTool/1.0 (https://example.com)
```
泛用 UA（`database`、`Bangumi/1.0`、`curl`）**会被封禁**。

### R18 限制

- 成人条目需 `Authorization: Bearer <token>`
- Token 申请：`https://next.bgm.tv/demo/access-token`
- **账号需注册满 60 天**才有 R18 权限
- 未授权请求 R18 条目直接 **404**（不是 403）

```bash
curl -X POST 'https://api.bgm.tv/v0/search/subjects' \
  -H 'User-Agent: mydev/GalTool/1.0' \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"关键字","filter":{"nsfw":true}}'
```

### 缓存

公开条目 `Cache-Control: max-age=3600`，可自行缓存。

---

## DLsite

- **需日区 IP**，大量条目地域锁定
- 番号格式 `RJ01026809` / `VJ012345` —— 目录名或 readme 里常带
- 拿到番号可直接 `https://www.dlsite.com/maniax/work/=/product_id/RJ01026809.html`

---

## Getchu

- 番号形如 `2026050301`（`[080620]` 这种是 YYMMDD）
- `http://www.getchu.com/pc/2026050301/` 格式

---

## Hikarinagi / YMGal / Kungal / Shionlib

ReinaManager 已集成的数据源，均有公开 API 或 RSS：
- Hikarinagi：日文 galgame 站
- YMGal：中文 galgame 站，有 API
- Kungal：中文 galgame 库
- Shionlib：视觉小说归档

---

## 查询策略

### 多查询串提高命中率

同一个目标用多种写法搜，取并集去重：

```python
def variants(name, exe=None):
    out = [name]
    out.append(re.sub(r'[（(\[【].*?[)）\]】]', '', name).strip())
    out.append(re.sub(r'[\s_\-]*(v|ver)\d+(\.\d+)*$', '', name, flags=re.I))
    if exe:
        out.append(exe)
        out.append(re.sub(r'[\s_\-]*(32|64)$', '', exe, flags=re.I))
    out.append(re.sub(r'[^A-Za-z0-9]', '', name))   # 去符号
    return [q for q in dict.fromkeys(out) if q]
```

### 缓存

以「归一化名 + 引擎」为 key 缓存 API 响应，避免重复查询：

```python
import hashlib, json, pathlib
CACHE = pathlib.Path.home() / '.cache' / 'gal-lookup'
CACHE.mkdir(parents=True, exist_ok=True)

def cached(key, fn, ttl_days=30):
    f = CACHE / (hashlib.md5(key.encode()).hexdigest() + '.json')
    if f.exists() and (time.time() - f.stat().st_mtime) < ttl_days * 86400:
        return json.loads(f.read_text(encoding='utf-8'))
    data = fn()
    f.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return data
```

### 批量规模估算

500 个游戏 × 平均 3.5 次查询 × 1.8s ≈ **53 分钟**。
务必后台运行 + 缓存 + 断点续传。
