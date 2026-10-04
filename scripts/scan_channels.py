"""全库五分类扫描 —— 为每个游戏目录标注类别与目标索引站。

输出 `.workbuddy/scan/channel_map.json`（统一清单，不往游戏目录写任何文件）
+ `channel_map.csv`（表格视图）

分类依据（按可靠性）：
  1. 渠道 URL 文件（发行方自己留的，最硬）
  2. 引擎/数据结构特征（RPG Maker 的 Actors.json、Ren'Py 的 game+lib…）
  3. 兜底：引擎不明 -> E 类

用法：
    python scan_channels.py                      # 扫全库
    python scan_channels.py --limit 20           # 先试 20 个
    python scan_channels.py --out xxx.json       # 指定输出
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from channel_classify import classify_channel  # noqa: E402
from atomic_detect import (STRUCTURAL_DIR, RESOURCE_DIR,  # noqa: E402
                           TOOL_DIR, VARIANT_DIR, detect_non_game)

ROOT = Path("G:/Game")
DEFAULT_OUT = ROOT / ".workbuddy" / "scan" / "channel_map.json"
SKIP_TOP = {".workbuddy", "unknow"}

CAT_META = {
    "A": {"name": "厂商 galgame", "index": "VNDB",
          "desc": "传统引擎的商业作品，VNDB 收录最全",
          "engines": "KiriKiri / SiglusEngine / cats-system / Majiro / "
                     "BGI / Artemis / Eushully / NScripter"},
    "B": {"name": "RPG Maker 小游戏", "index": "DLsite",
          "desc": "同人制作，通常在 DLsite 按作品售卖",
          "engines": "RPG Maker MV/MV/nw.js 打包"},
    "C": {"name": "Ren'Py 小游戏", "index": "itch.io",
          "desc": "海外同人，多在 itch.io 免费/付费发布",
          "engines": "Ren'Py（game/ + lib/）"},
    "D": {"name": "Unity 游戏", "index": "Steam",
          "desc": "Unity 制作的独立游戏，Steam 是主要平台",
          "engines": "Unity（*_Data/ + UnityPlayer.dll）"},
    "E": {"name": "查不到", "index": "—",
          "desc": "引擎不明或无渠道痕迹，需要人工判断",
          "engines": "—"},
}


def is_top_dir(name: str) -> bool:
    return bool(STRUCTURAL_DIR.match(name) or RESOURCE_DIR.match(name)
                or RESOURCE_DIR.match(name.lower())
                or VARIANT_DIR.match(name) or TOOL_DIR.match(name))


def scan(base: Path, max_depth: int = 4, limit: int = 0):
    """递归扫描，返回条目列表。"""
    out = []

    def walk(d: Path, lv: int, group: str):
        if lv > max_depth or (limit and len(out) >= limit):
            return
        try:
            kids = sorted(d.iterdir())
        except OSError:
            return
        for c in kids:
            if not c.is_dir() or c.name.startswith("."):
                continue
            cat, eng, why = classify_channel(c)
            rec = {
                "path": str(c).replace("\\", "/"),
                "name": c.name,
                "category": cat,
                "engine": eng,
                "evidence": why,
                "index_site": CAT_META.get(cat, {}).get("index", "—"),
                "index_name": CAT_META.get(cat, {}).get("name", "?"),
                "group": group.replace("\\", "/"),
            }
            if cat == "tool":
                continue
            if cat == "container":
                # 容器本身记一条（保留结构），然后下探
                rec["is_container"] = True
                out.append(rec)
                walk(c, lv + 1, group + "/" + c.name)
                continue
            rec["is_container"] = False
            out.append(rec)
            if cat == "unknown":
                walk(c, lv + 1, group + "/" + c.name)

    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if d.name in SKIP_TOP:
            continue
        walk(d, 0, "G:/Game")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--depth", type=int, default=4)
    a = ap.parse_args()

    bases = [Path(d) for d in a.dirs] or [ROOT]
    allrec = []
    t0 = time.time()
    for b in bases:
        allrec.extend(scan(b, max_depth=a.depth, limit=a.limit))

    # 去重（同路径只留一条，优先非容器）
    seen = {}
    for r in allrec:
        k = r["path"]
        if k not in seen or (seen[k].get("is_container")
                             and not r.get("is_container")):
            seen[k] = r
    allrec = list(seen.values())

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(allrec, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    # CSV
    csvp = out.with_suffix(".csv")
    with csvp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["类别", "本地目录", "本地名", "引擎/渠道", "目标索引站",
                    "判定依据", "是否合集层", "上级目录"])
        for r in sorted(allrec, key=lambda x: (x["category"], x["name"])):
            w.writerow([
                r["category"], r["path"], r["name"],
                r["engine"] or "", r["index_site"], r["evidence"],
                "Y" if r.get("is_container") else "",
                r.get("group", ""),
            ])

    # 汇总
    c = Counter(r["category"] for r in allrec)
    games = [r for r in allrec if not r.get("is_container")]
    cg = Counter(r["category"] for r in games)
    print("=" * 66)
    print("五分类扫描完成")
    print("=" * 66)
    print("总条目 %d（其中合集层 %d，纯游戏 %d）"
          % (len(allrec), len(allrec) - len(games), len(games)))
    print("耗时 %.1f 秒" % (time.time() - t0))
    print()
    print("【按类别分布】")
    for k in "ABCDE":
        n = cg.get(k, 0)
        meta = CAT_META[k]
        print("  %s %-16s %4d 个  → 查 %s" % (k, meta["name"], n, meta["index"]))
    print()
    print("【引擎细分】")
    eng = Counter((r["category"], r["engine"]) for r in games
                  if r["engine"])
    for (k, e), n in sorted(eng.items(), key=lambda x: (x[0][0], -x[1])):
        print("  %s  %-24s %4d" % (k, e, n))
    print()
    print("清单 JSON : %s" % out)
    print("表格 CSV : %s" % csvp)


if __name__ == "__main__":
    main()
