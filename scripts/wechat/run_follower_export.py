# -*- coding: utf-8 -*-
"""关注者数据导出 - 自动找页面和帧，不用坐标"""
import asyncio, os, httpx
from datetime import date
from playwright.async_api import async_playwright

CHROME_PORT = 9222
HERE = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(HERE, "..", "..", "downloads")
SAVE_DIR = os.environ.get("MEDIAEXPORT_OUTPUT_DIR", os.path.join(HERE, "..", "..", "output"))

import time

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def log_step(step, msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{step}] {msg}", flush=True)

def find_stat_page(context):
    for pg in context.pages:
        if 'channels.weixin.qq.com/platform' in pg.url:
            return pg
    return context.pages[-1] if context.pages else None

async def wait_for_csv(download_dir, timeout=20):
    existing = set(os.listdir(download_dir))
    for _ in range(timeout * 2):
        await asyncio.sleep(0.5)
        files = [f for f in os.listdir(download_dir) if f.endswith('.csv') and f not in existing]
        if files: return files[0]
    return None

async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(SAVE_DIR, exist_ok=True)

    log("=" * 50)
    log("视频号 - 关注者数据导出")
    log("=" * 50)
    log(f"输出目录: {SAVE_DIR}")

    log_step("1/7", "连接 Chrome CDP...")
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"http://127.0.0.1:{CHROME_PORT}/json/version", timeout=5)
        ws_url = resp.json()["webSocketDebuggerUrl"]

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url)
        context = browser.contexts[0]
        page = find_stat_page(context)
        cdp = await context.new_cdp_session(page)
        await cdp.send("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": DOWNLOAD_DIR, "eventsEnabled": True})

        log_step("1/7", "✓ Chrome CDP 连接成功")

        log_step("2/7", "导航到 数据中心 → 关注者数据...")
        await page.evaluate("""
            () => {
                const items = document.querySelectorAll('.finder-ui-desktop-menu__icon_menu__name');
                for (const item of items) { if (item.innerText.includes('数据中心')) item.click(); }
            }
        """)
        await asyncio.sleep(0.8)
        await page.evaluate("""
            () => {
                const items = document.querySelectorAll('.finder-ui-desktop-sub-menu__item');
                for (const item of items) { if (item.innerText.includes('关注者数据')) item.click(); }
            }
        """)
        await asyncio.sleep(3)
        log_step("2/7", "✓ 导航完成")

        for fn in os.listdir(DOWNLOAD_DIR):
            if fn.endswith('.csv'): os.remove(os.path.join(DOWNLOAD_DIR, fn))

        for f in page.frames:
            if 'micro/statistic/follower' in f.url:
                log_step("3/7", "找到关注者数据 iframe，切换「近30天」...")
                # 直接找增长详情区域的 radio input
                r = await f.evaluate("""
                    () => {
                        // 找"增长详情"元素
                        const iter = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                        let zengzhang = null;
                        let node;
                        while (node = iter.nextNode()) {
                            if (node.textContent.trim() === "增长详情") { zengzhang = node; break; }
                        }
                        if (!zengzhang) return "增长详情 not found";

                        // 向上走到finder-card
                        let card = zengzhang.parentElement;
                        for (let i = 0; i < 10; i++) {
                            card = card ? card.parentElement : null;
                            if (!card) break;
                            if (card.className && card.className.includes('finder-card')) break;
                        }
                        if (!card) return "finder-card not found";

                        // 在finder-card内找所有input[type=radio]
                        const radios = card.querySelectorAll('input[type="radio"]');
                        for (const inp of radios) {
                            if (inp.value === '2') {
                                inp.click();
                                return "clicked radio value=2, checked=" + inp.checked;
                            }
                        }
                        return "radio value=2 not found in finder-card";
                    }
                """)
                if "clicked" in r:
                    log_step("3/7", f"✓ 近30天切换成功: {r}")
                else:
                    log_step("3/7", f"⚠ 近30天切换结果: {r}")
                await asyncio.sleep(5)

                log_step("4/7", "读取日期范围...")
                date_range = await f.evaluate("""
                    () => {
                        const iter = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                        let node;
                        while (node = iter.nextNode()) {
                            const t = node.textContent.trim();
                            if (t.includes('统计时间') && t.length < 30) return t;
                        }
                        return "not found";
                    }
                """)
                log_step("4/7", f"✓ 日期范围: {date_range}")

                log_step("5/7", "点击「下载表格」按钮...")
                dl = await f.evaluate("""
                    () => {
                        const iter = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                        let node;
                        while (node = iter.nextNode()) {
                            if (node.textContent.trim() === '下载表格') { node.parentElement.click(); return 'ok'; }
                        }
                        return 'not found';
                    }
                """)
                if dl == "ok":
                    log_step("5/7", "✓ 已点击下载")
                else:
                    log_step("5/7", f"✗ 下载按钮未找到: {dl}")

                log_step("6/7", "等待CSV文件下载...")
                csv = await wait_for_csv(DOWNLOAD_DIR)
                if csv:
                    content = open(os.path.join(DOWNLOAD_DIR, csv), 'r', encoding='utf-8-sig').read()
                    dates = [l for l in content.split('\n') if '2026/' in l and l.strip()]
                    filename = f"wechat_follower_{date.today().strftime('%Y%m%d')}.csv"
                    save_path = os.path.join(SAVE_DIR, filename)
                    with open(save_path, 'w', encoding='utf-8-sig') as out:
                        out.write(content)
                    log_step("6/7", f"✓ 下载完成")
                    log(f"  文件名: {filename}")
                    log(f"  数据量: {len(dates)}行")
                    if dates:
                        log(f"  时间范围: {dates[-1][:20]} -> {dates[0][:20]}")
                    log(f"  保存路径: {save_path}")
                else:
                    log_step("6/7", "✗ 下载超时，未收到CSV")
                break

        await browser.close()
        log_step("7/7", "✓ 关注者数据导出完成")
        log("=" * 50)

asyncio.run(main())
