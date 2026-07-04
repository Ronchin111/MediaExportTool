# -*- coding: utf-8 -*-
"""
关注者画像数据导出 - 全自动版 (v2 - fixed parsing)
"""
import asyncio, json, os, time
from datetime import date, timedelta
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

# Fixed mappings based on actual API response
AGE_MAP = {
    "fage_01_17": "17岁及以下",
    "fage_18_24": "18-24岁",
    "fage_25_29": "25-29岁",
    "fage_25_30": "25-30岁",
    "fage_30_39": "30-39岁",
    "fage_31_40": "31-40岁",
    "fage_40_49": "40-49岁",
    "fage_41_50": "41-50岁",
    "fage_50_+": "50岁以上",
    "fage_51_60": "51-60岁",
    "fage_61_": "60岁以上",
}

SEX_MAP = {"1": "男", "2": "女"}

DEVICE_MAP = {"1": "iPhone", "2": "Android"}

async def main():
    log("=" * 50)
    log("视频号 - 关注者画像数据导出")
    log("=" * 50)

    log_step("1/6", "连接 Chrome CDP...")
    ws_url = get_ws_url(CHROME_PORT)
    log_step("1/6", "Chrome CDP 连接成功")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url)
        context = browser.contexts[0]

        log_step("2/6", "查找视频号后台页面...")
        page = None
        for pg in context.pages:
            if "channels.weixin.qq.com" in pg.url:
                page = pg
                break
        if not page:
            log("视频号后台页面未打开")
            return
        log_step("2/6", "已找到视频号后台页面")

        log_step("3/6", "导航到 数据中心 → 关注者数据...")
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
        log_step("3/6", "已导航到关注者数据页面")

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
            log("关注者数据 iframe 未找到")
            return
        log_step("4/6", "iframe 已加载")

        log_step("5/6", "点击关注者画像标签...")
        clicked = await follower_frame.evaluate("""
            () => {
                const allElements = document.querySelectorAll('*');
                for (const el of allElements) {
                    if (el.textContent && el.textContent.trim() === '关注者画像' && el.children.length === 0) {
                        el.click();
                        return 'clicked leaf';
                    }
                }
                for (const el of allElements) {
                    if (el.textContent && el.textContent.trim().includes('画像') && typeof el.click === 'function') {
                        el.click();
                        return 'clicked: ' + el.tagName;
                    }
                }
                return 'tab not found';
            }
        """)
        log_step("5/6", "标签点击: " + str(clicked))
        await asyncio.sleep(3)

        out_dir = os.environ.get("MEDIAEXPORT_OUTPUT_DIR", os.path.join(HERE, "..", "..", "output"))
        os.makedirs(out_dir, exist_ok=True)
        date_str = date.today().strftime("%Y%m%d")

        log_step("6/6", "请求关注者画像 API...")
        result = await follower_frame.evaluate("""
            async () => {
                const url = 'https://channels.weixin.qq.com/cgi-bin/mmfinderassistant-bin/statistic/fans_portrait';
                const resp = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                });
                return await resp.json();
            }
        """)

        # Save raw response for debugging
        raw_file = os.path.join(out_dir, f"wechat_fans_portrait_raw_{date_str}.json")
        with open(raw_file, "w", encoding="utf-8-sig") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        log(f"  原始JSON已保存")

        if result.get("errCode") != 0:
            log(f"  API 错误: {result}")
            return

        resp_json_str = result["data"]["respJson"]
        data = json.loads(resp_json_str)

        # FIXED: metric_info_list, not "data"
        metric_list = data.get("metric_info_list", [])
        if not metric_list:
            log("  画像数据为空（metric_info_list）")
            return

        # Build lookup by name
        metrics = {}
        for m in metric_list:
            metrics[m["name"]] = m["value"]

        lines = []
        lines.append("【视频号关注者画像】")
        lines.append(f"导出日期: {date_str}")
        lines.append("")

        # ---- age ----
        age_data = metrics.get("age_list", [])
        valid_age = [x for x in age_data if x["dim"] not in ("@_all", "unknow")]
        total_age = sum(int(x["value"]) for x in valid_age)
        lines.append("【年龄分布】")
        for item in valid_age:
            label = AGE_MAP.get(item["dim"], item["dim"])
            val = int(item["value"])
            pct = val / total_age * 100 if total_age > 0 else 0
            lines.append(f"{label}：{val}人（{pct:.1f}%）")
        lines.append(f"合计：{total_age}人")
        if valid_age:
            log(f"  年龄分布: {total_age} 人")

        # ---- sex ----
        sex_data = metrics.get("sex_list", [])
        valid_sex = [x for x in sex_data if x["dim"] not in ("@_all", "unknow")]
        total_sex = sum(int(x["value"]) for x in valid_sex)
        lines.append("")
        lines.append("【性别分布】")
        for item in valid_sex:
            label = SEX_MAP.get(item["dim"], item["dim"])
            val = int(item["value"])
            pct = val / total_sex * 100 if total_sex > 0 else 0
            lines.append(f"{label}：{val}人（{pct:.1f}%）")
        lines.append(f"合计：{total_sex}人")
        if valid_sex:
            log(f"  性别分布: {total_sex} 人")

        # ---- device ----
        device_data = metrics.get("device_list", [])
        valid_dev = [x for x in device_data if x["dim"] not in ("@_all", "unknow")]
        total_dev = sum(int(x["value"]) for x in valid_dev)
        lines.append("")
        lines.append("【设备分布】")
        for item in valid_dev:
            label = DEVICE_MAP.get(item["dim"], item["dim"])
            val = int(item["value"])
            pct = val / total_dev * 100 if total_dev > 0 else 0
            lines.append(f"{label}：{val}人（{pct:.1f}%）")
        lines.append(f"合计：{total_dev}人")
        if valid_dev:
            log(f"  设备分布: {total_dev} 人")

        # ---- province top10 ----
        province_data = metrics.get("province_list", [])
        province_valid = [x for x in province_data if x["dim"] not in ("@_all", "unknown", "unknow")]
        province_sorted = sorted(province_valid, key=lambda x: int(x["value"]), reverse=True)
        top10_prov = province_sorted[:10]
        total_prov = sum(int(x["value"]) for x in province_valid)
        lines.append("")
        lines.append("【省份分布 Top 10】")
        for item in top10_prov:
            val = int(item["value"])
            pct = val / total_prov * 100 if total_prov > 0 else 0
            lines.append(f"{item['dim']}：{val}人（{pct:.1f}%）")
        lines.append(f"其他：{total_prov - sum(int(x['value']) for x in top10_prov)}人")

        # ---- city top10 ----
        city_data = metrics.get("city_list", [])
        city_valid = [x for x in city_data if x["dim"] not in ("@_all", "unknown", "unknow")]
        city_sorted = sorted(city_valid, key=lambda x: int(x["value"]), reverse=True)
        top10_city = city_sorted[:10]
        total_city = sum(int(x["value"]) for x in city_valid)
        lines.append("")
        lines.append("【城市分布 Top 10】")
        for item in top10_city:
            val = int(item["value"])
            pct = val / total_city * 100 if total_city > 0 else 0
            lines.append(f"{item['dim']}：{val}人（{pct:.1f}%）")
        lines.append(f"其他：{total_city - sum(int(x['value']) for x in top10_city)}人")

        # ---- save ----
        txt_file = os.path.join(out_dir, f"wechat_fans_portrait_{date_str}.txt")
        with open(txt_file, "w", encoding="utf-8-sig") as f:
            f.write("\n".join(lines))

        json_out = {
            "age": [{"dim": x["dim"], "label": AGE_MAP.get(x["dim"], x["dim"]), "value": int(x["value"])} for x in valid_age],
            "sex": [{"dim": x["dim"], "label": SEX_MAP.get(x["dim"], x["dim"]), "value": int(x["value"])} for x in valid_sex],
            "device": [{"dim": x["dim"], "label": DEVICE_MAP.get(x["dim"], x["dim"]), "value": int(x["value"])} for x in valid_dev],
            "province": [{"dim": x["dim"], "value": int(x["value"])} for x in province_valid],
            "city": [{"dim": x["dim"], "value": int(x["value"])} for x in city_valid]
        }
        json_file = os.path.join(out_dir, f"wechat_fans_portrait_{date_str}.json")
        with open(json_file, "w", encoding="utf-8-sig") as f:
            json.dump(json_out, f, ensure_ascii=False, indent=2)

        log("  关注者画像已保存:")
        log(f"    TXT: {txt_file}")
        log(f"    JSON: {json_file}")
        log(f"    原始: {raw_file}")
        log("=" * 50)

asyncio.run(main())
