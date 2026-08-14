#!/usr/bin/env python3
# TVBox 自动采集器：从种子接口抓取并校验，生成多仓 JSON（影视仓 storeHouse 格式）
import json
import os
import asyncio
import aiohttp

SEEDS = [
    "http://饭太硬.top/tv",
    "http://肥猫.love",
    "http://cdn.qiaoji8.com/tvbox.json",
    "https://weixine.net/ysc.json",
    "http://52bsj.vip:98/wuai",
    "http://tv.nxog.top/api.php?mz=xb&id=1&b=欧歌",
    "https://gitlab.com/duomv/apps/-/raw/main/fast.json",
    "https://fmbox.cc/",
    "https://www.mpanso.com/小米/DEMO.json",
    "https://pastebin.com/raw/5NHaxyGR",
    "https://raw.githubusercontent.com/YueChan/Live/refs/heads/main/IPTV.m3u",
]

MAX_WAREHOUSE = 60
HEADERS = {"User-Agent": "Mozilla/5.0 TVBox-AutoCollector/1.0"}


async def fetch(session, url, timeout):
    try:
        async with session.get(url, timeout=timeout) as resp:
            if resp.status == 200:
                return await resp.text()
    except Exception:
        return None
    return None


def extract_urls(text):
    out = set()
    try:
        data = json.loads(text)
    except Exception:
        return out
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                for k in ("url", "sourceUrl"):
                    if item.get(k):
                        out.add(item[k])
    elif isinstance(data, dict):
        for key in ("urls", "storeHouse"):
            val = data.get(key)
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        out.add(item)
                    elif isinstance(item, dict):
                        for k in ("url", "sourceUrl"):
                            if item.get(k):
                                out.add(item[k])
    return out


def host_of(url):
    return url.split("//")[-1].split("/")[0] or url


async def main():
    connector = aiohttp.TCPConnector()
    timeout = aiohttp.ClientTimeout(total=15)
    live = {}

    async with aiohttp.ClientSession(connector=connector, headers=HEADERS) as session:
        for url in SEEDS:
            text = await fetch(session, url, timeout)
            if text is None:
                print(f"[跳过] 不可达: {url}")
                continue
            if url.lower().endswith(".m3u") or "iptv" in url.lower() or "/live" in url.lower():
                live[url] = 1
                print(f"[直播] 有效: {url}")
                continue
            live[url] = 0
            print(f"[影视] 有效(种子): {url}")
            for s in extract_urls(text):
                if s in live:
                    continue
                if await fetch(session, s, timeout) is not None:
                    live[s] = 0
                    print(f"[影视] 有效(子): {s}")
                else:
                    print(f"[跳过] 子接口不可达: {s}")

    movie_urls = [u for u, t in live.items() if t == 0][:MAX_WAREHOUSE]
    out_dir = os.path.dirname(os.path.abspath(__file__))
    # 影视仓专用：storeHouse 结构
    store = {"storeHouse": [
        {"sourceName": host_of(u), "sourceUrl": u} for u in movie_urls
    ]}
    with open(os.path.join(out_dir, "tvbox_storehouse.json"), "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    # 原版 TVBox 兼容：顶层数组
    arr = [{"name": host_of(u), "url": u, "type": 0} for u in movie_urls]
    arr += [{"name": host_of(u), "url": u, "type": 1} for u, t in live.items() if t == 1]
    with open(os.path.join(out_dir, "tvbox.json"), "w", encoding="utf-8") as f:
        json.dump(arr, f, ensure_ascii=False, indent=2)

    print(f"\n生成完成：影视仓仓库 {len(movie_urls)} 个 + 直播 {sum(1 for t in live.values() if t==1)} 个")


if __name__ == "__main__":
    asyncio.run(main())
