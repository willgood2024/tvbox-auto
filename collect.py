#!/usr/bin/env python3
# TVBox / 影视仓 自动发布器（无需第三方依赖，仅用标准库）
#
# 设计要点：
#  - GitHub 运行器无法连通国内源，故不做连通校验，直接发布精选清单。
#  - sourceUrl 必须是「单仓配置」(返回含 sites 的 JSON)，不要用「多仓 storeHouse」URL，
#    否则影视仓点击时无法递归解析会报「接口解析失败」。
#  - 影视源地址时效性极强，请定期对照发布页（home.132130.xyz / link3.cc/qingningshare）增删。
#  - ghproxy.net 仅能代理 raw.githubusercontent.com，不能代理 github.io 页面与 gitlab。

import json
import os

# 影视仓（storeHouse）/ 原版 TVBox 共用的「单仓配置」订阅清单（均应为返回 sites 的单仓 JSON）
# 已实测可用：raw.liucn.cc/box/m.json、9280.kstore.vip/newwex.json
SOURCES = [
    ("饭太硬",   "http://饭太硬.top/tv"),
    ("肥猫",     "http://肥猫.live"),
    ("巧技",     "http://cdn.qiaoji8.com/tvbox.json"),
    ("俊哥",     "http://home.jundie.top:81/top98.json"),
    ("巧儿",     "https://pandown.pro/tvbox/tvbox.json"),
    ("OK猫",     "http://ok321.top/tv"),
    ("猎手",     "https://raw.liucn.cc/box/m.json"),
    ("王二小",   "https://9280.kstore.vip/newwex.json"),
    ("潇洒",     "https://9877.kstore.space/AnotherD/api.json"),
    ("天天秒播", "http://tv.laohu.cool/tvbox.json"),
    ("荷城茶秀", "http://rihou.cc:88/荷城茶秀"),
    ("纯一骚零", "https://100km.top/0"),
    ("吾爱线路", "https://freed.yuanhsing.cf/tvbox/meowcf.json"),
]

# 直播源：单独放入影视仓的「直播」配置，不要混进 storeHouse（否则易 404）
LIVE_SOURCES = [
    ("国内直播IPTV", "https://raw.githubusercontent.com/YueChan/Live/refs/heads/main/IPTV.m3u"),
    ("国际直播",     "https://raw.githubusercontent.com/YueChan/Live/refs/heads/main/Global.m3u"),
]


def main():
    out_dir = os.path.dirname(os.path.abspath(__file__))

    # 影视仓专用：storeHouse 结构（每项指向「单仓配置」URL）
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
          f"（单仓配置清单，规避多仓嵌套导致的解析失败）")


if __name__ == "__main__":
    main()
