r"""游戏五分类 —— 按「发行渠道」而非「引擎」分类。

核心认识（用户纠正）
--------------------
我之前的做法是「一律拿 VNDB 判准」，这是错的。不同发行渠道，
索引站点完全不同：

| 类 | 作品性质 | 引擎特征 | 主索引站 |
|---|---|---|---|
| **A** | 厂商发布的 galgame | KiriKiri / Siglus / BGI / Artemis / NScripter 等传统引擎 | **VNDB** |
| **B** | RPG Maker 制作的小游戏 | `Game.ini` / `*.rpgmvp` / `RGSS3a` / `www`+`package.json` | **DLsite** |
| **C** | Ren'Py 制作的小游戏 | `game/` + (`lib`\|`MonoBleedingEdge`) | **itch.io** |
| **D** | Unity 游戏 | `*_Data/` / `UnityPlayer.dll` / `app.info` | **Steam** |
| **E** | 找不到的 | 都不匹配 | —— |

**为什么必须分开**：拿 VNDB 查 Ren'Py 小游戏基本查不到（itch.io 独占），
拿 itch.io 查厂商 galgame 也查不到。**用错索引站 = 全库判为「查不到」。**

⚠️ 引擎与渠道不是一一对应，但**强相关**：
   - KiriKiri/Siglus/BGI/Artemis → 几乎都是 A 类
   - Ren'Py → 几乎都是 C 类
   - Unity → D 类（Steam 上大量 Unity 3D/2D 游戏）
   - RPG Maker → B 类，但**经常被打包成 NW.js**（`www/` + `package.json` + `data/*.rpgmvo`）
     所以 NW.js 不等于 B 类，要看 `data/` 里有没有 `.rpgmvo`

这个分类只做**归属判断**，不联网。真正的「远端有没有条目」是下一步的事。
"""

import re
from pathlib import Path

# ================================================================ 引擎签名

A_CLASS_SIGS = {
    # 传统 galgame 引擎 —— A 类强信号
    "KiriKiri": [
        (re.compile(r"(?i)^data\.xp3(\.sig)?$"), "data.xp3"),
        (re.compile(r"(?i)^data\.xp3\.sig$"), None),
    ],
    "SiglusEngine": [(re.compile(r"(?i)^siglusengine\.exe$"), "SiglusEngine.exe")],
    "NScripter": [(re.compile(r"(?i)^nscr\.dat$"), "nscr.dat")],
    "Artemis": [(re.compile(r"(?i)^.*\.pfs$"), "*.pfs")],
    "Ethornella": [
        (re.compile(r"(?i)^.*\.bgl$"), "*.bgl"),
        (re.compile(r"(?i)^.*\.xfl$"), "*.xfl"),
    ],
    "Eushully": [(re.compile(r"(?i)^.*\.noa$"), "*.noa")],
    "Majiro": [(re.compile(r"(?i)^.*\.arc$"), "*.arc")],
    "DMFS": [(re.compile(r"(?i)^data\.pfs$"), None)],
}

C_CLASS_SIGS = {
    "Ren'Py": [
        # 目录组合
        (None, ("game", "lib")),
        (None, ("renpy", "game")),
        # Ren'Py 标准三件套：xxx.exe + xxx.py + xxx.sh
        (re.compile(r"(?i)^.+_Data$"), "（无 Unity 特征但见 game/lib）"),
    ],
}

D_CLASS_SIGS = {
    "Unity": [
        (re.compile(r"(?i)^.+_Data$"), "*_Data/"),
        (re.compile(r"(?i)^unityplayer\.dll$"), "UnityPlayer.dll"),
        (re.compile(r"(?i)^app\.info$"), "app.info"),
        (re.compile(r"(?i)^globalgamemanagers$"), "globalgamemanagers"),
        (re.compile(r"(?i)^.*\.unity3d$"), "*.unity3d"),
        (re.compile(r"(?i)^boot\.config$"), "boot.config"),
    ],
}

B_CLASS_SIGS = {
    "RPGMaker": [
        (re.compile(r"(?i)^game\.ini$"), "Game.ini"),
        (re.compile(r"(?i)^.*\.rgss3a$"), "*.rgss3a"),
        (re.compile(r"(?i)^.*\.rgssad$"), "*.rgssad"),
        (re.compile(r"(?i)^.*\.rpgmvp$"), "*.rpgmvp"),
        (re.compile(r"(?i)^.*\.rpgmvo$"), "*.rpgmvo"),
    ],
}

# ================================================================ 排除清单
# 这些文件名虽像封包但不是
NON_PACK = re.compile(
    r"(?i)^(icudtl\d*l?\.dat|resources\.dat|snapshot_blob\.bin|"
    r"v8_context_snapshot\.bin|app\.info|boot\.config|"
    r"globalgamemanagers|globalgamemanagers\.assets)$")


def _items(folder: Path):
    """返回 (files, dirs, {小写名: 对象})"""
    try:
        items = list(folder.iterdir())
    except OSError:
        return [], [], {}
    files = [f for f in items if f.is_file()]
    dirs = [d for d in items if d.is_dir()]
    return files, dirs, {f.name.lower(): f for f in items}



# ================================================================ 封包在子目录
# 实测：大量厂商 galgame 的封包不在顶层，而在同名子目录里：
#   kiss_crisis/  pac/{bgm,se,cg,cgsys,op}.ypf   → cats/system engine
#   CLOCKUP2/     pac/*.ypf + YSOHC.DLL + yscfg.dat →  Ditto/yssystem
#   ANIM僕ママ/   K2sXg4VN/*.gax  + emotedriver.dll → BGI/BeE2
# 只看顶层会全部漏判成 E 类。

SUB_PACK_SIGS = [
    (re.compile(r"(?i)^(pac|yspac|data|archive|resource|res|assets|"
                r"ygp|graphic|sound|bgm|cg|se|voice|scenario|"
                r"system|font|effect|movie|sumire|sumirec|"
                r"k2svg4vn|ygp_gfx|workspace|.*_data)$"), None),
]

# 引擎级目录名 —— 出现即说明引擎
ENGINE_DIR_SIGS = [
    (re.compile(r"(?i)^(pac|yspac)$", ), "cats/system/yssystem"),
    (re.compile(r"(?i)^k2svg4vn$"), "BGI/BeE2"),
    (re.compile(r"(?i)^kirikiri( ?ad)?$"), "KiriKiriAD"),
    (re.compile(r"(?i)^tyranoe?$"), "TyranoBuilder"),
    (re.compile(r"(?i)^sumire( ?script)?$"), "吉里吉里系(sumire)"),
    (re.compile(r"(?i)^krkr$"), "KiriKiri"),
    (re.compile(r"(?i)^tyrano$"), "TyranoBuilder"),
]


def detect_subdir_engine(dirs):
    """看子目录名能否确定引擎。返回 (引擎, 证据) 或 (None, '')。"""
    dn = {d.name: d for d in dirs}
    for pat, eng in ENGINE_DIR_SIGS:
        for name in dn:
            if pat.match(name):
                return eng, name + "/"
    # pac/ 里若是 .ypf -> cats/system；若是 .gax -> BGI
    pac = dn.get("pac") or dn.get("yspac")
    if pac and pac.is_dir():
        try:
            exts = {f.suffix.lower() for f in pac.iterdir() if f.is_file()}
        except OSError:
            exts = set()
        if ".ypf" in exts:
            return "cats/system", "pac/*.ypf"
        if ".gax" in exts or ".g00" in exts or ".gyu" in exts:
            return "BGI/BeE2", "pac/*.gax"
    k2 = dn.get("K2sXg4VN")
    if k2 and k2.is_dir():
        try:
            exts = {f.suffix.lower() for f in k2.iterdir() if f.is_file()}
        except OSError:
            exts = set()
        if exts & {".gax", ".g00", ".gyu"}:
            return "BGI/BeE2", "K2sXg4VN/*.gax"
    return None, ""




# ================================================================ 渠道 URL（最高优先级）
# 发行方自己留下的链接文件，是**最硬的证据** —— 比任何引擎推断都可靠。
# 实测：男の娘オナホール-朱莉-/ 里有 DLsite.url，引擎看不出来但渠道明确。
#
# ⚠️ 重要发现：引擎 != 渠道。
#   Amusement Dark II Revelation 是 Unity，但发行在 DLsite。
#   所以「Unity → Steam」只是**先验倾向**，有 URL 证据时以 URL 为准。

CHANNEL_URL_SIGS = [
    (re.compile(r"(?i)(dlsite\.com|dlsite\.url|dlsite)"), "B", "DLsite"),
    (re.compile(r"(?i)(itch\.io|itchio)"), "C", "itch.io"),
    (re.compile(r"(?i)(steamcommunity|store\.steampowered|steam_appid|"
                r"steam[^a-z]*url)"), "D", "Steam"),
    (re.compile(r"(?i)(vndb\.org|vndb\.url)"), "A", "VNDB"),
    (re.compile(r"(?i)(getchu|fanza|dmm\.com|pixiv)"), "A", "Getchu/FANZA"),
]


def detect_channel_url(files):
    """看顶层是否有渠道 URL 文件。返回 (类别, 渠道, 文件名) 或 (None,'','')。"""
    for f in files:
        nl = f.name.lower()
        for pat, cat, ch in CHANNEL_URL_SIGS:
            if pat.search(nl):
                return cat, ch, f.name
        # Steam 的 steam_appid.txt
        if nl == "steam_appid.txt":
            return "D", "Steam", f.name
    return None, "", ""



def classify_channel(folder: Path):
    """返回 (类别, 引擎, 证据)。

    类别：'A' | 'B' | 'C' | 'D' | 'E' | 'container' | 'tool' | 'unknown'
    """
    if not folder.is_dir():
        return "unknown", None, "不是目录"

    files, dirs, dnames = _items(folder)
    names = {f.name.lower(): f for f in files}
    dnames_l = {d.name.lower(): d for d in dirs}

    # ---- 工具/附件/非游戏 ----
    from atomic_detect import (STRUCTURAL_DIR, RESOURCE_DIR, TOOL_DIR,
                               detect_non_game, VARIANT_DIR)
    if TOOL_DIR.match(folder.name):
        return "tool", None, f"目录名是已知工具: {folder.name}"
    nongame = detect_non_game(folder, files, dirs)
    if nongame:
        return "tool", None, nongame

    # ---- 容器判定：子目录里还有游戏 ----
    # （简化：若有 >=2 个子目录自身像游戏，则是容器）
    game_kids = []
    for d in dirs:
        if (STRUCTURAL_DIR.match(d.name) or RESOURCE_DIR.match(d.name)
                or RESOURCE_DIR.match(d.name.lower())
                or VARIANT_DIR.match(d.name)):
            continue
        sub = classify_channel(d)
        if sub[0] in ("A", "B", "C", "D"):
            game_kids.append(d)
    if len(game_kids) >= 2:
        return "container", None, "含 %d 个游戏子目录: %s" % (
            len(game_kids), [d.name[:12] for d in game_kids[:3]])

    # ---- 渠道 URL 证据（最高优先级，压过引擎推断）----
    cat, ch, fname = detect_channel_url(files)
    if cat:
        return cat, ch, "渠道链接文件: %s" % fname

    # ---- C 类：Ren'Py（最可靠，目录组合唯一）----
    if "game" in dnames_l:
        rt = {"lib", "mono", "monobleedingedge", "python", "renpy"}
        if rt & set(dnames_l):
            hit = sorted(rt & set(dnames_l))[0]
            return "C", "Ren'Py", "game/ + %s/" % hit
    # Ren'Py 三件套
    pys = [f for n, f in names.items() if n.endswith(".py")]
    shs = [f for n, f in names.items() if n.endswith(".sh")]
    if pys and shs:
        return "C", "Ren'Py", "*.py + *.sh 三件套"

    # ---- D 类：Unity ----
    for d in dirs:
        if re.match(r"(?i)^.+_data$", d.name) and d.name.lower() != "gamedata":
            return "D", "Unity", "%s/" % d.name
    for key in ("unityplayer.dll", "app.info", "globalgamemanagers",
                "boot.config"):
        if key in names:
            return "D", "Unity", key

    # ---- B 类：RPG Maker ----
    for pat, ev in B_CLASS_SIGS["RPGMaker"]:
        for n, f in names.items():
            if pat.match(f.name) and f.stat().st_size > 64 * 1024:
                return "B", eng_of("RPGMaker"), f"{ev} ({f.stat().st_size//1024//1024}MB)"

    # ---- B 类补充：RPG Maker MV 被打包成 NW.js/Electron ----
    # 铁证：`data/` 目录里有 `Actors.json` / `Map001.json`（RPG Maker MV 数据格式）
    # 实测：精灵村 / お姉ちゃんに搾られックス６C / Bravery Ordeal 全是这个形态。
    data_dir = dnames_l.get("data")
    if data_dir and data_dir.is_dir():
        rpgmv = False
        try:
            dn = {f.name for f in data_dir.iterdir()}
            # RPG Maker MV 的标志性文件
            rpgmv = (bool(dn & {"Actors.json", "MapInfos.json",
                                "System.json", "CommonEvents.json"})
                     or any(re.match(r"^Map\d{3}\.json$", n) for n in dn))
        except OSError:
            pass
        if rpgmv:
            return "B", "RPGMakerMV", "data/ 内含 Actors.json/Map*.json"
        # 旧版：data/ 里是 .rpgmvo/.rpgmvp
        try:
            rpgmv = [f for f in data_dir.iterdir()
                     if f.suffix.lower() in (".rpgmvo", ".rpgmvp")]
            if rpgmv:
                return "B", "RPGMaker", "data/ 内 %d 个 .rpgmvo" % len(rpgmv)
        except OSError:
            pass

    # ---- NW.js 打包的 RPG Maker MV ----
    # 关键：`data/` 在 **www/ 里面**（NW.js 打包会把资源都塞进 www/）。
    # 实测「变成妈妈的身体v1.01」：www/data/Actors.json + www/js/rpg_core.js
    # 这是 RPG Maker MV 的**铁证**（rpg_core.js 是 MV 的核心脚本）。
    www = dnames_l.get("www")
    if www and www.is_dir():
        try:
            wsub = {d.name.lower(): d for d in www.iterdir() if d.is_dir()}
        except OSError:
            wsub = {}
        # 铁证 1：www/js/rpg_core.js
        js = wsub.get("js")
        if js and js.is_dir():
            try:
                if (js / "rpg_core.js").exists():
                    return "B", "RPGMakerMV/NW.js", "www/js/rpg_core.js（RPGM 核心脚本）"
            except OSError:
                pass
        # 铁证 2：www/data/ 里的 MV 数据文件
        wdata = wsub.get("data")
        if wdata and wdata.is_dir():
            try:
                dn = {f.name for f in wdata.iterdir()}
                if (dn & {"Actors.json", "MapInfos.json", "System.json"}
                        or any(re.match(r"^Map\d{3}\.json$", n) for n in dn)):
                    return "B", "RPGMakerMV/NW.js", "www/data/ 内含 Actors.json/Map*.json"
                if any(f.suffix.lower() in (".rpgmvo", ".rpgmvp") for f in wdata.iterdir()):
                    return "B", "RPGMaker/NW.js", "www/data/ 内含 .rpgmvo"
            except OSError:
                pass

        # 有 www/ 但不是 RPG Maker -> 厂商 galgame 用了 NW.js 外壳
        has_kr = any(n == "data.xp3" or n.endswith((".pfs", ".bgl", ".arc", ".bdb"))
                    for n in names)
        if has_kr:
            return "A", "KiriKiri/NW.js", "data.xp3 + www/ 外壳"
        # 顶层 *.rpgmvo（NW.js 之外的 RPGM 桌面版）
        try:
            if any(f.suffix.lower() in (".rpgmvo", ".rpgmvp")
                   for f in www.iterdir()):
                return "B", "RPGMaker", "www/ 内含 .rpgmvo"
        except OSError:
            pass
        return "E", "NW.js", "NW.js 外壳但无 RPG Maker 特征，引擎不明"

    # ---- BGI / BeE2 系列：封包分散在 bgm/ cg/ 等子目录 ----
    # 实测漏判：いつまでも息子のまま…4/ 有 bgm/*.eog + cg/*.gax + emotedriver.dll
    #          ANIM僕ママ/ 同样形态（先前是靠 K2sXg4VN 命中的）
    bg_ext = set()
    for dn in ("bgm", "cg", "bg", "se", "voice", "movie", "effect",
               "sys", "system"):
        sub = dnames_l.get(dn)
        if not sub or not sub.is_dir():
            continue
        try:
            for f in sub.iterdir():
                if f.is_file():
                    bg_ext.add(f.suffix.lower())
        except OSError:
            continue
    if bg_ext & {".gax", ".g00", ".eog", ".gyu"}:
        ev = sorted(bg_ext & {".gax", ".g00", ".eog", ".gyu"})[0]
        return "A", "BGI/BeE2", "子目录封包 %s" % ev

    # ---- EAGLS / SystemSprite：live.dll + *.pak ----
    if "live.dll" in names:
        pak = [n for n in names if n.endswith(".pak")]
        if pak:
            return "A", "EAGLS/SystemSprite", "live.dll + %d 个 .pak" % len(pak)
        return "A", "EAGLS/SystemSprite", "live.dll"

    # ---- ELFIMAGE 目录（AI6Win / 媚肉之香类）----
    elfi = dnames_l.get("elfimage")
    if elfi and elfi.is_dir():
        return "A", "ELF/EAGLS", "ELFIMAGE/ 目录"

    # ---- 封包在子目录里的传统引擎 ----
    sub_eng, sub_ev = detect_subdir_engine(dirs)
    if sub_eng:
        return "A", sub_eng, "子目录特征: %s" % sub_ev

    # ---- A 类：传统 galgame 引擎 ----
    for pat, ev in (A_CLASS_SIGS["KiriKiri"] + A_CLASS_SIGS["Artemis"]
                    + A_CLASS_SIGS["Ethornella"] + A_CLASS_SIGS["Eushully"]
                    + A_CLASS_SIGS["Majiro"] + A_CLASS_SIGS["NScripter"]
                    + A_CLASS_SIGS["SiglusEngine"]):
        for n, f in names.items():
            if NON_PACK.match(f.name):
                continue
            if pat.match(f.name):
                eng = engine_of_pat(pat)
                return "A", eng, "%s: %s" % (ev, f.name)
    # BGI 的 .arc/.gdb
    for n, f in names.items():
        if re.match(r"(?i)^(data|.*)\.(gdb|hvl|db)$", n) and f.stat().st_size > 1024 * 1024:
            return "A", "Ethornella/BGI", f.name

    # ---- 兜底：主程序 + 资源 ----
    from atomic_detect import pick_main_exe
    main_exe, all_exes, why = pick_main_exe(folder)
    res = [d.name for d in dirs
           if RESOURCE_DIR.match(d.name) or RESOURCE_DIR.match(d.name.lower())]
    if main_exe and res:
        # 有资源但引擎不明 -> 可能是 B 类（RPG Maker 无封包）或小型 A 类
        return "E", None, f"主程序[{main_exe}] + 资源目录 {res[:2]}，引擎不明"
        return "E", None, f"主程序[{main_exe}] + 资源目录，但引擎无法确定"

    if main_exe:
        return "E", None, why
    if dirs:
        return "container", None, f"含 {len(dirs)} 个子目录，无引擎指纹"
    return "unknown", None, "无法判定"


def eng_of(x):
    return x


def engine_of_pat(pat):
    pats = pat.pattern.lower()
    if "xp3" in pats:
        return "KiriKiri"
    if "pfs" in pats:
        return "Artemis"
    if "bgl" in pats or "xfl" in pats:
        return "Ethornella"
    if "noa" in pats:
        return "Eushully"
    if "arc" in pats:
        return "Majiro"
    if "nscr" in pats:
        return "NScripter"
    if "siglus" in pats:
        return "SiglusEngine"
    return "未知"


if __name__ == "__main__":
    import csv
    import sys
    from collections import Counter

    rows = list(csv.DictReader(
        open("G:/Game/.workbuddy/plan/plan-flat-zones-20261003-215652.csv",
             encoding="utf-8-sig")))
    CONT = {"Galgame", "renpy", "rpgmaker"}
    cand = [Path(x["src"].replace("\\", "/")) for x in rows
            if x["launcher"] and x["src_name"] not in CONT
            and Path(x["src"].replace("\\", "/")).is_dir()]

    print("样本池 %d\n" % len(cand))
    cat = Counter()
    engs = Counter()
    samples = {}
    for p in cand:
        c, e, why = classify_channel(p)
        cat[c] += 1
        if c in "ABCD":
            engs[(c, e)] += 1
            samples.setdefault(c, (p.name[:32], why[:44]))

    names = {"A": "A 厂商 galgame → VNDB",
             "B": "B RPG Maker → DLsite",
             "C": "C Ren'Py → itch.io",
             "D": "D Unity → Steam",
             "E": "E 找不到",
             "container": "  (合集层)",
             "tool": "  (工具/附件)",
             "unknown": "  (未定)"}
    print("=== 五类分布 ===")
    for k in "ABCDEcontainer tool unknown".split():
        if cat.get(k):
            print("  %-28s %3d  %s" % (names.get(k, k), cat[k],
                                      samples.get(k, ("", ""))[1][:40]))
    print()
    print("=== 引擎细分 ===")
    for (c, e), n in engs.most_common(14):
        print("  %s  %-22s %3d" % (c, e, n))
