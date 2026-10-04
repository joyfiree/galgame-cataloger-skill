#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""平铺执行器 —— 消费 flatten_triage 的计划，**安全地**移动目录。

铁律（2026-10-04 与用户确认，不可绕过）：

  1. **默认 dry-run** —— 不加 `--apply` 绝不碰文件，只出预览。
  2. **不覆盖** —— 目的已存在就跳过并记账，绝不 merge / 覆盖。
  3. **不删除** —— 只做 `os.rename`；搬空后的空壳只登记，不自动删。
  4. **写 manifest** —— 每步 src/dst/status 落盘，随时可查。
  5. **可回滚** —— `--rollback manifest.json` 逆序搬回，一步不差。
  6. **同盘 rename** —— 全程只在 `G:/Game` 内部移动，同盘 rename 是原子操作，
     不复制数据（秒级），换盘/失败即中止并记账，不会动到半个文件。

执行顺序（关键）：
  **quarantine 先于 flatten** —— 副本嵌在父目录里面（`催眠学习/催眠学习 [副本1]`），
  必须趁原路径还有效时先隔离；否则父目录一平铺，副本路径就跟着变了。
  统一按 **src 深度降序**（深的先做）保证嵌套正确。

用法：
  python flatten_exec.py .workbuddy/flatten/plan.json                # dry-run 预览
  python flatten_exec.py .workbuddy/flatten/plan.json --apply        # 真执行
  python flatten_exec.py --rollback .workbuddy/flatten/manifest_xxx.json
"""
import argparse
import csv
import datetime
import json
import os
import sys
from pathlib import Path

QUARANTINE_DIR = "_待删除"


def ts():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def s(p):
    return str(p).replace("\\", "/")


def load_json(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def build_moves(rows, base: Path):
    """从分诊计划生成动作列表。

    只消费 `quarantine` 与 `flatten` 两类 action；其余（expand/keep/exclude/
    review/split）是**元信息或人工项**，执行器不碰。
    """
    moves = []
    for r in rows:
        act = r.get("action")
        src = Path(r["src"])
        if act == "quarantine":
            # 保留相对路径做隔离目标，避免同名副本互相撞：
            #   G:/Game/_待删除/2024-07/催眠学习/催眠学习 [副本1]
            try:
                rel = src.relative_to(base)
            except ValueError:
                rel = Path(src.name)
            moves.append(dict(action="quarantine", src=s(src),
                              dst=s(base / QUARANTINE_DIR / rel),
                              shape=r.get("shape", ""), note=r.get("note", "")))
        elif act == "flatten":
            dst = Path(r["dst"]) if r.get("dst") else base / src.name
            moves.append(dict(action="flatten", src=s(src), dst=s(dst),
                              shape=r.get("shape", ""), note=r.get("note", "")))
    # 深的先做：保证「子项先于父项」搬走（副本在父目录内）
    moves.sort(key=lambda m: (-len(Path(m["src"]).parts), m["src"]))
    return moves


def same_device(src: Path, dst: Path):
    """src 与 dst 是否同一卷（跨卷 rename 会失败）。"""
    probe = dst
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return os.stat(src).st_dev == os.stat(probe).st_dev
    except OSError:
        return False


def precheck(m, base: Path):
    """干跑一样要做的体检。返回 (ok, status, reason)。"""
    src, dst = Path(m["src"]), Path(m["dst"])
    if not src.exists():
        return False, "missing_src", "源不存在"
    if not src.is_dir():
        return False, "not_dir", "源不是目录"
    if src == dst:
        return False, "noop", "源与目的相同"
    if dst.exists():
        return False, "conflict", "目的已存在（不覆盖）"
    # 防自嵌套：目的不能在源内部
    try:
        dst.relative_to(src)
        return False, "self_nested", "目的在源内部"
    except ValueError:
        pass
    # 防把顶层目录整个搬走
    if src.parent == base and m["action"] == "flatten" and dst.parent == base:
        return False, "already_top", "本来就在顶层"
    if not same_device(src, dst):
        return False, "cross_device", "跨盘移动（需手工）"
    return True, "planned", ""


def ancestors_upto(p: Path, base: Path):
    out, cur = [], p
    while cur != base and base in cur.parents:
        out.append(cur)
        cur = cur.parent
    return out


def find_empty_shells(dirs, base: Path):
    """搬空后变空的目录（含链式变空），只登记不删。"""
    cand = set()
    for d in dirs:
        cand.update(ancestors_upto(d, base))
    empty, changed = set(), True
    while changed:
        changed = False
        for d in sorted(cand, key=lambda x: -len(x.parts)):
            if d in empty or d == base or not d.exists():
                continue
            try:
                if not any(d.iterdir()):
                    empty.add(d)
                    changed = True
            except OSError:
                pass
    return sorted(s(d) for d in empty)


def do_moves(moves, base: Path, apply_it: bool):
    from collections import Counter
    # 目标重名：**一律不猜**。同一 dst 有多个源时不自动挑一个，
    # 全部跳过交人工（实测 4 组：galfamily 内容完全相同，
    # ナギツルギ / 姦禁病棟 是两个不同版本 —— 不能替用户决定）。
    dst_cnt = Counter(m["dst"] for m in moves)
    results = []
    for m in moves:
        ok, status, reason = precheck(m, base)
        if ok and dst_cnt[m["dst"]] > 1:
            ok = False
            status = "dup_dst"
            reason = "目标同名，共 %d 个源（需人工挑选）" % dst_cnt[m["dst"]]
        rec = dict(m)
        rec["status"] = status
        rec["reason"] = reason
        rec["ts"] = now()
        if ok:
            if apply_it:
                src, dst = Path(m["src"]), Path(m["dst"])
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(src, dst)          # 同盘原子
                    rec["status"] = "done"
                except OSError as e:
                    rec["status"] = "error"
                    rec["reason"] = str(e)
            else:
                rec["status"] = "planned"
        results.append(rec)
    return results


def summarize(results, base: Path):
    from collections import Counter, defaultdict
    c = Counter(r["status"] for r in results)
    by_act = Counter((r["action"], r["status"]) for r in results)
    print("动作总数: %d" % len(results))
    for (act, st), n in sorted(by_act.items()):
        print("  %-11s %-9s %4d" % (act, st, n))
    print()
    warn = ("conflict", "missing_src", "error", "cross_device",
            "self_nested", "not_dir", "dup_dst")
    if any(c.get(k) for k in warn):
        print("⚠️ 需要关注的条目：")
        for r in results:
            if r["status"] in warn:
                print("  [%-13s] %-56s %s"
                      % (r["status"],
                         s(Path(r["src"])).replace(s(base) + "/", "")[:56],
                         r["reason"][:34]))
        print()
    # 重名冲突成组列出（同一目标的多个源）
    groups = defaultdict(list)
    for r in results:
        if r["status"] == "dup_dst":
            groups[r["dst"]].append(r["src"])
    if groups:
        print("⚠️ 目标重名组（已全部跳过，请人工指定保留哪一个）：")
        for dst, srcs in groups.items():
            print("  目标: %s" % s(Path(dst)).replace(s(base) + "/", ""))
            for x in srcs:
                print("      源: %s" % s(Path(x)).replace(s(base) + "/", ""))
        print()


def write_report(results, base, out_dir: Path, apply_it: bool):
    if not results:
        print("没有需要执行的动作，不写 manifest。")
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    empty = []
    if apply_it:
        done = [Path(r["src"]) for r in results
                if r["status"] == "done" and r["action"] == "flatten"]
        empty = find_empty_shells(done, base)
    man = dict(
        created=now(), root=s(base), mode="apply" if apply_it else "dry-run",
        quarantine_dir=s(base / QUARANTINE_DIR),
        stats=dict(results=len(results), empty_shells=empty),
        moves=results)
    # ⚠️ apply 的 manifest 是**回滚凭据**，前缀 manifest_ 且只增不删；
    # dry-run 的产物一律 preview_ 前缀，随便删都不会伤到回滚能力。
    prefix = "manifest" if apply_it else "preview"
    mpath = out_dir / ("%s_%s.json" % (prefix, ts()))
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=1)
    cpath = out_dir / ("%s_%s.csv" % (prefix, ts()))
    with open(cpath, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print("manifest -> %s" % s(mpath))
    print("moves    -> %s" % s(cpath))
    if empty:
        print("\n空壳目录（已搬空，**未删除**，请你确认后再处理）：%d 个" % len(empty))
        for e in empty[:40]:
            print("  " + e.replace(s(base) + "/", ""))
        if len(empty) > 40:
            print("  ...(还有 %d 个)" % (len(empty) - 40))
    return mpath


def check_rollback(manifest_path: Path):
    """只检查「能不能回滚」，不真回滚。回滚凭据要能随时自证可用。"""
    man = load_json(manifest_path)
    moves = [m for m in man.get("moves", []) if m.get("status") == "done"]
    print("manifest: %s (mode=%s, done=%d)"
          % (s(manifest_path), man.get("mode"), len(moves)))
    ok = blocked = 0
    for m in moves:
        src, dst = Path(m["src"]), Path(m["dst"])
        if not dst.exists():
            print("  ✖ 目的已不在，无法搬回: %s" % s(dst))
            blocked += 1
        elif src.exists():
            print("  ✖ 原位置已被占用，搬回会冲突: %s" % s(src))
            blocked += 1
        else:
            ok += 1
    print("可回滚 %d 项，受阻 %d 项 -> %s"
          % (ok, blocked, "✅ 随时可回滚" if not blocked else "⚠️ 部分受阻"))


def rollback(manifest_path: Path):
    man = load_json(manifest_path)
    moves = [m for m in man.get("moves", []) if m.get("status") == "done"]
    if not moves:
        print("manifest 里没有 done 的移动，无需回滚。")
        return
    print("回滚 %d 步（逆序）…" % len(moves))
    ok = fail = 0
    for m in reversed(moves):
        src, dst = Path(m["src"]), Path(m["dst"])
        if not dst.exists():
            print("  ⏭  目的已不在，跳过: %s" % s(dst))
            fail += 1
            continue
        if src.exists():
            print("  ⛔ 原位置已被占用，跳过（不覆盖）: %s" % s(src))
            fail += 1
            continue
        try:
            src.parent.mkdir(parents=True, exist_ok=True)
            os.rename(dst, src)
            ok += 1
        except OSError as e:
            print("  ✖ %s: %s" % (s(dst), e))
            fail += 1
    print("回滚完成：成功 %d，跳过 %d" % (ok, fail))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="平铺执行器（默认 dry-run）")
    ap.add_argument("plan", nargs="?", default=".workbuddy/flatten/plan.json",
                    help="flatten_triage 产出的计划 JSON")
    ap.add_argument("--root", default="G:/Game", help="库根目录")
    ap.add_argument("--apply", action="store_true",
                    help="真的移动（不加则只预览）")
    ap.add_argument("--rollback", metavar="MANIFEST",
                    help="按 manifest 逆序回滚")
    ap.add_argument("--check-rollback", metavar="MANIFEST",
                    help="只检查回滚可行性，不真回滚")
    ap.add_argument("--out", default=".workbuddy/flatten",
                    help="manifest / 报告输出目录")
    ap.add_argument("--only", choices=["flatten", "quarantine"],
                    help="只做某一类动作")
    args = ap.parse_args()

    base = Path(args.root).resolve()

    if args.rollback:
        rollback(Path(args.rollback))
        return
    if args.check_rollback:
        check_rollback(Path(args.check_rollback))
        return

    rows = load_json(args.plan)
    moves = build_moves(rows, base)
    if args.only:
        moves = [m for m in moves if m["action"] == args.only]

    print("根目录: %s" % s(base))
    print("计划:   %s" % s(Path(args.plan).resolve()))
    print("模式:   %s\n" % ("★ APPLY（真移动）" if args.apply else "DRY-RUN（预览，不动文件）"))
    print("执行顺序: quarantine 先于 flatten，同级按 src 深度降序\n")

    results = do_moves(moves, base, args.apply)
    summarize(results, base)

    if not args.apply:
        print("—— 前 25 条预览 ——")
        for r in results[:25]:
            tag = "<-" if r["action"] == "quarantine" else "->"
            print("  [%-9s] %s\n      %s %s"
                  % (r["status"],
                     s(Path(r["src"])).replace(s(base) + "/", ""),
                     tag, s(Path(r["dst"])).replace(s(base) + "/", "")))
        print("\n（预览结束。确认无误后加 --apply 真执行）")

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = base / out_dir
    write_report(results, base, out_dir, args.apply)


if __name__ == "__main__":
    main()
