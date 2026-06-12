# -*- coding: utf-8 -*-
"""
WeChat Channels Source Distribution Data Export
Endpoint: new_post_total_data
Data: play count, last 7 days, all fans
Output: channel name + value table -> source_dist_result.txt/json
"""
import asyncio, json, os, urllib.request
from datetime import date
from playwright.async_api import async_playwright
import time

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def log_step(step, msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{step}] {msg}", flush=True)

HERE = os.path.dirname(os.path.abspath(__file__))

def get_ws_url():
    try:
        with urllib.request.urlopen("http://localhost:9222/json/version", timeout=3) as r:
            return json.loads(r.read())["webSocketDebuggerUrl"]
    except:
        return None

async def main():
    log("=" * 50)
    log("视频号 - 来源分布数据导出")
    log("=" * 50)

    log_step("1/5", "连接 Chrome CDP...")
    ws_url = get_ws_url()
    if not ws_url:
        log("✗ Chrome 未运行（CDP端口9222不可达）")
        log("请先启动视频号助手登录.bat")
        return
    log_step("1/5", "✓ Chrome CDP 连接成功")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url)
        context = browser.contexts[0]

        page = None
        for pg in context.pages:
            if "channels.weixin.qq.com" in pg.url and "platform" in pg.url:
                page = pg
                break
        if not page:
            log("✗ 未找到视频号后台页面")
            return

        log_step("2/5", "监听 API 请求 (new_post_total_data)...")
        captured = {}

        async def on_response(response):
            url = response.url
            if "new_post_total_data" not in url:
                return
            try:
                body = await response.text()
                data = json.loads(body)
                items = data["data"]["dataByTabtype"]
                results = [(item["tabTypeName"], sum(int(x) for x in item["data"]["browse"])) for item in items]
                captured["results"] = results
                log(f"  ✓ API 响应已捕获: {len(items)} 个渠道")
            except Exception as e:
                captured["error"] = str(e)
                log(f"  ⚠ API 响应解析异常: {e}")

        page.on("response", on_response)
        for frame in page.frames:
            frame.on("response", on_response)

        # Force fresh load to trigger API request
        log_step("3/5", "刷新页面触发 API 请求...")
        await page.goto("about:blank", timeout=5000)
        await asyncio.sleep(1)
        try:
            await page.goto("https://channels.weixin.qq.com/platform/statistic/post",
                           timeout=15000, wait_until="load")
        except:
            pass
        await asyncio.sleep(6)

        if "results" not in captured:
            log("✗ 未捕获到 new_post_total_data 响应")
            if "error" in captured:
                log(f"  异常: {captured['error']}")
            log("可能原因：页面未正常加载或登录已过期")
            return

        results = captured["results"]
        total = sum(v for _, v in results)
        log_step("3/5", f"✓ 数据已获取: {len(results)} 个渠道, 总播放量 {total}")

        # Write table to UTF-8 file
        log_step("4/5", "生成来源分布报告...")
        lines = ["来源分布 (近7天/播放量)", "", f"{'渠道':<12} {'数值'}", "-" * 20]
        for name, val in results:
            pct = val / total * 100 if total > 0 else 0
            lines.append(f"{name:<12} {val} ({pct:.1f}%)")
        lines.append("-" * 20)
        lines.append(f"{'合计':<12} {total}")

        output = "\n".join(lines)
        out_dir = os.environ.get("MEDIAEXPORT_OUTPUT_DIR", os.path.join(HERE, "..", "..", "output"))
        os.makedirs(out_dir, exist_ok=True)
        date_str = date.today().strftime("%Y%m%d")
        txt_path = os.path.join(out_dir, f"wechat_source_dist_{date_str}.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(output)
        json_path = os.path.join(out_dir, f"wechat_source_dist_{date_str}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"results": results, "total": total}, f, ensure_ascii=False, indent=2)

        log_step("4/5", "✓ 报告已生成")

        log_step("5/5", "输出汇总:")
        for name, val in results:
            pct = val / total * 100 if total > 0 else 0
            log(f"  → {name}: {val} ({pct:.1f}%)")
        log(f"  → 合计: {total}")
        log(f"  ✓ 文件已保存: {txt_path}")
        log(f"  ✓ JSON已保存: {json_path}")
        log("=" * 50)

asyncio.run(main())
