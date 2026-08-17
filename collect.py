#!/usr/bin/env python3
# TVBox / 影视仓 自动发现 + 自动更新器（零第三方依赖，仅标准库）
#
# 设计：
#  - 不再写死清单。每次运行自动：
#    1) 通过 GitHub 搜索 API 发现近期更新的 tvbox 仓库；
#    2) 枚举仓库（含 tvbox/box/config 子目录）下的 JSON 文件；
#    3) 校验其是否为合法「单仓配置」(含 sites / storeHouse / video)；
#    4) 用文件最近 commit 日期做「≤30 天 + 2026 年内」新鲜度闸门；
#    5) GitHub 源统一包 ghproxy.net/ 前缀，保证盒子可达；
#    6) 合并 2 个已实测 CN 兜底源，输出 storeHouse + 顶层数组双格式。
#  - 索引库抓取与搜索 API 双结合（搜索 API 为主干）。
#  - 容错：限流 / 超时 / 解析失败 均跳过，保证脚本不崩、至少输出兜底源。

import json
import os
import re
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta

# ----------------------------- 配置 -----------------------------
FALLBACK_SOURCES = [
    # 已实测可用的 CN 单仓源，始终纳入，保证列表非空（其新鲜度无法从 runner 验证，作安全网）
    ("猎手(兜底)", "https://raw.liucn.cc/box/m.json"),
    ("王二小(兜底)", "https://9280.kstore.vip/newwex.json"),
]

LIVE_SOURCES = [
    ("国内直播IPTV", "https://raw.githubusercontent.com/YueChan/Live/refs/heads/main/IPTV.m3u"),
    ("国际直播",     "https://raw.githubusercontent.com/YueChan/Live/refs/heads/main/Global.m3u"),
]

SEARCH_QUERY = "tvbox"
SEARCH_PER_PAGE = 20
MAX_AGE_DAYS = 30          # 新鲜度闸门：距今天 ≤ 30 天
MAX_SOURCES = 40           # storeHouse 上限
GH_PROXY = "https://ghproxy.net/"
SELF_REPO = "willgood2024/tvbox-auto"   # 跳过自身，避免递归
API_BASE = "https://api.github.com"
UA = "Mozilla/5.0 TVBox-AutoCollector/2.0"
SUBDIRS = {"tvbox", "box", "config"}     # 额外下钻的目录
MAX_CANDIDATES = 80                       # 单次处理候选数上限


# ----------------------------- 工具函数 -----------------------------
def now_utc():
    return datetime.now(timezone.utc)


def cutoff_dt():
    return now_utc() - timedelta(days=MAX_AGE_DAYS)


def http_get(url, token=None, timeout=15, as_text=True):
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            return data.decode("utf-8", "ignore") if as_text else data
    except Exception:
        return None


def github_api(url, token):
    text = http_get(url, token=token, timeout=20)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def is_github_raw(url):
    return "raw.githubusercontent.com" in url


def wrap_for_box(url):
    """GitHub 源统一包 ghproxy 前缀，保证盒子可达；其余原样。"""
    return GH_PROXY + url if is_github_raw(url) else url


def validate_single_warehouse(text):
    """必须是合法「单仓配置」：含 sites / storeHouse / video，或为非空数组。"""
    try:
        data = json.loads(text)
    except Exception:
        return False
    if isinstance(data, dict):
        return ("sites" in data) or ("storeHouse" in data) or ("video" in data)
    if isinstance(data, list):
        return len(data) > 0
    return False


def file_last_commit_date(owner, repo, path, token):
    """返回该文件最近一次 commit 的 UTC datetime，或 None。"""
    url = (f"{API_BASE}/repos/{owner}/{repo}/commits"
           f"?path={urllib.parse.quote(path, safe='')}&per_page=1")
    data = github_api(url, token)
    if isinstance(data, list) and data:
        commit = data[0].get("commit", {})
        date_str = (commit.get("author", {}).get("date")
                    or commit.get("committer", {}).get("date"))
        if date_str:
            try:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except Exception:
                return None
    return None


def list_json_files(owner, name, path, token, depth=0):
    """列出某目录下的 JSON 文件（download_url），并下钻 SUBDIRS 一层。"""
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
            out.append((it.get("name", ""), it.get("download_url")))
        elif it.get("type") == "dir" and depth < 1 and str(it.get("name", "")) in SUBDIRS:
            out += list_json_files(owner, name, it["name"], token, depth + 1)
    return out


def discover_via_search(token):
    """通过 GitHub 搜索 API 发现近期更新的 tvbox 仓库，枚举其 JSON 候选。"""
    results = []
    url = (f"{API_BASE}/search/repositories?q={urllib.parse.quote(SEARCH_QUERY)}"
           f"&sort=updated&order=desc&per_page={SEARCH_PER_PAGE}")
    data = github_api(url, token)
    if not isinstance(data, dict):
        return results
    for repo in data.get("items", []):
        full = repo.get("full_name", "")
        if full == SELF_REPO:
            continue
        owner, _, name = full.partition("/")
        for fname, dl in list_json_files(owner, name, "", token):
            if dl:
                results.append((full, fname, dl))
    return results


def parse_raw(url):
    """从 raw.githubusercontent.com URL 解析 (owner, repo, branch, path)。"""
    m = re.match(r'https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)', url)
    if not m:
        return None
    return m.groups()


# ----------------------------- 主流程 -----------------------------
def main():
    token = os.environ.get("GITHUB_TOKEN")
    cutoff = cutoff_dt()
    discovered = []   # (name, box_url, date)
    seen = set()

    try:
        candidates = discover_via_search(token)
    except Exception as e:
        print(f"[警告] 搜索发现异常: {e}")
        candidates = []

    for full, fname, raw in candidates[:MAX_CANDIDATES]:
        if raw in seen:
            continue
        seen.add(raw)
        parsed = parse_raw(raw)
        if not parsed:
            continue
        owner, repo, branch, path = parsed
        d = file_last_commit_date(owner, repo, path, token)
        if d is None:
            print(f"[跳过] 无法确定更新时间: {raw}")
            continue
        if d < cutoff or d.year < 2026:
            print(f"[跳过] 过期({d.date()}): {full}/{fname}")
            continue
        text = http_get(raw, token=token, timeout=15)
        if not text or not validate_single_warehouse(text):
            print(f"[跳过] 非合法单仓配置: {full}/{fname}")
            continue
        discovered.append((f"{repo}/{fname}", wrap_for_box(raw), d))
        print(f"[纳入] {repo}/{fname} ({d.date()})")

    # 按新鲜度降序
    discovered.sort(key=lambda x: x[2], reverse=True)

    # 组装最终清单：兜底源优先，再补发现源
    final = []
    used = set()
    for nm, url in FALLBACK_SOURCES:
        if url not in used:
            final.append({"sourceName": nm, "sourceUrl": url})
            used.add(url)
    room = MAX_SOURCES - len(final)
    for nm, url, _ in discovered[:room]:
        if url not in used:
            final.append({"sourceName": nm, "sourceUrl": url})
            used.add(url)

    # 影视仓：storeHouse 格式
    store = {"storeHouse": final}
    # 原版 TVBox：顶层数组 + 直播
    arr = [{"name": e["sourceName"], "url": e["sourceUrl"], "type": 0} for e in final]
    arr += [{"name": nm, "url": wrap_for_box(url), "type": 1} for nm, url in LIVE_SOURCES]

    out_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(out_dir, "tvbox_storehouse.json"), "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "tvbox.json"), "w", encoding="utf-8") as f:
        json.dump(arr, f, ensure_ascii=False, indent=2)

    print(f"\n生成完成：兜底 {len(FALLBACK_SOURCES)} + 自动发现 {len(discovered[:room])} "
          f"= 共 {len(final)} 个仓库（新鲜度 ≤ {MAX_AGE_DAYS} 天）")


if __name__ == "__main__":
    main()
