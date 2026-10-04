#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""顶层平铺分诊 —— 决定每个候选条目「怎么平铺」。

只读，不移动任何文件。产出可复核的分诊计划（plan），
供 flatten 执行器消费（执行器需另行实现，且默认 dry-run + manifest）。

形态（shape）与动作（action）：

| shape        | 判据                                   | action               |
|--------------|----------------------------------------|----------------------|
| DUPLICATE    | 目录名匹配 `xxx [副本N]`               | quarantine → 待删除/ |
| GAME         | 自身是原子游戏（classify_one == game） | flatten              |
| STAGING      | 下载暂存目录（目录名与内容无关）       | split（按内容重新归位）|
| CONTAINER    | ≥2 个游戏子目录                        | expand（子游戏各自平铺）|
| VARIANT_WRAP | 子目录只有 位/语言/版本 变体           | flatten（整目录当一条）|
| SHELL        | 只有 1 个游戏子目录                    | flatten（整目录当一条）|
| ARCHIVE      | 只有压缩包/镜像，无 exe                | keep（不动，列清单）  |
| TOOL         | 工具 / 残骸 / 无主程序                 | exclude（列清单）     |
| UNKNOWN      | 判不出                                 | review（人工）        |

关键决策（2026-10-04 与用户确认）：
  1. 合集（厂商/系列/引擎）**全部拆平** —— 子游戏各自提到顶层。
  2. 空壳 / 变体包装 **整目录当一个游戏条目，内部结构原样不动**。
  3. `[副本N]` 重复目录 → 移到 `待删除/`（不直接删）。
  4. 未解压压缩包 **保持不动**，只列清单。

用法：
  python flatten_triage.py "G:/Game"                    # 打印摘要
  python flatten_triage.py "G:/Game" --csv plan.csv     # 导出表格
  python flatten_triage.py "G:/Game" --json plan.json   # 导出机器可读计划
  python flatten_triage.py "G:/Game" --shape CONTAINER  # 只看某一形态
"""
import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_detect as ad  # noqa: E402

# 附属子目录：补丁 / 备份 / 存档 / 攻略 / 系统 —— 不是独立游戏，随本体走
ANCILLARY = re.compile(
    r"(?i)^("
    r"save|savedata|存档|存档备份|全cg|全cg存档|皆传|"
    r"readme|read me|说明|说明书|使用说明|必看|攻略|"
    r"patch|补丁|汉化补丁|破解|crack|备份|backup|原版|"
    r"optional|bonus|特典|ost|sound ?track|音声|amsr|广播剧|"
    r"directx|vcredist|redist|dxsetup|"
    r"system|設定|设置|config|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-.*"
    r")[\s\d._\-（）()]*$")

COPY_RE = re.compile(r"^(.*?)\s*\[副本\d*\]\s*$")
# 严格版变体名（位版本 / 语言版本 / 版次）。
# ⚠️ 不能直接用 atomic_detect.VARIANT_DIR —— 它含 release/build/project/dist，
# 会把工具的 `Release/`（MisakaTranslator）误当变体。
STRICT_VARIANT = re.compile(
    r"(?i)^(32位|64位|32bit|64bit|x86|x64|"
    r"简体字?版|繁体字?版|繁體版|中文版|日文版|日本語版|英文版|english版|"
    r"旧版|新版|重置版|重制版|汉化版|步兵版|无码版|有码版|官中版|完全版|"
    r"dl版|パッケージ版|体験版|体验版|demo|trial)$")
# 明确是「下载暂存」的强信号（与 SKILL.md 一致，不用泛化词）
DOWNLOAD_MARK = ad.DOWNLOAD_MARK
DOWNLOAD_SITE = ad.DOWNLOAD_SITE

# 游戏子目录判定时要排除的（引擎运行时 / 资源 / 结构性 / 附属 / 变体）
SKIP_AS_GAME = re.compile(r"(?i)^(game|lib|renpy|data|www|locales|swiftshader|"
                          r"mono|monobleedingedge|python|etc|managed|plugins|"
                          r"resources|streamingassets|bgm|se|voice|image|"
                          r"scenario|movie|audio|fonts|icon|img|js|movies|save|"
                          r"savedata|plugin|anim|document|manual)$")


def is_archive(name: str) -> bool:
    l = name.lower()
    return (l.endswith(tuple(ad.ARCHIVE_EXT)) or
            bool(re.search(r"\.(part|7z\.)\d+", l)) or
            l.endswith(".tar"))


def _has_game_in(dirs):
    """这些目录里至少有一个装着游戏（有 exe，或 classify_one 判为 game）。"""
    for v in dirs:
        try:
            if any(x.is_file() and x.suffix.lower() == ".exe"
                   for x in v.iterdir()):
                return True
            if ad.classify_one(v).level() in ("game", "container"):
                return True
        except (OSError, RecursionError):
            continue
    return False


def game_children(path: Path, dirs):
    """找出「会成为游戏子条目」的子目录（排除附属/引擎运行时/变体）。"""
    out = []
    for d in dirs:
        n = d.name.strip()
        if (n.lower() in ("",) or SKIP_AS_GAME.match(n) or ANCILLARY.match(n)
                or ad.STRUCTURAL_DIR.match(n) or ad.RESOURCE_DIR.match(n)
                or ad.RESOURCE_DIR.match(n.lower())
                or ad.VARIANT_DIR.match(n)):
            continue
        try:
            lv = ad.classify_one(d).level()
        except RecursionError:
            continue
        if lv in ("game", "container"):
            out.append(d)
            continue
        # 变体包装的游戏（如 Heiwachan 的 `4P Play 32bit 64bit/` 里
        # 只有 `32位/`+`64位/`）—— classify_one 判不出来，但它是子游戏。
        try:
            subs = [x for x in d.iterdir() if x.is_dir()]
        except OSError:
            continue
        variants = [x for x in subs if STRICT_VARIANT.match(x.name)]
        other = [x for x in subs if not (STRICT_VARIANT.match(x.name)
                                         or ANCILLARY.match(x.name.strip())
                                         or ad.STRUCTURAL_DIR.match(x.name))]
        if variants and not other and _has_game_in(variants):
            out.append(d)
    return out


def _tree_size(path: Path) -> float:
    """目录总字节数（MB）。"""
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1048576


def is_redundant_copy(path: Path):
    """`xxx [副本N]` 是否是**冗余**副本，是则返回判据说明（否则返回 ""）。

    判据（见 SKILL.md「踩坑 1」）—— 必须**有证据**，不能见到 `[副本N]` 就当冗余，
    也不能凭「父目录里没看到像样的 exe」就断定副本是唯一本体：

      ① 父目录本身是游戏（`classify_one(parent) == game`）
      ② 副本与父目录**直接子项**名字重合 ≥60%（整份拷贝）
      ③ **体量对照**：父层总大小 ≈ 副本 × 2，即「父层净内容 ≈ 副本体量」

    实测全库 5 个 `[副本N]` 全部命中 ③（催眠学习 5986.9 / 2990.8、
    素晴らしき日々HD版 9386.2 / 4679.3、メスガキ 1998.4 / 992.1、
    完全痴漢 7643.8 / 3821.9、五印传说 2224.1 / 796.9 ），
    父层都是自带完整本体，副本纯冗余 —— **可安全隔离**。
    注意副本通常缺「发布说明」txt，但游戏本体（封包/exe/dll）是齐的，
    不要因为少了几个说明文件就以为是唯一一份。
    """
    m = COPY_RE.match(path.name)
    if not m:
        return ""
    base = m.group(1).strip()
    parent = path.parent
    try:
        ps = {p.name for p in parent.iterdir()}
        cs = {p.name for p in path.iterdir()}
    except OSError:
        return ""
    copy_mb = _tree_size(path)
    parent_mb = _tree_size(parent) - copy_mb  # 父层净内容（剔除副本自身）
    # ③ 体量对照：父层净内容与副本相当 -> 副本就是父层的一份拷贝
    if copy_mb > 1 and parent_mb >= copy_mb * 0.85:
        return "冗余副本(副本%.0fMB vs 父层净%.0fMB)" % (copy_mb, parent_mb)
    try:
        if ad.classify_one(parent).level() == "game":
            return "冗余副本(父目录本身是游戏)"
        sibs = [p for p in parent.iterdir()
                if p.is_dir() and p.name != path.name]
    except (OSError, RecursionError):
        return "冗余副本(待复核)" if cs and len(ps & cs) / len(cs) >= 0.6 else ""
    if cs and len(ps & cs) / len(cs) >= 0.6:
        return "冗余副本(子项重合%.0f%%)" % (100 * len(ps & cs) / len(cs))
    if any(s.name.startswith(base) or base in s.name for s in sibs):
        return "冗余副本(同级存在主件)"
    return ""


def shape_of(path: Path):
    """判定一个候选条目的形态，返回 (shape, note)。"""
    name = path.name
    _dup = is_redundant_copy(path)
    if _dup:
        return "DUPLICATE", _dup

    try:
        items = list(path.iterdir())
    except OSError as e:
        return "UNKNOWN", "无法读取: %s" % e
    files = [f for f in items if f.is_file()]
    dirs = [d for d in items if d.is_dir()]

    # 自身就是原子游戏 -> 最高优先级（避免把 Ren'Py 的 game/lib 误当合集）
    try:
        if ad.classify_one(path).level() == "game":
            # 免验证 / 运行补丁（AlphaROM、SiglusEngine_Patch、BepInEx…）是游戏
            # 能跑的前提，**不是「污染」**。这里只作信息标注，不改 action，
            # 也不给「删除」建议。判据只认可执行文件，音效素材的 "cracker"
            # 子串（Nutcrackers.mp3 / ads06_cracker_pulls_01）不算。
            note = ""
            try:
                cracks = ad.detect_cracker_tools(path)
            except Exception:
                cracks = []
            if cracks:
                note = "自带运行补丁（免验证，勿删）: %s" % ", ".join(cracks[:2])
            else:
                try:
                    if ad.detect_ui_patch(path):
                        note = "注入式补丁 BepInEx（汉化常用，勿删）"
                except Exception:
                    pass
            return "GAME", note
    except RecursionError:
        pass

    # 下载暂存：目录名与内容无关
    # detect_staging 返回 (原因, 实际压着的作品名列表)
    try:
        stg = ad.detect_staging(path, files, dirs)
    except Exception:
        stg = ("", [])
    reason = stg[0] if isinstance(stg, (tuple, list)) else (stg or "")
    if reason:
        staged = stg[1] if isinstance(stg, (tuple, list)) and len(stg) > 1 else []
        return "STAGING", reason + ("｜压着: %s" % staged[:3] if staged else "")

    gc = game_children(path, dirs)
    if len(gc) >= 2:
        return "CONTAINER", "%d 个游戏子目录" % len(gc)

    # 变体包装：子目录（非附属）全是 位/语言/版次，且里面有游戏
    non_anc = [d for d in dirs
               if not (ANCILLARY.match(d.name.strip())
                       or SKIP_AS_GAME.match(d.name.strip()))]
    variants = [d for d in non_anc if STRICT_VARIANT.match(d.name)]
    if variants and len(variants) == len(non_anc) and _has_game_in(variants):
        return "VARIANT_WRAP", "只有变体子目录: %s" % [d.name for d in variants][:4]

    if len(gc) == 1:
        return "SHELL", "本体在子目录 %s" % gc[0].name[:30]

    # 只有压缩包/镜像，无 exe -> 不动
    exes = [f for f in files if f.suffix.lower() == ".exe"]
    arch = [f for f in files if is_archive(f.name)]
    if arch and not exes and not gc:
        return "ARCHIVE", "%d 个压缩包/镜像" % len(arch)

    try:
        lv = ad.classify_one(path).level()
    except RecursionError:
        lv = "unknown"
    if lv == "tool":
        return "TOOL", "工具/残骸"
    return "UNKNOWN", "判不出"


ACTION = {
    "DUPLICATE":    ("quarantine", "移到 待删除/"),
    "GAME":         ("flatten", "整目录提到顶层"),
    "STAGING":      ("split", "按内容拆出真游戏"),
    "CONTAINER":    ("expand", "子游戏各自平铺（递归）"),
    "VARIANT_WRAP": ("flatten", "整目录当一条（内部不动）"),
    "SHELL":        ("flatten", "整目录当一条（内部不动）"),
    "ARCHIVE":      ("keep", "不动，列待解压清单"),
    "TOOL":         ("exclude", "排除，列清单"),
    "UNKNOWN":      ("review", "人工判断"),
}


def plan(base: Path):
    """对 base 下每个日期目录的每个候选条目做分诊，CONTAINER 递归展开。"""
    rows = []

    def emit(src: Path, shape, note, dst_base: Path, depth=0):
        action, desc = ACTION[shape]
        rows.append(dict(
            shape=shape, action=action, depth=depth,
            src=str(src).replace("\\", "/"),
            dst=(str(dst_base / src.name).replace("\\", "/")
                 if action == "flatten" else ""),
            note=note, desc=desc))
        # 目录内的冗余副本（如 `ゲーム名 [副本1]`）单独登记为隔离项，
        # 即使父目录本身是 GAME / SHELL（这些形态不再递归）。
        try:
            kids = [d for d in src.iterdir() if d.is_dir()]
        except OSError:
            kids = []
        for d in kids:
            _dup = is_redundant_copy(d)
            if _dup:
                a2, d2 = ACTION["DUPLICATE"]
                rows.append(dict(shape="DUPLICATE", action=a2, depth=depth + 1,
                                 src=str(d).replace("\\", "/"), dst="",
                                 note="父目录 %s 内: %s" % (src.name[:18], _dup),
                                 desc=d2))
        if shape != "CONTAINER":
            return
        for d in game_children(src, kids):
            sh, nt = shape_of(d)
            emit(d, sh, nt, dst_base, depth + 1)

    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir() or ad.is_internal(date_dir.name):
            continue
        for entry in sorted(date_dir.iterdir()):
            if not entry.is_dir() or ad.is_internal(entry.name):
                continue
            sh, nt = shape_of(entry)
            emit(entry, sh, nt, base)

    # 去重：同一 src 可能被「父级扫描」和「合集展开」各登记一次
    seen, uniq = set(), []
    for r in rows:
        key = (r["action"], r["src"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def main():
    ap = argparse.ArgumentParser(description="顶层平铺分诊（只读）")
    ap.add_argument("root", nargs="?", default="G:/Game")
    ap.add_argument("--csv", help="导出 CSV")
    ap.add_argument("--json", help="导出 JSON 计划")
    ap.add_argument("--shape", help="只看某一形态")
    args = ap.parse_args()

    base = Path(args.root)
    rows = plan(base)
    if args.shape:
        rows = [r for r in rows if r["shape"] == args.shape]

    from collections import Counter
    print("分诊条目: %d" % len(rows))
    for k, v in Counter(r["shape"] for r in rows).most_common():
        print("  %-14s %4d  (%s)" % (k, v, ACTION[k][1]))
    print()
    for r in rows:
        if r["shape"] in ("CONTAINER", "SHELL", "VARIANT_WRAP", "STAGING",
                          "ARCHIVE", "TOOL", "UNKNOWN", "DUPLICATE"):
            print("[%-12s] %s%s  %s"
                  % (r["shape"], "  " * r["depth"],
                     r["src"].replace(str(base).replace("\\", "/") + "/", ""),
                     r["note"][:50]))

    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        print("\nCSV -> %s" % args.csv)
    if args.json:
        json.dump(rows, open(args.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("JSON -> %s" % args.json)


if __name__ == "__main__":
    main()
