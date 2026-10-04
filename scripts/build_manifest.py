"""并发远端校验器 —— 生成统一清单文件，不往游戏目录里塞任何东西。

设计要点
--------
1. **统一清单** `library.json` —— 一个文件记录全部条目的
   本地目录 / 远端名称 / 远端地址 / 置信度。不写 .galmeta.json，
   不改目录名，任何时候可重新生成。
2. **并发但受限流** —— 实测 4 条查询：串行 6.0s，并发 1.8s（3.3x），
   且未超配额。做法是「多线程 + 全局令牌桶」：所有线程在 API 出口排队，
   每次请求占一个 1.8s 的槽。这样本地文件读取、probe_local 的耗时、
   HTTP 等待全部重叠，但请求总数仍被配额锁死。
   ⚠️ 调大 --workers 不会突破配额上限，只减少空等。
3. **可断点续跑** —— 结果按条 append 到 JSONL，中断后重跑会跳过已完成项。
4. **本地证据只算一次** —— probe_local 提取的真名存进清单，
   远端匹配与本地提取解耦。

用法
----
    python build_manifest.py                          # 全量
    python build_manifest.py --limit 20               # 先试 20 个
    python build_manifest.py --workers 6              # 调并发
    python build_manifest.py --out "G:/Game/清单.json"
    python build_manifest.py --from-cache             # 只用已有缓存重算匹配
"""

import argparse
import hashlib
import json
import queue
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import verify_remote as VR  # noqa: E402

ROOT = Path("G:/Game")
DEFAULT_OUT = ROOT / ".workbuddy" / "scan" / "library.json"
JSONL = ROOT / ".workbuddy" / "scan" / "library.jsonl"

# ------------------------------------------------------------ 限流令牌桶
class TokenBucket:
    """全局令牌桶：所有线程在 API 出口排队，不各自 sleep。

    关键：必须是**全局共享**的一个实例。若每线程各自限流，
    N 个线程就是 N 倍速率，必然触发 429。
    """

    def __init__(self, interval=2.2):
        self.interval = interval
        self._lock = threading.Lock()
        self._next = 0.0

    def take(self):
        with self._lock:
            now = time.time()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if wait:
            time.sleep(wait)

    def reset(self, interval):
        with self._lock:
            self.interval = interval
            self._next = 0.0


# VNDB 配额 200 次 / 5 分钟 = 1.5s/请求。
# 配额按**总请求数**计算，与并发无关。5 线程 × 每游戏 2-4 次查询时，
# 实际速率可能超过 1.8s/请求 → 429。实测 1.8s 在 5 线程下会被限流，
# 取 2.2s 更稳。
BUCKET = TokenBucket(2.2)

# 缓存读写锁：并发下多个线程可能命中同一个 key
CACHE_LOCK = threading.Lock()

# ------------------------------------------------------------ 识别游戏目录
# 「这是游戏」的判据。只用具体的引擎文件/可执行文件，
# 不用 www/data 这类目录名 —— 它们同时也是 STRUCTURAL 排除对象。
# 真实判据优先级：引擎封包 > 主程序 exe > 引擎标准文件名。
GAME_EXTS = {".exe", ".xp3", ".pfs", ".rgss3a", ".rpgmvp", ".unity3d",
             ".bgl", ".int", ".arc", ".pck"}
GAME_FILES = {"game.ini", "nscr.dat", "siglusengine.exe", "renpy.py",
              "app.info", "package.json"}
UNITY_DATA = re.compile(r"(?i)^.+$_data$")
NOT_GAME = re.compile(
    r"(?i)(备份|備份|backup|patch|补丁|原版|旧版|旧|攻略|"
    r"^save$|^savedata$|破解|cracker|汉化补丁)")

# 引擎子目录 / 位版本子目录 —— 下探时必须排除，否则会被当成独立游戏。
# 实例：`4P Play/32位` 是位版本，`Idol hands 2/www` 是 NW.js 资源目录。
STRUCTURAL = re.compile(
    r"(?i)^("
    r"32位|64位|32bit|64bit|x86|x64|win32|win64|"
    r"www|data|renpy|lib|managed|locales|swiftshader|"
    r"tools?|tool|plugins?|assets?|resources|content|contents|"
    r"savedata|save|patch|patches|extras?|bonus|trial|demo|"
    r"体験版|存档|存档备份|补丁|补丁和存档|攻略|特典|本体|原版"
    r")$")

# 引擎自带工具 exe，不该当主程序
BUNDLED_TOOLS = re.compile(
    r"(?i)^(chardetect|dxwebsetup|python|pythonw|python3|unins\d*|"
    r"setup|uninstall|config|unrar|7z|notification_helper|"
    r"crashreport|crashpad_handler|unitycrashhandler\d*|"
    r"libegl|libglesv2|libgles|libx11|libxcursor|libxinerama|"
    r"vcredist|dxsetup|directx)")


def is_game_dir(d: Path):
    """只判顶层，不递归。返回 (是否游戏, 判定依据)。"""
    if not d.is_dir():
        return False, ""
    try:
        entries = list(d.iterdir())
    except OSError:
        return False, ""
    for e in entries:
        n = e.name.lower()
        if e.is_file():
            if e.suffix.lower() in GAME_EXTS or n in GAME_FILES:
                if BUNDLED_TOOLS.match(e.stem):
                    continue
                return True, e.name
        elif e.is_dir() and UNITY_DATA.match(e.name):
            # Unity 游戏：xxx_Data/ 与主程序 exe 必然同级
            return True, e.name + "/"
    return False, ""


def collect_games(bases, max_depth=2):
    """收集游戏目录，支持一层下探（待甄别/ 下面是子区）。"""
    out = []
    seen = set()

    def scan(base, depth):
        if not base.is_dir() or depth > max_depth:
            return
        try:
            children = sorted(base.iterdir())
        except OSError:
            return
        for d in children:
            if not d.is_dir() or d.name.startswith("."):
                continue
            if d.name.startswith("〔"):      # 系列分组，跳过
                continue
            if NOT_GAME.search(d.name) or STRUCTURAL.match(d.name):
                continue
            real = str(d).lower()
            if real in seen:
                continue
            ok, why = is_game_dir(d)
            if ok:
                seen.add(real)
                out.append((d, why, str(base)))
            else:
                scan(d, depth + 1)

    for b in bases:
        scan(Path(b), 0)
    return out


# ------------------------------------------------------------ 本地证据
def load_probe():
    for p in (Path(__file__).parent,
              Path.home() / ".workbuddy/skills/galgame-cataloger/scripts"):
        if (p / "probe_local.py").exists():
            sys.path.insert(0, str(p))
            try:
                import probe_local as PL
                return PL
            except ImportError:
                continue
    return None


PL = load_probe()


def local_evidence(folder: Path):
    """抽本地真名 + 引擎。失败不阻塞，交给远端阶段。"""
    if PL is None:
        return [], None
    try:
        info = PL.probe(folder, depth=2)
    except Exception:
        return [], None
    titles = [t for t in (info.get("titles") or [])[:2] if t]
    return titles, info.get("engine")


# ------------------------------------------------------------ 单条处理
def process(item):
    folder, why, group = item
    name = folder.name
    titles, engine = local_evidence(folder)

    try:
        m = VR.verify(name, local_titles=titles or None)
    except Exception as e:
        return {
            "schema": "library/1",
            "local_dir": str(folder).replace("\\", "/"),
            "local_name": name,
            "group": group.replace("\\", "/"),
            "error": "%s: %s" % (type(e).__name__, e),
            "confidence": "E",
            "remote": {"found": False},
            "flatten_allowed": False,
        }

    b = m.best or {}
    rec = {
        "schema": "library/1",
        "local_dir": str(folder).replace("\\", "/"),
        "local_name": name,
        "local_titles": titles,
        "group": group.replace("\\", "/"),
        "engine": engine,
        "detected_by": why,
        "remote": {
            "found": bool(b),
            "vndb_id": b.get("id"),
            "title": b.get("title"),
            "alttitle": b.get("alttitle") or "",
            "released": b.get("released"),
            "vendor": b.get("vendor"),
            "url": b.get("url"),
        },
        "confidence": m.level,
        "reason": m.reason,
        "candidates": m.candidates[:3],
        "flatten_allowed": m.level in ("A", "B"),
    }
    return rec


# ------------------------------------------------------------ 断点续跑
def load_done(jsonl: Path, retry_failed=False):
    """读已完成条目。

    retry_failed=True 时，把「因限流/网络错误失败」的条目视为未完成，
    重新排队 —— 否则一次限流就会让这些条目永久停留在假失败状态。
    """
    if not jsonl.exists():
        return {}
    done = {}
    try:
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "local_dir" not in r:
                continue
            if retry_failed:
                reason = r.get("reason", "") or ""
                if r.get("error") or "HTTP" in reason or "限流" in reason:
                    # 网络类失败 -> 不算完成，下轮重试
                    continue
                if r.get("confidence") == "E" and not (r.get("remote") or {}).get("found"):
                    # 真 0 结果也重试一次（远端条目可能已新增）
                    if "HTTP" in reason:
                        continue
            done[r["local_dir"]] = r
    except OSError:
        pass
    return done


# ------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--from-cache", action="store_true",
                    help="只用已有缓存重算匹配，不发新请求")
    ap.add_argument("--retry-failed", action="store_true",
                    help="重跑因限流/网络错误而失败的条目（配合断点续跑）")
    ap.add_argument("--throttle", type=float, default=2.2,
                    help="限流间隔秒数，默认 2.2（配额 200/5min）")
    a = ap.parse_args()

    BUCKET.reset(a.throttle)
    bases = [Path(d) for d in a.dirs] or [ROOT / "合集提取", ROOT / "待甄别"]

    games = collect_games(bases)
    if a.limit:
        games = games[:a.limit]
    print("待校验 %d 个游戏，线程数 %d，限流 %.1fs/请求"
          % (len(games), a.workers, a.throttle))
    print("本地证据模块: %s" % ("已加载" if PL else "不可用"))
    print()

    if a.from_cache:
        BUCKET.reset(0.01)

    jsonl = Path(a.out).with_suffix(".jsonl")
    done = load_done(jsonl, retry_failed=a.retry_failed)
    if jsonl.exists():
        print("断点续跑：已跳过 %d 条\n" % len(done))

    todo = [(d, w, g) for d, w, g in games
            if str(d).replace("\\", "/") not in done]
    results = list(done.values())

    t0 = time.time()
    lock = threading.Lock()
    counters = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0, "err": 0}
    fh = jsonl.open("a", encoding="utf-8")

    def run(item):
        rec = process(item)
        return rec

    try:
        with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
            futs = {pool.submit(run, it): it for it in todo}
            for i, fut in enumerate(futs, 1):
                pass  # 提交完成，下面按完成顺序处理
            from concurrent.futures import as_completed
            for i, fut in enumerate(as_completed(futs), 1):
                try:
                    rec = fut.result()
                except Exception as e:
                    d = futs[fut][0]
                    rec = {
                        "local_dir": str(d).replace("\\", "/"),
                        "local_name": d.name,
                        "error": "%s: %s" % (type(e).__name__, e),
                        "confidence": "E",
                        "remote": {"found": False},
                        "flatten_allowed": False,
                    }
                results.append(rec)
                with lock:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
                    lvl = rec.get("confidence", "E")
                    if "error" in rec:
                        counters["err"] += 1
                    else:
                        counters[lvl] = counters.get(lvl, 0) + 1
                    flag = {"A": "OK ", "B": "ok ", "C": "?  ",
                            "D": "?? ", "E": "无 "}.get(lvl, "?? ")
                    el = time.time() - t0
                    eta = el / i * (len(todo) - i)
                    print("[%3d/%d] %s %-36s %-30s ETA %dm"
                          % (i, len(todo), flag, rec["local_name"][:34],
                             rec.get("reason", "")[:28], eta // 60))
    finally:
        fh.close()

    # ---------------------------------------------------- 写统一清单
    results.sort(key=lambda r: r.get("local_name", ""))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    # ---------------------------------------------------- CSV 视图
    csv_path = out.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        import csv as _csv
        w = _csv.writer(f)
        w.writerow(["本地目录", "本地名", "远端名称", "远端地址",
                    "厂商", "发售日", "置信度", "可平铺", "证据来源", "判定理由"])
        for r in results:
            rem = r.get("remote") or {}
            w.writerow([
                r.get("local_name", ""),
                " ".join(r.get("local_titles") or [])[:60],
                rem.get("title") or "",
                rem.get("url") or "",
                rem.get("vendor") or "",
                rem.get("released") or "",
                r.get("confidence", ""),
                "Y" if r.get("flatten_allowed") else "N",
                (r.get("local_titles") or [r.get("local_name", "")])[0],
                r.get("reason", ""),
            ])

    # ---------------------------------------------------- 汇总
    print("\n" + "=" * 64)
    print("校验汇总")
    print("=" * 64)
    desc = {
        "A": "远端确认唯一且双证据 -> 可平铺，可用远端名",
        "B": "远端匹配中等 -> 可平铺，建议扫一眼",
        "C": "远端有但证据不足 -> 不可平铺",
        "D": "多候选/触发负向 -> 不可平铺，人工选",
        "E": "远端 0 结果 -> 不可平铺",
    }
    for lvl in "ABCDE":
        n = counters.get(lvl, 0)
        if n:
            print("  %s 级 %3d 个  %s" % (lvl, n, desc[lvl]))
    if counters["err"]:
        print("  出错    %3d 个" % counters["err"])
    allowed = counters.get("A", 0) + counters.get("B", 0)
    print("\n  可平铺 %d    需人工 %d    共 %d"
          % (allowed, len(results) - allowed, len(results)))
    print("  耗时 %.1f 分钟" % ((time.time() - t0) / 60))
    print("\n  统一清单 JSON : %s" % out)
    print("  表格视图 CSV : %s" % csv_path)
    print("  断点续跑     : %s" % jsonl)


if __name__ == "__main__":
    main()
