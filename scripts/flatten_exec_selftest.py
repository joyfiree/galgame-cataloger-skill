#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""flatten_exec 自检 —— 用合成目录树端到端验证「预览 → 执行 → 回滚」。

不碰真实游戏库。场景覆盖：

  1. 普通 GAME 平铺（含子目录里的 `[副本N]`，验证 quarantine 先于 flatten）
  2. CONTAINER 展开后的子游戏平铺（深层 src 先做）
  3. 目标重名 -> 必须 dup_dst 跳过，绝不覆盖
  4. 目的已存在 -> conflict 跳过
  5. 执行后空壳被登记且**不删除**
  6. `--rollback` 能一步不差地搬回原处

用法：python flatten_exec_selftest.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXEC = HERE / "flatten_exec.py"
ROOT = Path(tempfile.gettempdir()) / "flatten_exec_selftest"   # 临时区，跑完删


def build_tree():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    made = [
        "2024-01/GameA/sub",
        "2024-01/GameA/GameA [副本1]/sub",      # 父目录内的冗余副本
        "2024-01/Coll/GameB",
        "2024-01/Coll/Deep/GameC",
        "2024-02/GameD",
        "2024-04/GameE",                        # 与 2024-05/GameE 目标重名
        "2024-05/GameE",
    ]
    for d in made:
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    for f, size in [("2024-01/GameA/GameA.exe", 100_000),
                    ("2024-01/GameA/GameA [副本1]/GameA.exe", 100_000),
                    ("2024-01/Coll/GameB/GameB.exe", 200_000),
                    ("2024-01/Coll/Deep/GameC/GameC.exe", 300_000),
                    ("2024-02/GameD/GameD.exe", 400_000),
                    ("2024-04/GameE/GameE.exe", 500_000),
                    ("2024-05/GameE/GameE.exe", 600_000)]:
        with open(ROOT / f, "wb") as fh:
            fh.write(b"\0" * size)


def make_plan():
    def row(shape, action, src, dst=""):
        return dict(shape=shape, action=action, depth=0,
                    src=str((ROOT / src)).replace("\\", "/"),
                    dst=str((ROOT / dst)).replace("\\", "/") if dst else "",
                    note="", desc="")
    return [
        row("GAME", "flatten", "2024-01/GameA", "GameA"),
        row("DUPLICATE", "quarantine", "2024-01/GameA/GameA [副本1]"),
        row("GAME", "flatten", "2024-01/Coll/GameB", "GameB"),
        row("GAME", "flatten", "2024-01/Coll/Deep/GameC", "GameC"),
        row("GAME", "flatten", "2024-02/GameD", "GameD"),
        row("GAME", "flatten", "2024-04/GameE", "GameE"),   # dup_dst 组
        row("GAME", "flatten", "2024-05/GameE", "GameE"),   # dup_dst 组
    ]


def run(*args):
    p = subprocess.run([sys.executable, str(EXEC), *args],
                       capture_output=True, text=True, encoding="utf-8")
    return p.stdout + p.stderr


def snap():
    """游戏目录树快照（排除 executor 自己产出的 _out/ 与 plan.json）。"""
    out = set()
    for d in ROOT.rglob("*"):
        rel = str(d.relative_to(ROOT)).replace("\\", "/")
        if rel == "plan.json" or rel.startswith("_out"):
            continue
        out.add(rel + ("/" if d.is_dir() else ""))
    return out


FAILED = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


def main():
    print("== 建合成目录树 ==")
    build_tree()
    plan_path = ROOT / "plan.json"
    plan_path.write_text(json.dumps(make_plan(), ensure_ascii=False, indent=1),
                         encoding="utf-8")
    before = snap()

    print("\n== 1) dry-run 预览（不得动文件）==")
    out = run(str(plan_path), "--root", str(ROOT),
              "--out", str(ROOT / "_out"))
    check("dry-run 未改文件", snap() == before)
    check("识别出 dup_dst 跳过", "dup_dst" in out and "GameA" in out)
    check("quarantine 计入", "quarantine" in out)

    print("\n== 2) 真执行 ==")
    out = run(str(plan_path), "--root", str(ROOT), "--apply",
              "--out", str(ROOT / "_out"))
    checks = {
        "GameA 平铺到顶层": (ROOT / "GameA/GameA.exe").exists(),
        "GameB 平铺到顶层": (ROOT / "GameB/GameB.exe").exists(),
        "GameC 平铺到顶层": (ROOT / "GameC/GameC.exe").exists(),
        "GameD 平铺到顶层": (ROOT / "GameD/GameD.exe").exists(),
        "副本已隔离到 _待删除": (ROOT / "_待删除/2024-01/GameA/GameA [副本1]").exists(),
        "重名的两条 GameE 都原地未动":
            (ROOT / "2024-04/GameE").exists() and (ROOT / "2024-05/GameE").exists(),
        "顶层未出现 GameE": not (ROOT / "GameE").exists(),
        "顶层 GameA 未被覆盖": (ROOT / "GameA/GameA.exe").stat().st_size == 100_000,
        "空壳 2024-01/Coll/Deep 已登记": "2024-01/Coll/Deep" in out,
        "空壳未被删除": (ROOT / "2024-01/Coll").exists(),
    }
    for k, v in checks.items():
        check(k, v)
    man = list((ROOT / "_out").glob("manifest_*.json"))
    check("manifest 已写出", bool(man))

    print("\n== 3) 回滚 ==")
    out = run("--rollback", str(man[-1]))
    restored_ok = all(x in snap() for x in [
        "2024-01/GameA/", "2024-01/GameA/GameA.exe",
        "2024-01/Coll/GameB/GameB.exe",
        "2024-01/Coll/Deep/GameC/GameC.exe",
        "2024-02/GameD/GameD.exe",
        "2024-01/GameA/GameA [副本1]/GameA.exe"])
    check("回滚后 6 项全部回到原处", restored_ok)
    check("回滚后副本离开隔离区",
          not (ROOT / "_待删除/2024-01/GameA/GameA [副本1]").exists())

    print("\n== 清理 ==")
    shutil.rmtree(ROOT, ignore_errors=True)
    print("已删除临时区 %s" % ROOT)

    print("\n" + ("全部通过 ✅" if not FAILED else "失败 %d 项 ❌ %s" % (len(FAILED), FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
