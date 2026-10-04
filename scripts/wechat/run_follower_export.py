# -*- coding: utf-8 -*-
"""关注者数据导出 - 自动找页面和帧，不用坐标"""
import asyncio, os, sys, httpx
from datetime import date
from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(HERE, "..", "..", "downloads")
SAVE_DIR = os.environ.get("MEDIAEXPORT_OUTPUT_DIR", os.path.join(HERE, "..", "..", "output"))

import time

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from dl_utils import artifact_dirs, snapshot_dirs, wait_for_new_file, verify_csv, load_wc_selectors   # noqa: E402
from tool_utils import get_chrome_port

CHROME_PORT = get_chrome_port()
SEL = load_wc_selectors()

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


async def click_menu(page, selector, text):
    await page.evaluate(f"""
        () => {{
            const items = document.querySelectorAll('{selector}');
            for (const item of items) {{ if (item.innerText.includes('{text}')) {{ item.click(); return; }} }}
        }}
    """)


async def check_fp(page, key):
    required = SEL.get("fingerprint", {}).get(key, [])
    missing = [t for t in required if not await page.get_by_text(t, exact=True).count()]
    if missing:
        log(f"  ⚠ 疑似页面改版（{key}），缺关键文本: {missing}")


async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(SAVE_DIR, exist_ok=True)

    log("=" * 50)
    log("视频号 - 关注者数据导出")
    log("=" * 50)
    log(f"输出目录: {SAVE_DIR}")

    log_step("1/7", "连接 Chrome CDP...")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{CHROME_PORT}/json/version", timeout=5)
            ws_url = resp.json()["webSocketDebuggerUrl"]
    except Exception as e:
        log(f"✗ Chrome 未运行（CDP端口{CHROME_PORT}不可达）: {str(e)[:80]}")
        log("请先启动视频号助手登录.bat")
        sys.exit(1)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url)
        context = browser.contexts[0]
        page = find_stat_page(context)
        # 下载容错：让 Chrome 直接把文件落到 downloads/，浏览器崩了也能收编 .crdownload
        try:
            cdp = await context.new_cdp_session(page)
            await cdp.send("Browser.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": os.path.abspath(DOWNLOAD_DIR),
                "eventsEnabled": False,
            })
            log(f"  下载目录: {os.path.abspath(DOWNLOAD_DIR)}")
        except Exception as e:
            log(f"  ⚠ 指定下载目录失败（改用默认）: {str(e)[:100]}")

        log_step("1/7", "✓ Chrome CDP 连接成功")

        log_step("2/7", "导航到 数据中心 → 关注者数据...")
        await click_menu(page, SEL["top_menu_selector"], SEL["menus"]["数据中心"])
        await asyncio.sleep(0.8)
        await click_menu(page, SEL["sub_menu_selector"], SEL["menus"]["关注者数据"])
        await asyncio.sleep(3)
        log_step("2/7", "✓ 导航完成")
        await check_fp(page, "follower")

        for fn in os.listdir(DOWNLOAD_DIR):
            if fn.endswith('.csv'): os.remove(os.path.join(DOWNLOAD_DIR, fn))

        ok = False
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
                    break
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
                dl_dirs = [os.path.abspath(DOWNLOAD_DIR)] + artifact_dirs()
                before = snapshot_dirs(dl_dirs)
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
                csv_path, partial = await wait_for_new_file(dl_dirs, before, timeout=45, log=log)
                if csv_path and partial:
                    log("  ⚠ 浏览器在下载期间崩溃了（本机 Chrome 已知问题），已收编中途文件")
                if csv_path:
                    content = open(csv_path, 'r', encoding='utf-8-sig').read()
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
                    ok = True
                else:
                    log_step("6/7", "✗ 下载超时，未收到CSV")
                break

        await browser.close()
        log_step("7/7", "✓ 关注者数据导出完成" if ok else "✗ 关注者数据导出失败")
        log("=" * 50)
        if not ok:
            sys.exit(1)

asyncio.run(main())
