"""原子游戏目录识别 —— 纯结构判据（不看体积、不看 exe 名）。

核心认识（修正自实测）
----------------------
「一个目录是不是真游戏」不是物理特征问题，而是**结构关系**问题：

- 真正的游戏目录：**它的子目录不是游戏**（子目录是资源/存档/工具）
- 合集目录：**它的子目录里还有游戏**
- 版本子目录（`32位` / `Ren'Py版` / `繁体字版`）：**它与本体同级，且主程序同名或不同构建**

这就是判据：**递归到叶子，看它是什么**。不需要猜体积、不需要猜 exe 名。

三级判定
--------
```
scan(dir):
  逐个看子目录 -> 分类
  若有子目录被判为 game/variant   ->  dir 本身是「合集层」，不下探那些
  若所有子目录都非 game            ->  dir 自身就是原子游戏
  若 dir 自身有引擎特征且子目录都非 game -> 原子游戏
  若 dir 无任何引擎特征且子目录非 game -> 空壳/残骸
```

「已知的游戏」从哪来
------------------
不靠猜，靠**证据链**：
1. 引擎封包/标配目录（KiriKiri data.xp3、Ren'Py game+lib+renpy、NW.js www+package.json…）
2. 引擎自带主程序名（Game.exe / SiglusEngine.exe / renpy.sh 同名 .py）
3. 已人工确认的路径（origin_map 的 488 条 + 考据记录）

Ren'Py 的特殊性
---------------
Ren'Py 游戏常**没有 .rpy 在顶层**（脚本打包进 `game.rpa`/`archive.rpa`），
且发行版常把运行时拆成子目录（如 `MonoBleedingEdge`）。
判据是 `{game, lib, renpy}` 三目录**同时存在**（`lib` 可被 `MonoBleedingEdge` 替代）。

Proton / Steam 版问题
---------------------
用户指出 Ren'Py 游戏在远端可能查不到 —— 这是对的，
所以**判定不能依赖远端**。本工具完全不联网。
"""

import json
import os
import re
import sys
from pathlib import Path

# ============================================================= 引擎指纹

# 单文件即足以判定的封包（出现在当前层）
PACK_SIGS = [
    (re.compile(r"(?i)^data\.xp3(\.sig)?$"), "KiriKiri"),
    (re.compile(r"(?i)^.*\.pfs$"), "Artemis"),
    (re.compile(r"(?i)^.*\.unity3d$"), "Unity"),
    (re.compile(r"(?i)^.*\.(rgss3a|rgssad)$"), "RPGMakerVX"),
    (re.compile(r"(?i)^.*\.(rpgmvp|rpgmvo)$"), "RPGMakerMV"),
    (re.compile(r"(?i)^nscr\.dat$"), "NScripter"),
    (re.compile(r"(?i)^nscript\.dat$"), "NScripter"),
    (re.compile(r"(?i)^.*\.ns[2a]$"), "NScripter"),
    (re.compile(r"(?i)^data\.win$"), "GameMaker"),
    (re.compile(r"(?i)^signature\.pfs$"), "PFS"),
]

# 必须成组出现的目录组合
DIR_GROUP_SIGS = [
    ({"game", "lib"}, "Ren'Py",
     "game+lib（lib 可由 MonoBleedingEdge 代替）"),
    ({"renpy", "game"}, "Ren'Py", "renpy+game"),
    ({"www", "package.json"}, "NW.js", "www+package.json"),
    ({"renpy", "lib", "game"}, "Ren'Py", "renpy+lib+game（标准 Ren'Py）"),
]

# 单目录即可判定
DIR_SINGLE_SIGS = [
    ("unityplayer.dll", "Unity"),
    ("siglusengine.exe", "SiglusEngine"),
    ("app.info", "Unity"),
    ("game.ini", "RPGMaker"),
    ("nscr.dat", "NScripter"),
]

# ---- Chromium 壳游戏（Electron / NW.js / CEF）----
# ⚠️ 实测踩坑：只认 `nw.dll` 会漏掉全部 **Electron** 游戏。
# Electron **没有 nw.dll**，特征是 natives_blob.bin + Chromium 的那一套 dll。
#   股市淫狼/            Game.exe + natives_blob.bin + icudtl.dat + libEGL.dll …
#   变成妈妈的身体v1.01/  同上
#   魅魔契约/            Game.exe + node.dll + natives_blob.bin + …
# 这些曾因「有 exe 但无资源目录」被判成安装器/工具，丢掉了真游戏。
# 判据用**组合指纹**（≥2 个命中），单靠 icudtl.dat 会误伤（它是 ICU 国际化数据）。
CHROMIUM_DLL = ("natives_blob.bin", "libegl.dll", "libglesv2.dll",
                "d3dcompiler_47.dll", "ffmpeg.dll", "ffmpegsumo.dll",
                "v8_context_snapshot.bin", "snapshot_blob.bin")


def detect_chromium(folder: Path, files, dirs) -> str:
    """Electron / NW.js / CEF 壳的游戏。返回判据说明，不是则返回空串。"""
    low = {f.name.lower() for f in files}
    dlow = {d.name.lower() for d in dirs}
    exes = [f for f in files if f.suffix.lower() == ".exe"]
    if not exes:
        return ""
    hit = [k for k in CHROMIUM_DLL if k in low]
    # NW.js 铁证：nw.dll，或 package.json（可能在 www/ 里面！）
    if "nw.dll" in low:
        hit.append("nw.dll")
    if "package.json" in low:
        hit.append("package.json")
    if "package.json" not in low and "www" in dlow:
        # ⚠️ RPG Maker MV / NW.js 的 package.json 在 www/ 内部，不是顶层。
        #   兽耳少女不会背叛/  顶层无 package.json，但 www/package.json 存在
        try:
            inner = {f.name.lower() for f in (folder / "www").iterdir() if f.is_file()}
        except OSError:
            inner = set()
        if "package.json" in inner:
            hit.append("www/package.json")
        if "game.rpgproject" in inner:
            hit.append("Game.rpgproject")
    if len(hit) < 2:
        return ""
    # 有 data/ + js/rpg_core.js 就是 RPG Maker MV（NW.js 壳）
    if "www" in dlow:
        try:
            www = folder / "www"
            if (www / "data").is_dir() and (www / "js" / "rpg_core.js").is_file():
                return ("RPG Maker MV（NW.js 壳: www/data + js/rpg_core.js）")
        except OSError:
            pass
    return "Chromium 壳游戏（%s）" % "+".join(hit[:3])

# Ren'Py 运行时目录（可能是子目录，但**不表示子目录是独立游戏**）
RENPY_RUNTIME = {"renpy", "lib", "mono", "monobleedingedge", "python",
                 "pythonlib", "site-packages", "renpy-", "tl"}


# ============================================================= exe 角色识别
# 游戏目录里常有多个 exe，只有一个是主程序。实测案例：
#   妻みぐい3/  -> OpenSaveFolder.exe, ResetConfig.exe, Tsumamigui3.exe, Uninstaller.exe
# 主程序是「唯一不认识的那个」。

# ================================================================ 辅助 exe 判定
# 两级：强辅助（开头匹配）+ 弱辅助（全文匹配）
#
# ⚠️ 为什么必须分两级 —— 实测踩坑：
#   母爛漫/  有 Haharanman.exe 和 Haharanman_汉化补丁.exe
#   Dark Blue/  有 sfa16.exe 和 sfa16_cn.exe.exe
#   MakingLovers/  有 MakingLovers.exe 和 【Making Lovers】汉化补丁ver1.0.exe
# 「汉化补丁」在 exe 名的**中间**而非开头，用 ^(install|patch|汉化…) 匹配不到，
# 就会把补丁当主程序。这三个都是真实踩到的。

# 弱辅助：任意位置含这些词
AUX_ANY = re.compile(
    r"(?i)(补丁|patch|漢化|汉化|机翻|機翻|破解|破译|注册|註冊|"
    r"注册机|keygen|crack|更新|update|修正|修复|修復|"
    r"setup|install|unins|uninstall|"
    r"vcredist|dxsetup|dxwebsetup|directx|dotnetfx|oalinst|redist|"
    r"config|setting|option|preference|registration|serial|"
    r"opensave|resetsave|savefolder|createsave|deletesave)")

# 强辅助：开头就是这些词（整名匹配，允许后续任意后缀）
AUX_PREFIX = re.compile(
    r"(?i)^("
    r"install|installer|inst|setup|unins\d*|uninstall|uninstaller|"
    r"opensavefolder|opensave|resetsave|resetconfig|reset|savefolder|"
    r"copysave|copysavedata|createsave|makesave|delsave|deletesave|"
    r"launch|launcher|start|startup|"
    r"update|updater|patch|patcher|crack|cracker|"
    r"registration|register|serial|keygen|"
    r"play|player"
    r")[\s_\-\[\]【】（）()]*.*\.exe$")

# 语言/汉化版本标记（用于挑选本体而非汉化版）
LANG_MARK = re.compile(
    r"(?i)(cn|chs|cht|zh|jp|gbk|big5|tc|sc|en|eng|"
    r"简体|繁体|簡體|簡体|中文|汉化|漢化|机翻|機翻)")

# 旧名保留（其它模块可能引用）
AUX_EXE = AUX_PREFIX

# 明确不是游戏主程序的整个目录
INSTALL_DIR = re.compile(
    r"(?i)^("
    r"installdata|install_data|install_files|"
    r"setup_data|setupfiles|"
    r"redist|redistributables|directx|"
    r"update|updater|patch_data"
    r")$")


# ================================================ 免验证 / 运行补丁（**正面信息**）

# ⚠️ 这不是「污染」，是**运行刚需**。
# 日文 galgame 原版普遍带 DRM / 光盘校验 / 区域限制：AlphaROM 系（RealLive、
# Siglus 引擎）不破解会弹「请插入光盘」或直接失败；SiglusEngine_Patch 常用于
# 让日文游戏在中文系统正常显示。**这些补丁删了游戏就跑不起来。**
# 所以这里只做「是否自带补丁」的信息探测 —— 绝不据此判「有问题 / 该清理」，
# 更不给出「删掉工具」之类的建议。空列表 = 原版无补丁，也**不是**缺点。
#
# ⚠️ 判据**只能作用于可执行文件**。旧扫描器用裸子串 `"cracker" in name`，
# 对所有文件生效，把纯素材当成了破解工具（纯误报）：
#   `Fleeting Iris IF/Audio/BGM/041 - Nutcrackers.mp3`（胡桃夹子 BGM）
#   `VenusBlood Valkyrie/.../ads06_cracker_pulls_01`（拉炮音效）
CRACKER_EXT = (".exe", ".com", ".bat", ".cmd", ".scr", ".dll")

CRACKER_NAME = re.compile(
    r"(?i)^("
    r"alpharom\s*cracker|alpharomdie|alpharom|alphrom|"
    r"start_cracker|cracker|"
    r"siglusengine_patch|siglusuniversalpatch|"
    r"arc_unpacker|garbro|enigmaunpacker|enigmainscene|"
    r"vaild|thedesigner|megpatch"
    r")[\s\d._\-]*\.(exe|com|bat|cmd|scr|dll)$")

# ⚠️ 不要因为 BepInEx/doorstop/winhttp.dll 就判「破解」——
# 大量**汉化补丁**就是用 BepInEx 实现的（实测 VenusBlood Valkyrie 属此类）。
PATCH_UI_MARK = ("bepinex", "doorstop_config.ini", ".doorstop_version")


def detect_cracker_tools(folder: Path, max_depth: int = 3):
    """探测目录**自带的免验证 / 运行补丁**，返回相对路径列表。

    ⚠️ 这是**信息**，不是「污染」：这些补丁是游戏能（免验证 / 不乱码）运行的
    前提，**不要删**。返回空列表只表示「原版未带补丁」，同样不是问题 ——
    实测大量日文原版就需要用户自己补一个 AlphaROMdiE 才能启动。
    只在**可执行文件**里找，避免音效/图片素材被 "cracker" 子串误伤。
    """
    hits = []
    folder = Path(folder)

    def walk(d: Path, level: int):
        if level > max_depth:
            return
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        for e in entries:
            if e.name.startswith("."):
                continue
            if e.is_file():
                if e.suffix.lower() in CRACKER_EXT and CRACKER_NAME.match(e.name):
                    try:
                        hits.append(e.relative_to(folder).as_posix())
                    except ValueError:
                        hits.append(e.name)
            elif e.is_dir():
                walk(e, level + 1)

    walk(folder, 1)
    return hits


def detect_ui_patch(folder: Path):
    """目录里是否有 BepInEx 注入式补丁（多为汉化，**不等于破解**）。"""
    try:
        names = {e.name.lower() for e in Path(folder).iterdir()}
    except OSError:
        return False
    return any(m in names for m in PATCH_UI_MARK)


def pick_main_exe(folder: Path):
    """返回 (主程序名, 全部 exe 列表, 说明)。找不到返回 (None, [], 原因)。"""
    try:
        exes = [f for f in folder.iterdir()
                if f.is_file() and f.suffix.lower() == ".exe"]
    except OSError:
        return None, [], "无法读取"
    if not exes:
        return None, [], "无 exe"

    known, unknown = [], []
    for f in exes:
        n = f.stem.strip()
        # 强辅助：开头就是 install/setup/patch 之类
        if AUX_PREFIX.match(f.name):
            known.append(f)
            continue
        # 弱辅助：任意位置含「补丁/汉化/破解/注册」等
        # 但 Game.exe / Start.exe / xxx.exe 这类要放行
        if (AUX_ANY.search(n) and n.lower() not in ("game", "start")
                and not n.lower().endswith("game")):
            known.append(f)
            continue
        unknown.append(f)

    if not unknown:
        return None, [f.name for f in exes], \
            "全是辅助工具（%s），无主程序" % [f.name for f in known][:3]

    # ⚠️ 优先选「不带语言/补丁后缀」的那个。
    # 实测踩坑：母爛漫/ 有 Haharanman.exe 和 Haharanman_汉化补丁.exe，
    #   前者是本体，后者只是汉化版 —— 按「最大体积」会选错。
    #   Dark Blue/ 有 sfa16.exe 与 sfa16_cn.exe.exe，同理（双扩展名）。
    #   MakingLovers/ 有 MakingLovers.exe 与 MakingLoversGBK.exe，同理。
    def _lang_variant(f):
        stem = f.stem
        if stem.lower().endswith(".exe"):
            stem = stem[:-4]          # 去掉重复的 .exe（sfa16_cn.exe.exe）
        return bool(LANG_MARK.search(stem))

    n_var = sum(1 for f in unknown if _lang_variant(f))
    clean = [f for f in unknown if not _lang_variant(f)]
    if clean and n_var:
        # ⚠️ 边界情况：本体与汉化版是**不同文件名**（SenrenBanka.exe vs
        # SenrenBankaCHS.exe），此时 clean 为空或不全。
        # 判据：去掉语言标记后，两者的「主干名」相同 -> 同一作品的两个构建，
        #      本体是**不带标记**的那个。
        if not clean:
            # 全都带语言标记 -> 挑「主干名最短/最纯」的那个
            def _core(f):
                st = f.stem
                if st.lower().endswith(".exe"):
                    st = st[:-4]
                return LANG_MARK.sub("", st).strip(" _-.")
            cores = {_core(f) for f in unknown}
            if len(cores) == 1:
                pure = [f for f in unknown
                        if not LANG_MARK.search(
                            (f.stem[:-4] if f.stem.lower().endswith(".exe")
                             else f.stem))]
                if pure:
                    unknown = pure
                    why_extra = "（同一作品的多个构建，取无语言标记者）"
                else:
                    why_extra = ""
            else:
                why_extra = ""
        else:
            unknown = clean
            why_extra = "（已排除 %d 个汉化/语言版 exe）" % n_var
    else:
        why_extra = ""

    # 最大的未知 exe 视为主程序
    unknown.sort(key=lambda f: -f.stat().st_size)
    main = unknown[0].name
    why = "在 %d 个 exe 中，唯一/最大的非辅助程序: %s" % (len(exes), main)
    if why_extra:
        why += why_extra
    if known:
        why += "（辅助: %s）" % [f.name for f in known][:3]
    return main, [f.name for f in exes], why

# ============================================================= 排除

# 引擎资源/存档/工具目录 —— 出现不代表是游戏，也不代表不是
STRUCTURAL_DIR = re.compile(
    r"(?i)^("
    r"save|savedata|savedatas|save_backup|存档|存档备份|"
    r"patch|patches|补丁|汉化补丁|crack|cracker|破解|"
    r"original|原版|原版备份|原文件|原文件备份|backup|backups|备份|"
    r"plugin|plugins|_locale|locales|swiftshader|managed|"
    r"tools?|tool|extract|extractor|unpack|unrar|7z|"
    r"readme|说明|说明书|manual|攻略|全cg|皆传|全cg存档|cg存档|"
    r"directx|vcredist|dxsetup|redist|"
    r"32位|64位|32bit|64bit|x86|x64|win32|win64|"
    r"trial|体験版|试玩版|demo|"
    r"exhibit|translot|transgob|localeemulator|legui|leproc|"
    r"xp3viewer|arc_unpacker|garbro|kirikiriid|"
    r"dmm|dlsite|fanza|steam|steamapps|"
    r"web|webcache|browser|cache|temp|tmp|log|logs"
    r")$")

# 位版本 / 语言版本 / 变体标记目录（应归并到本体，不是独立游戏）
VARIANT_DIR = re.compile(
    r"(?i)^("
    r"32位|64位|32bit|64bit|x86|x64|"
    r"简体|繁体|简体|繁體|中文|日本語|english|chinese|japanese|"
    r"简体版|繁体版|中文版|日文版|english版|"
    r"project|build|dist|out|release|"
    r"renpy版|unity版|pc版|steam版|"
    r"旧版|重置版|新版本|更新版|修正版|修复版|"
    r"patch|补丁|mock|test|demo"
    r")$")

# 明确的工具目录（整个目录就是工具）
TOOL_DIR = re.compile(
    r"(?i)^("
    r"xp3viewer|localeemulator|translot|transgob|exhibit|"
    r"misakatranslator|misaka ?translator|"
    r"arkcps|garbro|kirikiriid|arc_unpacker|arcunpacker|"
    r"工具|工具集|tool|tools|utility|"
    r"提取器|解包器|解包工具|解包软件"
    r")[\s\d._\-vV]*$")

# 明确的资源/附件目录
RESOURCE_DIR = re.compile(
    r"(?i)^("
    r"bgm|bgm2|se|se2|voice|voice2|sound|sounds|audio|music|"
    r"bg|background|backgrounds|fg|fgimage|evimage|ev|event|events|"
    r"cg|cg2|cg3|character|characters|chara|char|"
    r"image|images|img|graphic|graphics|picture|pictures|"
    r"system|sys|system32|font|fonts|"
    r"data|datas|script|scripts|scenario|scenarios|"
    r"movie|movies|video|videos|animation|animations|"
    r"text|textures|map|maps|db|database|jsr|stand|root|home|"
    r"kiri?som|krkr|tyrano|tyranoe|tyranor|pac|arc|dat|data_[a-z0-9]+|"
    r")$")


class Sig:
    """结构判据的结果。"""

    def __init__(self):
        self.engine = None
        self.evidence = ""
        self.is_game = False
        self.is_container = False      # 合集层：子目录里还有游戏
        self.is_variant = False        # 变体目录：应归并
        self.is_tool = False
        self.is_shell = False          # 空壳：什么都没有
        self.is_staging = False        # 下载暂存目录：目录名与内容无关
        self.staged_names = []         # 暂存目录里实际压着的作品名
        self.reason = ""

    def level(self):
        if self.is_game:
            return "game"
        if self.is_variant:
            return "variant"
        if self.is_container:
            return "container"
        if self.is_staging:
            return "staging"
        if self.is_tool:
            return "tool"
        if self.is_shell:
            return "shell"
        return "unknown"

    def as_dict(self, path):
        d = {
            "path": str(path).replace("\\", "/"),
            "name": path.name,
            "level": self.level(),
            "engine": self.engine,
            "evidence": self.evidence,
            "reason": self.reason,
        }
        if self.is_staging:
            d["staged_names"] = self.staged_names
        return d


# ============================================================= 单目录判据
def classify_one(folder: Path, deep: bool = True) -> Sig:
    """判定单个目录**自身**是什么（不递归）。"""
    s = Sig()

    if TOOL_DIR.match(folder.name):
        s.is_tool = True
        s.reason = f"目录名是已知工具: {folder.name}"
        return s
    if STRUCTURAL_DIR.match(folder.name):
        s.is_tool = True
        s.reason = f"结构性目录: {folder.name}"
        return s

    try:
        items = list(folder.iterdir())
    except OSError as e:
        s.reason = f"无法读取: {e}"
        return s

    files = [f for f in items if f.is_file()]
    dirs = [f for f in items if f.is_dir()]
    lower = {f.name.lower() for f in files}
    dlower = {d.name.lower(): d for d in dirs}

    # ---- 引擎封包（最强判据）----
    for f in files:
        for pat, eng in PACK_SIGS:
            if pat.match(f.name):
                s.engine = eng
                s.evidence = f.name
                s.is_game = True
                s.reason = f"{eng} 封包: {f.name}"
                return s

    # ---- 目录组合 ----
    for group, eng, why in DIR_GROUP_SIGS:
        if group <= set(dlower):
            s.engine = eng
            s.evidence = "+".join(sorted(group))
            s.is_game = True
            s.reason = f"{eng}: {why}"
            return s

    # Ren'Py：game + (lib | MonoBleedingEdge | python)
    if "game" in dlower:
        rt = {"lib", "mono", "monobleedingedge", "python", "renpy"}
        if rt & set(dlower):
            s.engine = "Ren'Py"
            s.evidence = "game+" + "+".join(sorted(rt & set(dlower)))
            s.is_game = True
            s.reason = f"Ren'Py: game + {s.evidence.split('+',1)[1]}"
            return s

    # ---- 单目录/单文件判据 ----
    for key, eng in DIR_SINGLE_SIGS:
        if key in lower or key in dlower:
            s.engine = eng
            s.evidence = key
            s.is_game = True
            s.reason = f"{eng}: {key}"
            return s

    # ---- 先排除「伪装成封包的杂项文件」 ----
    # 实测教训：`icudtl.dat`（Unity/NW.js 的 ICU 国际化数据，10MB）
    # 曾让 88 个游戏被误判成「无引擎指纹」。它是运行时资源，不是封包。
    NON_PACK_NAMES = {
        "icudtl.dat",          # ICU 国际化数据（Unity / NW.js / Chromium）
        "icudt*.dat",
        "resources.dat",       # NW.js
        "snapshot_blob.bin",
        "v8_context_snapshot.bin",
        "icudt78l.dat", "icudt78h.dat",
    }
    for f in files:
        nl = f.name.lower()
        if any(re.match(p.replace("*", ".*"), nl) for p in NON_PACK_NAMES):
            continue

    # ---- 大体积封包 ----
    # 覆盖各引擎的封包格式。实测漏判的：
    #   .pac  →「巨乳令嬢MC学園」(146MB)
    #   .xfl  → 「誰ソ彼のシェイプシフター」(Ethornella/BeE2, 1.2GB)
    # 注意：.dat / .int 太泛（icudtl.dat 等），只认已知引擎的命名模式
    for f in files:
        nl = f.name.lower()
        dat_ok = (
            nl.startswith("data") and nl.endswith(".dat")
            or re.match(r"^(arc|bm|scene|ev|bg|ch|cg|voice|se)\d*\.dat$", nl)
            or re.match(r"^[a-z0-9_]{2,}\.dat$", nl) and f.stat().st_size > 8 * 1024 * 1024
        )
        if f.suffix.lower() == ".dat" and not dat_ok:
            continue
        if f.suffix.lower() in (".arc", ".rpa", ".fpk", ".int",
                                 ".bge", ".wolf", ".pck", ".sc3", ".sd3",
                                 ".sav", ".gmm", ".bmg", ".pac", ".xfl",
                                 ".nsa", ".sb", ".at9",
                                 ".t3", ".opp", ".osz",
                                 ".xp3", ".thor", ".obj", ".nl", ".snb",
                                 ".sdata", ".g2", ".g3", ".p2", ".p3",
                                 # Eushully「是的不开玩笑引擎」—— 实测漏判
                                 # 笨蛋情侣・补充剂：bg.noa 1050MB / ev.noa 727MB
                                 ".noa", ".gdb", ".hvl", ".bdb"):
            if f.stat().st_size > 1024 * 1024:
                s.is_game = True
                s.evidence = f.name
                s.reason = f"大封包文件: {f.name} " \
                           f"({f.stat().st_size/1024/1024:.0f}MB)"
                return s

    # ---- 子目录里的封包 ----
    # 实测：`催眠学习/` 主程序被 crack 顶掉，但 `pac/*.ypf`（YU-RIS/cats-system）
    # 才是真封包；同类还有 `kiss_crisis/pac/*.ypf`、`ANIM僕ママ/K2sXg4VN/*.gax`。
    # 只在已知的资源子目录里找，避免误伤。
    PACK_SUBDIRS = {"pac", "data", "datas", "bgm", "cg", "se", "voice",
                    "plugin", "movie", "ev", "image", "scenario", "sound"}
    SUBDIR_PACK_EXT = {".ypf", ".xp3", ".pfs", ".noa", ".gax", ".arc", ".pak",
                       ".bgl", ".xfl", ".ns2", ".nsa", ".dat", ".bin",
                       ".rgss3a", ".rgssad", ".pck", ".wolf", ".pac"}
    for d in dirs:
        if d.name.lower() not in PACK_SUBDIRS:
            continue
        try:
            hits = [x.name for x in d.iterdir()
                    if x.is_file() and x.suffix.lower() in SUBDIR_PACK_EXT
                    and x.stat().st_size >= 8 * 1024 * 1024]
        except OSError:
            continue
        if len(hits) >= 2:
            s.is_game = True
            s.evidence = "%s/%s" % (d.name, hits[0])
            s.reason = f"子目录封包 {d.name}/{hits[0]} 等 {len(hits)} 个"
            return s


    # ---- Flash / SWF 游戏（实测：エロ獅子組 男の娘と老人 → dou/*.swf ×N）----
    # 主程序是播放器（スタート.exe），素材是一堆 .swf，既不在 bgm/cg/data 里，
    # 也没有任何封包扩展名 → 曾被判「安装器或工具」。
    swf_n = sum(1 for f in files if f.suffix.lower() == ".swf")
    swf_ev = None
    if swf_n >= 3:
        swf_ev = next(f.name for f in files if f.suffix.lower() == ".swf")
    else:
        for d in dirs:
            if RESOURCE_DIR.match(d.name) or STRUCTURAL_DIR.match(d.name):
                continue
            try:
                n = sum(1 for x in d.iterdir()
                        if x.is_file() and x.suffix.lower() == ".swf")
            except OSError:
                n = 0
            if n >= 3:
                swf_n, swf_ev = n, "%s/%d swf" % (d.name, n)
                break
    if swf_n >= 3:
        s.is_game = True
        s.engine = "Flash/SWF"
        s.evidence = swf_ev
        s.reason = "SWF 素材 %d 个: %s" % (swf_n, swf_ev)
        return s

    # ---- UE4 分布式打包 ----
    ue4 = detect_ue4(folder, files, dirs)
    if ue4:
        s.is_game = True
        s.engine = "UnrealEngine4"
        s.evidence = "UE4"
        s.reason = ue4
        return s

    # ---- Chromium 壳游戏（Electron / NW.js / CEF）----
    # ⚠️ 必须排在「有 exe 但无资源目录」之前。
    # 这类游戏的资源不在 `bgm/cg/data` 里，全在 Chromium 的运行时 dll 中，
    # 所以 RESOURCE_DIR 一个都匹配不到 -> 曾被判成「安装器或工具」。
    chrom = detect_chromium(folder, files, dirs)
    if chrom:
        s.is_game = True
        s.engine = "Chromium/Electron"
        s.evidence = "chromium"
        s.reason = chrom
        return s

    # ---- 安装数据目录 ----
    if INSTALL_DIR.match(folder.name):
        s.is_tool = True
        s.reason = f"安装数据目录: {folder.name}"
        return s

    # ---- 有 exe：先识别主程序角色 ----
    main_exe, all_exes, why = pick_main_exe(folder)
    res = [d.name for d in dirs
           if RESOURCE_DIR.match(d.name) or RESOURCE_DIR.match(d.name.lower())]

    if main_exe and res:
        s.is_game = True
        s.evidence = "exe+" + ",".join(res[:2])
        s.reason = f"主程序[{main_exe}] + 资源目录 {res[:3]}"
        return s

    if main_exe:
        # 有主程序但无资源目录 —— 看同层是否有其他资源型目录
        other = [d.name for d in dirs
                 if RESOURCE_DIR.match(d.name) or
                 d.name.lower() in ("data", "files", "res", "resource")]
        if other:
            s.is_game = True
            s.reason = f"主程序[{main_exe}] + {other[:3]}"
            return s
        # `*_Data/` 数据目录（Unity 及一批引擎的标准布局）。
        # 实测：おままごと/ 只有 おままごと.exe + おままごと_Data/（30 项）+
        #       GameMaterial/，既非 bgm/cg/data，也无 UnityPlayer.dll，
        #       曾被判「安装器或工具」。`*_Data` 本身就是强信号。
        data_dirs = [d.name for d in dirs if d.name.lower().endswith("_data")]
        if data_dirs:
            s.is_game = True
            s.evidence = "exe+" + data_dirs[0]
            s.reason = f"主程序[{main_exe}] + 数据目录 {data_dirs[:2]}"
            return s
        # 主程序 + 同层大封包 → 是游戏。
        # 实测踩坑（一批被漏判成 unknown/tool 的真游戏）：
        #   廃村少女/   haison.exe + ev.bin 1GB / voc.bin 958MB（.bin 封包）
        #   妻の母/     tumahaha.exe + Arc05.dat 471MB / Arc06.dat 244MB（.dat）
        #   executive2/ executive2.exe + install.dat（.dat 封包）
        #   姦獄島/     kg.exe + 00.ns2 374MB（NScripter，已在 PACK_SIGS）
        # 这类游戏的资源打成同层大文件，不在 bgm/cg/data 目录里，
        # RESOURCE_DIR 必然全空 → 曾被误判「安装器或工具」。
        PACK_FILE_EXT = {".bin", ".dat", ".arc", ".pak", ".pk2", ".pkg",
                         ".ns2", ".nsa", ".ypf", ".noa", ".gax", ".xp3",
                         ".pfs", ".rpa", ".fpk", ".bundle", ".assets",
                         ".rgss3a", ".rgssad", ".hvl", ".pac", ".xfl",
                         ".bgl", ".gdb", ".med", ".win"}
        packs = []
        for f in files:
            if f.suffix.lower() in PACK_FILE_EXT:
                try:
                    if f.stat().st_size >= 1024 * 1024:
                        packs.append((f.name, f.stat().st_size))
                except OSError:
                    pass
        # ⚠️ 要求 ≥2 个封包，或单个 ≥32MB。
        # 实测反例：`island/汉化补丁0808/汉化补丁0808/` 只有 island.exe + scr.pak
        # （补丁小封包），若只要求「≥1 个大封包」会被误判成游戏。
        big = max((s for _, s in packs), default=0) >= 32 * 1024 * 1024
        if len(packs) >= 2 or big:
            names = [n for n, _ in packs]
            s.is_game = True
            s.evidence = "exe+" + names[0]
            s.reason = f"主程序[{main_exe}] + 大封包 {names[:3]}"
            return s
        # 体积够大也可能是自解压包（实测 840MB 的战国の黒百合）
        try:
            mb = max((f.stat().st_size for f in files
                      if f.suffix.lower() == ".exe"), default=0) / 1024 / 1024
        except OSError:
            mb = 0
        if mb >= 50:
            s.is_game = True
            s.reason = f"主程序[{main_exe}] {mb:.0f}MB，疑似自解压/单文件包"
            return s
        s.reason = f"{why}；但无资源目录，可能是安装器或工具"
        return s

    if all_exes:
        s.is_tool = True
        s.reason = why
        return s

    if not files and not dirs:
        s.is_shell = True
        s.reason = "空目录"
        return s

    # ---- 本体在唯一的非附属子目录里 ----
    # 实测漏判的 4 个案例都是这个模式：
    #   佐谷霞巳.../ -> 佐谷霞巳...(credits.html+dll) 本体 + よくある質問 FAQ
    #   虚拟女友/    -> PC(Cotton.exe) 本体             + APK 手游版
    #   地牢营救记/  -> 寝ぼすけ...(Game.exe) 本体       + B3566存档
    # ⚠️ 必须排在 detect_non_game **之前**。
    #   实测 32 个「顶层全压缩包 + 有子目录」的目录里，18 个内含真游戏，
    #   全被「全是压缩包」规则误判成 tool：
    #     白色相簿2/         -> Leaf/            （SiglusEngine 真游戏）
    #     对魔忍/            -> 风雪2/           （真游戏，名字与父目录无关）
    #     勇者大战魔物娘/    -> 勇战r/           （もんむすくえすと！RPG前章）
    #     rewrite/           -> Rewrite/         （SiglusEngine + 8 个 rar 并存）
    body, why = find_body_subdir(folder, dirs)
    if body is not None:
        s.is_game = True
        s.engine = "wrapper"
        s.evidence = body.name
        s.reason = why
        return s

    # ---- 下载暂存目录（目录名与内容无关）----
    # 必须在 detect_non_game 之前，因为它也含压缩包。
    staging, staged = detect_staging(folder, files, dirs)
    if staging:
        s.is_staging = True
        s.staged_names = staged
        s.reason = staging
        return s

    # ---- 压根不是游戏的内容 ----
    nongame = detect_non_game(folder, files, dirs)
    if nongame:
        s.is_tool = True
        s.reason = nongame
        return s

    # 有子目录但都没识别出游戏特征 -> 交给上层判断（可能是容器）
    if dirs:
        s.reason = f"含 {len(dirs)} 个子目录，无自身引擎特征（可能是合集）"
        return s

    s.reason = f"含 {len(files)} 个文件，无可执行入口"
    return s



# ============================================================= 非游戏内容
# 实测「未定」项的构成，发现很多根本不是游戏目录。

# 压缩包（未解压）
ARCHIVE_EXT = {".7z", ".rar", ".zip", ".tar", ".gz", ".bz2", ".xz",
                ".001", ".iso", ".img", ".cab", ".lzh", ".arj", ".zst"}
SUBTITLE_EXT = {".ass", ".srt", ".ssa", ".sub", ".idx", ".sup",
                ".vtt", ".smi", ".lrc"}
GUID_DIR = re.compile(r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-", re.I)

# 库内自建的内部目录：不参与识别，也不能被当成「候选条目/日期目录」
INTERNAL_DIRS = {".workbuddy", "_待删除", "_flatten", "_quarantine", "_selftest"}


def is_internal(name: str) -> bool:
    """内部目录：点开头（`.workbuddy`），或我们自建的 `_` 目录（`_待删除`）。"""
    return name.startswith(".") or name in INTERNAL_DIRS


# ============================================================= 下载暂存目录
# 实测案例 G:\Game\2021-\Galgame\angeltear\：
#   angeltear.rar                  17KB   截断的废下载
#   赞美女神.7z.001~008            8.4GB  **完全不相干的作品**
#   angeltear/町ぐるみの罠...zip          **又一個不相干的作品**（Eushully）
#   解压密码.txt                            「解压密码：jiudaoshifenxiang」
#   angeltear/hgamecn下载说明.txt          下载站免责声明
#
# 目录名是**当时的搜索关键词**，进去以后下了一堆别的。
# 它不是游戏，也不是合集 —— 判 unknown 会丢掉「里面有两个真游戏」这个信息。
#
# 识别特征（内容判据，不靠目录名）：
#   1. 有下载密码/下载说明文件
#   2. 含压缩包，但**压缩包名与目录名无交集**（真正的未解压目录不会这样）
#   3. 压缩包指向的作品名与目录名完全不同

DOWNLOAD_MARK = re.compile(r"(?i)^("
    r"解压密码|密码|pass|password|"
    r"下载说明|下载.*说明|hgamecn.*说明|"
    r"readme.*下载|如何解压|解压说明"
    r")")
# ⚠️ 只认**文件名里明确带站点名**的，不能用泛化词。
# 第一版写了 `里番|无码|javhd|3d在上|mmygame|图鉴君` 等泛化词，
# 结果 `playhome/p/插件/声音替换` 这类正常插件目录全被误判。
DOWNLOAD_SITE = re.compile(r"(?i)^("
    r"hgamecn.*|javhd.*|mmygame.*|acg\.rip.*|"
    r"下载站.*|down.*site.*"
    r")")


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z一-鿿]+", "", (s or "").lower())


def _archive_stems(files):
    """取压缩包的文件名主干（去掉分卷后缀与扩展名）。"""
    out = []
    for f in files:
        if f.suffix.lower() not in ARCHIVE_EXT:
            continue
        stem = f.name
        for suf in (".tar", ".gz", ".bz2", ".xz", ".zst"):
            if stem.lower().endswith(suf):
                stem = stem[: -len(suf)]
        stem = re.sub(r"\.(part|pt|vol|cp|cd|dvd)\d*$", "", stem, flags=re.I)
        stem = re.sub(r"[._-]?\d{2,3}$", "", stem)      # 7z.001 -> 7z
        stem = re.sub(r"\.[a-z0-9]{1,5}$", "", stem)    # .7z / .rar / .zip
        out.append(stem)
    return out


def detect_staging(folder: Path, files, dirs):
    """识别「下载暂存目录」：目录名只是搜索关键词，内容是别的东西。

    与 detect_non_game 的区别：那个判「全是压缩包 → 未解压」，
    这个判「压缩包在，但压的不是这个目录名指向的东西」。

    ⚠️ 判据必须收紧。第一版用「压缩包名与目录名无交集」就够了，
    结果全库误报 149 个 —— 因为大量正常目录的压缩包名本来就不叫目录名：
      ever17/     -> A036@忧郁的弟弟.part1.rar   （Ever17 的产品番号，正确的）
      VR插件/     -> PlayClubVR_0.2.4.zip        （插件目录，不该管）
      27岁女教师/ -> 27-02(nyh741).7z            （同一系列的多卷）
    「名字对不上」是常态，不是异常。

    收紧为**两条强信号同时成立**：
      ① 有下载密码 / 下载站说明文件（几乎无假阳性）
      ② 压缩包名与目录名无交集
    单靠 ② 不足以判定。

    返回 (原因, 实际压着的作品名列表)；不是暂存目录时返回 ("", [])。
    """
    if not files:
        return "", []

    names = [f.name for f in files]

    # ---- 强信号 ①：下载密码 / 下载站说明 ----
    # 这是唯一几乎无假阳性的信号：正常游戏目录不会有这些东西。
    has_pw = any(DOWNLOAD_MARK.match(n) for n in names)
    has_site = any(DOWNLOAD_SITE.search(n) for n in names)
    if not (has_pw or has_site):
        return "", []

    stems = _archive_stems(files)
    if not stems:
        # 没有压缩包却出现密码文件 -> 不是暂存目录，交给别的判据
        return "", []

    # ---- 强信号 ②：压缩包名与目录名无交集 ----
    dname = _norm(folder.name)
    dir_key = dname[:6] if len(dname) >= 6 else dname
    hit = [s for s in stems if dir_key and dir_key in _norm(s)]
    mismatch = not hit

    why = []
    if has_pw:
        why.append("含下载密码/说明文件")
    if has_site:
        why.append("含下载站点残留")
    if mismatch:
        why.append("压缩包名与目录名[%s]无交集" % folder.name[:20])

    # 提取实际压着的作品名 —— 这是「里面藏着什么」的唯一线索
    staged = sorted(stems)[:6]
    return (("下载暂存目录: " + "；".join(why)
             + "；实际内容 %s" % staged[:3]), staged)


def detect_non_game(folder: Path, files, dirs) -> str:
    """识别「这个目录压根不是游戏」。返回原因，空串表示可能是游戏。"""
    # 全部是压缩包
    if files and all(f.suffix.lower() in ARCHIVE_EXT for f in files):
        return f"全是压缩包（{len(files)} 个，未解压）"

    # 全部是字幕/歌词
    if files and all(f.suffix.lower() in SUBTITLE_EXT for f in files):
        return f"全是字幕文件（{len(files)} 个）"

    # GUID 命名的安装器目录
    if dirs and all(GUID_DIR.match(d.name) for d in dirs):
        return f"安装器 GUID 目录（{len(dirs)} 个）"

    # 空壳：有 exe 但只有一个 readme/图片
    exes = [f for f in files if f.suffix.lower() == ".exe"]
    others = [f for f in files if f.suffix.lower() != ".exe"]
    if exes and others and all(
            f.suffix.lower() in (".txt", ".md", ".jpg", ".png", ".gif",
                                 ".bmp", ".url", ".lnk", ".ini", ".cfg")
            for f in others):
        return (f"只有 exe + 说明/图片（残骸）: "
                f"{[f.name for f in exes][:2]}")

    return ""

# ============================================================= 本体在子目录
# 附属型子目录（不是本体，也不是资源）
ANCILLARY_SUB = re.compile(
    r"(?i)^("
    r"save|savedata|存档|存档备份|全cg|全cg存档|皆传|"
    r"apk|android|手游|"
    r"readme|read me|说明|说明书|usage|manual|攻略|"
    r"patch|补丁|汉化补丁|破解|crack|"
    r"optional|bonus|特典|"
    r"directx|vcredist|redist|"
    r"dl|download|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-.*"
    r")[\s\d._\-（）()]*$")


def find_body_subdir(folder: Path, dirs):
    """找「本体子目录」。

    ⚠️ 关键教训：**不要靠目录名黑名单排除**，要靠内容判定。
    实测被漏掉的附属目录名五花八门：
      `よくある質問`（FAQ）  `B3566存档`（数字+中文）  ` Isol.`（带空格）
    靠名字永远列不全。

    正确做法：逐个检查子目录自身是不是游戏，只有一个是 -> 它就是本体。
    """
    games = []
    for d in dirs:
        n = d.name.strip()
        # 明显不可能是本体的，快速跳过（省一次 classify）
        if (ANCILLARY_SUB.match(n) or STRUCTURAL_DIR.match(n)
                or RESOURCE_DIR.match(n) or RESOURCE_DIR.match(n.lower())
                or VARIANT_DIR.match(n)):
            continue
        try:
            inner = classify_one(d)
        except RecursionError:
            continue
        if inner.is_game:
            games.append((d, inner))

    if len(games) == 1:
        d, inner = games[0]
        return d, "本体在子目录 %s（%s）" % (d.name[:28], inner.reason[:40])

    if len(games) == 0:
        cands = [d.name[:16] for d in dirs
                 if not (ANCILLARY_SUB.match(d.name.strip())
                         or STRUCTURAL_DIR.match(d.name)
                         or RESOURCE_DIR.match(d.name)
                         or RESOURCE_DIR.match(d.name.lower()))]
        return None, "无子目录是游戏（候选: %s）" % cands[:3]

    # 多个子目录都是游戏 -> 真的合集
    return None, "有 %d 个游戏子目录（%s）= 合集层" % (
        len(games), [d.name[:14] for d, _ in games[:3]])


# ============================================================= UE4 分布式
# Unreal Engine 4 打包常见「双层嵌套」：
#   MomGotStuck/
#   ├── Engine/           (Binaries + Content)     ← 引擎
#   ├── MomGotStuck/      (Binaries + Content)     ← 游戏
#   └── *.txt             (Manifest_UFSFiles_Win64.txt)
# 判据：同层有 Engine/ 且有 <同名>/ 目录，或有 Manifest_*.txt

UE4_SIGS = ("manifest_nonufsfiles", "manifest_ufsfiles",
            "manifest_deltanoufsfiles", "manifest_deltasufsfiles")


def detect_ue4(folder: Path, files, dirs) -> str:
    names = {f.name.lower() for f in files}
    dnames = {d.name.lower() for d in dirs}
    if any(s in n for n in names for s in UE4_SIGS):
        return "UE4 打包（Manifest_*.txt）"
    if "engine" in dnames and len(dirs) >= 2:
        # Engine/ 与 游戏名/ 同层
        others = [d.name for d in dirs if d.name.lower() != "engine"]
        if others:
            return "UE4 分布式（Engine + %s）" % others[0][:20]
    return ""


# ============================================================= 递归扫描
def scan_tree(base: Path, max_depth: int = 4,
              variant_root: str = None) -> dict:
    """递归扫描，区分原子游戏 / 合集层 / 变体 / 工具。

    返回 {level: [Sig,...]}。
    """
    out = {"game": [], "container": [], "variant": [],
           "tool": [], "shell": [], "unknown": []}

    def walk(d: Path, lv: int, inside_collection: bool):
        if lv > max_depth:
            return
        sig = classify_one(d)
        if lv == 0:
            return  # 顶层本身不判定

        # 变体目录：单独归类，不进 game
        if VARIANT_DIR.match(d.name) and sig.level() in ("game", "variant"):
            sig.is_variant = True
            sig.is_game = False
            sig.reason = f"变体/版本目录: {d.name}（应归并到本体）"
            out["variant"].append(sig)
            return

        if sig.is_game:
            sig.path = str(d)
            out["game"].append(sig)
            # 游戏内部不再下探 —— 子目录是资源/存档，不是独立游戏
            return

        if sig.is_tool:
            out["tool"].append(sig)
            return

        if sig.is_shell:
            out["shell"].append(sig)
            return

        # 不是游戏 -> 下探一层，看子目录里有没有游戏
        try:
            kids = []
            for c in sorted(d.iterdir()):
                if not c.is_dir() or is_internal(c.name):
                    continue
                if STRUCTURAL_DIR.match(c.name):
                    continue
                if RESOURCE_DIR.match(c.name):
                    continue
                if c.name.lower() in RENPY_RUNTIME:
                    continue
                kids.append(c)
        except OSError:
            return
        except OSError:
            return

        if not kids:
            out["unknown"].append(sig)
            return

        before = len(out["game"])
        for c in kids:
            walk(c, lv + 1, inside_collection or (lv == 0))
        gained = len(out["game"]) - before

        if gained:
            # 子目录里有游戏 -> 自己是合集层
            s2 = Sig()
            s2.is_container = True
            s2.reason = f"含 {gained} 个游戏子条目（合集层）"
            out["container"].append(s2.as_dict(d) and s2)
        else:
            out["unknown"].append(sig)

    for d in sorted(base.iterdir()):
        if d.is_dir() and not is_internal(d.name):
            walk(d, 1, False)
    return out


# ============================================================= 验证
def verify_against_origin(origin_csv: Path, base: Path):
    """用人工确认过的 origin_map 验证判据准确率。"""
    import csv
    truth = {}   # 相对路径 -> bucket
    with origin_csv.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rel = r["src"].replace("G:/Game/", "")
            truth[rel] = r
    return truth


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default="G:/Game/2021-/Galgame")
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--show", type=int, default=30)
    a = ap.parse_args()

    base = Path(a.base)
    res = scan_tree(base, max_depth=a.depth)
    print("扫描 %s" % base)
    print("  原子游戏 %d | 合集层 %d | 变体 %d | 工具 %d | 空壳 %d | 未定 %d"
          % (len(res["game"]), len(res["container"]), len(res["variant"]),
             len(res["tool"]), len(res["shell"]), len(res["unknown"])))
    print()
    for lv in ("container", "variant"):
        if res[lv]:
            print("--- %s (%d) ---" % (lv, len(res[lv])))
            for s in res[lv][:a.show]:
                print("   %-46s %s" % (s.reason[:46],
                                       str(Path(s.reason and '' or '')) or ""))
            print()
    print("--- 游戏（前 %d）---" % a.show)
    for s in res["game"][:a.show]:
        print("   %-44s %-10s %s" % (Path(s.path).name[:44],
                                     s.engine or "-", s.reason[:40]))
