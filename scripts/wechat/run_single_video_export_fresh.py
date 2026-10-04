# -*- coding: utf-8 -*-
"""
Single Video Export - Fixed Version (2026-05-10)
Based on working export_final.py logic
"""
import asyncio
import os
import sys
import httpx
from datetime import date
from playwright.async_api import async_playwright
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(HERE, "..", "..", "downloads")
SAVE_DIR = os.environ.get("MEDIAEXPORT_OUTPUT_DIR", os.path.join(HERE, "..", "..", "output"))

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
    """Find the WeChat Channels page"""
    for page in context.pages:
        if 'channels.weixin.qq.com' in page.url:
            return page
    return context.pages[-1] if context.pages else None


async def wait_for_csv(download_dir, timeout=20):
    """Wait for CSV file to appear in download directory"""
    existing = set(os.listdir(download_dir))
    for _ in range(timeout * 2):
        await asyncio.sleep(0.5)
        files = [f for f in os.listdir(download_dir) if f.endswith('.csv') and f not in existing]
        if files:
            return files[0]
    return None


def clean_downloads(download_dir):
    """Remove old CSV files from download directory"""
    count = 0
    for fn in os.listdir(download_dir):
        if fn.endswith('.csv'):
            try:
                os.remove(os.path.join(download_dir, fn))
                count += 1
            except:
                pass
    if count:
        log(f"  清理了 {count} 个旧CSV文件")


def save_csv(src_path, dst_name):
    """Save CSV with filtering"""
    content = open(src_path, 'r', encoding='utf-8-sig').read()

    # Filter out non-data rows
    lines = content.split('\n')
    filtered = []
    for l in lines:
        if not l.strip():
            continue
        if l.startswith('由于') or l.startswith('视频号') or l.startswith('时间'):
            continue
        if l.startswith('"时间"'):
            continue
        filtered.append(l)

    save_path = os.path.join(SAVE_DIR, dst_name)
    with open(save_path, 'w', encoding='utf-8-sig') as out:
        out.write('\n'.join(filtered))

    dates = [l for l in filtered if '2026/' in l or '2025/' in l]
    return len(dates), dates[-1][:10] if dates else '', dates[0][:10] if dates else ''


async def click_menu(page, text):
    """Click main menu item"""
    await page.evaluate(f"""
        () => {{
            const items = document.querySelectorAll('{SEL["top_menu_selector"]}');
            for (const item of items) {{ if (item.innerText.includes('{text}')) {{ item.click(); return; }} }}
        }}
    """)
    await asyncio.sleep(0.8)


async def click_submenu(page, text):
    """Click submenu item"""
    await page.evaluate(f"""
        () => {{
            const items = document.querySelectorAll('{SEL["sub_menu_selector"]}');
            for (const item of items) {{ if (item.innerText.includes('{text}')) {{ item.click(); return; }} }}
        }}
    """)


async def check_fp(page, key):
    required = SEL.get("fingerprint", {}).get(key, [])
    missing = [t for t in required if not await page.get_by_text(t, exact=True).count()]
    if missing:
        log(f"  ⚠ 疑似页面改版（{key}），缺关键文本: {missing}")


async def export_video_single(page, DOWNLOAD_DIR, SAVE_DIR):
    """Export single video data - based on export_final.py logic"""
    log_step("导出", "正在查找数据 iframe...")
    clean_downloads(DOWNLOAD_DIR)

    # Find the statistic iframe
    for f in page.frames:
        if 'micro/statistic/post' in f.url:
            log_step("导出", "✓ 找到数据 iframe")

            # Step 1: Click "单篇视频" tab first!
            log_step("导出", "切换到「单篇视频」标签...")
            tab_result = await f.evaluate("""
                () => {
                    const navs = document.querySelectorAll('.weui-desktop-tab__nav a');
                    for (const nav of navs) {
                        if (nav.innerText.trim() === '单篇视频') {
                            nav.click();
                            return 'ok';
                        }
                    }
                    return 'not found';
                }
            """)
            if tab_result == "ok":
                log_step("导出", "✓ 已切换到「单篇视频」")
            else:
                log_step("导出", f"⚠ 切换标签结果: {tab_result}")
                return False
            await asyncio.sleep(2)

            # Step 2: Click "近30天" filter
            log_step("导出", "切换时间范围为「近30天」...")
            r = await f.evaluate("""
                () => {
                    const iter = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                    let node;
                    while (node = iter.nextNode()) {
                        if (node.textContent.trim() === "近30天") {
                            let label = node.parentElement;
                            while (label && label.tagName !== "LABEL") { label = label.parentElement; }
                            if (!label) continue;
                            const inputs = label.querySelectorAll('input[type=radio]');
                            for (const inp of inputs) {
                                if (inp.value === '2') { inp.click(); return "ok"; }
                            }
                        }
                    }
                    return "not found";
                }
            """)
            if r == "ok":
                log_step("导出", "✓ 近30天已切换")
            else:
                log_step("导出", f"⚠ 近30天切换结果: {r}")
                return False
            await asyncio.sleep(5)

            # Click download button
            log_step("导出", "点击「下载表格」按钮...")
            dl_dirs = [os.path.abspath(DOWNLOAD_DIR)] + artifact_dirs()
            before = snapshot_dirs(dl_dirs)
            dl = await f.evaluate("""
                () => {
                    const iter = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                    let node;
                    while (node = iter.nextNode()) {
                        if (node.textContent.trim() === "下载表格") {
                            node.parentElement.click();
                            return 'ok';
                        }
                    }
                    return 'not found';
                }
            """)
            if dl == "ok":
                log_step("导出", "✓ 已点击下载，等待文件...")
            else:
                log_step("导出", f"✗ 下载按钮未找到: {dl}")

            csv_path, partial = await wait_for_new_file(dl_dirs, before, timeout=45, log=log)
            if csv_path and partial:
                log("  ⚠ 浏览器在下载期间崩溃了（本机 Chrome 已知问题），已收编中途文件")
            if csv_path:
                ok, rows_raw = verify_csv(csv_path)
                if not ok:
                    log_step("导出", f"✗ 文件不完整（{rows_raw} 行），放弃")
                    return False
                filename = "wechat_video_single_" + date.today().strftime('%Y%m%d') + ".csv"
                rows, start, end = save_csv(csv_path, filename)
                log_step("导出", "✓ 下载完成")
                log(f"  文件名: {filename}")
                log(f"  数据量: {rows}行")
                log(f"  时间范围: {start} -> {end}")
                log(f"  保存路径: {os.path.join(SAVE_DIR, filename)}")
                return True
            else:
                log_step("导出", "✗ 下载超时，未收到CSV")
                return False
            break
    log_step("导出", "✗ 未找到数据 iframe，导出失败")
    return False


async def main():
    log("=" * 50)
    log("视频号 - 单篇视频数据导出")
    log("=" * 50)
    log(f"输出目录: {SAVE_DIR}")

    # Connect to Chrome via CDP
    log_step("1/5", "连接 Chrome CDP...")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{CHROME_PORT}/json/version", timeout=5)
            ws_url = resp.json()["webSocketDebuggerUrl"]
            log_step("1/5", "✓ Chrome CDP 连接成功")
    except Exception as e:
        log(f"✗ CDP 连接失败: {e}")
        sys.exit(1)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url)
        context = browser.contexts[0]
        page = find_stat_page(context)

        if not page:
            log("✗ 未找到视频号后台页面")
            return

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

        # Navigate
        log_step("2/5", "导航到 数据中心 → 视频数据 → 单篇视频...")
        await click_menu(page, SEL["menus"]["数据中心"])
        await asyncio.sleep(1)

        log_step("3/5", "点击「视频数据」子菜单...")
        await click_submenu(page, SEL["menus"]["视频数据"])
        await asyncio.sleep(2)

        log_step("4/5", "点击「单篇视频」子菜单...")
        await click_submenu(page, SEL["menus"]["单篇视频"])
        await asyncio.sleep(3)
        await check_fp(page, "video_single")

        # Export
        log_step("5/5", "执行单篇视频数据导出...")
        ok = await export_video_single(page, DOWNLOAD_DIR, SAVE_DIR)

        await browser.close()

    log("=" * 50)
    log(f"{'✓ 单篇视频导出完成' if ok else '✗ 单篇视频导出失败'} | 保存到: {SAVE_DIR}")
    log("=" * 50)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
