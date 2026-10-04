---
name: galgame-cataloger
description: 查证 galgame/视觉小说的真实作品名与元数据。当用户需要为游戏目录、压缩包、下载文件确定「这是什么游戏」，或需要批量补全厂商、标签、发售日、评分等元数据时使用。基于本地文件元数据优先、远端数据库补充的证据分级流程，产出带置信度和证据来源的考据结果。触发词：查游戏名、这是什么游戏、galgame 元数据、VNDB、Bangumi、批量刮削、作品名考究、厂商补全、标签补全。
---

# galgame 真名考据与元数据补全

## 核心原则

**本地文件元数据优先，远端数据库负责「确认身份」和「补全字段」。**

这不是偏好问题，而是实测结果：一次批量考据中，9 个「不知道是什么」的游戏，
**7 个从本地文件（`Game.ini`、`readme.txt`、`index.html`）直接拿到确切答案**，
只有 2 个需要联网，且联网主要用来交叉验证中文译名。

但**远端校验不可省** —— 它决定「能不能平铺」。本地文件能告诉你作品叫什么，
却不能告诉你「这是个有官方条目的作品，还是某个冷门团体的私人发布」。
后者不该被平铺，因为它无法与外部数据库建立关联。

三句话说清分工：

| 问题 | 由谁回答 |
|---|---|
| 这是什么游戏？ | **本地文件**（`Game.ini` / `readme` / `index.html` / exe） |
| 这是不是同一个作品？ | **远端数据库**（双信号匹配） |
| 能不能平铺？ | **远端是否有对应条目** |

远端数据库还提供**本地文件里根本没有的信息**：厂商、标签、评分、
发售日、系列关系。它不是用来「猜名字」的。

## 铁律

1. **证据不足时标 `[待查]`，绝不编造。** 编造游戏名比留空更有害——
   用户会基于错误的名字去搜索、去入库，然后传播错误。
2. **绝不做「字面翻译」。** `AngelTear` 不是「天使の涙」，
   `JUTAIJIMA` 不是「即時ドラマス」，`KZK` 不是「枕味幻想乡」。
   拼音/罗马字的音译猜测一律不算证据。
3. **每一结论必须附证据来源。** 写清是哪个文件的哪一行/哪个字段。
4. **只移动/改名不改内容。** 所有操作默认 dry-run，写 manifest，可回滚。
5. **平铺的前提是远端有对应条目。** 厂商确认不够——厂商只回答「谁做的」，
   没回答「这是哪一部作品」。详见「平铺准入」章节。

## 原子游戏目录的定义（不依赖远端）

**这一节排在所有流程之前。** 之前我犯的错是：用「远端能不能查到」来判定
「是不是真游戏」—— 这是**循环定义**。远端查不到恰恰可能因为它不是标准
galgame（Ren'Py 独走的、Unity 短作、Patreon 限定、Steam 版独占）。

**判定必须完全脱离远端，纯靠目录结构。**

### 七级分类

`scripts/atomic_detect.py` 的 `classify_one(folder)`：

| 级别 | 含义 | 处理 |
|---|---|---|
| `game` | 原子游戏目录本体 | 可作为独立条目 |
| `container` | 合集层（子目录里还有游戏） | 保留结构，下探 |
| `variant` | 同作品的版本构建（`32位`/`繁体版`） | **归并**到本体 |
| `staging` | **下载暂存目录**（目录名与内容无关） | 按实际内容重新归位 |
| `tool` | 工具/补丁/压缩包/字幕/安装器 | 排除 |
| `shell` | 空目录或只剩残骸 | 报告让人看 |
| `unknown` | 无法判定 | **保守留待人工**，不硬判 |

### 判据优先级

```
1. 引擎封包在当前层     data.xp3 / .pfs / .unity3d / .rgss3a / .bgl / .nsc
2. 目录组合            game+lib → Ren'Py    www+package.json → NW.js
3. 单文件指纹          unityplayer.dll / siglusengine.exe / app.info / game.ini
4. 大封包文件(>1MB)    .arc .rpa .fpk .pac .xfl .noa .dat .gdb .hvl .nsa .bdb
5. UE4 打包            Manifest_*.txt  或  Engine/ + 游戏名/ 双层
6. 主程序 + 资源目录    exe + bgm/cg/pac/Data/...
7. 本体在唯一非附属子目录     ← 必须在 8 之前
8. 下载暂存目录 / 非游戏内容
```

⚠️ **7 必须排在 8 之前**，这是一个真实踩过的 bug，见下节。

### 踩坑：「顶层全压缩包」不等于「不是游戏」

实测 32 个「顶层全是压缩包 + 有子目录」的目录里，**18 个内含真游戏**，
全被 `detect_non_game` 的「全是压缩包 → 未解压」规则误判成 `tool`：

| 目录 | 实际内容 |
|---|---|
| `白色相簿2/` | `Leaf/`（SiglusEngine 真游戏） |
| `对魔忍/` | `风雪2/`（KiriKiri，名字还跟父目录完全无关） |
| `勇者大战魔物娘/勇战r/` | `もんむすくえすと！RPG前章 ver1.21` |
| `rewrite/` | `Rewrite/` + 8 个 rar 并存（下载完没删压缩包） |
| `海猫鸣泣之时/` | `海猫鸣泣之时 散/` |

**根因是判据顺序**：`detect_non_game` 跑在 `find_body_subdir` 之前，
一旦顶层全是压缩包就直接下结论，**根本不看子目录里有没有游戏**。

**修正**：`find_body_subdir` 提前到所有「非游戏」判据之前。
只有当子目录里确实一个游戏都没有时，「全是压缩包」才成立。

### 踩坑：Electron 游戏没有 `nw.dll`（Chromium 壳识别）

只认 `nw.dll` 会漏掉**全部 Electron 游戏** —— Electron 根本没有这个文件：

```
股市淫狼/            Game.exe  natives_blob.bin  icudtl.dat  libEGL.dll …
变成妈妈的身体v1.01/  Game.exe  credits.html  d3dcompiler_47.dll  ffmpeg.dll …
魅魔契约/             Game.exe  node.dll  natives_blob.bin  …
```

这些曾因「有 exe 但 `RESOURCE_DIR` 一个都匹配不到」被判成
**「安装器或工具」**，真游戏直接丢掉。

**判据必须用组合指纹（≥2 个命中）**，单靠 `icudtl.dat` 会误伤 ——
它只是 ICU 国际化数据，10MB，很多非游戏目录也有。

```python
CHROMIUM_DLL = ("natives_blob.bin", "libegl.dll", "libglesv2.dll",
                "d3dcompiler_47.dll", "ffmpeg.dll", "ffmpegsumo.dll",
                "v8_context_snapshot.bin", "snapshot_blob.bin")
# 加上 NW.js 铁证：package.json + www/ 或 node.dll
```

⚠️ 必须排在「有 exe 但无资源目录」**之前** —— 这类游戏的资源
不在 `bgm/cg/data` 里，全在 Chromium 运行时 dll 中，`RESOURCE_DIR` 必然全空。

`credits.html` 在这类游戏里起到 readme 的作用，是很好的本地证据来源
（制作组、作者、致谢，常含作品名）。

### 踩坑：目录名可能完全撒谎（`staging` 级）

实测案例 `G:\Game\2021-\Galgame\angeltear\`：

```
angeltear.rar                17KB   截断的废下载
赞美女神.7z.001~008          8.4GB  **完全不相干的作品**
angeltear/町ぐるみの罠～白濁にまみれた優理～.zip   **又一個不相干的作品**（Eushully）
解压密码.txt                          「解压密码：jiudaoshifenxiang」
angeltear/hgamecn下载说明.txt        下载站免责声明
```

目录名是**当时的搜索关键词**，进去以后下了一堆别的。它不是游戏也不是合集 ——
判 `unknown` 会丢掉「里面藏着两个真游戏」这个关键信息。

**识别判据（内容判据，不靠目录名）**：

| 信号 | 强度 | 说明 |
|---|---|---|
| 有 `解压密码.txt` / `密码` / `pass` | **强** | 正常游戏目录不会有这些东西 |
| 含文件名带下载站名的（`hgamecn*` 等） | **强** | 站点声明文件 |
| 压缩包名与目录名无交集 | **弱，不可单独用** | 见下 |

```python
def _norm(s):  return re.sub(r"[^0-9a-z一-鿿]+", "", (s or "").lower())
def _archive_stems(files):   # 剥掉 .part1 / .001 / .7z 等后缀
    ...
dir_key = _norm(folder.name)[:6]
mismatch = not any(dir_key in _norm(s) for s in stems)
```

⚠️ **「压缩包名与目录名无交集」不能单独作为判据。**
第一版只用这一条，全库误报 **149 个**。因为「名字对不上」是常态：

| 目录 | 压缩包 | 为什么是正常的 |
|---|---|---|
| `ever17/` | `A036@忧郁的弟弟.part1.rar` | Ever17 的**产品番号**，完全正确 |
| `27岁女教师/` | `27-02(nyh741).7z` | 同一系列多卷，卷名带卷号 |
| `playhome/p/插件/VR插件/` | `PlayClubVR_0.2.4.zip` | 插件目录，本来就不叫游戏名 |

**正确做法：两条强信号同时成立才判 staging。**
强信号（密码文件/站点名）几乎无假阳性，是主判据；「无交集」只作辅助说明。

⚠️ 站点正则同样要收紧。**不能用泛化词** ——
第一版写了 `里番|无码|javhd|3d在上|图鉴君`，结果
`playhome/p/插件/声音替换` 这类正常插件目录全被误判。
只认**文件名里明确带站点名**的：`^hgamecn.*|^javhd.*|^down.*site.*`。

**处置**：`staging` 目录里的东西**按实际内容重新归位**，
用 `staged_names`（从压缩包名提取的真实作品名）作为考据种子，
**不要用目录名去查远端** —— 目录名在这里是噪声。

全库只有 2 个 `解压密码.txt`，都在 `2021-\Galgame\` 下，
说明是同一批下载习惯留下的，不是普遍现象。

### 三条实测教训

**1. 不要靠目录名黑名单排除附属目录**

漏掉的例子：`よくある質問`（FAQ）、`B3566存档`（数字+中文）、` Isol.`（带空格）、
`VR_patch`。**靠名字永远列不全。**

正确做法是**内容判定** —— 逐个检查子目录自身是不是游戏，只有一个是就说明它是本体：

```python
games = [d for d in dirs if classify_one(d).is_game]
if len(games) == 1:
    return games[0]          # 它就是本体
if len(games) > 1:
    return "container"       # 真合集
```

**2. exe 要分角色，主程序是「唯一不认识的那个」**

```
妻みぐい3/
├── OpenSaveFolder.exe   ← 辅助（打开存档目录）
├── ResetConfig.exe      ← 辅助
├── Uninstaller.exe      ← 辅助
└── Tsumamigui3.exe      ← 主程序（唯一非辅助）
```

不能取「最大的 exe」—— `GAME_SYS_CN.exe` 3.9MB vs `GAME_SYS_CN1.exe` 都一样大。
也不能取「第一个」。

```python
AUX_EXE = re.compile(r"(?i)^(install|setup|unins\d*|uninstall|"
    r"opensavefolder|resetsave|resetconfig|config|setting|update|"
    r"patch|crack|keygen|registration|...)")
main = max((f for f in exes if not AUX_EXE.match(f.stem)),
           key=lambda f: f.stat().st_size)
```

**3. 判据之间有顺序依赖**

UE4 判据**必须**在「残骸」判据之前。UE4 目录形态是
「exe + 一堆说明 txt/url」，会被「只有 exe + 说明文件 = 残骸」的规则误杀。

同理，「本体在子目录」要在「有 exe 但无资源目录」之后 ——
先看自己是不是完整的，再看子目录。

### Ren'Py 特别容易误判

| 现象 | 正确判据 |
|---|---|
| 脚本打包进 `game.rpa`，顶层无 `.rpy` | `game` + (`lib`\|`MonoBleedingEdge`\|`python`) 目录组合 |
| 运行时被拆成子目录 | `MonoBleedingEdge/` 是**运行时**，不是独立游戏 |
| `steam_appid.txt` | 是 Steam 发行标记，**不影响它是个游戏** |
| exe 与 `.py`/`.sh` 同名 | `xxx.exe` + `xxx.py` + `xxx.sh` 是 Ren'Py 标准三件套 |

Ren'Py 游戏在 VNDB 常查不到（Patren/itch.io 独占），**这不影响本地判定** ——
它就是原子游戏目录，只是远端没有对应条目而已。

### 准确率实测

三组随机种子 × 120 样本：**97% / 91% / 94%**（平均 94%）。

剩余漏判**全部是保守方向**（判 `unknown` 而非误判成 `game`）—— 这是对的方向。
宁可留待人工，不可错判。

---

## 工作流

### 第 0 步：排除非游戏内容

用上面的七级分类扫一遍，**`tool` / `shell` 级的直接排除**，不做后续查询：

| 判据 | 例子 | 处理 |
|---|---|---|
| 全是压缩包 | `HoneySelect2.7z.001` | 未解压，提示先解压 |
| 全是字幕/歌词 | `mv_36ek60l.ass` | 机翻脚本残骸 |
| GUID 命名目录 | `{0BE9572E-8558-...}` | 安装器临时目录 |
| 只有 exe + 说明/图片 | `拍卖会/Game.exe + read me.txt` | 残骸，人工看 |
| 目录名是已知工具 | `Extractor2.5`、`XP3Viewer` | 工具 |
| 目录名带 `备份/补丁/原版` | `原文件备份`、`汉化补丁备份` | 附属资源 |

**引擎识别特征**（辅助判断）：
```
KiriKiri      *.xp3
吉里吉里/Zygeme   *.xp3
Artemis       *.pfs
NScripter     nscr.dat
Ethornella/BGI   *.bgl / *.xfl
SiglusEngine  SiglusEngine.exe
RPG Maker     *.rpgmvp / *.rpgmvo / Game.rmvp / www/
RPG Maker VX Ace  Game.rgss3a
Ren'Py        game/ + (lib/ | MonoBleedingEdge/)
Eushully      *.noa
Unity         *_Data/ + UnityPlayer.dll
NW.js         nw.dll + package.json(main=www/index.html) + locales/
Unreal Engine 4   Manifest_*.txt  或  Engine/ + 游戏名/
NScripter     nscr.dat / nscript.dat / *.ns2 / *.nsa   ← 本轮补
GameMaker     data.win                                 ← 本轮补
Flash/SWF     ≥3 个 *.swf（同层或子目录）              ← 本轮补
```
NW.js 结构极易误判（`node.dll` + `nw.dll` + `www/`），其真名在
`www/index.html` 的 `<title>` 里。


### 第 1 步：抽取本地证据（最高优先级）

按可靠度排序，逐个尝试：

#### 1.1 `Game.ini` 的 `Title=` 字段（RPG Maker，最直接）
```python
# RPG Maker VX Ace / MV / MZ 通用
[Game]
Title=青山汉化-地牢女祭司0.32-
RTP=RPGVXAce
```
`Title` 字段直接给出作品名 + 版本 + 汉化组。**这是最可靠的单一来源。**

#### 1.2 `readme.txt` 首行
日文游戏的 readme 常把标题放在 `═══` 或 `───` 装饰框里：
```
══════════════════════════════════════
■受胎島「どうしてアンタみたいなブサ男に種付けされなきゃいけないのよ？！」
                    readme.txt
══════════════════════════════════════
```
注意读编码——见 1.6。

#### 1.3 `www/index.html` 的 `<title>`（NW.js 游戏必查）
```html
<title>Idol hands 2</title>
```

#### 1.4 exe 文件名
exe 名往往直接是英文原名或罗马字：
```
ResistTheSuccubus_TheEndOfTheFemaleKnight.exe  → 抗魔少女
MagicalMILFs.exe                                → MagicalMILFs
Suposeku.exe                                    → スポセク 的内部名
```
排除工具类 exe：`unins`、`setup`、`config`、`unrar`、`7z`、
`Uninstaller`、`notification_helper`、`UnityCrashHandler*`、
`CrashReport`、`crashpad_handler`。

#### 1.5 其他元数据文件
| 文件 | 提取什么 |
|---|---|
| `package.json` | NW.js 游戏；`window.title` 常为空，要读 `www/index.html` |
| `credits.html` | 制作组、作者、致谢（常含作品名） |
| `System.json`（RPG Maker MZ） | `gameId`、配置项；`Actors.json` 的角色名 |
| `*.ini` | 可能是 UTF-16！见 1.6 |
| 攻略 `.htm`/`.txt` | 文件名常含作品名（`Anbie 润色-鬼作 攻略` → 「鬼作」） |
| `README.txt` | 厂商署名 + 作品名（见 1.7） |
| `COPYING` / 版权声明 | 厂商名 |
| `.arb` / `.pak` / 资源包名 | 版本标记、社团名 |

#### 1.6 ⚠️ 编码处理（必做，否则会读出乱码）

同一批文件里混杂多种编码。**必须先探测再解码**：

```python
def read_text_safe(path, max_bytes=8192):
    """按 BOM 和有效性探测编码，返回可读文本。"""
    data = open(path, 'rb').read(max_bytes)
    if not data:
        return ""
    # 1. BOM 优先
    for bom, enc in [(b'\xff\xfe', 'utf-16-le'),
                     (b'\xfe\xff', 'utf-16-be'),
                     (b'\xef\xbb\xbf', 'utf-8-sig')]:
        if data.startswith(bom):
            return data.decode(enc, errors='ignore')
    # 2. NUL 字节多 → 很可能是 UTF-16 无 BOM
    if data[:200].count(0) > len(data[:200]) * 0.2:
        for enc in ('utf-16-le', 'utf-16-be'):
            try:
                t = data.decode(enc, errors='strict')
                if t.count('\ufffd') == 0:
                    return t
            except UnicodeDecodeError:
                continue
    # 3. 按候选编码试解码，取「日文假名/汉字能正常显示」最多的
    best, best_score = '', -1
    for enc in ('utf-8', 'cp932', 'gbk', 'big5', 'euc-jp'):
        try:
            t = data.decode(enc, errors='strict')
        except UnicodeDecodeError:
            continue
        # 评分：假名/CJK 越多越好，替换符越少越好
        score = sum(1 for c in t if '\u3040' <= c <= '\u30ff'
                    or '\u4e00' <= c <= '\u9fff') - t.count('\ufffd') * 10
        if score > best_score:
            best, best_score = t, score
    return best
```

**实战案例**：Kanon PC 版的 `bsxx.ini` 是 **UTF-16LE 无 BOM**，
按 UTF-8 读会得到 `K Z K @ B I S H O P` 夹着乱码，
正确解码后直接给出 `KZK@BISHOP 家属～母と姉妹の嬌声～ 逸樹 渡瀬`。

#### 1.7 README 里的厂商与原型信息

厂商署名往往直接说明作品原型与版本关系：
```
汉化文本全部来自【冥月·凌雪汉化组】《素晴らしき日々～不連続存在～》汉化补丁v0.99
程序：巨魔战将
```
→ 原型是「巨魔战将」（BGI/Ethornella 引擎），不是汉化组。

### 第 2 步：结构信号判定（不需要数据库）

目录名/文件名里的这些信号，能在查询前就判定「这是同一作品的不同版本」：

| 信号 | 示例 | 含义与处理 |
|---|---|---|
| `_32` / `_64` / `32bit 64bit` | `4P_PLAY_32` + `4P_PLAY_64` | 同作品位版本，**归并**为 `游戏名/{32位,64位}` |
| 结尾 `0.32` 等版本号 | `Npri_alpha032` | 版本号，**不要**误判为 32 位 |
| 纯数字（7-10 位） | `2109284216` | 製品番号，查数据库得真名 |
| `旧版` / `重置版` / `新版本` / `製品版` | `旧版`、`重置版` | 版本关系，真名在**父目录** |
| `繁体字版` / `English版` / `中文版` | `繁体字版` | 语言版本，真名在父目录 |
| `_Data` / `game` / `renpy` / `lib` | `Game_Data` | 引擎子目录，**真身在上一层** |
| `Project` / `build` / `dist` | `Project` | 构建目录，真身在上一层 |
| `xx备份` / `原版ファイル` | `原文件备份` | 补丁备份，非游戏 |
| `副本N` / `copy` | `galfamily [副本1]` | 重复副本，验证后删 |
| exe 大小完全相同、时间戳不同 | 两版各 641536 字节 | 同作品重打包，保留新版 |

**父目录追溯是硬要求。** 本次有 4 处错误都是因为没看父目录：
`繁体字版`→`魔界洞ルル・ファレア`、`Game_Data`→`スポセク ～天井特化のバトルファック～`、
`Project`→`五印传说`、`yongzhan/`→`もんむす・くえすと！RPG終章`（勇者大战魔物娘）。

### 第 3 步：远端查询（补充元数据 + 交叉验证）

只有本地证据不足，或需要补全厂商/标签/评分时才联网。

#### 3.1 VNDB Kana API（首选）

```bash
curl -X POST 'https://api.vndb.org/kana/vn' \
  -H 'Content-Type: application/json' \
  --data '{
    "filters": ["search", "=", "关键字"],
    "fields": "id,title,alttitle,titles{title,lang,main},aliases,olang,released,developers{id,name,original},platforms,image{url}",
    "results": 10
  }'
```

关键参数：
- 端点：`POST /kana/vn`（作品）、`/kana/release`（发行版本）、`/kana/producer`（厂商）
- **`fields` 必须显式列出**，不支持通配符
- ⚠️ **嵌套对象必须写明子字段**（点号与大括号等价），只写父对象名会 HTTP 400：
  - ✅ `image.url` ≡ `image{url}`；`titles{title,lang,main}`、`developers{id,name,original}`
  - ❌ `titles`、`developers`、`image`（裸露父对象名——必须带子字段）
- ⚠️ **`alttitle` 返回类型不稳定**，可能是字符串也可能是列表，
  必须用 `isinstance` 判断后再用
- 免认证；限流 **200 次 / 5 分钟**，单次请求超时 3 秒，超限返回 `429`
- `results` 默认 10，**最多 100**；设 `count: true` 可只取匹配总数
- `search` 会同时匹配标题、别名、发行版本标题
- `GET /kana/schema` 可查枚举值和可用字段

**多策略搜索**（命中率关键）：

```python
def vndb_search_variants(name, exe_name=None):
    """同一目标用多种查询串，提高命中率。"""
    queries = [name]
    # 去掉括号内容与版本后缀
    queries.append(re.sub(r'[（(\[【].*?[)）\]】]', '', name).strip())
    queries.append(re.sub(r'[\s_\-]*(v|ver)\d+(\.\d+)*$', '', name, flags=re.I))
    # 用 exe 名（去掉 _32/-64 等后缀）
    if exe_name:
        queries.append(exe_name)
        queries.append(re.sub(r'[\s_\-]*(32|64)$', '', exe_name, flags=re.I))
    # 罗马字 -> 去符号
    queries.append(re.sub(r'[^A-Za-z0-9]', '', name))
    seen, out = set(), []
    for q in queries:
        if not q or q.lower() in seen:
            continue
        seen.add(q.lower())
        out.extend(vndb_query(q))
    return out
```

#### 3.2 Bangumi API（中文条目更好）

```bash
# 搜索（免认证）
POST https://api.bgm.tv/v0/search/subjects
{"keyword": "关键字", "filter": {"nsfw": true}}
# 详情
GET https://api.bgm.tv/v0/subjects/{subject_id}
```

**必须设 User-Agent**，格式：`{开发者ID}/{应用名}/{版本}`，
泛用 UA（如 `database`、`Bangumi/1.0`）会被封。

**R18 限制**：成人条目需 Bearer Token，且 Bangumi 账号需注册满 **60 天**才有权限，
未授权请求直接返回 **404**。Token 申请：`https://next.bgm.tv/demo/access-token`。

#### 3.3 其他源

| 站点 | 说明 |
|---|---|
| DLsite | 需日区 IP；作品编号形如 `RJ01026809`，可直接搜 |
| Getchu | 编号在文件名前缀，如 `[080620]`（年月日） |
| Hikarinagi | 日文 galgame 站，ReinaManager 已集成 |
| YMGal / Kungal / Shionlib | 中文站，ReinaManager 有 API |

#### 3.4 厂商官网 —— 被低估的强线索

**readme 里的官网域名是最佳线索。** 例如厂商 `あたまがへいわなせかい`
的官网是 `heiwachan.com` —— 拿到域名后：

1. 直接 `WebFetch` 官网首页 / 作品页
2. 官网通常有完整的作品列表、发售日、RJ 番号
3. 若官网已失效，搜索引擎仍可能索引了它的页面

**注意：搜索引擎对冷门小站常常索引不到。** 实测案例：
搜 `"heiwachan" "4P Play"` 返回的全是无关内容（同名 OVA、帕尔科厂商）。
这类情况不能只依赖搜索关键词组合。

#### 3.5 多源搜索的顺序（一个都查不到才算查不到）

```
① 本地文件           ← 90% 的答案在这里
② 厂商官网           ← readme 里的域名，最高性价比
③ VNDB /vn           ← 主流作品
④ VNDB /release      ← DLsite/FANZA 渠道的小众作品可能只在发行版本里
⑤ DLsite / Getchu    ← 按 RJ 番号或标题直接搜
⑥ Bangumi            ← 中文条目更全
⑦ Wayback Machine    ← 官网已失效时的兜底
⑧ Google             ← 最后手段
```

**必须明确报告「查过哪些源、分别结果如何」。** 只说「查不到」而不说
查过哪里，用户无法判断是「真没有」还是「你方法不对」。

**「0 条结果」是有效结论**，此时处置：
- 作品名就用英文/罗马字原名（不翻译、不音译）
- 标注厂商（从 readme / app.info 取，通常是可靠的）
- 置信度定为 **C 级**：厂商可确认，作品名按原名保留
- 若该作连英文名都无法确定，才降到 **D/E**

#### 3.6 批量查询的节流

```python
import time, random
class RateLimiter:
    """VNDB: 200 req / 5 min ≈ 1.5s 一次。留余量取 1.8s。"""
    def __init__(self, min_interval=1.8):
        self.min_interval = min_interval
        self.last = 0.0
    def wait(self):
        delta = time.time() - self.last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta + random.uniform(0, 0.3))
        self.last = time.time()
```
每个目标平均 3-5 次查询。按 1.8s 间隔、均摊 3.5 次算，500 个游戏约需 45-55 分钟。
**必须缓存**，用作品名归一化后的哈希做 key，避免重复查询。

### 第 4 步：结果验证（最容易被忽略的一环）

查到候选结果 ≠ 查对了。必须验证：

1. **引擎是否吻合** — 库里是 KiriKiri，候选是 Unity → 可能不是同一个作品
2. **发售年份是否吻合** — 候选 2016 年，文件时间戳 2019 年，不一定矛盾但要留意
3. **厂商是否自洽** — 同一目录下多个游戏若来自同一社团，应能对上
4. **规模是否吻合** — 候选时长 50h，目录只有 200MB（试玩版？）→ 可能是 demo
5. **是否有多版本** — 查到的是旧版，本地是重制版 → 补充版本标注

**版本关系判据**：

| 现象 | 结论 |
|---|---|
| arc/封包数不同（43 vs 55） | 不同版本，应并存并标注 |
| exe 大小时间戳不同、大小相同 | 同作品重打包，保留新版 |
| 目录名带 `_32/_64` | 位版本，归并为子目录 |
| 候选结果只有一个、但本地有多个同名目录 | 可能重复副本 |

## 置信度分级（必须输出）

每个结论标注置信度，让用户知道哪些需要复核。**置信度统一用字母 `A`~`E`**
（与 `probe_local.py --grade`、`library.json.confidence` 完全一致）：

| 置信度 | 依据 | 处置 |
|---|---|---|
| **A 确定** | `Game.ini` Title / `index.html` title / readme 头部 / NOL 分隔 ini 键值 | 直接采用 |
| **B 高** | exe 名推断，或 A 级来源但有多个候选 | 建议人工扫一眼 |
| **C 中** | 仅远端结果吻合；或仅 exe 名 + 引擎未识别 | 采用但标注「需联网核对」 |
| **D 低** | 标题含猜测语气/水印，或首位候选可疑 | **列出候选，不下结论** |
| **E 存疑** | 无任何本地证据 | **标 `[待查真名]`，绝不猜测** |

上表是**本地证据级**（`probe_local.py --grade` 的输出）。远端匹配也有自己的
置信度，同样用 `A`~`E` 但判据不同（LCS/CJK 双信号），见「平铺准入」一章。

**降级触发条件**（写进 `probe_local.py --grade`）：

```
A → B   提取到 >3 个候选（首位可能是噪音）
A → D   标题含「大概/似乎/推测」等猜测语气
A → D   标题含转录组水印（绅士回廊/枫之/电击rukka/galgamez…）
B → C   引擎未识别（缺一层佐证）
任意 → E  清洗后为空，或完全无证据
```

**注意：B 不是「可疑」，只是「建议确认」。** A/B/C 都可以采用，
只是要不要再看一眼。真正必须停下来的是 D 和 E。

---

## 查不清时的分级处置（关键）

这是最容易糊弄过去的环节。**「查不到」不是一个结果，而是一串需要分诊的症状。**

### 症状 1：提取到的「标题」其实是章节名

```
候选: はじめに / 最低 Pentium166MHz 推奨 … / フルカラー (800×600 65,536色)
```
「はじめに」= 前言、「ご挨拶」= 致意、「起動方法」= 启动方法 ——
这些是 readme 的**目录项**，不是作品名。

**处置：**
1. 检查该字符串是否是 readme 目录里的条目（通常在「目次/■はじめに」附近）
2. 真名往往在**目录项之前**，用全角空格缩进或出现在前 3 行
3. 实战案例：`sinkon` 的真名是
   `あさひとつぐみの新婚生活！～幼なじみはベッドヤクザアナザー～`，
   在 readme 第 2 行，带 `　　` 全角缩进
4. 找不到 → 降级为 D，**保留 exe 名**（`sinkon` 至少是罗马字，可搜）

### 症状 2：标题里混进了转录组水印

```html
<title>兽耳少女不会背叛（大概） 绅士回廊云翻</title>
```
「（大概）」说明连打包者都不确定；「绅士回廊云翻」是转录组名，不是作品信息。

**处置：**
1. 剥离水印词：`绅士回廊|枫之|电击rukka|片想字幕|喵萌|softsub|pcrip|galgamez|nyaa` 等
2. 剥离猜测语气后若剩余部分够长（≥4 字），降级为 **D** 并保留
3. **不要**因为剥掉了水印就当作已确认 —— 原标题自带「大概」意味着整个条目都不可靠

### 症状 3：exe 名指向引擎自带工具

```
候选: chardetect / dxwebsetup / python / libEGL
```
Ren'Py 和 NW.js 会捆绑 Python 运行时、chardetect、dxwebsetup 等。

**处置：** 排除路径含 `lib/`、`site-packages/`、`python-packages/` 的 exe；
排除文件名 `chardetect|dxwebsetup|python|pythonw|lib*|gtk-*|SDL_*` 等。

### 症状 4：多个候选互相矛盾

比如搜出 3 个同名作品，引擎/年份/规模都不吻合。

**处置：** 降级 **D**，把候选连同各自的 VNDB ID、发售日、厂商一起列出，
让人来判断。**不要选「最像的那个」**——那等于把猜测包装成结论。

### 症状 5：完全无证据

目录里没有任何可读文本，exe 名是纯罗马字且无匹配。

**处置：**
1. 标 `[待查真名]`，**保留原目录名不动**
2. 写进 `待查清单.md`，附：路径、体积、引擎线索、可能的来源（如 `XXX合集/内`）
3. **不要**用「看起来像」的翻译命名。这条铁律不可破。

### 分诊流程

```
probe_local.py --grade 批量跑
        ↓
  ┌─ A/B/C ─→ 直接采用（写入 manifest，可回滚）
  └─ D/E   ──→ 人工分诊
                ├─ 有强线索（章节名/水印/工具exe）→ 修脚本规则，批量重跑
                ├─ 候选矛盾 → 查远端 + 列候选让人判断
                └─ 完全无线索 → 标 [待查真名]，进待查清单
```

**关键认知：D/E 级不是失败，是正确的产出。** 编造一个名字才是失败。
本次实测 11 例中 10 例查清（7 个 A + 3 个 B + 1 个 C），1 例正确降级为 D
（`shouerG`，标题自带「大概」）——**这个降级是成功的，不是失败。**


## 输出格式

```markdown
| 原目录名 | 考据结果 | 置信度 | 证据 |
|---|---|---|---|
| KZK | Kanon PC版 家属～母と姉妹の嬌声～ | A | bsxx.ini(UTF-16): "KZK@BISHOP 家属～母と姉妹の嬌声～ 逸樹 渡瀬" |
| Succubus | Idol hands 2 | A | www/index.html: <title>Idol hands 2</title> |
| Npri_alpha0.32 | Nymphomania Priestess (地牢女祭司 0.32) | A | Game.ini: Title=青山汉化-地牢女祭司0.32- |
| JUTAIJIMA | 受胎島 どうしてアンタみたいなブサ男に種付けされなきゃいけないのよ | A | readme.txt 首行 |
| Tannshouharem | おねショタ無人島性活 御姐×正太无人岛性活 v2.3.1 | C | 来源路径 + 联网核对中文译名 |
| XXXXX | [待查真名] | E | 无本地证据，远端 0 结果 |
```

**附加字段**（若数据库有）：
```
厂商 / 首发日期 / 标签 / 评分 / VNDB ID / DLsite RJ号 / 系列关系
```

## 操作安全

所有改名/移动操作必须：

1. **默认 dry-run**，输出完整计划 CSV 供人工过目
2. **写 manifest**（`{src, dst, timestamp}`），支持一键回滚
3. **不覆盖** —— 目标已存在时自动加 `(2)` 后缀避让
4. **不删除** —— 重复项也要保留，只改名加标记
5. **不编造** —— 查不到就标待查

```python
# 回滚模式
def rollback(manifest_path):
    for step in json.loads(open(manifest_path))['steps'][::-1]:
        src, dst = Path(step['dst']), Path(step['src'])
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dst)
```

---

## 批量整理时的分组原则（重要）

从合集/厂商目录里提取游戏时，**目录名本身就是元数据的载体**。
把它拆平会让「这几个游戏是同厂」这个知识从文件系统中消失——
只剩下操作记录里的文字，人在文件管理器里根本看不出来。

### 三条规则

| 情况 | 处理 | 理由 |
|---|---|---|
| **来源是厂商** | **先过平铺准入**（远端有条目）→ 才可平铺，厂商写进 `library.json` | 厂商确认不足以准入，见「平铺准入」章节 |
| **来源是系列** | 保留 `合集提取/〔系列〕xxx/` 分组 | **系列关系是作品本身的属性**，平铺会永久丢失 |
| **来源恢复不了** | 集中到 `待甄别/` 下，**并写明原位置** | 不假装没有这层关系 |

> ⚠️ 不要因为「厂商查到了」就平铺。实测反例：Heiwachan 4 个游戏厂商
> 明确（readme + `app.info` 双确认），但 VNDB 0 结果，属冷门作品。
> 这类应保持在分组目录里等远端条目出现。

### 反面教材

```
✗ 把 Heiwachan Games Unity Games/ 拆了，4 个游戏平铺出去
  → 用户完全看不出这 4 个是同厂，除非有人专门告诉他

✗ 把 [Orcsoft]/ 拆了，12 个游戏平铺出去
  → 12 个 ORCSOFT 作品散落各处，同厂关系消失
```

### 正确做法

```
1. 提取时/提取后，把「游戏 ← 来源目录」写进 manifest
2. 按来源分类：
     厂商  → 远端校验 → A/B 级才平铺，厂商写进 library.json
     系列  → 保留 〔系列〕xxx/ 分组（永远保留）
     杂项  → 待甄别/（如「原 misc 合集」「原单游戏目录」）
3. 待甄别目录里必须写 `说明.md`，说明每个子区「原本来自哪里」
```

### 为什么「平铺」不等于「丢弃来源」

平铺的前提是**元数据已经入库**。在入库之前，文件系统是唯一的载体。
所以正确顺序是：

```
提取 → 记录来源 → 分组/标记 → 考据真名 → 远端校验入库 → 才可以完全平铺
```

若跳过「记录来源」直接平铺，就是信息销毁。

**`library.json` 取代 `_来源信息.txt`**：前者是结构化的、可被工具读取的
集中清单，支持 remote/local 双名切换；后者是散落在游戏目录里的临时文本。
只有远端查不到、连清单条目都写不出来的条目，才退回写 `_来源信息.txt`。

### `_来源信息.txt` 的格式

```markdown
厂商（考据确认）: ORCSOFT
来源目录: 原 [Orcsoft] 合集
证据: 目录名前缀 [ORCSOFT]-
考据日期: 2026-10-03
说明: 此文件是元数据入库前的临时记录，入库后可删除。
```

**入库后可以删掉它** —— 因为那时厂商已经是数据库里的字段了。

## 远端匹配算法（决定「是不是同一个作品」）

查到候选 ≠ 查对了。这一节是**假阳性与假阴性的实战经验**，务必按此实现。

### 双信号打分：LCS 管同语言，CJK 管跨语言

**不要用字符集 Jaccard。** 它会被子串严重误导：

```
AngelTear  vs  Neo Angelique: Tenshi no Namida
   字符集 Jaccard = 0.95   ← 看起来是同一个作品
   最长公共子序列 = 0.31   ← 实际毫无关系
```

原因：短标题的字符集会被长标题里的**子串**吞掉。
`Angelique` 包含了 `Angel` + `Tear` 需要的全部字母。

```python
def similarity(a, b):
    """最长公共子序列 / 较长串长度。O(min*max)，对短标题足够快。"""
    A, B = normalize(a), normalize(b)
    if not A or not B:
        return 0.0
    if len(A) > len(B):
        A, B = B, A
    prev = [0] * (len(B) + 1)
    for ca in A:
        cur = [0]
        for j, cb in enumerate(B):
            cur.append(prev[j] + 1 if ca == cb
                       else max(cur[j], prev[j + 1]))
        prev = cur
    return prev[-1] / max(len(A), len(B))


def cjk_ratio(a, b):
    """CJK 汉字重叠度 —— 跨语言匹配的唯一可靠信号。"""
    def cj(s):
        return set(c for c in s
                   if '\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9fff')
    A, B = cj(a), cj(b)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))
```

**两个信号都不能单独用**：

| 情形 | LCS | CJK | 单独用会怎样 |
|---|---:|---:|---|
| `AngelTear` vs `Neo Angelique: Tenshi no Namida` | 0.31 | **1.00** | CJK 假阳性（探针含「天使の涙」，候选含「天使」） |
| `家属～母と姉妹の嬌声～` vs `Kazoku ~...~`（**只比主标题**） | **0.00** | 1.00 | LCS 假阴性——汉字与罗马字无任何公共字符 |
| `AngelTear` vs `Nerawareta Megami Angeltear`（正确候选） | 0.47 | 0.29 | 两个信号都不高，需靠负向检查排除假阳性 |

> 关键理解：**LCS 为 0 不代表不匹配**。汉字与罗马字之间 LCS 必然是 0。
> 跨语言场景完全依赖 CJK；LCS 的作用是在**同语言**时提供确认信号，
> 以及在 CJK 高分时充当「巧合护栏」。

所以 **A 级要求双证据**：

```python
decisive_same  = top_lcs >= 0.75
decisive_cross = top_cjk >= 0.80 and top_lcs >= 0.35   # LCS 是护栏
if not (decisive_same or decisive_cross):
    return Match("C", "证据不足：LCS %.2f / CJK %.2f（可能是巧合）" % ...)
```

**LCS ≥0.35 这道护栏是关键**。`AngelTear` 的 LCS 只有 0.31，
刚好被拦在门外；而真正跨语言的 `家属～母と姉妹の嬌声～` LCS 是 1.00。

### 负向检查只适用于同语言

字符重叠检查会**误杀中文译名**（中文译名 vs 日文原名本就不该有字符重叠）。
所以启用前必须判断语言：

```python
probe_is_latin = not re.search(r'[\u3040-\u30ff\u4e00-\u9fff]', probe)
title_is_latin = not re.search(r'[\u3040-\u30ff\u4e00-\u9fff]', title)
if not (probe_is_latin and title_is_latin):
    return False, ""   # 跨语言交给 LCS/CJK，不要用字符重叠
```

### 查询变体：本地真名作种子（可平铺率 +150%）

**只拿目录名去查远端，命中率很低。** 必须把本地提取的真名一起作为种子：

| 做法 | 10 个样本的可平铺率 |
|---|---:|
| 只用目录名查 VNDB | **2 / 10** |
| 本地真名作种子 + 远端验证 | **5 / 10** |

典型案例，一步之差：

```
目录名   Kanon PC版 家属～母と姉妹の嬌声～ (KZK@BISHOP)
         → 直接查：0 结果

本地真名 家属～母と姉妹の嬌声～   ← 从 bsxx.ini(UTF-16) 提取
         → A 级命中：Kazoku ~Haha to Shimai no Kyousei~
           v26206 · 2019-09-27 · BISHOP
           CJK 1.00 + LCS 1.00（与中文别名 alttitle 完全一致）
```

注意 LCS 为什么能拿 1.00：探针是中文，候选的 `alttitle` 字段**也是中文**
（`家属～母と姉妹の嬌声～`）。**跨语言匹配靠 CJK，同语言匹配靠 LCS，
两条路径在候选的不同字段上各走一遍** —— 这就是双信号的实现方式。

需要生成的查询变体（按优先级）：

1. 本地提取的真名（**最高价值**）
2. 目录名原样
3. 括号内的内容 —— `xxx (KZK@BISHOP)` 的括号里常是原名
4. 去掉括号的主干名
5. 去掉版本后缀（`v1.05` / `完全版` / `DL版` / `パッケージ版`）
6. 副标题前的主段（`～不連続存在～` 之前）
7. exe 名、去掉 `_32/_64` 的 exe 名
8. 罗马字去符号
9. 厂商名 + 作品名

### 探针要多个，且必须剥离括号

**只用一个探针会漏掉大量正确匹配。** 目录名和本地提取的真名各有优势：

| 探针 | 擅长 | 实测 |
|---|---|---|
| 目录名 | 保留完整日文原名 | `セクフレ幼馴染` → 远端 `alttitle` 一字不差，CJK 1.00 → **A** |
| exe 名 / 本地真名 | 干净、无水印 | `AngelTear` 的 `angeltear` 排除了中文译名干扰 |

真实案例：`セクフレ幼馴染` 目录下 exe 名是 `cfriends`（罗马字缩写）。
只用它比对 → CJK 0.00、LCS 0.30 → **误降为 C 级**；
加上目录名做探针 → CJK 1.00 → **正确 A 级**。

**实现**：两个探针各打一次分，按最终分择优，同一作品取各探针下的最高分。

```python
probes = []
for cand in [name] + local_titles:
    probes.append(clean_probe(cand))   # 剥离括号
    probes.append(cand)                # 原始也保留
```

### 探针必须剥离括号内容

目录名括号里常是**中文译名或水印**，不是作品原名：

```
AngelTear [天使の涙]     ← 「天使の涙」是中文译名
```

若不剥离，探针里的「天使」会让 `Neo Angelique: Tenshi no Namida`
的 CJK 变成 1.00 —— 一个彻底的假阳性。

```python
def clean_probe(s):
    s = re.sub(r"[\(\[\uff08\u3010].*?[\)\]\uff09\u3011]", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip() or s.strip()
```

### CJK 高分要警惕「两字巧合」

`AngelTear`（SYRUP）命中 `Neo Angelique: Tenshi no Namida`（光荣）
—— CJK 1.00，因为双方都有「天使」。**巧合通常只共享 1-2 个常用字。**

所以 CJK ≥ 0.8 时要求至少一项独立佐证：

```python
def _cjk_distinct(x, y):
    cx = set(c for c in x if '\u3040' <= c <= '\u9fff')
    cy = set(c for c in y if '\u3040' <= c <= '\u9fff')
    return len(cx & cy)

if top_c >= 0.8:
    distinct = _cjk_distinct(probe, best_title)
    if top_lcs >= 0.35:
        return Match("A", ...)          # 双证据
    if distinct >= 4:
        return Match("A", ...)          # 跨语言确认
    return Match("D", "仅共享 %d 个汉字，疑为巧合" % distinct)
```

实测：`AngelTear` 共享 2 个汉字 → 正确降级 **D**（列出候选不���论），
而 `セクフレ幼馴染` 共享 10+ 个汉字 → 正确 **A**。

### 探针优先级：靠前探针优先，不是「任一探针最高分」

多个探针合并时，**不能按「任一探针的最高分」择优** —— 会被污染的探针带偏。

实测对比（`AngelTear [天使の涙]`）：

| 探针 | 最佳命中 | LCS |
|---|---|---:|
| `AngelTear`（剥离括号后） | `Nerawareta Megami Tenshi Angeltear` ✓ | 0.15 |
| `AngelTear [天使の涙]`（原始） | `Neo Angelique: Tenshi no Namida` ✗ | 0.80 |
| `angeltear`（exe 名） | `Nerawareta Megami Tenshi Angeltear` ✓ | 0.15 |

原始目录名因为含中文译名「天使」，会额外召回光荣的《Neo Angelique》
（CJK 1.00 假阳性）。**若按最高分择优，A 级假阳性胜出。**

正确做法 —— **探针有序，靠前的优先，只有靠前探针完全无结果时才让靠后参与**：

```python
probes = []
for cand in [name] + local_titles:
    probes.append(clean_probe(cand))   # 清洗过的排前面
    probes.append(cand)                # 原始的排后面

best_pri = min(r.pri for r in all_scored if r.hits)
all_scored = [r for r in all_scored if r.pri == best_pri]
```

这样 `セクフレ幼馴染`（需要完整日文原名）和 `AngelTear`（需要剥离译名）
两种相反的需求都能满足。

### 分数必须与探针一起打包传递

多探针场景下，最容易出的 bug 是**「分数来自探针 A，配对计算却用了探针 B」**：

```python
# ❌ distinct 在判定阶段才算，用的是外层 probe（只对应 top1 候选）
distinct = cjk_distinct(probe, best_title)   # probe 可能是另一个候选的

# ✅ 在 score_one 里就算好，跟分数一起返回
scored.append((final, best_cjk, v, best_lcs, best_distinct))
```

症状是清单里出现 `CJK 100%（0 个不同汉字）` 这种自相矛盾的记录。
**看到这种输出，立刻怀疑配对错位。**

---

## ★ 五分类：按发行渠道决定查哪个站

**这是远端查询的前置步骤。** 之前我一律拿 VNDB 判准 —— 那是错的：
Ren'Py 小游戏在 VNDB 基本查不到（itch.io 独占），
用错索引站等于把整个类别判成「查不到」。

| 类 | 作品性质 | 引擎特征 | 查这个站 |
|---|---|---|---|
| **A** | 厂商 galgame | KiriKiri / Siglus / cats-system / Majiro / BGI / Artemis / Eushully / EAGLS | **VNDB** |
| **B** | RPG Maker 小游戏 | `data/Actors.json`、`www/js/rpg_core.js`、`*.rpgmvo` | **DLsite** |
| **C** | Ren'Py 小游戏 | `game/` + (`lib` \| `MonoBleedingEdge`) | **itch.io** |
| **D** | Unity 游戏 | `*_Data/` / `UnityPlayer.dll` / `app.info` | **Steam** |
| **E** | 查不到 | 都不匹配 | —— |

工具：`scripts/channel_classify.py`（单目录判定）+ `scripts/scan_channels.py`（全库扫描）

### 渠道 URL 优先级最高

发行方自己留的链接文件是**最硬的证据**，压过一切引擎推断：

```
DLsite.url  / *.dlsite.com*   → B
itch.io.url / *itchio*         → C
steam_appid.txt / *steam*     → D
vndb.url                      → A
```

### ⚠️ 引擎 ≠ 渠道

实测反例：`Amusement Dark II Revelation` 是 **Unity 但发行在 DLsite**。

所以「Unity → Steam」只是**先验倾向**。有 URL 证据时以 URL 为准，
没有时才用引擎推断。

### 四个实测踩坑

**1. `icudtl.dat` 不是封包** —— 那是 Unity/NW.js 的 ICU 国际化数据（10MB）。
曾让 88 个游戏误判成「无引擎指纹」。必须排除。

**2. RPG Maker 的 `data/` 在 `www/` 里面** —— NW.js 打包会把资源全塞进 `www/`：

```
变成妈妈的身体v1.01/
├── Game.exe  nw.dll  natives_blob.bin  icudtl.dat  …
└── www/
    ├── data/Actors.json, Map001.json, ...   ← RPGM MV 数据
    ├── js/rpg_core.js                       ← RPGM 核心脚本（铁证）
    ├── Game.rpgproject                      ← 另一条铁证
    └── package.json                         ← ⚠️ 在 www/ 里，不在顶层！
```

只看顶层 `data/` 会把整批 RPG Maker 误判成 E 类。
**铁证是 `www/data/` + `www/js/rpg_core.js` 同时存在。**

**3. 封包常在子目录里**：

```
kiss_crisis/pac/*.ypf          → cats/system engine
ANIM僕ママ/K2sXg4VN/*.gax     → BGI/BeE2
いつまでも息子のまま…4/bgm/*.eog + cg/*.gax → BGI/BeE2
```

**4. NW.js ≠ RPG Maker —— 但也不能只看 `www/` 存在就下结论**

判据是 **`www/data/` + `www/js/rpg_core.js` 同时存在**，不是「有没有 `www/`」。

⚠️ **本条曾因判据不足而得出错误结论，已修正。**
原写「`股市淫狼` 有 `www/` 但 `data/` 里没有 RPG Maker 数据，是厂商 galgame」
—— **错的**。实际 `股市淫狼/www/data/` 与 `www/js/rpg_core.js` 都在，
它就是 **RPG Maker MV 作品**（B 类），当时因判据只查顶层 `data/` 而误判。

**注意 `package.json` 在 `www/` 里面，不在顶层。** 只查顶层会漏掉整批
RPG Maker MV 游戏（实测 `兽耳少女不会背叛`、`NPC系列` 全系列等）。

### 各站的查询方式

| 站 | 怎么查 | 限流 |
|---|---|---|
| **VNDB** | `POST api.vndb.org/kana/vn` | 200 次/5 分钟 |
| **DLsite** | 需日区 IP；作品号形如 `RJ01026809` | 无公开 API，靠搜索 |
| **itch.io** | 有公开 API（`itch.io/api/1/...`），但搜索需认证 | — |
| **Steam** | `store.steampowered.com/api/appdetails?appids=` | 200 次/分钟 |
| | Steam 的 `appid` 可从 `steam_appid.txt` 直接读 | — |

**注意 DLsite / itch.io 都没有好用的公开搜索 API**。实际做法是：
用作品名（日文原名优先）+ 厂商名去搜索引擎 site: 查询，或直接访问
`dlsite.jp/.../work/xxx.html`。

### 变体识别：语言版 / 补丁 exe

**同一个游戏在目录里常有多个 exe，只有一个是本体。** 判定错会导致
把汉化版当游戏本体，进而 VNDB 匹配到错误的条目。

```
母爛漫/          Haharanman.exe  ← 本体
                 Haharanman_汉化补丁.exe   ← 汉化版
Dark Blue/       sfa16.exe       ← 本体
                 sfa16_cn.exe.exe         ← 汉化版（注意双扩展名！）
MakingLovers/    MakingLovers.exe         ← 本体
                 MakingLoversGBK.exe      ← 汉化版
千恋＊万花/       SenrenBanka.exe         ← 本体
                 SenrenBankaCHS.exe      ← 汉化版
清纯会长肉奴洗脑篇/ SiglusEngine.exe       ← 本体（名字里有引擎名但确实是本体）
```

#### ⚠️ 关键词必须**全文匹配**，不能只匹配开头

```python
# ❌ 漏：Haharanman_汉化补丁.exe 的「汉化补丁」在中间
AUX_EXE = re.compile(r"(?i)^(install|patch|汉化|...)")

# ✅ 两级判定
AUX_PREFIX = re.compile(r"(?i)^(install|setup|unins\d*|uninstall|...)")
AUX_ANY    = re.compile(r"(?i)(补丁|patch|汉化|破解|注册机|crack|keygen|"
                         r"更新|update|修正|修复|...)")   # 任意位置
```

实测 290 个多 exe 目录，96% 能正确排除汉化版。
剩余几个是**正当例外**（`SiglusEngine.exe` 本身就是本体）—— 不要硬判。

#### 语言标记的形态

| 形态 | 例子 |
|---|---|
| 下划线 + 代号 | `_cn` `_CHS` `_cht` `_zh` `_jp` `_tc` |
| 紧贴 | `MakingLoversGBK.exe` `xxxBig5.exe` |
| 单词 | `_CN` `_en` `_ENG` |
| 中文 | `_汉化` `_繁体` `_简体` `_机翻` |
| **双扩展名** | `sfa16_cn.exe.exe` ← 剥一层 `.exe` 才能匹配 |

```python
def _lang_variant(f):
    stem = f.stem
    if stem.lower().endswith(".exe"):
        stem = stem[:-4]          # 去掉重复的 .exe
    return bool(LANG_MARK.search(stem))
```

#### 三种变体的层级形态

| 形态 | 判据 | 处理 |
|---|---|---|
| 同目录多 exe | `本体.exe` + `xxx_汉化补丁.exe` | 选无语言标记的那个 |
| 语言版子目录 | `魔界洞ルル・ファレア/簡体字版/Game.exe` | 归并到本体，标版本 |
| 补丁备份目录 | `汉化补丁备份/`、`官方修正补丁备份/` | **是附属，不是游戏** |

实测库里 81 个目录含补丁 exe、3 处语言版子目录、66 处补丁备份目录。

**变体目录不该被当成独立游戏。** 但要注意边界：`SLEEPLESS` 里的
Ren'Py 版是**子目录形态的独立构建**，那属于「同作品两个版本」，
应该归并（`游戏名/{本体, Ren'Py版}`），而不是当补丁。

## 平铺准入（核心策略）

**只有远端存在对应条目的游戏才允许平铺。**

厂商确认**不够** —— 厂商只回答「谁做的」，没回答「这是哪一部作品」。
实测反例：Heiwachan 那 4 个游戏厂商明确（readme + `app.info` 双确认），
但 VNDB `/vn` 和 `/release` 全是 0 结果，属于冷门小众作品。

⚠️ **字母 `A`~`E` 在本工具里有两个独立用途，别混**——它们出现在不同文件、
不同列，靠上下文区分：

| 轴 | 取值 | 出现在 | 含义 |
|---|---|---|---|
| **渠道类别** | `A`~`E` | `channel_map.json.category`、CSV「类别」列 | A 厂商galgame→VNDB / B RPGMaker→DLsite / C Ren'Py→itch / D Unity→Steam / E 查不到 |
| **置信度** | `A`~`E` | `library.json.confidence`、报告「置信度」列 | 这个结论有多可信（见下） |

两者**永不同现于同一列**：渠道类别回答「查哪个站」，置信度回答「结论有多可信」。

**远端匹配置信度**（`verify_remote.py` / `library.json.confidence`）：

| 置信度 | 判据 | 可平铺 | 命名来源 |
|---|---|---|---|
| **A 确定** | LCS≥0.75 或（CJK≥0.8 且 LCS≥0.35） | 是 | remote / local 均可 |
| **B 高** | 相似度 0.35~0.55 且候选唯一 | 是 | 建议 local |
| **C 中** | 有结果但证据不足 | 否 | — |
| **D 低** | 多候选，或触发负向检查 | 否 | — |
| **E 存疑** | 该站 0 结果 | 否 | — |

**准入是「按类别分别判定」的**：

| 类别 | 查哪个站 | 0 结果意味着 |
|---|---|---|
| A | VNDB | 冷门厂商作品，等收录 |
| B | DLsite | 可能已下架 / 改名 / 只在同人站 |
| C | itch.io | itch.io 页面可能已删除 |
| D | Steam | 可能已下架 / 从未上架 |
| E | —— | 引擎不明，**先人工判类别**再查 |

⚠️ **B/C/D 类的「0 结果」比 A 类宽松得多** ——
itch.io 和 Steam 的条目经常搜不到（改名、区域限制、页面删除），
不能因为查不到就断定是冷门。

### 命名来源的选择

统一清单里 `remote.title` 与 `local_name` **都保留**，切换不需要重查：

| 想用 | 取哪个字段 | 适合 |
|---|---|---|
| 远端官方名 | `remote.title`（日文原名居多） | 准确检索、避免生僻字 |
| 本地清洗名 | `local_name` | 中文友好、日常浏览 |
| 两者都要 | `中文名 (Remote Title)` | 折中 |

也可以合并成 `中文名 (Remote Title)` 两种都保留。

### 与分组原则的关系

平铺准入**替代**了旧的「厂商已确认就平铺」做法：

```
旧（错误）：厂商确认 → 平铺 + _来源信息.txt
新（正确）：远端有对应条目 → 落进 library.json → 才可平铺
```

厂商信息不丢弃 —— 它是清单条目的 `remote.vendor` 字段。
厂商官网查不到、远端无条目时，厂商仍可从 readme / `app.info` 确认，
写进条目的 `local_vendor` 字段供人工参考，但**不作为平铺依据**。

### 元数据记录：统一清单，不要散落文件

**不要往游戏目录里写 `.galmeta.json`。** 这有两个真实代价：

1. **证据污染** —— 元数据文件会被下一轮的 `probe_local` 当成游戏自身的
   元数据读回来，形成「远端标题 → 本地真名 → 再去查远端」的自我强化循环。
   实测后果：`AngelTear` 的证据被污染成 `Neo Angelique: Tenshi no Namida`，
   于是匹配到光荣的《Neo Angelique》（光荣，与 SYRUP 的作品完全无关）。
2. **信息仍会丢** —— 一旦游戏被移动/改名/删除，这个文件就与主体分离，
   而清单里的路径关系也断了。

正确做法是**一个统一清单文件**：

```
G:/Game/.workbuddy/scan/
├── library.json    ← 权威清单（数组，每项一个游戏）
├── library.csv     ← 表格视图，供 Excel/记事本查看
└── library.jsonl   ← 断点续跑用，按条 append
```

清单条目结构（本地 / 远端并列，一眼可读）：

```json
{
  "schema": "library/1",
  "local_dir": "G:/Game/合集提取/Kanon PC版 家属～母と姉妹の嬌声～ (KZK@BISHOP)",
  "local_name": "Kanon PC版 家属～母と姉妹の嬌声～ (KZK@BISHOP)",
  "local_titles": ["家属～母と姉妹の嬌声～"],
  "group": "G:/Game/合集提取",
  "engine": "BGI",
  "detected_by": "bsxx.ini",
  "remote": {
    "found": true,
    "vndb_id": "v26206",
    "title": "Kazoku ~Haha to Shimai no Kyousei~",
    "alttitle": "家属～母と姉妹の嬌声～",
    "released": "2019-09-27",
    "vendor": "BISHOP",
    "url": "https://vndb.org/v26206"
  },
  "confidence": "A",
  "reason": "CJK 汉字重叠 100% + LCS 1.00（双证据）",
  "candidates": [...],
  "flatten_allowed": true
}
```

CSV 视图列（面向人）：

```
本地目录 | 本地名 | 远端名称 | 远端地址 | 厂商 | 发售日 | 置信度 | 可平铺 | 证据来源 | 判定理由
```

**命名来源的选择也放这里**：`remote.title` 与 `local_name` 都保留，
需要改成哪个名字时按 `confidence` 决定即可，不重查。

⚠️ 若历史遗留了散落的元数据文件，**一次性清掉并记录 manifest**，
同时在 `probe_local` 里加排除名单（`SELF_GENERATED`），
否则旧的污染文件会继续被读进来。

### 并发：多线程 + 全局令牌桶

**瓶颈是 API 配额，不是串行。** VNDB 限流 200 次 / 5 分钟 ≈ 1.8s/请求。

但「多线程 + 全局令牌桶」依然有明显收益：本地文件读取、
`probe_local` 的目录遍历、HTTP 等待全部重叠。

实测 4 条查询：

| 方式 | 耗时 | 每次 |
|---|---:|---:|
| 串行 | 6.0s | 1.51s |
| 并发 4 线程 + 令牌桶 | **1.8s** | 0.45s |

```python
class TokenBucket:
    """全局令牌桶：所有线程在 API 出口排队，不各自 sleep。"""
    def __init__(self, interval=1.8):
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
```

要点：
- 令牌桶必须**全局共享**。若每个线程各自限流，N 个线程就是 N 倍速率，必然超配额
- 缓存也要加锁（多线程可能同时命中同一 key）
- 调大线程数**不能突破配额上限**，只减少空等
- 结果要**按条 append 到 JSONL**，中断后重跑跳过已完成项 —— 长任务必须可续跑

### ⚠️ 限流的两个致命坑

**坑 1：把 429 响应写进缓存。**

一旦缓存了 `{"error": "HTTP 429"}`，该条目的每次重跑都会命中这个假结果，
被永久判为「远端 0 结果」—— 而它其实根本没查成功。

```python
# ❌ 错误
cache_put(ck, data)          # data 可能是 {"error": "HTTP 429"}

# ✅ 正确：只缓存成功结果
if "error" not in data:
    cache_put(ck, data)
```

**坑 2：命中 429 不退避重试。**
应该在 429 时指数退避并重试，否则一批条目会集体失败。

```python
except urllib.error.HTTPError as e:
    if e.code == 429:
        time.sleep(12.0 * (attempt + 1))   # 退避
        continue                            # 重试
```

### 配额与并发速率的关系（实测）

VNDB 配额是 **200 次 / 5 分钟**，按**总请求数**计算，与并发无关。

| 线程数 | 节流间隔 | 实测结果 |
|---:|---:|---|
| 5 | 1.8s | ❌ 大量 429（177 个里 107 个失败） |
| 4 | 2.4s | ✅ 正常 |

**「1.8s/请求」在单线程下安全，多线程下不够** —— 因为每个游戏会发
2-4 次查询，实际请求速率远高于线程数直觉预期。

### 失败统计必须区分「真 0 结果」与「查询失败」

这是判断数据可信度的关键。跑完后务必核对：

```python
real_zero = sum(1 for r in rows
                if r['confidence'] == 'E' and 'HTTP' not in r.get('reason',''))
http_fail = sum(1 for r in rows if 'HTTP' in r.get('reason',''))
print('真 0 结果 %d，因限流/网络失败 %d' % (real_zero, http_fail))
```

若 `http_fail` 占比很高，**这批数据不能用**，必须清缓存 + 退避后重跑。
提供 `--retry-failed` 参数：把网络类失败视为未完成，重新排队。

## ★ 平铺总流程：三级漏斗（脚本 → AI → 用户）

「把库里的游戏全部提到顶层」**不是一个脚本跑完的事**，而是一条**逐级收窄的漏斗**：
每一级只处理上一级**漏下来**的，判不动的继续往下传，**绝不硬判**。

```
全部目录
   │
   ▼
L1  脚本甄别（flatten_triage.py）      ← 结构规律能覆盖的，这里全吃掉
   │  输出：flatten(平铺) / expand(拆平) / quarantine(副本) / keep / exclude
   ▼  漏下：UNKNOWN、语义桶里的目录
L2  AI 甄别（看实际内容）              ← 结构判不出的，靠「看里面有什么」定夺
   │  输出：✅可平铺 / 🗂合集 / 📦需解压 / ⚫非游戏
   ▼  漏下：取哪个当本体、要不要展开、半解压怎么办
L3  用户甄别（人拍板）                 ← 只有「取舍类」问题才上交，量很小
   │  输出：最终决定
   ▼
执行（flatten_exec.py，走六条铁律）
```

| 级 | 谁判 | 输入 | 判据 | 输出 | 实测占比 |
|---|---|---|---|---|---|
| **L1** | 脚本 | 全部目录 | 目录结构（引擎封包 / exe / 子游戏数） | 平铺计划 `plan.json` | ~80% |
| **L2** | AI | L1 的 `UNKNOWN` + 语义桶 | **看实际内容**：有无可执行入口 + 资源 | 五桶甄别表 | ~15% |
| **L3** | 用户 | L2 的 `❓待人工` | 价值判断（取哪个 / 要不要展开） | 拍板 | ~5% |

**三条铁律：**

1. **逐级收窄，不越级。** L1 能判的别拿去问用户；L3 只接真正需要取舍的。
   越级问 = 拿用户当脚本用。
2. **每一级都不硬判。** 判不动就下传。**硬判比搁置更糟** —— 搁置还能救，判错就没了。
3. **L2 的结论要反哺 L1。** 人工每发现一类新形态，补进 `shape_of()` / `atomic_detect`，
   下次漏斗就能自动吃掉。**每跑一轮，L1 的覆盖率都该变高。**

> **注**：本漏斗解决**「怎么平铺」（手段）**；**「够不够格平铺」（资格，远端是否对得上条目）**
> 见上一节「平铺准入」。两者独立 —— 形态判对了，不等于准入通过了。

> **实测（2026-10-04，`unknow/` 118 条）**：L1 判不动、交给 L2 的目录里，
> **约 93 个其实是能直接平铺的游戏**，真正要 L3 拍板的只有 5 个。
> 说明「判不动」里藏着大量好游戏 —— **下传不是放弃，是换手段**。

---

## ★ L1 脚本甄别 —— 平铺分诊（顶层 → 平铺）

**这是三级漏斗的第一级，也是「把库里的游戏全部提到顶层」这条主线的入口。**
在 `library.json` 元数据入库之前，文件系统是唯一的载体，所以每一步都要可复核、可回滚。

### 用户决策（2026-10-04 确认，后续默认执行）

| 情况 | 决策 |
|---|---|
| **合集**（厂商 / 系列 / 引擎桶） | **全部拆平** —— 子游戏各自提到顶层 |
| **空壳 / 变体包装**（顶层只剩一个子目录） | **整目录当一个游戏条目，内部结构原样不动** |
| **`[副本N]` 重复目录** | 移到 `待删除/`（**不直接删**） |
| **未解压压缩包** | **保持不动**，只列「待解压清单」 |

### 形态表（`scripts/flatten_triage.py` 的 `shape_of`）

| shape | 判据 | action |
|---|---|---|
| `DUPLICATE` | `xxx [副本N]` **且**有体量/内容证据（父目录是游戏 / 子项重合≥60% / 父层净内容≈副本体量） | quarantine → `待删除/` |
| `GAME` | 自身是原子游戏（`classify_one == game`） | flatten |
| `GAME`（带标注） | 同上，但 note 会附「自带运行补丁（勿删）: xxx」或「注入式补丁 BepInEx」 | flatten（**标注不改动作**；补丁是运行刚需，非"污染"） |
| `STAGING` | 下载暂存目录（`解压密码.txt` / 下载站文件） | split（按内容重新归位） |
| `CONTAINER` | ≥2 个游戏子目录 | expand（**递归**拆平） |
| `VARIANT_WRAP` | 子目录只有 位/语言/版次 变体，且里面有游戏 | flatten（整目录当一条） |
| `SHELL` | 只有 1 个游戏子目录 | flatten（整目录当一条） |
| `ARCHIVE` | 只有压缩包/镜像，无 exe | keep（不动，列清单） |
| `TOOL` | 工具 / 残骸 / 无主程序 | exclude |
| `UNKNOWN` | 判不出 | review（人工） |

判据**有顺序依赖**，务必按上表自上而下：

```
① 冗余副本判定  必须在「自身是不是游戏」之前（`五印传说 [副本1]` 本身是游戏）
② 自身是 GAME   必须在 CONTAINER 之前 —— 否则 Ren'Py 的 `game/`+`lib/`
                会被当成「两个游戏」→ 误判 CONTAINER
③ CONTAINER(≥2) 必须在 SHELL(==1) 之前
④ STAGING       必须早于 GAME —— 暂存目录里也有压缩包/游戏，但目录名是噪声
```

### 三个实测踩坑（都是真 bug，已修）

**1. 冗余副本 ≠ 见到 `[副本N]` 就删（但实测 5/5 都是冗余）**

反直觉点：`[副本N]` **名字看着像唯一本体**，其实全是父目录的一份完整拷贝。
实测全库 5 个（2026-10-04 复核）：

| 副本 | 父层总 / 副本 | 父层净内容 | 副本缺什么 |
|---|---|---|---|
| `2024-07/催眠学习 [副本1]` | 5986.9 / 2990.8 MB | ≈2996 MB | 缺 5 个发布说明 txt，**本体齐** |
| `2025-10/素晴らしき日々HD版 [副本1]` | 9386.2 / 4679.3 MB | ≈4707 MB | — |
| `2025-11/完全痴漢…[副本1]` | 7643.8 / 3821.9 MB | ≈3822 MB | — |
| `2025-03/メスガキ…[副本1]` | 1998.4 / 992.1 MB | ≈1006 MB | — |
| `2025-09/五印传说 [副本1]` | 2224.1 / 796.9 MB | ≈1427 MB | — |

**五个父目录都自带完整本体**（封包/exe/dll 齐），副本纯冗余，
隔离后共可省 ≈13.3 GB。

⚠️ 曾经踩的坑：凭「父目录里 `pick_main_exe` 只看到 `*_crack.exe`」就断定
「父目录只剩 crack exe、副本才是唯一本体」——**错的**。crack exe 就是启动器，
真正体量在 `pac/*.ypf`。**判冗余必须用体量/内容证据，不能凭 exe 印象**。

```python
# 判据（flatten_triage.is_redundant_copy，命中任一即冗余）：
#   ① 父目录本身是游戏（classify_one(parent) == game）
#   ② 副本与父目录直接子项名字重合 ≥60%
#   ③ 体量对照：父层净内容(剔除副本) ≈ 副本体量  ← 最可靠
# 三条都要求"有证据"，不确定时宁可返回 "" 交给人工复核
```

`2024-12/Heiwachan…/4P Play 32bit 64bit/` 是另一类坑：
目录名以 `32bit 64bit` 结尾，看起来很"变体"，其实**整份就是游戏**
（里面 `32位/`+`64位/` 各是一个完整 Unity 构建）。

### 附：`[副本N]` 是怎么来的（成因诊断法）

`[副本N]` **不是游戏自带的**，是「复制/重新解压时撞名被自动改名」的产物。
用 4 个信号 30 秒就能定性（以 `2024-07/催眠学习` 实测为例）：

| 信号 | 读法 | 催眠学习的实测值 |
|---|---|---|
| **创建时间差** | 副本 `ctime` 远晚于父目录 → 副本是后来复制出来的 | 父 2021-11-29，副本 2024-07-13（晚 2 年 7 个月） |
| **命名风格** | `[副本N]`（方括号、无空格）不是资源管理器的风格（它是 `xxx - 副本`），更像网盘客户端 / 下载解压工具撞名改名 | `催眠学习 [副本1]` |
| **mtime 整体偏移** | 副本内文件 mtime = 原件 − 1h（或 +1h）→ 跨时区/UTC 解释写盘，说明**重新解压**过而非纯复制 | 11 个 pac 文件全部 −1h |
| **内容重合** | 封包 md5 相同、pac 同名同尺寸、只是缺发布说明 txt | `bn/sc/vo.ypf` md5 全同；缺 5 个说明文件 |

其它 4 个副本的命名也印证了第 2 点：`メスガキ…_Ver1.02/メスガキ… [副本1]`、
`完全痴漢…_Ver1.0/完全痴漢… [副本1]` —— 都是**版本标记目录里又落了一份**，
典型的「换版本重下 → 撞名 → `[副本N]`」。

```python
# 快速诊断（只读）
# 1) 目录创建时间：st_ctime  （Windows 上是创建时间）
# 2) 副本内文件 mtime 与原件逐项做差，看是否为恒定 ±1h
# 3) 抽样 md5 比对关键封包
# 4) 体量：父层总 ≈ 副本 × 2  → 副本冗余
```

结论口径：**这类副本一律按冗余隔离**；若 4 个信号有任一不成立，
降级为 `review` 交人工，不要自动删。

**2. 子目录封包要单独认（`pac/*.ypf`）**

`2024-07/催眠学习/` 的主程序被 crack 顶掉，`pick_main_exe` 只看到
`Saimin_Gakushu_crack.exe` + `settings.exe`（全是辅助）→ 判 tool。
但真封包在 `pac/*.ypf`（YU-RIS / cats-system）。

```python
PACK_SUBDIRS = {"pac","data","bgm","cg","se","voice","plugin","movie","ev",...}
# 已知资源子目录里 ≥2 个 ≥8MB 的封包 -> 是游戏
```

同类：`kiss_crisis/pac/*.ypf`、`ANIM僕ママ/K2sXg4VN/*.gax`、`イチャ姉！/Data/*.arc`。

**3. `vcredist_x86.exe` 曾被当成主程序**

`廃村少女/` 里 `haison.exe` 1.7MB、`vcredist_x86.exe` 3.9MB，
`pick_main_exe` 取「最大非辅助」→ 选中了 vcredist → 判不出。
**辅助名单必须含** `vcredist|dxsetup|dxwebsetup|directx|dotnetfx|oalinst|redist`。

### 新增的引擎指纹（本轮补入 `PACK_SIGS` / 判据）

| 引擎 | 指纹 | 实测案例 |
|---|---|---|
| NScripter | `nscript.dat` / `*.ns2` / `*.nsa` | `姦獄島`（`00.ns2` 374MB） |
| GameMaker | `data.win` | `扶她的射精管理`（128MB） |
| Flash/SWF | 同层或子目录 ≥3 个 `*.swf` | `[エロ獅子組]`（`dou/*.swf` ×68） |
| 主程序 + 大封包 | 非辅助 exe + ≥2 个 ≥1MB 或 1 个 ≥32MB 的 `.bin/.dat/.med/.pk2…` | `廃村少女`、`妻の母`、`executive2` |
| 主程序 + `*_Data/` | exe + `xxx_Data/` 目录 | `おままごと` |
| 子目录封包 | 见上「踩坑 2」 | `催眠学习`、`イチャ姉！` |

> ⚠️ 「主程序 + 大封包」要求 **≥2 个封包或单个 ≥32MB**。
> 只要求「≥1 个」会被补丁目录误伤 —— 实测
> `island/汉化补丁0808/汉化补丁0808/` 只有 `island.exe` + `scr.pak`
> （补丁小封包），曾被判成独立游戏，进而让 `island` 误判成「两个游戏的合集」。

**4. 「破解补丁」是运行刚需，不是"污染"（别建议删除）**

⚠️ **定性先摆正**：日文 galgame 原版普遍带 DRM / 光盘校验。`AlphaROMdiE`、
`SiglusEngine_Patch`、`Start_Cracker`、`BepInEx` 这类文件是**免验证 / 正常显示**
的运行前提 —— **删了游戏就跑不起来或乱码**。所以「自带补丁」是**好消息**，
只作信息标注，**绝不给"删除"建议**。
（早期版本曾写「删工具留本体」—— 那是错的，游戏会启动不了。）

**第二层才是判据 bug**：旧扫描器用裸的 `"cracker" in name`，对**所有文件**生效，
把纯音效/音乐素材当成了破解工具，误丢进 `unknow/4-破解污染/`：

| 误报文件 | 实际是什么 | 命中的子串 |
|---|---|---|
| `Fleeting Iris IF/Audio/BGM/041 - Nutcrackers.mp3` | 胡桃夹子（柴可夫斯基）BGM | Nut**cracker**s |
| `VenusBlood Valkyrie/Standalone/effectaudio/ads06_cracker_pulls_01` | 拉炮音效（cracker=拉炮） | **cracker** |

正确判据 —— **只在可执行文件里找，且用锚定正则**：

```python
CRACKER_EXT = (".exe", ".com", ".bat", ".cmd", ".scr", ".dll")
CRACKER_NAME = re.compile(
    r"(?i)^(alpharom\s*cracker|alpharomdie|alphrom|start_cracker|cracker|"
    r"siglusengine_patch|siglusuniversalpatch|arc_unpacker|garbro|"
    r"enigmaunpacker|enigmainscene|vaild|thedesigner|megpatch)"
    r"[\s\d._\-]*\.(exe|com|bat|cmd|scr|dll)$")
```

⚠️ **反向陷阱**：不要因为 `BepInEx` / `doorstop_config.ini` / `winhttp.dll`
就判「破解」—— 大量**汉化补丁**正是用 BepInEx 注入实现的。实测
`VenusBlood Valkyrie`、`SUMMER ～夏の14日～`、`toeha1_0_1_signed`、
`FEMBOY FUTA HOUSE` 四个都属此类。

API：`atomic_detect.detect_cracker_tools(folder)` → 相对路径列表
（**空 = 原版未带补丁，也很正常**，不表示有问题）；
`atomic_detect.detect_ui_patch(folder)` → 是否含 BepInEx 注入补丁。
`flatten_triage` 把结果写进 GAME 行的 `note`（**不改 action**）。

### 实测回归（本轮）

顶层 375 → 分诊 586 条（含合集展开）。
下表为 **2026-10-04 副本隔离后** 的现状（隔离前 `DUPLICATE=5`、`CONTAINER=21`）：

| shape | 数量 |
|---|---:|
| GAME | 558（其中 2 个标注自带运行补丁、4 个标注 BepInEx 注入补丁 —— 均为信息，非问题） |
| CONTAINER | 20 |
| VARIANT_WRAP | 5 |
| SHELL | 1 |
| DUPLICATE | 0（5 个已隔离到 `_待删除/`） |
| UNKNOWN（残骸/工具组） | 2 |

修正前后逐条对比：**15 处变化，全部是 `unknown/tool → game`，零误降级**。
（13 个 unknown→game + 2 个 tool→game）

用法：

```bash
# 分诊（只读），打印摘要
python scripts/flatten_triage.py "G:/Game"
# 导出计划供复核
python scripts/flatten_triage.py "G:/Game" --csv 平铺计划.csv --json 平铺计划.json
# 只看某一形态
python scripts/flatten_triage.py "G:/Game" --shape CONTAINER
```

⚠️ **分诊只产出计划，不移动文件。**

### 执行器（`scripts/flatten_exec.py`）

消费分诊计划，**安全地**移动目录。铁律（2026-10-04 确认，不可绕过）：

| 铁律 | 实现 |
|---|---|
| **默认 dry-run** | 不加 `--apply` 绝不碰文件，只出预览 + manifest |
| **不覆盖** | `dst` 已存在 → `conflict` 跳过并记账，绝不 merge |
| **不删除** | 只 `os.rename`；搬空后的空壳只登记，不自动删 |
| **写 manifest** | 每步 `src/dst/status/reason/ts` 落盘 JSON + CSV |
| **可回滚** | `--rollback manifest.json` 逆序搬回，一步不差 |
| **同盘原子** | 全程只在库根内 rename（不复制数据，秒级） |
| **凭据只增不删** | `--apply` 产物前缀 `manifest_`（回滚凭据）；dry-run 前缀 `preview_`，随便删都不伤回滚 |
| **回滚可自证** | `--check-rollback` 只校验可行性，不真回滚 |

**执行顺序：必须按 `src` 深度降序（深的先做）。**
原因：`[副本N]` 嵌在父目录**里面**（`催眠学习/催眠学习 [副本1]`），
必须趁原路径还有效时先隔离；否则父目录一平铺，副本路径就跟着变了。
（`build_moves` 里统一 `sort(key=(-depth, src))`，天然满足
「quarantine 先于 flatten」——副本总是更深。）

**目标重名 → 一律不猜，全部跳过（`dup_dst`）。**
实测 4 组：`galfamily` 是完全相同内容放两处，
而 `ナギツルギ`(1796.9 vs 1087.0MB)、`姦禁病棟`(1563.2 vs 1590.1MB)
是**两个不同版本** —— 不能替用户挑。成组打印交人工。

```bash
# 预览（默认，不动文件）—— 产物是 preview_*.json/csv，删了不心疼
python scripts/flatten_exec.py .workbuddy/flatten/plan.json
# 真执行 —— 产物是 manifest_*.json，**回滚凭据，绝不能删**
python scripts/flatten_exec.py .workbuddy/flatten/plan.json --apply
# 只做最安全的一步：隔离副本
python scripts/flatten_exec.py .workbuddy/flatten/plan.json --only quarantine --apply
# 检查还能不能回滚（不真回滚）
python scripts/flatten_exec.py --check-rollback .workbuddy/flatten/manifest_xxx.json
# 回滚
python scripts/flatten_exec.py --rollback .workbuddy/flatten/manifest_xxx.json
```

⚠️ **踩过的坑（2026-10-04 实际发生）**：用 `ls -t | tail -n +3` 清理输出目录，
把刚 `--apply` 的 `manifest_*.json` 连同 `plan.json` 一起删了 ——
**回滚凭据没了，5 个副本就再也撤不回来**。
教训：
1. `manifest_*` 是凭据，**只能人工确认后删**；要清就只删 `preview_*`。
2. 万一手滑删了，可**按 `_待删除/` 的实际状态反推重建**（`dst` 去掉
   `_待删除/` 前缀就是 `src` 相对库根的路径），重建后立刻 `--check-rollback` 自证。
   重建产物要标 `reconstructed: true`，别假装是原件。

**自检**：`python scripts/flatten_exec_selftest.py`
在临时区建合成目录树，端到端验证 13 项（dry-run 不动文件 / 平铺 /
副本隔离 / dup_dst 跳过 / 不覆盖 / 空壳登记不删 / 回滚复原），**不碰真实库**。
改动执行器后**必须先跑它**再上真库。

---

## ★ L2 AI 甄别 —— 分诊判不出时，直接看内容定夺

`flatten_triage.py` 只覆盖**有结构规律**的情况。当它给出 `UNKNOWN`、
或一批目录是被旧扫描器按「语义桶」丢进某个**字面名字很吓人的目录**时，
脚本会失效 —— 这时**改用人工逐条看实际内容**。

### 何时启用

| 触发 | 说明 |
|---|---|
| 分诊结果 `UNKNOWN` / `review` | 脚本判不出，硬判会更糟 |
| 目录处在一个「语义桶」里 | `unknow/1-未解压/`、`2-空壳层/`、`4-破解污染/`… 这些桶名来自**旧扫描器** |
| 用户明确说「不要用脚本，你自己看」 | —— |

### 核心认知：桶名 ≠ 事实

**桶名只表示「当时那个脚本为什么把它丢进来」，与目录现状无关。**
实测（2026-10-04，`unknow/` 共 118 条）：

| 桶名 | 听起来 | 实际 |
|---|---|---|
| `1-未解压/` | 里面都是压缩包 | 25 个里 **19 个早已解压**，真需解压的只有 1 个 |
| `2-空壳层/` | 空壳、没用 | 42 个**全是「译名壳 + 内层真身」**，全部可用 |
| `3-无启动程序/` | 都没有 exe | 8 个里 **3 个其实有本体**（埋在子目录 / exe 被改名） |
| `4-破解污染/` | 该删的污染 | 4 个**全是能直接玩的游戏**（见「踩坑 4」：运行补丁是刚需）；**2026-10-04 已平铺并删桶** |

⚠️ 所以**不要沿用桶名，也不要相信桶名**。必须重新按内容判断一遍。
> `4-破解污染/` 于 2026-10-04 已**整体平铺到顶层并删除该桶** —— 见下「平铺是默认结局」。

### 判定标准（只有一条）

> 里面是不是一个**能直接用的游戏本体**，且**不需要先解压 / 拆壳**？

不需要识别引擎、不需要联网 —— 只要看目录里**有没有可执行入口 + 资源**。
（引擎/厂商/真名考据是**后面**的事，这一步只管「能不能平铺」。）

### 操作纪律

1. **只读**：只用 `ls` / `Read` 看结构与文本，**不改、不删、不移**。
   （要动手时另走 `flatten_exec.py` 的铁律流程。）
2. **逐条判**，每条给出「是什么」：入口文件 + 引擎 + 一句话。
3. 输出**五个桶**，让用户一眼能决策：

   | 桶 | 含义 | 后续动作 |
   |---|---|---|
   | ✅ 可直接平铺 | 本体已就绪 | 取目录提到顶层 |
   | 🗂 合集 | 展开后是多个游戏 | 递归拆平 |
   | 📦 需先解压 | 只有分卷/镜像 | 列清单，先解压 |
   | ⚫ 非游戏/残骸 | 工具 / 视频 / CG / 空壳 | 排除 |
   | ❓ 待人工 | 取哪个当本体 / 要不要展开 | 交用户拍板 |

4. 产出 `<桶目录>/平铺甄别.md`，附**结论总览表 + 逐条明细 + 重复项附录**。
   模板：`references/平铺甄别模板.md`。

### 高频形态速查（人工看目录时会反复遇到）

| 形态 | 长相 | 判定 |
|---|---|---|
| **壳 + 内层真身** | 外层是中文译名，里面一个日文/英文目录才是本体 | 取**内层**目录 |
| **双层同名** | `田舎の性活/Summer~…/Summer~…/` | 一直进去取最内层 |
| **乱码目录名** | `偁傑儅儅傎傝偭偔/`（GBK 误读 Shift-JIS） | **不影响运行**，别当残骸；里面 `amamama.exe` 正常 |
| **exe 被改名** | `Game.exe.org` | 改回 `.exe` 即可跑，属**可平铺** |
| **纯视频 / CG** | 只有 `.mp4` / 画集目录 | 非游戏 |
| **Unity 残骸** | 有 `*_Data/` 但**主程序 exe 没了**（只剩 `UnityCrashHandler64.exe`） | 残骸，除非能补回 exe |
| **网页资源** | `js/ css/ spine/`，DMM 页游资源 | 无可执行本体 → 非游戏 |
| **多语言版共存** | `China/`+`ENG/`+`JPN/` 三份 | 取其一 |
| **安装盘目录** | `Disc/files/` 里才是本体 | 取 `files/` 层 |
| **半解压** | 已有解压目录，又留着 `.rar/.zip` | 确认以哪个为准，再平铺 |

### 结论要反哺脚本

人工每发现一类**新形态**，就补进 `flatten_triage.shape_of()` / `atomic_detect` 的判据，
下次自动分诊即可覆盖。**人工甄别（L2）是脚本（L1）的教材，不是替代品。**

### 平铺是默认结局，桶不是分类

**一个目录只要确认是「能直接玩的游戏」，就该平铺到顶层** —— 不管它来自哪个桶、
当初为什么被丢进来。**留在 unknow 的正当理由只有一个：真的判不出来。**

- **桶是临时工作台，不是永久分类。** 不要为了「记录它曾经有个小毛病」而保留桶。
  `4-破解污染/` 就是反面教材：4 个游戏被一个误判标签困了很久，其实它们跟顶层任何
  游戏没有区别 —— 2026-10-04 已整体平铺、桶目录删除。
- **桶清空后直接 `rmdir` 删掉**，别留空壳。
- **平铺与否只看内容，不看标签。** 名字里有「破解」「未解压」「残骸」都不构成保留理由。
- 这类「桶内平铺」一般不在 `plan.json` 里（分诊只扫顶层），直接同盘 `mv` 即可 ——
  但仍要**先核对顶层无重名**、**写回滚凭据**再动手。

---

## ★ L3 用户甄别 —— 剩下的交人拍板

L2 跑完还会剩一小撮。注意区分：**它们不是「看不出」，而是「要取舍」。**
这类不该由 AI 代决 —— 列清楚、给选项、等用户拍板。

### 什么样的才算 L3

| 类型 | 例子 | 为什么 AI 不能定 |
|---|---|---|
| **取哪个当本体** | `雨音/` 里本体 + `amane_dezi_chs/` + `cg达成率100%修正补丁/` 三层 | 涉及"要不要保留补丁版"的偏好 |
| **要不要展开** | `TOFU SOFT/` 是合集，但用户可能想整体留着 | 涉及库的组织意图 |
| **半解压怎么办** | 已有解压目录，又留着 `.rar/.zip` | 以哪个为准 / 算不算重复 |
| **要不要补全** | Unity 残骸缺主程序 exe | 取决于用户手上有没有完整版 |

**判断口径**：如果这条需要「猜用户想要什么」，就是 L3；如果是「看事实」，那是 L2。
**L3 的条数应该很小（实测 ~5%）** —— 如果一上来就几十条要问用户，说明 L1/L2 没做够。

### 上交格式：一次列完，别一条条追问

| 条目 | 情况 | 需要你定 |
|---|---|---|
| `0/雨音` | 本体 + `amane_dezi_chs/` + `cg达成率100%修正补丁/` | 取哪个当本体？ |
| `1/アトリエかぐや BARE＆BUNNY` | 2 个已解压 + 其余 zip/iso | 要不要一起解压？ |
| `1+其他/①②③` | `1/` 见 1 个游戏，2/3/4 结构不明 | 要不要全展开？ |

拍板后**直接回到 L1 的执行流程**（`flatten_exec.py` 六条铁律），不再重跑甄别。

---

## 常见陷阱清单

| 陷阱 | 后果 | 防范 |
|---|---|---|
| 清洗目录名时丢掉括号后缀 | `女装学園（孕）` → `女装学園`，与`（妊）`混淆 | 后缀是身份的一部分，不删 |
| 按拼音音译猜名字 | 编造出「天使の涙」等假名 | 音译猜测不算证据 |
| 递归下钻引擎资源目录 | 把 `audio/se`、`www/` 当成游戏 | 只搜当前层，要排除资源目录 |
| 把外壳目录当本体 | 真名丢失 | 向上追溯父目录 |
| 把 32/64 当两个游戏 | 同一作品被拆散 | 看 readme 与 exe 关系 |
| 把汉化版 exe 当本体 | `Haharanman_汉化补丁.exe` 被选中 | 关键词**全文匹配**，非开头 |
| 只匹配 exe 开头找辅助程序 | `_汉化补丁` 在名字中间，漏判 | 用 `search` 而非 `match` |
| 漏了双扩展名 | `sfa16_cn.exe.exe` 匹配不到 | 先剥一层 `.exe` |
| 数字目录名当游戏名 | 製品番号被误解 | 纯数字 → 查数据库 |
| 只看顶层 exe | 漏掉深层真正的入口 | 递归找，但要排除工具 exe |
| **把 readme 章节名当标题** | 得出「はじめに」这种无意义结果 | 目录项附近的字符串要排除 |
| **把转录组水印当标题的一部分** | `兽耳少女不会背叛（大概） 绅士回廊云翻` | 剥离水印词并降级为 D |
| **把引擎自带 exe 当主程序** | Ren'Py 的 chardetect/dxwebsetup 被选中 | 排除 `lib/`、`site-packages/` 路径 |
| 按 UTF-8 读所有文件 | 读出乱码，错过答案 | 探测编码后再读 |
| **在目录名里用 `/` 分隔信息** | Windows 立即报错 `WinError 3` | 用 `〔〕` `（）` ` - ` 或 `_`，**动手前先验证** |
| **把「有多个候选」当作降级理由** | A 级被误降 | 只在首位候选可疑时降级 |
| **新建 `_待删除/` 后被当成日期目录扫进去** | 分诊计划里混进隔离区噪声行 | `atomic_detect.is_internal()`：点开头 或 `_待删除`/`_flatten`/`_quarantine` 一律跳过；`flatten_triage` 与 `scan_tree` 共用这一个判据，别各写各的 |
| **平铺时子项先于父项被搬走** | 父目录被掏空、游戏搬不全 | 执行器强制 `sort(key=(-src深度, src))`；`[副本N]` 必在父目录内，所以「深先做」天然满足「先隔离后平铺」 |
| **两个源平铺到同名目标** | 后者被覆盖，丢游戏 | 一律不猜：全部标 `dup_dst` 跳过并成组打印，交人工。实测 4 组里有 3 组是**不同版本**，自动挑必错 |
| 相信远端结果不验证 | 匹配到同名不同作品 | 验证引擎/年份/规模 |
| 批量查询不节流 | 被 API 限流封禁 | 1.8s 间隔 + 缓存 |
| **用字符集相似度匹配** | `AngelTear` 匹配到 `Neo Angelique`（0.95 假阳性） | **必须用 LCS** |
| **看到 LCS=0 就判定不匹配** | 汉字 vs 罗马字 LCS 必然为 0，跨语言场景会全被误杀 | LCS 只管同语言，跨语言看 CJK |
| **只用 CJK 或只用 LCS** | 前者假阳性、后者假阴性 | A 级要求双证据，LCS≥0.35 作护栏 |
| **跨语言时用字符重叠负向检查** | 误杀中文译名 | 负向检查仅限同为拉丁文时启用 |
| **只用目录名查远端** | 可平铺率减半 | 本地真名作查询种子 |
| **VNDB fields 写嵌套对象名** | HTTP 400 | 写 `titles{title,lang,main}`、`developers{id,name,original}` |
| **调试时不清缓存** | 拿到旧结果误判算法 | `rm -rf ~/.cache/gal-lookup` |
| **把厂商确认当作平铺依据** | 冷门作品无处可对就平铺了 | 准入要看远端是否有条目 |
| **往游戏目录里写元数据文件** | 下轮 `probe_local` 读回它，证据自我强化污染 | 统一清单，不散落文件 |
| **元数据文件被当成本地证据** | 实测 `AngelTear` 被污染成 `Neo Angelique`（光荣） | `probe_local` 排除 `SELF_GENERATED` 名单 |
| **每线程各自限流** | N 线程 = N 倍速率，必然 429 | 全局共享令牌桶 |
| **长任务不可续跑** | 中断即从头再来 | 结果 append 到 JSONL，重跑跳过已完成 |
| **下探时排除位版本/引擎子目录** | `4P Play/32位` 被当成独立游戏 | 排除 `32位/64位/www/locales/renpy/data` 等 |
| **「顶层全是压缩包」就判非游戏** | 实测 18 个真游戏被误判 tool（白色相簿2→Leaf、对魔忍→风雪2、勇者大战魔物娘→勇战r…） | `find_body_subdir` 必须排在 `detect_non_game` 之前 |
| **拿目录名当考据种子（staging 目录）** | `angeltear/` 里压的是「赞美女神」和「町ぐるみの罠」，跟目录名毫无关系 | staging 目录改用压缩包名当种子 |
| **用「压缩包名≠目录名」判 staging** | 误报 149 个：`ever17/` 里是 `A036@忧郁的弟弟`（产品番号）、`27岁女教师/` 是多卷、插件目录本来就不叫游戏名 | 必须两条强信号同时成立；「无交集」只能辅助 |
| **站点正则用泛化词** | `里番\|无码\|图鉴君` 让正常插件目录全被误判 | 只认文件名明确带站点名的 |
| **只用单个探针比对** | `セクフレ幼馴染` 误降 C 级（实际是 A） | 目录名与本地真名各跑一遍，取最佳 |
| **探针不剥离括号内容** | `AngelTear[天使の涙]` 的中文译名造成 CJK 假阳性 | 比对前先剥括号 |
| **CJK 高分就直接给 A** | 两字巧合被当成跨语言确认 | 要求 LCS≥0.35 **或** 共享≥4 个不同汉字 |
| **把 HTTP 429 写进缓存** | 该条目永久被判「远端 0 结果」 | 只缓存成功响应 |
| **命中 429 不退避重试** | 一批条目集体失败 | 指数退避 + 重试 |
| **多线程下沿用 1.8s 节流** | 实测 5 线程会大量 429 | 4 线程用 2.4s |
| **不区分「真 0 结果」与「查询失败」** | 把限流当成「查不到」，数据全废 | 跑完统计 HTTP 错误占比 |
| **多探针按「任一最高分」择优** | 污染探针带偏，AngelTear 误判为光荣作品 | 探针有序，靠前优先 |
| **分数与探针分开传递** | 出现 `CJK 100%（0 个汉字）` 这种自相矛盾输出 | 在打分处一并算好，打包返回 |
| **把 `vcredist_x86.exe` 当主程序** | `廃村少女/haison.exe` 1.7MB 被 vcredist 3.9MB 顶掉，真游戏判不出 | 辅助名单含 `vcredist\|dxsetup\|directx\|dotnetfx\|redist` |
| **漏了子目录封包 `pac/*.ypf`** | `催眠学习` 主程序被 crack 顶掉，判成工具 | 已知资源子目录里 ≥2 个 ≥8MB 封包 → 是游戏 |
| **凭「父目录只剩 crack exe」断定 `[副本N]` 是唯一本体** | 曾把 `催眠学习 [副本1]` 误标成"唯一本体、删了就没了"；实测父目录本体齐全（`pac/*.ypf` 全在），副本纯冗余 | 判冗余只看**体量/内容证据**：父层净内容 ≈ 副本体量 / 子项重合≥60% / 父目录本身是游戏。`*_crack.exe` 就是启动器，别当"没有主程序" |
| **把变体包装目录当成「两个游戏」** | `Heiwachan…/4P Play 32bit 64bit/`（内是 `32位/`+`64位/`）被判成合集 | `*_Data`+exe 即游戏；变体目录有 exe 也算游戏本体 |
| **自身是 GAME 却去数子目录** | Ren'Py 的 `game/`+`lib/` 被当成两个游戏 → 误判 CONTAINER | `classify_one(自身)==game` 必须先判 |
| **「非辅助 exe + 1 个小封包」当游戏** | `island/汉化补丁0808/` 的 `island.exe+scr.pak` 被当独立游戏 | 要求 ≥2 个封包或单个 ≥32MB |
| **把运行补丁当"污染"要清理** | `AlphaROMdiE`/`SiglusEngine_Patch`/`Start_Cracker` 是日文游戏能跑的前提，删了启动不了/乱码 | 只作信息标注、**绝不删**；见踩坑 4 |
| **裸子串 `"cracker" in name` 判破解** | `Nutcrackers.mp3`（胡桃夹子 BGM）、`ads06_cracker_pulls_01`（拉炮音效）被当破解工具 | 只在**可执行文件**里找，用锚定正则 |
| **见 `BepInEx`/`winhttp.dll` 就判破解** | 大量汉化补丁就是 BepInEx 注入；实测 4 个游戏被误标 | BepInEx 只作信息标注，不算问题 |
| **把「引擎 Unknown」当成问题** | 判不出引擎 ≠ 有问题，会白丢一大批好游戏 | Unknown 只是认不出引擎，不是 quarantine 理由 |
| **把旧扫描器的「语义桶」当事实** | `unknow/1-未解压/` 里 25 个有 19 个早已解压、`2-空壳层/` 42 个全可用、`4-破解污染/` 4 个全是好游戏 —— 桶名只记录「当初为什么被丢进来」 | 桶名不可信，必须重新按内容判；见「L2 AI 甄别」章节 |
| **分诊器给 `UNKNOWN` 就硬判或放弃** | 硬判会把好游戏判死，放弃则永远搁置 | 转人工逐条看内容；人工发现的新形态反哺 `shape_of()` |
| **乱码目录名当残骸** | `偁傑儅儅傎傝偭偔/` 是 GBK 误读 Shift-JIS，里面 `amamama.exe` 运行正常 | 乱码只看编码，不代表目录坏 |
| **有 `*_Data/` 就判游戏** | `ShutenOrder`、`2090013680` 只剩 `*_Data/` + `UnityCrashHandler64.exe`，主程序没了 | 必须确认**主程序 exe 在**，否则是残骸 |

## 脚本

- `scripts/channel_classify.py` — **五分类判定**（按发行渠道，返回该查哪个站）
- `scripts/scan_channels.py` — 全库五分类扫描，输出 `channel_map.json` + CSV
- `scripts/atomic_detect.py` — **原子游戏目录识别**（七级分类，纯结构判据不联网）
  - `detect_cracker_tools(folder)` — 自带运行补丁探测（**只认可执行文件**；是信息，不是"污染"）
  - `detect_ui_patch(folder)` — BepInEx 注入式补丁探测（多为汉化，≠破解）
- `scripts/flatten_triage.py` — **L1 脚本甄别**（顶层 → 形态 → 平铺计划，只读）
- `scripts/flatten_exec.py` — **漏斗终点：平铺执行器**（默认 dry-run / 不覆盖 / 不删除 / 可回滚）
- `scripts/flatten_exec_selftest.py` — 执行器自检（合成树端到端，不碰真实库）

> **平铺三级漏斗**（L1 脚本 → L2 AI → L3 用户）见「★ 平铺总流程」章节。
- `scripts/probe_local.py` — 抽取本地证据（编码安全），`--grade` 按置信度分组
- `scripts/verify_remote.py` — 远端匹配（LCS+CJK 双信号、变体生成、节流缓存）
- `scripts/build_manifest.py` — **并发**校验并生成统一清单 `library.json` + CSV
- `references/engines.md` — 引擎指纹与目录结构特征
- `references/apis.md` — VNDB / Bangumi / DLsite API 细节
- `references/metadataschema.md` — `library.json` 字段定义与命名切换
- `references/平铺甄别模板.md` — **L2 甄别报告模板**（分诊判不出时用，五个桶 + 逐条明细）
- `references/待查清单模板.md` — D/E 级结果的落地模板（含已解决留档区）

典型用法：

```bash
# 第一步：五分类（决定查哪个站，不联网）
python scripts/scan_channels.py
# 单目录判定
python -c "import sys;sys.path.insert(0,'scripts');\
from channel_classify import classify_channel;\
print(classify_channel('G:/Game/2022/xxx'))"

# 第二步：原子目录识别（不联网）
python scripts/atomic_detect.py "G:/Game/2022" --depth 4

# 单个目录：抽本地证据 + 分级
python scripts/probe_local.py "G:/Game/合集提取/xxx" --grade

# 单个作品：查远端并输出 meta
python scripts/verify_remote.py "作品名" --exe "game.exe" \
    --publisher "厂商" --local-title "本地真名"

# 批量：并发校验，生成统一清单（不往游戏目录写任何文件）
python scripts/build_manifest.py --workers 4
# 中断后重跑会自动跳过已完成项
python scripts/build_manifest.py --workers 4

# ===== 平铺三级漏斗 =====
# L1 脚本甄别（只读，出计划）
python scripts/flatten_triage.py G:/Game --json .workbuddy/flatten/plan.json
# L2 AI 甄别：对 plan 里的 UNKNOWN / 语义桶目录逐条看内容 → 产出 平铺甄别.md
#              模板见 references/平铺甄别模板.md
# L3 用户甄别：把 ❓待人工 列成表交用户拍板
# 执行（先 dry-run 看，确认后加 --apply）
python scripts/flatten_exec.py .workbuddy/flatten/plan.json --only flatten
```

## 与库管理工具的衔接

考据完成后，元数据的**唯一权威去处是统一清单**：

1. **`library.json`**（库根 `.workbuddy/scan/` 下）—— 集中式权威记录，
   支撑平铺准入与命名切换。**绝不往游戏目录里散写 `.galmeta.json`**
   （理由见「元数据记录：统一清单，不要散落文件」——那会造成证据污染）。
2. **ReinaManager 的 SQLite 数据库**（`/producer`、`/tag`、`/developer` 表）
   —— 索引层的权威记录

两处元数据让厂商、题材、年份都成为**可查询的字段**，不再依赖目录结构。
这也是「文件层只承载不变信息、其余交给索引层」原则的落地。

注意两者的关系：`library.json` 的厂商字段来自远端数据库，
可信度较高；ReinaManager 的库可以由用户手工补充和修正，覆盖清单里的值。
**冲突时以人工修正为准** —— 人比任何算法都更懂自己的收藏。
