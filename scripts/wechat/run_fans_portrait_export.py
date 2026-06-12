# -*- coding: utf-8 -*-
"""
粉丝画像数据导出 - 全自动版
自动导航到粉丝画像页面，无需人工干预
"""
import asyncio, json, os, time
from datetime import date
from playwright.async_api import async_playwright

CHROME_PORT = 9222
HERE = os.path.dirname(os.path.abspath(__file__))

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def log_step(step, msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{step}] {msg}", flush=True)

def get_ws_url(port):
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=5) as r:
        return json.loads(r.read())["webSocketDebuggerUrl"]

async def main():
    log("=" * 50)
    log("视频号 - 粉丝画像数据导出")
    log("=" * 50)

    log_step("1/6", "连接 Chrome CDP...")
    ws_url = get_ws_url(CHROME_PORT)
    log_step("1/6", "✓ Chrome CDP 连接成功")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url)
        context = browser.contexts[0]

        # 1. 找到视频号后台页面
        log_step("2/6", "查找视频号后台页面...")
        page = None
        for pg in context.pages:
            if "channels.weixin.qq.com" in pg.url:
                page = pg
                break
        if not page:
            log("✗ 视频号后台页面未打开，请先启动Chrome")
            return
        log_step("2/6", "✓ 已找到视频号后台页面")

        log_step("3/6", "导航到 数据中心 → 关注者数据...")
        # 2. 点击侧边栏"数据中心" → "关注者数据"
        await page.evaluate("""
            () => {
                const items = document.querySelectorAll('.finder-ui-desktop-menu__icon_menu__name');
                for (const item of items) { if (item.innerText.includes('数据中心')) item.click(); }
            }
        """)
        await asyncio.sleep(1.5)

        await page.evaluate("""
            () => {
                const items = document.querySelectorAll('.finder-ui-desktop-sub-menu__item');
                for (const item of items) { if (item.innerText.includes('关注者数据')) item.click(); }
            }
        """)
        await asyncio.sleep(3)
        log_step("3/6", "✓ 已导航到关注者数据页面")

        # 3. 在 follower iframe 中点击"粉丝画像"tab
        log_step("4/6", "等待关注者数据 iframe 加载...")
        follower_frame = None
        for _ in range(15):
            for f in page.frames:
                if "micro/statistic/follower" in f.url:
                    follower_frame = f
                    break
            if follower_frame:
                break
            await asyncio.sleep(1)

        if not follower_frame:
            log("✗ 关注者数据 iframe 未找到")
            return
        log_step("4/6", "✓ iframe 已加载")

        log_step("5/6", "点击「粉丝画像」标签...")
        # 点击"粉丝画像" tab
        clicked = await follower_frame.evaluate("""
            () => {
                const tabs = document.querySelectorAll('[class*="tab"]');
                for (const tab of tabs) {
                    if (tab.textContent.trim() === '粉丝画像') {
                        tab.click();
                        return 'ok';
                    }
                }
                // 尝试文本匹配
                const iter = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                let node;
                while (node = iter.nextNode()) {
                    if (node.textContent.trim() === '粉丝画像') {
                        node.click();
                        return 'ok';
                    }
                }
                return 'tab not found';
            }
        """)
        if clicked == "ok":
            log_step("5/6", "✓ 已切换到粉丝画像标签")
        else:
            log_step("5/6", f"⚠ 粉丝画像标签点击结果: {clicked}")
        await asyncio.sleep(3)  # 等待数据加载

        # 4. 找到粉丝画像 iframe
        target_frame = None
        for f in page.frames:
            if "micro/statistic/follower" in f.url:
                target_frame = f
                break

        if not target_frame:
            log("✗ 粉丝画像 iframe 丢失")
            return

        log_step("6/6", "请求粉丝画像 API 数据...")
        result = await target_frame.evaluate("""
            async () => {
                const url = 'https://channels.weixin.qq.com/cgi-bin/mmfinderassistant-bin/statistic/fans_portrait';
                const resp = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Referer': 'https://channels.weixin.qq.com/platform/statistic/follower'
                    },
                    body: JSON.stringify({})
                });
                return await resp.json();
            }
        """)

        if result.get("errCode") != 0:
            log(f"✗ API 错误: {result}")
            return

        log_step("6/6", "✓ API 数据已获取")

        resp_json_str = result["data"]["respJson"]
        data = json.loads(resp_json_str)
        out_dir = os.environ.get("MEDIAEXPORT_OUTPUT_DIR", os.path.join(HERE, "..", "..", "output"))
        os.makedirs(out_dir, exist_ok=True)
        date_str = date.today().strftime("%Y%m%d")

        # ---- 画像解析 ----
        metrics = data.get("data", {})
        if not metrics:
            log("⚠ 画像数据为空")
            return

        lines = []
        lines.append("【视频号粉丝画像】")
        lines.append(f"导出日期: {date_str}")
        lines.append("")

        age_map = {
            "fage_01_17": "17岁及以下",
            "fage_18_24": "18-24岁",
            "fage_25_30": "25-30岁",
            "fage_31_40": "31-40岁",
            "fage_41_50": "41-50岁",
            "fage_51_60": "51-60岁",
            "fage_61_": "60岁以上",
        }
        sex_map = {"fsex_1": "男", "fsex_2": "女"}
        device_map = {"iPhone": "iPhone", "Android": "Android", "other": "其他"}

        # ---- age ----
        age_data = metrics.get("age_list", [])
        valid_age = [x for x in age_data if x["dim"] not in ("@_all", "unknow")]
        total_age = sum(int(x["value"]) for x in valid_age)
        lines.append("【年龄分布】")
        for item in valid_age:
            label = age_map.get(item["dim"], item["dim"])
            val = int(item["value"])
            pct = val / total_age * 100
            lines.append("{}：{}人（{:.2f}%）".format(label, val, pct))
        lines.append("合计：{}人".format(total_age))
        if valid_age:
            log(f"  → 年龄分布: {total_age} 人")
            for item in valid_age[:3]:
                label = age_map.get(item["dim"], item["dim"])
                log(f"    {label}: {int(item['value'])}人")

        # ---- sex ----
        sex_data = metrics.get("sex_list", [])
        valid_sex = [x for x in sex_data if x["dim"] not in ("@_all", "unknow")]
        total_sex = sum(int(x["value"]) for x in valid_sex)
        lines.append("")
        lines.append("【性别分布】")
        for item in valid_sex:
            label = sex_map.get(item["dim"], item["dim"])
            val = int(item["value"])
            pct = val / total_sex * 100
            lines.append("{}：{}人（{:.2f}%）".format(label, val, pct))
        lines.append("合计：{}人".format(total_sex))
        if valid_sex:
            log(f"  → 性别分布: {total_sex} 人")
            for item in valid_sex:
                label = sex_map.get(item["dim"], item["dim"])
                log(f"    {label}: {int(item['value'])}人 ({int(item['value'])/total_sex*100:.1f}%)")

        # ---- device ----
        device_data = metrics.get("device_list", [])
        valid_dev = [x for x in device_data if x["dim"] not in ("@_all")]
        total_dev = sum(int(x["value"]) for x in valid_dev)
        lines.append("")
        lines.append("【设备分布】")
        for item in valid_dev:
            label = device_map.get(item["dim"], item["dim"])
            val = int(item["value"])
            pct = val / total_dev * 100
            lines.append("{}：{}人（{:.2f}%）".format(label, val, pct))
        lines.append("合计：{}人".format(total_dev))
        if valid_dev:
            log(f"  → 设备分布: {total_dev} 人")

        # ---- province top5 ----
        province_data = metrics.get("province_list", [])
        province_valid = [x for x in province_data if x["dim"] not in ("@_all", "unknown", "unknow")]
        province_sorted = sorted(province_valid, key=lambda x: int(x["value"]), reverse=True)
        top5_prov = province_sorted[:5]
        total_prov = sum(int(x["value"]) for x in province_valid)
        lines.append("")
        lines.append("【省份分布 Top 5】")
        for item in top5_prov:
            val = int(item["value"])
            pct = val / total_prov * 100
            lines.append("{}：{}人（{:.2f}%）".format(item["dim"], val, pct))
        lines.append("其他：{}人".format(total_prov - sum(int(x["value"]) for x in top5_prov)))
        if top5_prov:
            log(f"  → 省份 Top5:")
            for item in top5_prov:
                log(f"    {item['dim']}: {int(item['value'])}人")

        # ---- city top5 ----
        city_data = metrics.get("city_list", [])
        city_valid = [x for x in city_data if x["dim"] not in ("@_all", "unknown", "unknow")]
        city_sorted = sorted(city_valid, key=lambda x: int(x["value"]), reverse=True)
        top5_city = city_sorted[:5]
        total_city = sum(int(x["value"]) for x in city_valid)
        lines.append("")
        lines.append("【城市分布 Top 5】")
        for item in top5_city:
            val = int(item["value"])
            pct = val / total_city * 100
            lines.append("{}：{}人（{:.2f}%）".format(item["dim"], val, pct))
        lines.append("其他：{}人".format(total_city - sum(int(x["value"]) for x in top5_city)))
        if top5_city:
            log(f"  → 城市 Top5:")
            for item in top5_city:
                log(f"    {item['dim']}: {int(item['value'])}人")

        # ---- save ----
        txt_file = os.path.join(out_dir, "wechat_fans_portrait_{}.txt".format(date_str))
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        # save JSON also
        json_out = {
            "age": [{"dim": x["dim"], "label": age_map.get(x["dim"], x["dim"]), "value": int(x["value"])} for x in valid_age],
            "sex": [{"dim": x["dim"], "label": sex_map.get(x["dim"], x["dim"]), "value": int(x["value"])} for x in valid_sex],
            "device": [{"dim": x["dim"], "label": device_map.get(x["dim"], x["dim"]), "value": int(x["value"])} for x in valid_dev],
            "province": [{"dim": x["dim"], "value": int(x["value"])} for x in province_valid],
            "city": [{"dim": x["dim"], "value": int(x["value"])} for x in city_valid]
        }
        json_file = os.path.join(out_dir, "wechat_fans_portrait_{}.json".format(date_str))
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(json_out, f, ensure_ascii=False, indent=2)

        log("✓ 粉丝画像已保存:")
        log(f"  → TXT: {txt_file}")
        log(f"  → JSON: {json_file}")
        log("=" * 50)

asyncio.run(main())
