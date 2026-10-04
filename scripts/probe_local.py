"""本地证据抽取器 —— 从游戏目录里找出作品名与元数据。

只读，绝不修改任何文件。

用法：
    python probe_local.py <游戏目录> [<游戏目录> ...]
    python probe_local.py --json <游戏目录>          # 输出 JSON
    python probe_local.py --depth 3 <游戏目录>        # 递归深度
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# ------------------------------------------------------------ 编码探测

BOMS = [
    (b'\xff\xfe', 'utf-16-le'),
    (b'\xfe\xff', 'utf-16-be'),
    (b'\xef\xbb\xbf', 'utf-8-sig'),
    (b'\xff\xfe\x00\x00', 'utf-32-le'),
    (b'\x00\x00\xfe\xff', 'utf-32-be'),
]

CANDIDATE_ENC = ('utf-8', 'cp932', 'gbk', 'big5', 'euc-jp', 'cp949')


def read_text_safe(path: Path, max_bytes: int = 16384) -> tuple[str, str]:
    """返回 (文本, 编码名)。按 BOM 和内容有效性探测编码。"""
    try:
        data = path.read_bytes()[:max_bytes]
    except OSError:
        return '', ''
    if not data:
        return '', ''

    for bom, enc in BOMS:
        if data.startswith(bom):
            try:
                return data.decode(enc, errors='ignore'), enc
            except UnicodeDecodeError:
                continue

    # NUL 字节出现在奇数位或偶数位 -> 无 BOM 的 UTF-16
    # 关键：不能只看 NUL 数量，必须看 NUL 的位置分布。
    # UTF-16LE ASCII 文本：偶数位是字符，奇数位是 0
    # UTF-16BE ASCII 文本：奇数位是字符，偶数位是 0
    head = data[:600]
    if head.count(0) > len(head) * 0.1:
        even_null = sum(1 for i in range(0, min(len(head), 400), 2)
                        if head[i] == 0)
        odd_null = sum(1 for i in range(1, min(len(head), 400), 2)
                       if head[i] == 0)
        n = min(len(head), 400) // 2
        if n and (odd_null / n) > 0.6 and even_null / n < 0.2:
            try:
                t = data.decode('utf-16-le', errors='strict')
                if '\ufffd' not in t:
                    return t, 'utf-16-le'
            except UnicodeDecodeError:
                pass
        elif n and (even_null / n) > 0.6 and odd_null / n < 0.2:
            try:
                t = data.decode('utf-16-be', errors='strict')
                if '\ufffd' not in t:
                    return t, 'utf-16-be'
            except UnicodeDecodeError:
                pass
        # 位置分布不明确时也试一次（有些文件 NUL 不那么规整）
        for enc in ('utf-16-le', 'utf-16-be'):
            try:
                t = data.decode(enc, errors='strict')
                if '\ufffd' not in t:
                    return t, enc
            except UnicodeDecodeError:
                continue

    best, best_score, best_enc = '', -1e9, ''
    for enc in CANDIDATE_ENC:
        try:
            t = data.decode(enc, errors='strict')
        except (UnicodeDecodeError, LookupError):
            continue
        kana = sum(1 for c in t if '\u3040' <= c <= '\u30ff')
        cjk = sum(1 for c in t if '\u4e00' <= c <= '\u9fff')
        latin = sum(1 for c in t if c.isascii() and c.isalpha())
        bad = t.count('\ufffd') * 20
        # 非文本文件（大量控制字符）直接排除
        ctrl = sum(1 for c in t if ord(c) < 9) * 5
        score = kana * 3 + cjk * 2 + latin * 0.1 - bad - ctrl
        if score > best_score:
            best, best_score, best_enc = t, score, enc

    if best_score < -20:
        return '', ''
    return best, best_enc


# ------------------------------------------------------------ 引擎指纹

EXE_NOISE = re.compile(
    r"(?i)^(unins|uninstall|setup|install|config|setting|save|load|"
    r"update|patch|unrar|winrar|7z|extract|explorer|cmd|regedit|"
    r"crashpad|notification_helper|unitycrashhandler|"
    r"bugreport|report|check|launcher|更新|檢查)")

ENGINE_SIGS = [
    (re.compile(r"(?i)^Game\.ini$"), "RPGMaker", None),
    (re.compile(r"(?i)\.rgss3a$"), "RPGMakerVXAce", None),
    (re.compile(r"(?i)\.rpgmvp$"), "RPGMaker", None),
    (re.compile(r"(?i)\.xp3$"), "KiriKiri", None),
    (re.compile(r"(?i)\.pfs$"), "Artemis", None),
    (re.compile(r"(?i)^SiglusEngine\.exe$"), "SiglusEngine", None),
    (re.compile(r"(?i)\.bgl$"), "Ethornella", None),
    (re.compile(r"(?i)^nscr\.dat$"), "NScripter", None),
    (re.compile(r"(?i)^tyrano\.js$"), "TyranoScript", None),
]

NWJS_SIGS = ['nw.dll', 'package.json']
RENPY_SIGS = ['renpy.py', 'renpy/']
ENGINE_DIRS = {
    'renpy': 'RenPy', 'www': 'RPGMaker', 'data': 'KiriKiri',
    'tyrano': 'TyranoScript', 'lib': 'RenPy', 'game': 'RenPy/NW.js',
}

# 引擎 -> 查真名时优先看哪些文件
ENGINE_HINTS = {
    'RPGMakerVXAce': ['Game.ini'],
    'RPGMaker': ['Game.ini', 'Data/System.json'],
    'RenPy': ['game/script.rpy', 'game/options.rpy', 'renpy.py'],
    'NW.js': ['package.json', 'www/index.html'],
}


def detect_engine(folder: Path, depth: int = 2) -> tuple[str, str]:
    """返回 (引擎, 证据)。只搜当前层与有限下探。"""
    votes, reason = {}, {}
    try:
        entries = list(folder.iterdir())
    except OSError:
        return 'Unknown', ''

    for e in entries:
        n = e.name
        low = n.lower()
        for pat, eng, _ in ENGINE_SIGS:
            if pat.search(n):
                votes[eng] = votes.get(eng, 0) + 2
                reason.setdefault(eng, n)
        if low.endswith('_data'):
            votes['Unity'] = votes.get('Unity', 0) + 3
            reason.setdefault('Unity', f'{n}/')
        if low in ENGINE_DIRS:
            eng = ENGINE_DIRS[low]
            votes[eng] = votes.get(eng, 0) + 1
            reason.setdefault(eng, f'{n}/')
        if e.is_file() and low in NWJS_SIGS:
            votes['NW.js'] = votes.get('NW.js', 0) + 2
            reason.setdefault('NW.js', n)

    if depth > 0 and not votes:
        for e in entries:
            if e.is_dir() and not e.name.startswith('.'):
                sub, why = detect_engine(e, depth - 1)
                if sub != 'Unknown':
                    votes[sub] = votes.get(sub, 0) + 1
                    reason.setdefault(sub, f'{e.name}/ 内 {why}')

    if not votes:
        return 'Unknown', ''
    return max(votes, key=votes.get), reason[votes and max(votes, key=votes.get)]


# Ren'Py / NW.js 自带的工具 exe，绝不能当主程序
BUNDLED_TOOLS = re.compile(
    r"(?i)^(python|chardetect|dxwebsetup|launcher|libreoffice|"
    r"pythonw|unins|python-dbg|ssl-cert|pyi|opencc|renpy\.py)$")

# 不该作为「启动入口」的 exe（补充规则，作用在候选标题提取上）
EXE_NOT_TITLE = re.compile(
    r"(?i)^(chardetect|dxwebsetup|python|lib.*|site-packages|"
    r"gtk-.*|glib-.*|gdk-.*|pango-.*|harfbuzz|icu.*|freetype|"
    r"SDL_.*|libEGL|libGLES|swiftshader|angle_.*|vk_.*|"
    r"notification_helper|UnityCrashHandler.*|CrashReport.*|"
    r"crashpad.*|UE4Editor|atidriver|uninstall|setup|config)")

# 标题里的噪音：转录组水印、猜测语气、版本后缀
TITLE_NOISE = [
    # 猜测语气 —— 说明标题本身就不可靠
    (re.compile(r'(大概|大约|似乎|好像是|guess|maybe|推测|不确定)'), '标题含猜测语气'),
    # 转录组 / 汉化组水印
    (re.compile(r'(?i)(绅士回廊|枫之|电击rukka|片想字幕|喵萌|'
                r'softsub|pcrip|uncensored|汉化组|字幕组|云翻|机翻|'
                r'AI\s*翻|渣翻|生肉|熟肉|无码流出|破解)'), '含转录组水印'),
    # 文件末尾的组名标注
    (re.compile(r'[\s_\-]*[（(【\[]?\s*(?:galgamez|hentai|nyaa|sukebei|'
                r'anon-?mouse|2di[bp])\s*\S*\s*[）)】\]]?$', re.I), '含站点水印'),
]
# 标题里允许出现的「版本/规格」后缀（这些是有效信息，不能删）
KEEP_SUFFIX = re.compile(
    r'(?i)[\s_]*(?:ver|version|v)?\s*\d+(?:\.\d+)*'
    r'[\s_]*(?:完整?版|完全版|製品版|package|pc|android|安卓|'
    r'ios|iphone|switch|ps\d|xbox|hd|4k|uncensored|无码|无修正)?$')


def strip_title_noise(title: str) -> tuple[str, list]:
    """剥离标题里的水印/猜测语气。返回 (干净标题, 警告列表)。"""
    warnings = []
    out = title
    for pat, label in TITLE_NOISE:
        if pat.search(out):
            warnings.append(label)
            out = pat.sub('', out)
    out = re.sub(r'[\s_\-·、,，]{2,}', ' ', out).strip(' 　-·')
    # 清洗后可能留下空括号、全角空格残留
    out = re.sub(r'[（(【\[\s]*[)）】\]]', '', out).strip()
    out = re.sub(r'\s{2,}', ' ', out)
    if not out:
        warnings.append('清洗后为空')
    return out, warnings


def find_exes(folder: Path, depth: int = 3) -> list[str]:
    """递归找 exe，优先非工具类，排除引擎自带工具。"""
    found = []

    def walk(d, lv):
        if lv > depth:
            return
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        for e in entries:
            if e.name.startswith('.'):
                continue
            try:
                if e.is_file() and e.suffix.lower() == '.exe':
                    stem = e.stem.lower()
                    if EXE_NOISE.match(stem) or 'uninstall' in stem:
                        continue
                    if BUNDLED_TOOLS.match(stem):
                        continue
                    # 路径里含 lib/ 或 packages/ 的一律是引擎自带
                    rel_parts = str(e.relative_to(folder)).lower()
                    if any(seg in rel_parts for seg in
                           ('lib/', 'lib\\', 'site-packages', 'python-packages',
                            'dxwebsetup')):
                        continue
                    rel = e.relative_to(folder).as_posix()
                    found.append(rel)
                elif e.is_dir() and e.name.lower() not in (
                        'save', 'savedata', 'patch', 'tool', 'tools'):
                    walk(e, lv + 1)
            except OSError:
                continue

    walk(folder, 0)
    return found


# ------------------------------------------------------------ 标题抽取

TITLE_PATTERNS = [
    # Game.ini 的 Title=
    (re.compile(r'(?im)^\s*Title\s*=\s*(.+?)\s*$'), 'Game.ini:Title',
     lambda s: s.split('-')[0].strip() or s),
    # readme 的装饰框标题
    (re.compile(r'[═─=]{4,}\s*\n?[■【]?\s*(.{4,80}?)\s*[】]?\s*\n'
                r'[═─=]{4,}'), 'readme 装饰框',
     lambda s: s.strip('■【】=─ ')),
    # HTML title
    (re.compile(r'(?is)<title[^>]*>(.*?)</title>'), 'html:title',
     lambda s: s.strip()),
    # package.json 的 window.title
    (re.compile(r'(?s)"title"\s*:\s*"([^"]{2,80})"'), 'package.json:title',
     lambda s: s.strip()),
    # 标题行
    (re.compile(r'(?m)^\s*[【『]\s*(.{3,60}?)\s*[】』]\s*$'), '方括号标题',
     lambda s: s),
]

# readme 前几行里的高显著度标题：含「■」或全角装饰，且不是目录项
HEAD_TITLE = re.compile(r'^[■□◆●○★☆※＝─]+(.{3,70}?)[■□◆●○★☆※＝─]*$')


def extract_head_title(text: str, max_lines: int = 8) -> list[str]:
    """从文件开头几行找标题（日文 readme 的常见格式）。

    典型结构：
        ═══════════════
        ■受胎島「どうして...」
                        readme.txt
        ═══════════════
    """
    out = []
    lines = [l for l in text.splitlines()[:max_lines] if l.strip()]
    for i, line in enumerate(lines):
        m = HEAD_TITLE.match(line.strip())
        if not m:
            continue
        v = m.group(1).strip().strip('「」『』【】□■◆●○★☆※')
        v = re.sub(r'^[=＝\-─]{2,}', '', v).strip()
        if is_noise_title(v):
            continue
        if not (3 <= len(v) <= 70):
            continue
        # 排除「【目次】」「(readme.txt)」这类
        if v.lower().endswith('.txt') or '目次' in v or '仕様' in v:
            continue
        if re.match(r'^[=\-─＝]{3,}$', v):
            continue
        # 只排除「纯说明文」：以句号/读点结尾，或含明确的说明词
        if re.search(r'(です|ます|ください|について|方法|容量|'
                     r'^\d+[\.．、]|目次| golf )', v):
            continue
        if v not in out:
            out.append(v)
    return out[:2]

# 噪声行过滤（这些不是标题）
NOT_TITLE = re.compile(
    r"(?i)^(readme|read\s*me|说明|说明书|製品情報|操作|起動|インストール|"
    r"copyright|ver|version|制作|著作|oto|©|all\s*rights|注意|警告|"
    r"免責|ライセンス|support|thanks?|special|product|package|file|index|"
    r"title|update|download|install|setup|config|setting|"
    r"はじめに|ご挨拶|本品について|取扱|注意書き|免責|仕様|"
    r"最低|推奨|必要|起動方法|プレイ方法|インストール方法|"
    r"password|pass|readme|crack|serial|registration)")

# ---- readme 里的规格/章节噪音（实测总结）----
# 这类字符串常被误当标题，实际全是环境要求或分隔线：
#   `2GB以上(8GB以上推奨)` `推奨動作環境` `第１章━━━━━━`
#   `必要ROM容量` `動作環境` `更能` `◆注意◆`
# 特征：含容量/推荐/单位/大量装饰字符，或是纯分隔线。
NOT_TITLE_PAT = re.compile(
    r"(?i)(GB|MB|HDD|ROM|容量|動作環境|動作|仕様|要件|"
    r"以上|推奨|必須|必要|patch|パッチ|体験版|特典|打成|"
    r"directx|_redist| Redist|カーソル|操作説明| distrito|"
    r"gpu|cpu|vram|メモリ|解像度|ウインドウ|ウィンドウ|"
    r"уголов|екран|Delta|incre|patchе|obia|"
    r"^[━─═=＝\-—_·\.]{6,}$|"
    r"^第[0-9０-９一二三四五六七八九十]+[章話回節部篇]|"
    r"^[0-9]+[0-9\.GBgb 　]*$)")

# 运行环境/操作说明专有名词（游戏 readme 里高频出现但不是标题）
NOT_TITLE_WORDS = re.compile(
    r"(?i)(directx|redist|cursor|カラー| Keys|キー配置|"
    r"セーブデータ|存档位置|データ Folder|システム要件|"
    r"操作方法|ayant|owei|unci|pecific|equire)")

# 只有 1-3 个字符且不含假名/汉字的，多半是符号或代码
_TOO_SHORT_NO_CJK = re.compile(r"^[\s\W_]{1,3}$")


def is_noise_title(v: str) -> bool:
    """判断一个候选标题是否该丢弃。集中判定便于统一维护。"""
    if not v or len(v) < 2 or len(v) > 100:
        return True
    if NOT_TITLE.match(v) or NOT_TITLE_PAT.search(v):
        return True
    if NOT_TITLE_WORDS.search(v):
        return True
    if _TOO_SHORT_NO_CJK.match(v):
        return True
    # 纯下划线/连字符构成的标识符（如 hbunny_Claude-3-7）不是标题，
    # 但含中文的 `female_sex_汉化版` 可能是，要区别对待
    if re.match(r"^[A-Za-z0-9_\-\. ]+$", v) and not re.search(
            r"[぀-ヿ一-鿿]", v):
        # 纯拉丁标识符：若含 2+ 段下划线分隔且像文件名，判为噪音
        if v.count("_") >= 1 and not re.search(r"\s", v):
            return True
    return False

# 厂商署名（可用于确认制作方）
PRODUCER_PAT = re.compile(
    r"(?im)^.{0,20}(?:制作|製作|开发|開發|厂商|廠商|product(?:or)?|"
    r"circle|ブランド|brand)\s*[:：]\s*(.{2,40})$")


def extract_title_from_text(text: str, source: str) -> list[str]:
    """从文本里抽候选标题。"""
    out = []
    for pat, _, clean in TITLE_PATTERNS:
        for m in pat.finditer(text):
            v = clean(m.group(1)).strip()
            if is_noise_title(v):
                continue
            if v not in out:
                out.append(v)
    return out[:5]


def find_producers(text: str) -> list[str]:
    out = []
    for m in PRODUCER_PAT.finditer(text):
        v = m.group(1).strip()
        if 2 <= len(v) <= 40 and v not in out:
            out.append(v)
    return out[:3]


# ------------------------------------------------------------ ini 键值
# 某些 ini 直接写着完整作品名，如 Kanon 的
#   bsxx.ini = "KZK@BISHOP 家属～母と姉妹の嬌声～ 逸樹 渡瀬"
INI_VALUE_PAT = re.compile(r'(?m)^[^\s=：:;#]{1,24}\s*[=：:]\s*([^;\r\n]{4,90}?)\s*$')

# ini 值里哪些像作品名（含 CJK/假名且不含明显工具词）
INI_NOT_VALUE = re.compile(
    r"(?i)(version|ver|copyright|company|product|website|url|http|"
    r"exe|dll|path|dir|file|lang|encoding|codepage|system|"
    r"rpt|lib|dat|scr|save|config|window|size|font|color|"
    r"^\d+$|^[\d\.\s]+$|制作|著作|権利表記| throats)")


def extract_ini_values(text: str) -> list[str]:
    """从 ini 内容里找像作品名的值。"""
    out = []
    for m in INI_VALUE_PAT.finditer(text):
        v = m.group(1).strip().strip('"\'')
        if is_noise_title(v):
            continue
        if len(v) < 4 or len(v) > 90:
            continue
        if INI_NOT_VALUE.search(v):
            continue
        # 必须含 CJK 或日文假名，且不能像路径
        has_cjk = any('\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9fff'
                      for c in v)
        if not has_cjk:
            continue
        if '/' in v or '\\' in v or v.count('.') > 2:
            continue
        # 太长的句子不像标题
        if len(v) > 40:
            continue
        # 出现多个日文分句助词 -> 是说明句不是标题
        if len(re.findall(r'[\u3042-\u3046\u3048-\u306a\u306b-\u306f]', v)) >= 2:
            continue
        if v not in out:
            out.append(v)
    return out[:3]


# 某些配置文件是「UTF-16LE 与 UTF-8 混合」，解码后 NUL 会夹在字间。
# 实战案例：Kanon PC 版的 bsxx.ini
#   解码得 'KZK@BISHOP\x00家属～母と姉妹の嬌声～\x00逸樹\x00渡瀬'
# 这类文件不能按行解析，要按「NUL 分隔 + 清洗」提取。
MIXED_NUL = re.compile(r'[^\x00-\x08\x0b\x0c\x0e-\x1f]{3,45}')

# 二进制封包里也会出现可读片段（含引号、数字串、剧本片段），要排除
BINARY_NOISE = re.compile(
    r'["\[\]{}<>\\/|]|\d{4,}|^\s*\d|[\x00-\x08]')


def extract_mixed_nul_chunks(text: str) -> list[str]:
    """从 NUL 混杂字符串里切出可读片段，应对 UTF-16/UTF-8 混合文件。

    只在**纯文本类**文件上调用；.bsa/.dat 等二进制封包会产出大量噪音。
    """
    out = []
    for m in MIXED_NUL.finditer(text):
        v = m.group(0).replace('\x00', '').replace('�', '').strip()
        if not (3 <= len(v) <= 40):
            continue
        if BINARY_NOISE.search(v):
            continue
        has_cjk = any('\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9fff'
                      for c in v)
        if not has_cjk:
            continue
        if INI_NOT_VALUE.search(v):
            continue
        if v not in out:
            out.append(v)
    return out[:3]


# ------------------------------------------------------------ 目录名信号

DIR_SIGNALS = [
    (re.compile(r'(?i)[\s_\-]*(32|64)\s*(?:bit)?$'), '位版本后缀',
     '同一作品的 32/64 位构建，应归并'),
    (re.compile(r'^\d{7,10}$'), '纯数字',
     '可能是製品番号/DLsite 番号，需查数据库'),
    (re.compile(r'(?i)\[?RJ\d{6,}\]?'), 'RJ 番号',
     'DLsite 番号，可直接搜 DLsite'),
    (re.compile(r'(?i)^[\[(（【]\d{6}[\])）】]'), '日期前缀 [080620]',
     '发布日 2008-06-20'),
    (re.compile(r'(备份|備份|backup|原版ファイル|旧ファイル)'), '备份标记',
     '补丁/原版备份，不是游戏本体'),
    (re.compile(r'(?i)^(old|new|ver\d|v\d|製品版|旧版|舊版|新版本|'
                r'重置版|完全版|豪华版)'), '版本标记', '版本关系，真名在父目录'),
    (re.compile(r'(?i)(繁体字版|繁體版|english版|中文版|简中版|日文版)'),
     '语言版本', '语言版本，真名在父目录'),
    (re.compile(r'(?i)^(_?data$|^game$|^renpy$|^lib$|^www$|^project$|'
                r'^build$|^dist$|_data$)'), '引擎/构建目录',
     '真身在上一层'),
    (re.compile(r'(?i)^(tools?|工具| extractor|mtool)'), '工具目录', '不是游戏'),
    (re.compile(r'(?i)(unrar|winrar|7z|arc_unpacker|unger|'
                r'crack|破解|补丁)'), '解包/破解工具', '不是游戏'),
    (re.compile(r'(?i)副本\s*\d+$|\(\d+\)$|\bcopy\s*\d*$'), '副本标记',
     '重复副本'),
    (re.compile(r'(?i)(18禁|18\+|r18|adult)'), '成人标记', '正常内容标记'),
]


def analyze_dir_name(name: str) -> list[dict]:
    out = []
    for pat, label, advice in DIR_SIGNALS:
        if pat.search(name):
            out.append({'signal': label, 'advice': advice})
    return out


# ------------------------------------------------------------ 非游戏判定

def is_not_game(folder: Path, engine: str, files: list, title: str) -> tuple:
    """返回 (是否非游戏, 原因)。"""
    name = folder.name.lower()
    top = list(folder.iterdir()) if folder.is_dir() else []
    top_files = [f for f in top if f.is_file()]
    top_dirs = [f for f in top if f.is_dir()]

    # 只有 1-2 个文件且是 DLL/exe 组合
    if len(top_files) <= 2 and not top_dirs:
        exts = {f.suffix.lower() for f in top_files}
        if exts and exts <= {'.dll', '.exe', '.txt', '.ini', '.xml'}:
            return True, f'仅 {len(top_files)} 个文件且无数据目录，疑似工具'
    if re.search(r'(备份|備份|backup)', name):
        return True, '备份目录'
    if re.search(r'(?i)^(tools?|工具|extractor|mtool|localeemulator|'
                 r'xp3viewer|legui)', name):
        return True, '工具目录'
    if not files and engine in ('Unknown', ''):
        return True, '无 exe 且无引擎特征'
    if re.search(r'\.(7z|zip|rar|001|002|003)$', name, re.I):
        return True, '压缩包文件，非已解压游戏'
    return False, ''


# ------------------------------------------------------------ 主流程

def probe(folder: Path, depth: int = 3) -> dict:
    info = {
        'path': str(folder).replace('\\', '/'),
        'name': folder.name,
        'engine': 'Unknown',
        'engine_evidence': '',
        'executables': [],
        'titles': [],
        'title_sources': [],
        'title_warnings': [],
        'producers': [],
        'encodings_used': [],
        'dir_signals': analyze_dir_name(folder.name),
        'is_game': True,
        'not_game_reason': '',
        'parent_chain': [],
    }

    # 父目录链（真名常在父目录）
    p = folder.parent
    chain = []
    for _ in range(3):
        if not p or p.name in ('', '/', 'G:/Game'):
            break
        chain.append(p.name)
        p = p.parent
    info['parent_chain'] = chain

    if not folder.is_dir():
        info['is_game'] = False
        info['not_game_reason'] = '不是目录'
        return info

    eng, why = detect_engine(folder, depth=2)
    info['engine'] = eng
    info['engine_evidence'] = why
    info['executables'] = find_exes(folder, depth=depth)[:5]

    # 候选文本文件
    # ⚠️ 必须排除本工具链自己产生的元数据文件。若把 .galmeta.json
    # 之类写进游戏目录，它们会被下面的 *.json 通配捞到，
    # 造成「证据污染」—— 上层远端标题反过来被当成本地真名，
    # 匹配算法拿着错的种子去查，形成自我强化的错误结论。
    SELF_GENERATED = {".galmeta.json", "library.json", "_来源信息.txt",
                      "library.jsonl"}
    cand_files = []
    for pri in ENGINE_HINTS.get(eng, []):
        f = folder / pri
        if f.is_file() and f.name.lower() not in SELF_GENERATED:
            cand_files.append(f)
    # 通用候选
    for pat in ('readme*.txt', 'Readme*', 'README*', '*.ini',
                'package.json', 'credits.html', '*.htm', '*.txt'):
        try:
            for f in folder.glob(pat):
                if f.is_file() and f not in cand_files:
                    if f.name.lower() in SELF_GENERATED:
                        continue
                    if f.stat().st_size < 512 * 1024:
                        cand_files.append(f)
        except OSError:
            pass
        if len(cand_files) > 25:
            break

    # 引擎未识别时，直接扫根目录下所有小文本文件
    if len(cand_files) < 3:
        try:
            for f in folder.iterdir():
                if f.is_file() and f not in cand_files:
                    if f.name.lower() in SELF_GENERATED:
                        continue
                    ext = f.suffix.lower()
                    if ext in ('.ini', '.cfg', '.dat', '.txt', '.json',
                               '.xml', '.inf'):
                        try:
                            if f.stat().st_size < 256 * 1024:
                                cand_files.append(f)
                        except OSError:
                            continue
        except OSError:
            pass

    # ini 里的键值对可能含答案（bsxx.ini 曾写着完整作品名）
    for f in cand_files[:25]:
        text, enc = read_text_safe(f)
        if not text:
            continue
        if enc and enc not in info['encodings_used']:
            info['encodings_used'].append(enc)
        for t in extract_ini_values(text):
            if t not in info['titles']:
                info['titles'].append(t)
                info['title_sources'].append(f'{f.name} 键值')
        if '\x00' in text:
            for t in extract_mixed_nul_chunks(text):
                if t not in info['titles']:
                    info['titles'].append(t)
                    info['title_sources'].append(f'{f.name} NOL分隔')
        for t in extract_head_title(text):
            clean_t, warns = strip_title_noise(t)
            for w in warns:
                info['title_warnings'].append(w)
            if clean_t and clean_t not in info['titles']:
                info['titles'].insert(0, clean_t)
                info['title_sources'].insert(0, f'{f.name} 头部')
        for t in extract_title_from_text(text, f.name):
            clean_t, warns = strip_title_noise(t)
            for w in warns:
                if w not in info['title_warnings']:
                    info['title_warnings'].append(w)
            if clean_t and clean_t not in info['titles']:
                info['titles'].append(clean_t)
                info['title_sources'].append(f.name)
        for pr in find_producers(text):
            if pr not in info['producers']:
                info['producers'].append(pr)

    # 兜底：exe 名
    if not info['titles'] and info['executables']:
        for e in info['executables'][:2]:
            stem = Path(e).stem
            stem = re.sub(r'[\s_\-]*(32|64)$', '', stem, flags=re.I)
            if len(stem) >= 3 and stem.lower() not in info['titles']:
                info['titles'].append(stem)
                info['title_sources'].append(f'exe:{Path(e).name}')

    ng, why = is_not_game(folder, eng, info['executables'],
                          info['titles'][0] if info['titles'] else '')
    info['is_game'] = not ng
    info['not_game_reason'] = why
    return info


def grade(results: list) -> dict:
    """对每个目标给出置信度评级与待办。

    这是「查不清时怎么办」的执行点。
    """
    out = []
    for r in results:
        titles = r['titles']
        reasons = []

        # 空标题 -> E 级
        if not titles:
            level = 'E'
            reasons.append('本地未找到任何标题证据')
        else:
            t = titles[0]
            src = r['title_sources'][0] if r['title_sources'] else '?'
            # 标题自带猜测语气/水印 -> 降级
            if any('猜测' in w or '水印' in w
                   for w in r.get('title_warnings', [])):
                level = 'D'
                reasons.append('提取到的标题含猜测语气或转录组水印')
            elif src.startswith('exe:'):
                level = 'B'
                reasons.append('仅由 exe 文件名推断')
            elif any(k in src for k in ('Game.ini', 'index.html',
                                        'package.json', '头部', 'title',
                                        'NOL分隔', '键值')):
                level = 'A'
            else:
                level = 'B'

        # 引擎未识别：仅当没有 A 级强来源时才降级
        if r['engine'] == 'Unknown' and level == 'B':
            level = 'C'
            reasons.append('引擎未识别，缺一层佐证')

        # 多个候选：仅当首位候选本身可疑才降级，不因「有后续候选」而降
        if len(titles) > 3 and level == 'A':
            level = 'B'
            reasons.append(f'提取到 {len(titles)} 个候选，首位可能是噪音')

        item = {
            'name': r['name'],
            'title': titles[0] if titles else None,
            'level': level,
            'reasons': reasons,
            'candidates': titles[:5],
            'parent_chain': r.get('parent_chain', []),
            'engine': r['engine'],
            'sources': r['title_sources'][:3],
        }

        # D/E 级：给出明确的下一步动作
        if level == 'D':
            item['next_action'] = (
                '人工在候选中选，或补查 VNDB/Bangumi；'
                '若都无果则标 [待查真名] 并保留原名')
        elif level == 'E':
            item['next_action'] = (
                '查远端数据库（VNDB search / Bangumi keyword）；'
                '仍无果则标 [待查真名]，不得猜测')
        elif level == 'C':
            item['next_action'] = '需联网交叉验证厂商/译名，确认后升级为 A'
        else:
            item['next_action'] = '可直接采用'
        out.append(item)
    return out


def print_grades(grades: list) -> None:
    """按置信度分组打印，便于人工扫读。"""
    buckets = defaultdict(list)
    for g in grades:
        buckets[g['level']].append(g)

    print('#' * 66)
    print(f"共 {len(grades)} 个目标")
    print('#' * 66)
    for lvl, label in [('A', 'A 级 · 可直接采用'),
                       ('B', 'B 级 · 高置信，建议确认'),
                       ('C', 'C 级 · 需联网核对'),
                       ('D', 'D 级 · 多候选，人工选择'),
                       ('E', 'E 级 · 无证据，标待查')]:
        items = buckets.get(lvl, [])
        if not items:
            continue
        print(f'\n{label}  （{len(items)} 个）')
        print('-' * 66)
        for g in items:
            t = g['title'] or '—'
            print(f"  {t[:52]:54s}  [{g['name'][:20]}]")
            if len(g['candidates']) > 1:
                print(f"      候选: {' / '.join(c[:22] for c in g['candidates'][:4])}")
            for rs in g['reasons']:
                print(f"      ! {rs}")
            print(f"      → {g['next_action']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dirs', nargs='+')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--grade', action='store_true',
                    help='按置信度分级输出（含待办）')
    ap.add_argument('--depth', type=int, default=3)
    args = ap.parse_args()

    results = []
    for d in args.dirs:
        p = Path(d)
        if not p.is_absolute():
            p = Path('G:/Game') / d
        if not p.exists():
            print(f'不存在: {d}', file=sys.stderr)
            continue
        results.append(probe(p, args.depth))

    grades = grade(results)

    if args.json:
        print(json.dumps(grades, ensure_ascii=False, indent=2))
        return
    if args.grade:
        print_grades(grades)
        return

    for r in results:
        print('=' * 62)
        flag = '' if r['is_game'] else f"  [非游戏: {r['not_game_reason']}]"
        print(f"{r['name']}{flag}")
        print(f"  引擎: {r['engine']}  ({r['engine_evidence']})")
        if r['parent_chain']:
            print(f"  父目录: {' / '.join(r['parent_chain'])}")
        if r['executables']:
            print(f"  exe: {', '.join(r['executables'][:3])}")
        if r['titles']:
            print('  候选标题:')
            for t, s in zip(r['titles'], r['title_sources']):
                print(f'    · {t[:60]}   ← {s}')
        if r['producers']:
            print(f"  厂商线索: {', '.join(r['producers'])}")
        if r['encodings_used']:
            print(f"  用到的编码: {', '.join(r['encodings_used'])}")
        if r['dir_signals']:
            print('  目录名信号:')
            for s in r['dir_signals']:
                print(f"    · {s['signal']} → {s['advice']}")


if __name__ == '__main__':
    main()
