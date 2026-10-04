# 引擎指纹与目录结构

用于判断「目录是不是游戏本体」以及「该去哪些文件里找真名」。

## 引擎识别表

| 引擎 | 标志文件 | 查真名优先看 |
|---|---|---|
| **KiriKiri**（吉里吉里） | `*.xp3`、`data.xp3` | `data.xp3` 同级的 txt/ini |
| **Artemis**（Aquaplus） | `*.pfs` | 同级 txt |
| **SiglusEngine**（Aquaplus） | `SiglusEngine.exe` | 同级 txt、patch/ |
| **NScripter** | `nscr.dat` | 同级 txt |
| **Ethornella / BGI**（巨魔战将） | `*.bgl`、`Scene.pck` | 同级 txt、`data*.arc` |
| **CatSystem2** | `*.int` | 同级 txt |
| **RPG Maker VX Ace** | `Game.rgss3a`、`Game.ini` | **`Game.ini` 的 `Title=`** |
| **RPG Maker MV** | `www/data/*.rpgmvp`、`www.rpgmvp` | `www/data/System.json` |
| **RPG Maker MZ** | `*.png_`（加密图）、`*.rpgmvp` | `data/System.json` |
| **Ren'Py** | `renpy.py`、`lib/` + `game/` | `game/options.rpy`、`game/gui.rpy` |
| **Unity** | `*_Data/`、`UnityPlayer.dll` | 同级 txt、`*_Data/StreamingAssets/` |
| **Unreal** | `*.pak`、`<项目>_Data/` | 同级 txt |
| **NW.js** | `nw.dll` + `package.json`（`main=www/index.html`）+ `locales/` | **`www/index.html` 的 `<title>`** |
| **TyranoScript** | `tyrano.js`、`*.ks` | 同级 txt |
| **Flash** | `*.swf` | 同级 txt |
| **KAG / 吉里吉里变种** | `*.ks`、`krkr/` | 同级 txt |

## 引擎资源目录黑名单（绝不能当成游戏）

递归找游戏时必须排除：

```
www  data  sound  bg  background  bgm  se  voice  audio  music
font  fonts  system  sys  graphic  graphics  chars  char
animation  animations  effect  effects  picture  pictures
panorama  title  event  events  map  maps  jsr  rgss  db  dat
deps  lib  libs  bin  resources  assets  asset  movie  video
full  script  scripts  scenario  sumire  root  home  content  contents
mtool  tool  tools  loaders  wolf  env  engine
tl  translation  lang  language  locale
common  archive  backup  elfimage  elf  arc  original
game  _data
patch  dlc  bonus  extra  omake  expansion
install  setup  setting  config  save  savedata
```

**判断法**：目录只含这些名字的子目录，且**没有直接含 exe/引擎封包** → 是资源层，继续下探。

**实战教训**：第一次扫描时没做这个排除，RPG Maker 的 `www/data/audio/se`
被识别成 117 个「游戏」。

## 目录名 = 外壳的信号

这些名字出现在**顶层**时，真名在**父目录**：

```
_Data  game  renpy  lib  www          ← 引擎子目录被当成游戏
Project  build  dist  Release         ← 构建输出目录
繁体字版  英文版  Chinese  English     ← 语言版本
旧版  新版  重置版  製品版  完全版      ← 版本标记
```

## 版本关系信号

| 目录名片段 | 含义 | 处理 |
|---|---|---|
| `_32` `_64` `32bit 64bit` | 位版本 | 归并为 `游戏名/{32位,64位}` |
| `0.32` `v1.05` `ver2.0` | 版本号 | 保留，不要误判为位宽 |
| `旧版` `重置版` `製品版` | 版本 | 与主版本并列，加标注 |
| `副本N` `(2)` `copy` | 重复副本 | 校验后删一份 |
| `[080620]` | 发布日 2008-06-20 | 可辅助判断版本 |
| `RJ\d{6,}` | DLsite 番号 | 可直接搜 DLsite |

## 非游戏目录特征

| 特征 | 例子 | 判定 |
|---|---|---|
| ≤2 个文件且无数据目录 | `XP3Viewer.dll` + `XP3Viewer.exe` | 工具 |
| 名字含 `工具/backup/补丁` | `原文件备份` | 附属 |
| `MTool/Tool/loaders` 且无主 exe | `漢` | 机翻残骸 |
| 名字含 `解包/破解/unrar/7z` | `unrar.exe` | 工具 |
| `*.7z.001` 等分卷 | `HoneySelect2.7z.001` | 未解压 |
| 无 exe 无引擎封包无文本 | 纯空壳 | 残骸 |

## Unity 产品的元数据位置

Unity 游戏**没有 `Game.ini` 之类的文本配置**，但有二进制元数据可读：

| 文件 | 内容 |
|---|---|
| `*_Data/app.info` | **厂商名 + 产品名**（UTF-8 纯文本，极易读） |
| `*_Data/boot.config` | 渲染配置，VR/HDR 开关 |
| `*_Data/globalgamemanagers` | Unity 版本号（如 `2019.4.1f1`） |
| `*_Data/Managed/Assembly-CSharp.dll` | 主程序集，引用了 Arbor/Fungus 等框架可判断玩法类型 |

**`app.info` 是查 Unity 游戏真名最有效的一手线索** —— 比 readme 还直接，
因为它是打包时写入的产品标识。实例：

```
app.info 内容: "Heiwachan\n4p_play"
→ 厂商 Heiwachan，产品名 4p_play
```

若 `app.info` 缺失，可从 `Managed/` 下的 DLL 名反推使用的框架
（`Arbor*` = 3D 互动节点框架，`Fungus*` = 视觉小说对话框架）。

## Unity / NW.js / Ren'Py 打包工具目录

这几个引擎会捆绑大量工具 exe，**绝不能当主程序**：

| 引擎 | 捆绑的东西 |
|---|---|
| Ren'Py | `python/`, `python-packages/`, `lib/python-x86/`, `chardetect.exe`, `dxwebsetup.exe` |
| NW.js | `node.dll`, `nw.dll`, `locales/`, `swiftshader/`, `credits.html` |
| Unity | `UnityCrashHandler*.exe`, `crashpad_handler.exe`, `UnityPlayer.dll` |
| KiriKiri | `krkr.exe`, `*_extract.exe` |

判定法：**路径含 `lib/` `site-packages/` `python-packages/` 的一律排除**。

## 二次元游戏与 galgame 的结构差异

**Unity / NW.js 游戏**极易误判：
```
NW.js:  node.dll  nw.dll  package.json  locales/  swiftshader/  www/
        → 真名在 www/index.html 的 <title>
Unity:  Game.exe  UnityPlayer.dll  Game_Data/
        → 封面在 Game_Data/，真名常只在 exe 名或外层目录
```
这类游戏里 `package.json` 的 `window.title` 往往是空字符串，
必须去读 `www/index.html`。
