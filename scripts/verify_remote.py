"""远端校验器 —— 判定作品在 VNDB/DLsite 等站点是否有对应条目。

这是「平铺准入」的依据：**只有远端有条目的游戏才允许平铺**。
产出统一清单 `library.json` 的单条记录（本地名/远端名/厂商/发售日/链接/置信度/命名来源），
**不往游戏目录写任何文件**。
"""

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

VNDB_LIMIT_SEC = 1.8
CACHE_DIR = Path.home() / ".cache" / "gal-lookup"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class RateLimiter:
    def __init__(self, interval=VNDB_LIMIT_SEC):
        self.interval = interval
        self.last = 0.0

    def wait(self):
        d = time.time() - self.last
        if d < self.interval:
            time.sleep(self.interval - d)
        self.last = time.time()


LIMITER = RateLimiter()


def cached(key, ttl_days=30):
    import hashlib
    f = CACHE_DIR / (hashlib.md5(key.encode()).hexdigest() + ".json")
    if f.exists() and (time.time() - f.stat().st_mtime) < ttl_days * 86400:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return None


def cache_put(key, data):
    import hashlib
    f = CACHE_DIR / (hashlib.md5(key.encode()).hexdigest() + ".json")
    try:
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# 注意：developers 是嵌套对象，必须写成 developers{id,name,original}
# 否则 API 返回 400: "The 'developers' object requires specifying sub-field(s)"
VNDB_FIELDS = ("id,title,alttitle,titles{title,lang,main},aliases,olang,released,"
               "platforms,developers{id,name,original},image{url}")


def vndb_search(keyword, endpoint="vn", limit=8, retries=3):
    ck = "vndb:%s:%s:%d" % (endpoint, keyword, limit)
    hit = cached(ck)
    if hit is not None:
        return hit
    body = {"filters": ["search", "=", keyword],
            "fields": VNDB_FIELDS, "results": limit}
    data = None
    for attempt in range(retries):
        req = urllib.request.Request(
            "https://api.vndb.org/kana/" + endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        LIMITER.wait()
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # 被限流：退避重试，且**不要缓存**限流响应，
                # 否则一次限流会导致该条目永久被判为「远端 0 结果」。
                wait = 12.0 * (attempt + 1)
                time.sleep(wait)
                continue
            data = {"error": "HTTP %d" % e.code}
            break
        except Exception as e:
            data = {"error": str(e)}
            break

    if data is None:
        data = {"error": "HTTP 429（已退避重试 %d 次仍被限流）" % retries}

    # 只缓存成功结果。错误响应会让后续运行误判为「查不到」。
    if "error" not in data:
        cache_put(ck, data)
    return data


STRIP_PAT = [
    (re.compile(r"[\(\[（【].*?[\)\]）】]"), ""),
    (re.compile(r"[\s_\-]*(?:v|ver|version)[\s_\.]*\d+(?:\.\d+)*[\s_\-]*$", re.I), ""),
    (re.compile(r"[\s_\-]*(?:完全版|製品版|豪华版|DL版?|パッケージ版)[\s_\-]*$", re.I), ""),
    (re.compile(r"[\s_\-\.、,，]+$"), ""),
]


def query_variants(name, exe=None, publisher=None):
    qs = [name.strip()]
    s = name
    for pat, rep in STRIP_PAT:
        s = pat.sub(rep, s)
    s = s.strip()
    if s and s != name:
        qs.append(s)
    if '～' in s or '〜' in s:
        qs.append(re.split(r'[～〜]', s)[0].strip())
    if exe:
        base = Path(exe).stem
        qs.append(base)
        qs.append(re.sub(r"[\s_\-]*(32|64)$", "", base, flags=re.I))
    if publisher:
        qs.append(("%s %s" % (publisher, name)).strip())
    romaji = re.sub(r"[^A-Za-z0-9\s]", "", name).strip()
    if romaji and romaji != name:
        qs.append(romaji)
    # 括号内的内容常常正是原名（如 "Kanon PC版 家属…（KZK@BISHOP）"）
    for m in re.finditer(r"[\(（\[【]([^\)）\]】]{2,40})[\)）\]】]", name):
        inner = m.group(1).strip()
        if inner and len(inner) >= 2 and inner.lower() != name.lower():
            qs.append(inner)
    # 去掉所有括号内容后的「主干名」
    bare = re.sub(r"[\(（\[【][^\)）\]】]*[\)）\]】]", " ", name)
    bare = re.sub(r"\s{2,}", " ", bare).strip()
    if bare and len(bare) >= 2 and bare not in qs:
        qs.append(bare)
    seen, out = set(), []
    for q in qs:
        if not q or len(q) < 2:
            continue
        k = q.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(q)
    return out


def normalize(s):
    s = s.lower()
    return re.sub(r"[\s_\-\.:：、。!！?？~～'\"()（）\[\]【】]+", "", s)


def similarity(a, b):
    """最长公共子序列比例。

    不要用字符集 Jaccard：它会被子串严重误导。
    实测误判：'AngelTear' vs 'Neo Angelique: Tenshi no Namida'
    字符集相似度 0.95（看起来像同一个），但实际上完全无关。
    LCS 只有 0.3 左右，能正确区分。
    """
    A, B = normalize(a), normalize(b)
    if not A or not B:
        return 0.0
    # 短的对长的做 LCS（O(min*max)），取较长串为基准
    if len(A) > len(B):
        A, B = B, A
    prev = [0] * (len(B) + 1)
    for ca in A:
        cur = [0]
        for j, cb in enumerate(B):
            if ca == cb:
                cur.append(prev[j] + 1)
            else:
                cur.append(max(cur[j], prev[j + 1]))
        prev = cur
    lcs = prev[-1]
    return lcs / max(len(A), len(B))


def seq_similarity(a, b):
    """按较短串归一化：短标题是长标题的子串时应该得满分。"""
    A, B = normalize(a), normalize(b)
    if not A or not B:
        return 0.0
    if A in B or B in A:
        return min(len(A), len(B)) / max(len(A), len(B))
    return similarity(a, b)


def cjk_distinct(a, b):
    """共享的不同汉字数。巧合通常只有 1-2 个，真实匹配会很多。"""
    ca = set(c for c in a if '\u3040' <= c <= '\u9fff')
    cb = set(c for c in b if '\u3040' <= c <= '\u9fff')
    return len(ca & cb)


def cjk_ratio(a, b):
    def cj(s):
        return set(c for c in s if '\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9fff')
    A, B = cj(a), cj(b)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


@dataclass
class Match:
    level: str
    reason: str = ""
    query_used: str = ""
    candidates: list = field(default_factory=list)
    best: object = None


def verify(name, exe=None, publisher=None, local_titles=None):
    # 本地提取到的真名（如 Game.ini 的 Title）比目录名可靠，优先拿来查
    seeds = [name] + [t for t in (local_titles or []) if t and t != name]
    qs = []
    for sd in seeds:
        qs.extend(query_variants(sd, exe, publisher))
    all_hits, errs = [], []

    for q in qs:
        d = vndb_search(q, "vn")
        if isinstance(d, dict) and "error" in d:
            errs.append("%s: %s" % (q, d.get("error")))
            continue
        for v in d.get("results", []):
            v["_query"] = q
            all_hits.append(v)

    uniq = {}
    for v in all_hits:
        uniq[v["id"]] = v
    hits = list(uniq.values())

    if not hits:
        reason = "远端 0 结果"
        if errs:
            reason += "（%d 次查询出错：%s）" % (len(errs), errs[0])
        return Match("E", reason, qs[0] if qs else "", [], None)

    # ---- 探针不只一个：目录名与本地提取的真名各跑一遍，取最佳 ----
    #
    # 关键教训 1：只用一个探针会漏掉大量正确匹配。
    #   `セクフレ幼馴染` 目录下 exe 名是 `cfriends`（罗马字缩写），
    #   远端 alttitle 是完整日文原名。
    #   拿 cfriends 比对 → CJK 0.00，LCS 0.30 → 误降为 C 级。
    #   拿目录名比对   → CJK 1.00 → 正确 A 级。
    #
    # 关键教训 2：目录名里的括号/方括号内容常是「中文译名」或「水印」，
    #   不是作品原名。`AngelTear [天使の涙]` 的「天使」会让
    #   `Neo Angelique: Tenshi no Namida` 的 CJK 变成 1.00（假阳性）。
    #   所以探针必须**剥离括号内容**再参与比对。
    def clean_probe(s):
        s = re.sub(r"[\(\[\uff08\u3010].*?[\)\]\uff09\u3011]", " ", s)
        s = re.sub(r"\s{2,}", " ", s).strip()
        return s or s.strip()

    probes = []
    for cand in [name] + [t for t in (local_titles or []) if t]:
        for c in (clean_probe(cand or ""), (cand or "").strip()):
            c = c.strip()
            if c and c.lower() not in {p.lower() for p in probes}:
                probes.append(c)

    def score_one(probe):
        scored = []
        for v in hits:
            titles = [v.get("title", "") or ""]
            alt = v.get("alttitle")
            if isinstance(alt, str):
                titles.append(alt)
            elif isinstance(alt, list):
                titles += [x for x in alt if isinstance(x, str)]
            for t in (v.get("titles") or []):
                if isinstance(t, dict) and t.get("title"):
                    titles.append(t["title"])
            titles += [x for x in (v.get("aliases") or []) if isinstance(x, str)]

            # 分项记录：同语言 LCS 与跨语言 CJK 分开算，取各自最优。
            # 关键：跨语言时 LCS 无意义（中文译名 vs 日文原名），
            #       同语言时 CJK 会误判（'AngelTear' vs '...Tenshi no Namida'）。
            best_lcs, best_cjk, best_mixed = 0.0, 0.0, 0.0
            for t in titles:
                if not t:
                    continue
                lcs = similarity(probe, t)                 # 同语言信号
                cjk = cjk_ratio(probe, t)                  # 跨语言信号
                best_lcs = max(best_lcs, lcs)
                best_cjk = max(best_cjk, cjk)
                best_mixed = max(best_mixed, max(lcs, cjk * 0.95))

            # 统一打分：两种信号都高才给高分，单一信号高分要打折
            if best_lcs >= 0.5 and best_cjk >= 0.4:
                final = min(1.0, best_lcs * 0.6 + best_cjk * 0.6)
            elif best_cjk >= 0.8:
                final = best_cjk * 0.95
            elif best_lcs >= 0.5:
                final = best_lcs * 0.8
            else:
                final = best_mixed
            # 与该探针配对的「不同汉字数」。必须在这里算，
            # 不能留到判定阶段用外层 probe —— 那个 probe 可能来自
            # 另一个候选，配对错就会算出「CJK 100% 但 0 个汉字」的矛盾值。
            best_distinct = 0
            for t in titles:
                if t:
                    best_distinct = max(best_distinct,
                                        cjk_distinct(probe, t))
            scored.append((final, best_cjk, v, best_lcs, best_distinct))
        return scored

    # 多个探针各打一次分。probes 的顺序即优先级（清洗过的在前），
    # 合并时用「靠前探针优先」—— 只有靠前探针完全没结果时，
    # 才让靠后探针的候选参与竞争。
    #
    # 为什么必须这样：`AngelTear [天使の涙]` 的清洗探针能正确命中
    # `Nerawareta Megami Tenshi Angeltear`；而带中文译名的原始目录名
    # 会额外召回 `Neo Angelique: Tenshi no Namida`（共享「天使」）。
    # 若按「任一探针的最高分」择优，假阳性就会胜出。
    all_scored = []
    for pri, p in enumerate(probes):
        for row in score_one(p):
            all_scored.append((row[0], row[1], row[2], row[3], row[4], p, pri))
    if not all_scored:
        return Match("E", "远端 0 结果", qs[0] if qs else "", [], None)

    # 找出真正有结果的最高优先级探针，只用它（及其同级）的结果
    best_pri = min(r[6] for r in all_scored)
    all_scored = [r for r in all_scored if r[6] == best_pri]

    all_scored.sort(key=lambda x: (-x[0], -x[1], -x[3]))
    # 元组结构：(final, cjk, vn, lcs, distinct, probe, pri)
    # 探针在倒数第二位，优先级在最后一位。
    probe = all_scored[0][-2]
    # 按候选 id 合并同一作品在不同探针下的最佳分。
    # 取最大值时，distinct 也必须跟着取同一行的值，不能各取各的最大。
    merged = {}
    for final, c, v, lcs, dist, p, _pri in all_scored:
        k = v["id"]
        if k not in merged or final > merged[k][0]:
            merged[k] = (final, c, v, lcs, dist, p)
    scored = [(f, c, v, l, d) for f, c, v, l, d, _p in merged.values()]

    scored.sort(key=lambda x: -x[0])
    top_s, top_c, top, top_lcs, top_distinct = scored[0]

    cand = [{
        "id": v["id"],
        "title": v.get("title", ""),
        "alttitle": (v.get("alttitle") if isinstance(v.get("alttitle"), str) else ""),
        "released": v.get("released", ""),
        "vendor": ((v.get("developers") or [{}])[0]).get("name", ""),
        "url": "https://vndb.org/" + v["id"],
        "similarity": round(s, 3),
        "cjk_overlap": round(c, 3),
    } for s, c, v, _ls, _d in scored[:5]]

    # ---- 负向检查：字符集相似度容易假阳性 ----
    # 典型误判：AngelTear -> "Neo Angelique: Tenshi no Namida"
    # 但要注意日文标题常带副标题（「～不連続存在～」），不能一律拦截。
    # 规则：只有当「长标题的主体部分（副标题前）」与探针毫无重叠时才拦截。
    def _anti_false_positive(probe_s, title_s):
        pr = normalize(probe_s)
        tr = normalize(title_s)
        if not pr or not tr:
            return False, ""
        if pr == tr or pr in tr or tr in pr:
            return False, ""
        # 取长标题的主体（副标题分隔符之前）
        for sep in ("~", "：", ":", "!", "？", "?", "!", "—", "－"):
            if sep in title_s:
                head = normalize(title_s.split(sep)[0])
                if head and (pr in head or head in pr):
                    return False, ""
        # 主体与探针长度接近但字符不重叠 -> 疑似不同作品
        short, long_ = (pr, tr) if len(pr) <= len(tr) else (tr, pr)
        if min(len(pr), len(tr)) < 4:
            return False, ""
        miss = sum(1 for c in set(short) if c not in long_)
        # 仅当探针是拉丁文（与标题同语言）时才做字符重叠检查；
        # 跨语言时 LCS/CJK 已经处理，字符重叠会误杀中文译名
        probe_is_latin = not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", probe_s)
        title_is_latin = not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", title_s)
        if not (probe_is_latin and title_is_latin):
            return False, ""
        if len(short) <= 14 and miss / max(len(set(short)), 1) > 0.5:
            return True, "短标题 %.0f%% 字符不重叠" % (
                miss / max(len(set(short)), 1) * 100)
        return False, ""

    def _best_title(v):
        """取信息量最大的标题（优先更长的非空 alttitle）。"""
        t = v.get("title", "") or ""
        alt = v.get("alttitle")
        if isinstance(alt, str) and alt.strip():
            return alt if len(alt) > len(t) else t
        return t

    if top_c >= 0.8:
        distinct = top_distinct
        if top_lcs >= 0.35:
            # distinct 可能为 0：探针是拉丁文时本就不该有汉字重叠，
            # 此时 LCS 已提供充分证据，不该输出「0 个不同汉字」这种矛盾措辞。
            extra = ("%d 个不同汉字 + " % distinct) if distinct else ""
            return Match("A", "CJK %.0f%%（%sLCS %.2f）"
                         % (top_c * 100, extra, top_lcs),
                         top.get("_query", ""), cand, cand[0])
        if distinct >= 4:
            return Match("A", "CJK %.0f%%（%d 个不同汉字，跨语言确认）"
                         % (top_c * 100, distinct),
                         top.get("_query", ""), cand, cand[0])
        return Match("D", "CJK %.0f%% 但仅共享 %d 个汉字，疑为巧合（%s）"
                     % (top_c * 100, distinct, _best_title(top)[:24]),
                     top.get("_query", ""), cand, cand[0])

    if top_lcs >= 0.75:
        return Match("A", "LCS %.2f（同语言强匹配）" % top_lcs,
                     top.get("_query", ""), cand, cand[0])


    top_title = top.get("title", "") or ""
    top_alt = (top.get("alttitle")
               if isinstance(top.get("alttitle"), str) else "")
    titles_all = [top_title] + ([top_alt] if top_alt else [])
    blocked, worst_reason = False, ""
    for t in titles_all:
        if not t:
            continue
        bad, why = _anti_false_positive(probe, t)
        if bad:
            blocked, worst_reason = True, why
            break

    if blocked:
        return Match("D", "疑似误判：%s（候选 %s）"
                     % (worst_reason, top_title[:26]),
                     top.get("_query", ""), cand, cand[0])

    if top_lcs >= 0.5:
        return Match("B", "LCS %.2f（中等，建议扫一眼）" % top_lcs,
                     top.get("_query", ""), cand, cand[0])
    if top_s >= 0.35:
        lvl = "B"
        if len(scored) > 1 and scored[1][0] > top_s - 0.08:
            lvl = "D"
        return Match(lvl, "相似度 %.2f（中等，建议扫一眼）" % top_s,
                     top.get("_query", ""), cand, cand[0])
    return Match("C", "相似度仅 %.2f，可能是不同作品" % top_s,
                 top.get("_query", ""), cand, cand[0])


def build_meta(local_name, match, engine=None, local_titles=None, source_dir=None):
    b = match.best or {}
    return {
        "schema": "library/1",
        "local_name": local_name,
        "local_dir": str(source_dir) if source_dir else None,
        "engine": engine,
        "local_titles": local_titles or [],
        "remote": {
            "found": bool(b),
            "vndb_id": b.get("id"),
            "remote_title": b.get("title"),
            "remote_alttitle": (b.get("alttitle") if isinstance(b.get("alttitle"), str) else ""),
            "released": b.get("released"),
            "vendor": b.get("vendor"),
            "url": b.get("url"),
        },
        "confidence": match.level,
        "reason": match.reason,
        "candidates": match.candidates,
        "naming_source": "remote" if b else "local",
        "flatten_allowed": match.level in ("A", "B"),
        "updated": time.strftime("%Y-%m-%d"),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--exe")
    ap.add_argument("--publisher")
    ap.add_argument("--local-title")
    a = ap.parse_args()
    m = verify(a.name, a.exe, a.publisher,
               [a.local_title] if a.local_title else None)
    print(json.dumps(build_meta(a.name, m,
                                local_titles=[a.local_title] if a.local_title else None),
                     ensure_ascii=False, indent=2))
