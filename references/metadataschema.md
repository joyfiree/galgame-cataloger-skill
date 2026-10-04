# `library.json` 统一清单格式

**一个文件记录全部条目**，不往游戏目录里写任何东西。
由 `build_manifest.py` 并发生成，是「平铺准入」的判定依据。

## 三个文件
```
library.json    权威清单（数组，每项一个游戏）
library.csv     表格视图，供 Excel / 记事本
library.jsonl   断点续跑用，按条 append，可随时中断重跑
```

## Schema

```json
{
  "schema": "library/1",
  "local_name": "Kanon PC版 家属～母と姉妹の嬌声～ (KZK@BISHOP)",
  "local_dir": "G:/Game/合集提取/Kanon PC版 家属～母と姉妹の嬌声～ (KZK@BISHOP)",
  "engine": "BGI",
  "local_titles": ["家属～母と姉妹の嬌声～"],
  "remote": {
    "found": true,
    "vndb_id": "v26206",
    "remote_title": "Kazoku ~Haha to Shimai no Kyousei~",
    "remote_alttitle": "家属～母と姉妹の嬌声～",
    "released": "2019-09-27",
    "vendor": "BISHOP",
    "url": "https://vndb.org/v26206"
  },
  "confidence": "A",
  "reason": "CJK 汉字重叠 100% + LCS 1.00（双证据）",
  "candidates": [
    {
      "id": "v26206",
      "title": "Kazoku ~Haha to Shimai no Kyousei~",
      "alttitle": "家属～母と姉妹の嬌声～",
      "released": "2019-09-27",
      "vendor": "BISHOP",
      "url": "https://vndb.org/v26206",
      "similarity": 1.0,
      "cjk_overlap": 1.0
    }
  ],
  "naming_source": "remote",
  "flatten_allowed": true,
  "detected_by": "data.xp3",
  "updated": "2026-10-03"
}
```

## 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | str | 格式版本号，未来不兼容时递增 |
| `local_name` | str | 目录名（清洗后的） |
| `local_dir` | str | 绝对路径，便于回滚定位 |
| `engine` | str | 引擎指纹识别结果 |
| `local_titles` | list | **从本地文件提取的真名**，远端查询的种子 |
| `remote.found` | bool | 远端是否有对应条目 |
| `remote.vndb_id` | str | VNDB ID（`v` + 数字），可直接拼 URL |
| `remote.remote_title` | str | VNDB 官方标题（日文原名居多） |
| `remote.remote_alttitle` | str | 别名，常是中文译名或带副标题的完整名 |
| `remote.released` | str | `YYYY-MM-DD` |
| `remote.vendor` | str | 厂商/社团名 |
| `confidence` | str | `A`/`B`/`C`/`D`/`E` |
| `reason` | str | 判定理由，必须能追溯到具体证据 |
| `candidates` | list | 前 5 个候选，含各自的 `similarity` / `cjk_overlap` |
| 命名来源 | — | 不设字段。`local_name` 与 `remote.title` 都保留，按需选用 |
| `flatten_allowed` | bool | **由 confidence 推导**，A/B 为 true |
| `detected_by` | str | 靠什么特征认出这是游戏目录 |
| `updated` | str | 最后更新日期 |

## 命名来源的选择

两个名字都存在清单里，**切换不需要重查**：

| 想用 | 取哪个字段 | 适合 |
|---|---|---|
| 远端官方名 | `remote.title` | 准确检索、避免生僻字 |
| 本地清洗名 | `local_name` | 中文友好、日常浏览 |
| 两者都保留 | `中文名 (Remote Title)` | 折中 |

⚠️ **不要把清单复制进游戏目录**。那会造成证据污染：
下一轮 `probe_local` 会把 `*.json` 捞进候选文本文件，读到上一轮
写进去的远端标题，当成本地真名再去查远端，形成自我强化的错误结论。
实测后果：`AngelTear` 被污染成 `Neo Angelique`（光荣），
而正确答案是 `Nerawareta Megami Tenshi Angeltear`（SYRUP, v11487）。

## 置信度与准入

```
A  LCS>=0.75 或 (CJK>=0.8 且 LCS>=0.35)   → flatten_allowed = true
B  相似度 0.35~0.55 且候选唯一             → flatten_allowed = true
C  有结果但证据不足                        → false
D  多候选 / 触发负向检查                   → false
E  远端 0 结果                             → false
```

**E 级不是失败。** 它意味着「这是个查不到远端条目的作品」——
应该保留在 `待甄别/` 或来源分组目录里，而不是平铺出去。

## 用途

- 一次性查看全库置信度分布
- 找出所有 `flatten_allowed: true` 的条目，生成平铺清单
- 找出 D/E 级条目，生成待查工作清单
- 远端条目日后出现时，直接重跑即可补全

## 与 `_来源信息.txt` 的关系

| | `library.json` | `_来源信息.txt` |
|---|---|---|
| 位置 | **库根的集中清单** | 游戏目录内 |
| 结构 | JSON，可被工具读 | 自由文本 |
| 远端信息 | ✅ 完整 | ❌ 无 |
| 双名切换 | ✅ | ❌ |
| 准入判定 | ✅ `flatten_allowed` | ❌ |
| 改名/移动后 | 路径需更新 | 文件与主体可能分离 |
| 何时用 | **默认** | **兜底**：远端查不到、条目写不出来时 |

远端可用时**不要**再写 `_来源信息.txt` —— 那是元数据入库前的临时形态。
