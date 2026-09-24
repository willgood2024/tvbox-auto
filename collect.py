#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影视仓 / TVBox  纯净源生成器（严格模式）
=========================================
目标：解决「源有名字但点开用不了」和「网盘源要登录」两大问题。

做法（在“站点(sites)”这一层做清洗，而不是只订阅别人的仓库）：
  1) 从一批上游配置（单仓 sites / 多仓 storeHouse 会递归一层）中提取全部 sites；
  2) 【严格】只保留 type == 1 —— 直连苹果CMS采集站 API，不依赖 spider.jar / 远程 JS，
     这也是国内网络下真正能用的那类源；
  3) 【全删网盘】按 api / name 关键字剔除 阿里/夸克/UC/迅雷/AList/WebDAV/盘搜 等；
     【违规源】name / api / 上游 URL 三层黑名单（含 草榴/色戒/lsb/adult 等），
     并整段跳过曾带入违规内容的仓库（如 hebijunge/tvbox-config）；
  4) 按 api 去重，并对每个 api 做一次「存活 + 延时」探测：
     dead(明确 4xx5xx 或返回非 JSON) 剔除；unknown(DNS/超时，多为探测侧网络问题) 保留，
     避免把“你盒子连得通、只是运行器连不上”的源误杀；
  5) 【v3 真实测速】两轮筛选：
     ① 接口延时粗筛 → 取前 DEEP_CANDIDATES 个；
     ② 真实取流测速（详情 → 首个 m3u8 → 首个分片，测吞吐 KB/s）→ 按吞吐降序取 MAX_SITES；
     站点名里追加的是**我们实测**的吞吐，并剥掉上游自带的 [xxms|yy] 假标注
     （那些是别人的测量、且测的是接口而非播放，容易误导）。
  6) 【低并发】searchable=1 / quickSearch=0 / filterable=0 —— 避免搜索与筛选时
     对所有站点并发请求、与正在播放的视频抢带宽（卡顿的常见放大器）。
  7) 产物**只保留 sites**，不再合并上游的 parses/rules/flags/doh/lives
     —— 异构配置的这些字段是影视仓闪退的主要诱因，且 type1 直连源本就不需要；
     并生成**只指向它一条**的 storeHouse / 顶层数组（单一订阅入口，避免重复加载多遍）。

兜底：内置 5 个已实测存活的直连采集站，保证任何情况下产物都非空、都能用。

零第三方依赖，仅标准库。
"""

import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

# ============================ 配置 ============================
# 已实测存活的直连采集站（type 1），始终纳入，保证产物永不为空
SEED_SITES = [
    ("百度采集", "https://api.apibdzy.com/api.php/provide/vod"),
    ("暴风采集", "https://bfzyapi.com/api.php/provide/vod"),
    ("索尼采集", "https://suoniapi.com/api.php/provide/vod"),
    ("量子采集", "https://cj.lziapi.com/api.php/provide/vod"),
    ("非凡采集", "http://cj.ffzyapi.com/api.php/provide/vod"),
]

# 已知上游配置（单仓 sites 或 多仓 storeHouse 均可，多仓会递归一层）
KNOWN_CONFIGS = [
    "https://raw.githubusercontent.com/gaotianliuyun/gao/master/0821.json",
    "https://raw.githubusercontent.com/gaotianliuyun/gao/master/0825.json",
    "https://raw.githubusercontent.com/gaotianliuyun/gao/master/0827.json",
    "https://raw.githubusercontent.com/gaotianliuyun/gao/master/0826.json",
    "https://raw.githubusercontent.com/gaotianliuyun/gao/master/0707.json",
    "https://dxawi.github.io/0/0.json",
    "https://raw.liucn.cc/box/m.json",
    "https://9280.kstore.vip/newwex.json",
]

# GitHub 搜索：自动发现更多近期更新的配置仓库，扩大 type1 源池
SEARCH_QUERY = "tvbox"
SEARCH_PER_PAGE = 20
MAX_AGE_DAYS = 30           # 新鲜度闸门：距今天 ≤ 30 天
MAX_CONFIGS = 60            # 最多抓取的配置文件数
MAX_SITES = 20              # 最终保留的源数量上限（按延时取最快的，宁精勿多）
PROBE_WORKERS = 16
PROBE_TIMEOUT = 8
DEEP_CANDIDATES = 40        # 接口延时粗筛后，进入"真实取流测速"的候选数
SEG_BYTES = 262144          # 测速时读取首个分片的前 256KB
SEG_TIMEOUT = 6             # 分片测速超时（秒）

GH_PROXY = "https://ghproxy.net/"          # 主镜像（实时代理，无缓存）
GH_PROXY2 = "https://ghfast.top/"          # 备用镜像
SELF_REPO = "willgood2024/tvbox-auto"      # 本地运行时使用；CI 里用 GITHUB_REPOSITORY 覆盖
API_BASE = "https://api.github.com"
UA = "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 Chrome/110 Mobile Safari/537.36"
SUBDIRS = {"tvbox", "box", "config"}

# 网盘源特征（type==1 已基本排除网盘，这里是第二道防线，宁缺毋滥）
PAN_NAME_KEYWORDS = [
    "网盘", "云盘", "盘搜", "盘Se", "米搜", "抠搜", "夸搜", "Up搜", "易搜",
    "AList", "alist", "WebDAV", "webdav", "本地存储", "夸克", "迅雷", "115",
    "天翼", "移动云盘", "阿里云盘", "七夜", "Zhaozy", "小雅",
]
PAN_API_KEYWORDS = [
    "pan", "alist", "webdav", "quark", "xunlei", "115", "caiyun", "189.cn",
    "alipan", "dovx", "zhaozy", "pansou", "pansearch",
]

# 成人 / 违规内容源：按合规要求一律剔除
BLOCK_NAME_KEYWORDS = [
    "大奶子", "色猫", "麻豆", "抖阴", "番号", "奶香", "松视", "souav", "蜜桃",
    "潘甜甜", "里番", "伦理", "情色", "成人", "福利视频", "午夜", "黑料",
    "草榴", "色戒", "lsb", "(18)", "18+", "adult",
]
BLOCK_API_KEYWORDS = [
    "souavzy", "91md.me", "semaozy", "maozyapi", "sexnguon", "888dav", "naixxzy",
    "danaizi", "apilj.com", "aosikazy", "shayuapi", "huosuapi", "heiapi", "slapibf",
    "apittzy", "155api", "yikanapi", "lbapi9", "ddapi.cc", "523zyw", "mgzyz1", "apiyutu",
    "heiliao", "apilsbzy", "subocaiji", "adult",
]

# 整段跳过的上游（曾带入违规内容）：命中即不抓取
BLOCK_URL_KEYWORDS = ["hebijunge/tvbox-config", "adult"]


# ============================ 工具 ============================
def now_utc():
    return datetime.now(timezone.utc)


def cutoff_dt():
    return now_utc() - timedelta(days=MAX_AGE_DAYS)


def http_get(url, token=None, timeout=15):
    """返回文本或 None（任何异常都吞掉）。"""
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "ignore")
    except Exception:
        return None


def _strip_jsonc(text):
    """去掉 // 行注释与 /* */ 块注释（字符串内不处理），提升对“伪 JSON”配置的兼容。"""
    out, in_str, esc, i, n = [], False, False, 0, len(text)
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def load_json(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        return json.loads(_strip_jsonc(text))
    except Exception:
        return None


def extract(data):
    """返回 (sites, sub_urls)：sites 为本配置直接给出的站点；sub_urls 为需下钻的子配置。"""
    sites, subs = [], []
    if isinstance(data, dict):
        s = data.get("sites")
        if isinstance(s, list):
            sites = [x for x in s if isinstance(x, dict)]
        for key in ("storeHouse", "urls"):
            v = data.get(key)
            if isinstance(v, list):
                for e in v:
                    if isinstance(e, str):
                        subs.append(e)
                    elif isinstance(e, dict):
                        u = e.get("sourceUrl") or e.get("url")
                        if isinstance(u, str):
                            subs.append(u)
    return sites, subs


def norm_api(api):
    """归一化 api 用于去重：忽略 scheme/query、去尾斜杠、host 小写。"""
    a = api.split("?")[0].strip().rstrip("/")
    try:
        p = urllib.parse.urlsplit(a if "//" in a else "//" + a)
        return f"{p.netloc.lower()}{p.path}"
    except Exception:
        return a.lower()


def is_private_host(api):
    """私网 / 回环地址：别人家内网的媒体库，对你不可达。"""
    try:
        host = (urllib.parse.urlsplit(api).hostname or "").lower()
    except Exception:
        return True
    if host in ("localhost", "0.0.0.0", "::1"):
        return True
    m = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$", host)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a in (0, 10, 127):
            return True
        if a == 192 and b == 168:
            return True
        if a == 172 and 16 <= b <= 31:
            return True
        if a == 169 and b == 254:
            return True
    return False


def is_pan(name, api):
    low_a = (api or "").lower()
    if any(k in low_a for k in PAN_API_KEYWORDS):
        return True
    nm = name or ""
    if any(k in nm for k in PAN_NAME_KEYWORDS):
        return True
    return False


def keep_strict(site):
    """严格闸门：type==1 + 公网 http + 非网盘 + 非成人/违规。"""
    if site.get("type") != 1:
        return False
    api = site.get("api")
    if not isinstance(api, str) or not api.startswith("http"):
        return False
    name = (site.get("name") or "").strip()
    if is_private_host(api) or is_pan(name, api):
        return False
    low = api.lower()
    if any(k in low for k in BLOCK_API_KEYWORDS):
        return False
    if any(k in name for k in BLOCK_NAME_KEYWORDS):
        return False
    return True


def probe_type1(api):
    """存活探测 + 延时测量：返回 (status, 秒)。status ∈ alive / dead / unknown。"""
    bare = api.split("?")[0]
    urls = [api] if "?" in api else [bare, bare + "?ac=list&pg=1"]
    saw_network_err = False
    for u in urls:
        text = None
        t0 = time.time()
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
                if resp.status != 200:
                    return ("dead", 99.0)
                text = resp.read().decode("utf-8", "ignore")
            latency = round(time.time() - t0, 2)
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410, 500, 502, 503):
                return ("dead", 99.0)
            saw_network_err = True
            continue
        except Exception:
            saw_network_err = True
            continue
        d = load_json(text)
        if isinstance(d, dict) and any(k in d for k in ("list", "class", "code")):
            return ("alive", latency)
        # 明确返回了内容但不是苹果CMS结构
        if text and "<html" in text[:2000].lower():
            return ("dead", 99.0)
    return ("unknown", 99.0) if saw_network_err else ("dead", 99.0)


def _first_uri(playlist_text):
    """取 m3u8 文本里的第一条非注释行（可能是变体 playlist，也可能是分片）。"""
    for line in (playlist_text or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def _first_m3u8(detail_json):
    """从苹果CMS详情里取第一条 http(s) 的 m3u8 播放地址。"""
    if not isinstance(detail_json, dict):
        return None
    for it in detail_json.get("list", []) or []:
        pu = str(it.get("vod_play_url") or "")
        for part in pu.split("#"):
            u = part.split("$")[-1].strip()
            if u.startswith("http") and ".m3u8" in u.lower():
                return u
    return None


def probe_play(api, timeout=PROBE_TIMEOUT):
    """真实取流测速：详情 → 首个 m3u8 → 首个分片，实测吞吐。
    返回 (ok: bool, kbps: float, note: str)。"""
    base = api.split("?")[0].rstrip("/")

    # 1) 详情：拿到真实播放地址（兼容 ac=detail / ac=videolist 两种写法）
    detail = None
    for q in ("?ac=detail&pg=1", "?ac=videolist&pg=1"):
        detail = load_json(http_get(base + q, timeout=timeout) or "")
        if _first_m3u8(detail):
            break
    m3u8 = _first_m3u8(detail)
    if not m3u8:
        return (False, 0.0, "无直链m3u8")

    # 2) m3u8（可能是主 playlist，指向变体 playlist）
    text = http_get(m3u8, timeout=timeout)
    if not text:
        return (False, 0.0, "m3u8不可达")
    seg = _first_uri(text)
    if not seg:
        return (False, 0.0, "playlist为空")
    seg = urllib.parse.urljoin(m3u8, seg)

    # 主 playlist → 变体 playlist：再下钻一层
    if seg.lower().endswith(".m3u8") or ".m3u8?" in seg.lower():
        sub = http_get(seg, timeout=timeout)
        nxt = _first_uri(sub) if sub else None
        if not nxt:
            return (False, 0.0, "变体playlist不可达")
        seg = urllib.parse.urljoin(seg, nxt)

    # 3) 分片：Range 取前 SEG_BYTES 字节，实测吞吐
    t0 = time.time()
    try:
        req = urllib.request.Request(
            seg, headers={"User-Agent": UA, "Range": f"bytes=0-{SEG_BYTES - 1}"})
        with urllib.request.urlopen(req, timeout=SEG_TIMEOUT) as resp:
            chunk = resp.read(SEG_BYTES)
        dt = max(time.time() - t0, 1e-6)
        if not chunk:
            return (False, 0.0, "分片为空")
        return (True, round(len(chunk) / 1024 / dt, 1), f"{len(chunk) // 1024}KB/{dt:.1f}s")
    except Exception as e:
        return (False, 0.0, f"分片失败:{str(e)[:20]}")


def github_api(url, token):
    text = http_get(url, token=token, timeout=20)
    return load_json(text)


def github_raw(url):
    return "raw.githubusercontent.com" in url


def list_json_files(owner, name, path, token, depth=0):
    if path:
        url = f"{API_BASE}/repos/{owner}/{name}/contents/{urllib.parse.quote(path, safe='')}"
    else:
        url = f"{API_BASE}/repos/{owner}/{name}/contents/"
    items = github_api(url, token)
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if it.get("type") == "file" and str(it.get("name", "")).endswith(".json"):
            out.append(it.get("download_url"))
        elif it.get("type") == "dir" and depth < 1 and str(it.get("name", "")) in SUBDIRS:
            out += list_json_files(owner, name, it["name"], token, depth + 1)
    return out


def discover_configs(token, cutoff):
    """GitHub 搜索 API 发现近期更新的 tvbox 仓库中的 JSON 配置。"""
    found = []
    url = (f"{API_BASE}/search/repositories?q={urllib.parse.quote(SEARCH_QUERY)}"
           f"&sort=updated&order=desc&per_page={SEARCH_PER_PAGE}")
    data = github_api(url, token)
    if not isinstance(data, dict):
        return found
    for repo in data.get("items", []):
        full = repo.get("full_name", "")
        if full == SELF_REPO:
            continue
        updated = repo.get("updated_at") or repo.get("pushed_at")
        if updated:
            try:
                d = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                if d < cutoff:
                    continue
            except Exception:
                pass
        owner, _, name = full.partition("/")
        for dl in list_json_files(owner, name, "", token):
            if dl:
                found.append(dl)
    return found


# ============================ 主流程 ============================
def gather_sites(config_urls):
    """抓取配置、提取 sites，并递归一层子配置。返回 (raw_sites, meta)。"""
    raw_sites, seen = [], set()
    meta = {"parses": [], "rules": [], "flags": [], "doh": [], "lives": []}
    queue = list(config_urls)
    fetched = 0
    while queue and fetched < MAX_CONFIGS:
        url = queue.pop(0)
        if not url or url in seen:
            continue
        seen.add(url)
        if any(k in url.lower() for k in BLOCK_URL_KEYWORDS):
            print(f"[跳过] 命中屏蔽名单 {url}")
            continue
        text = http_get(url, timeout=15)
        if not text:
            print(f"[跳过] 不可达 {url}")
            continue
        data = load_json(text)
        if data is None:
            print(f"[跳过] 非 JSON {url}")
            continue
        fetched += 1
        sites, subs = extract(data)
        if sites:
            raw_sites += sites
            print(f"[配置] sites={len(sites):3d}  {url}")
            if isinstance(data, dict):
                for k in meta:
                    v = data.get(k)
                    if isinstance(v, list) and v:
                        meta[k] += v
        for s in subs[:20]:
            if s not in seen:
                queue.append(s)
    return raw_sites, meta


def dedupe(lst, keyfn):
    out, seen = [], set()
    for x in lst:
        try:
            k = keyfn(x)
        except Exception:
            continue
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def build_clean(strict_sites):
    """只输出 sites —— 不再合并上游 parses/rules/flags/doh/lives（闪退主要诱因）。"""
    sites = []
    for i, s in enumerate(strict_sites):
        api = s["api"].split("?")[0].strip()      # 去掉上游自带的 ?ac=list 等，交给盒子自行拼接
        try:
            host = urllib.parse.urlsplit(api).netloc.lower()
        except Exception:
            host = f"site{i}"
        key = re.sub(r"[^0-9a-zA-Z]", "_", host) or f"site{i}"
        sites.append(OrderedDict([
            ("key", key),
            ("name", (s.get("name") or host).strip()),
            ("type", 1),
            ("api", api),
            ("searchable", 1),
            ("quickSearch", 0),     # 低并发：不在输入时对全部站点并发请求（避免与播放抢带宽）
            ("filterable", 0),      # 低并发：不在切换分类时对全部站点并发拉取
        ]))
    # key 去重
    seen_key = set()
    for s in sites:
        while s["key"] in seen_key:
            s["key"] += "_"
        seen_key.add(s["key"])

    clean = OrderedDict()
    clean["sites"] = sites
    clean["wallpaper"] = "https://bing.img.run/1920x1080.php"
    clean["warningText"] = "本配置仅聚合公开采集接口，仅供个人学习体验，请遵守当地法律法规。"
    return clean


def write_outputs(clean, sites_count):
    out_dir = os.path.dirname(os.path.abspath(__file__))
    repo = os.environ.get("GITHUB_REPOSITORY") or SELF_REPO
    branch = os.environ.get("GITHUB_REF_NAME") or "main"
    raw = f"https://raw.githubusercontent.com/{repo}/{branch}/tvbox_clean.json"

    # 单一订阅入口：只指向自建单仓一条（避免同一份内容被加载多遍 → 列表臃肿/闪退）
    primary = GH_PROXY + raw
    store = {"storeHouse": [{"sourceName": "纯净直连(严格·自建)", "sourceUrl": primary}]}
    arr = [{"name": "纯净直连(严格·自建)", "url": primary, "type": 0}]

    with open(os.path.join(out_dir, "tvbox_clean.json"), "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "tvbox_storehouse.json"), "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "tvbox.json"), "w", encoding="utf-8") as f:
        json.dump(arr, f, ensure_ascii=False, indent=2)
    print(f"\n生成完成：干净直连源 {sites_count} 个 → tvbox_clean.json / tvbox_storehouse.json / tvbox.json")
    print(f"订阅地址(唯一)：{primary}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    token = os.environ.get("GITHUB_TOKEN")
    cutoff = cutoff_dt()
    print(f"[info] token={'已注入' if token else '未注入'} python={sys.version.split()[0]} "
          f"模式=严格(仅type1·全删网盘)")

    configs = list(KNOWN_CONFIGS)
    try:
        extra = discover_configs(token, cutoff)
        print(f"[发现] GitHub 搜索得到 {len(extra)} 个候选配置")
        configs += extra
    except Exception as e:
        print(f"[警告] 搜索发现异常: {e}")

    raw_sites, meta = [], {}
    try:
        raw_sites, meta = gather_sites(configs)
    except Exception:
        traceback.print_exc()
        print("[警告] 抓取阶段异常，仅使用内置种子源")

    # 严格过滤
    strict, dropped_pan, dropped_type = [], 0, 0
    for s in raw_sites:
        if s.get("type") != 1:
            dropped_type += 1
            continue
        if not keep_strict(s):
            if isinstance(s.get("api"), str) and s["api"].startswith("http"):
                dropped_pan += 1
            continue
        strict.append(s)
    print(f"[过滤] 原始 {len(raw_sites)} → 非type1剔除 {dropped_type} · 网盘/异常剔除 {dropped_pan} "
          f"→ type1 剩 {len(strict)}")

    # 去重
    strict = dedupe(strict, lambda s: norm_api(s["api"]))
    print(f"[去重] 唯一 type1 源 {len(strict)}")

    # ---------- 第一轮：接口存活 + 延时粗筛 ----------
    results = {}
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as ex:
        futs = {ex.submit(probe_type1, s["api"]): s for s in strict}
        for fu in as_completed(futs):
            s = futs[fu]
            try:
                results[norm_api(s["api"])] = fu.result()
            except Exception:
                results[norm_api(s["api"])] = ("unknown", 99.0)

    scored, dead = [], 0
    for s in strict:
        st, lat = results.get(norm_api(s["api"]), ("unknown", 99.0))
        if st == "dead":
            dead += 1
            continue                     # 仅剔除“明确已死”的
        scored.append((s, lat))          # alive / unknown 均保留
    scored.sort(key=lambda t: t[1])
    print(f"[粗筛] 接口可用 {len(scored)}（明确死亡剔除 {dead}）")

    # 待测集合 = 延时最快的前 DEEP_CANDIDATES 个 + 内置种子（保证种子也有实测值）
    prelim = [s for s, _ in scored[:DEEP_CANDIDATES]]
    seen_api = {norm_api(s["api"]) for s in prelim}
    for nm, api in SEED_SITES:
        if norm_api(api) not in seen_api:
            prelim.append({"name": nm, "type": 1, "api": api})
            seen_api.add(norm_api(api))

    # ---------- 第二轮：真实取流测速（详情 → m3u8 → 分片） ----------
    play = {}
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as ex:
        futs = {ex.submit(probe_play, s["api"]): s for s in prelim}
        for fu in as_completed(futs):
            s = futs[fu]
            try:
                play[norm_api(s["api"])] = fu.result()
            except Exception:
                play[norm_api(s["api"])] = (False, 0.0, "err")

    rank = []
    for s in prelim:
        _ok, kbps, note = play.get(norm_api(s["api"]), (False, 0.0, "?"))
        rank.append((s, kbps, note))
    rank.sort(key=lambda t: t[1], reverse=True)     # 吞吐高的在前
    print(f"[测速] 实测 {len(rank)} 个 → 按吞吐取前 {MAX_SITES}：")
    for s, kbps, note in rank[:MAX_SITES]:
        print(f"   {kbps:8.1f} KB/s  [{note:<14}] {s.get('name')}")

    # 命名：剥掉上游自带的 [xxms|yy] 假标注 + 追加我们实测的吞吐
    kept = []
    for s, kbps, _ in rank[:MAX_SITES]:
        nm = re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", s.get("name") or "").strip() or "源"
        s2 = dict(s)
        s2["name"] = f"{nm}｜{kbps:.0f}KB/s" if kbps > 0 else f"{nm}｜待测"
        kept.append(s2)

    # 保底：内置种子源必须存在（附上它的实测值）
    existing = {norm_api(s["api"]) for s in kept}
    for nm, api in SEED_SITES:
        if norm_api(api) not in existing:
            _ok, kbps, _ = play.get(norm_api(api), (False, 0.0, ""))
            kept.append({"name": f"{nm}｜{kbps:.0f}KB/s" if kbps > 0 else f"{nm}｜待测",
                         "type": 1, "api": api})
            existing.add(norm_api(api))

    if not kept:  # 极端兜底
        kept = [{"name": nm, "type": 1, "api": api} for nm, api in SEED_SITES]

    clean = build_clean(kept)
    write_outputs(clean, len(clean["sites"]))
    for s in clean["sites"]:
        print(f"   · {s['name']}  ->  {s['api']}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
    sys.exit(0)
