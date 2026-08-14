#!/usr/bin/env python3
# TVBox / 影视仓 自动发布器（无需第三方依赖，仅用标准库）
#
# 设计要点（重要）：
#   GitHub 的运行器（美国/欧洲节点）无法连通国内影视源（http / 中文域名基本不可达），
#   所以在 GitHub 上做"连通性校验 + 提取子地址"必然得到又薄又死的清单（正是之前 404 的根因）。
#   正确做法：直接发布一份精选的「聚合多仓订阅 URL 清单」。
#   每个 URL 自身就是一个第三方维护的多仓 JSON，由维护者自己每天更新——
#   GitHub Action 只需每天重新发布这份稳定清单即可，影视仓加载后会展开每条聚合源得到几十条线路。
#   某个聚合源临时抽风，只那一条空，不影响其它；长期失效则改一次 SOURCES 即可。

import json
import os

# 影视仓（storeHouse）/ 原版 TVBox 共用的「聚合多仓订阅」清单
# 每一项都是一个多仓/聚合入口，稳定性远高于单源。
SOURCES = [
    ("饭太硬",       "http://饭太硬.top/tv"),
    ("肥猫",         "http://肥猫.love"),
    ("巧技",         "http://cdn.qiaoji8.com/tvbox.json"),
    ("运输车",       "https://weixine.net/ysc.json"),
    ("吾爱有三",     "http://52bsj.vip:98/wuai"),
    ("欧歌多仓",     "http://tv.nxog.top/api.php?mz=xb&id=1&b=欧歌"),
    ("多多聚合多仓", "https://gitlab.com/duomv/apps/-/raw/main/fast.json"),
    ("星辰",         "https://fmbox.cc/"),
    ("小米",         "https://www.mpanso.com/小米/DEMO.json"),
    ("道长",         "https://pastebin.com/raw/5NHaxyGR"),
]

# 直播源：单独放入影视仓的「直播」配置，不要混进 storeHouse（否则易 404）
LIVE_SOURCES = [
    ("国内直播IPTV", "https://raw.githubusercontent.com/YueChan/Live/refs/heads/main/IPTV.m3u"),
    ("国际直播",     "https://raw.githubusercontent.com/YueChan/Live/refs/heads/main/Global.m3u"),
]


def main():
    out_dir = os.path.dirname(os.path.abspath(__file__))

    # 影视仓专用：storeHouse 结构
    store = {"storeHouse": [
        {"sourceName": name, "sourceUrl": url} for name, url in SOURCES
    ]}
    with open(os.path.join(out_dir, "tvbox_storehouse.json"), "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

    # 原版 TVBox 兼容：顶层数组（含直播）
    arr = [{"name": name, "url": url, "type": 0} for name, url in SOURCES]
    arr += [{"name": name, "url": url, "type": 1} for name, url in LIVE_SOURCES]
    with open(os.path.join(out_dir, "tvbox.json"), "w", encoding="utf-8") as f:
        json.dump(arr, f, ensure_ascii=False, indent=2)

    print(f"生成完成：影视仓仓库 {len(SOURCES)} 个 + 直播 {len(LIVE_SOURCES)} 个"
          f"（未做连通校验，直接发布精选聚合清单，规避 GitHub 无法连通国内源的问题）")


if __name__ == "__main__":
    main()
