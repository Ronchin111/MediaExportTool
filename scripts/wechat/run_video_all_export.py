# -*- coding: utf-8 -*-
"""视频数据-全部视频导出 - 视频号"""
import asyncio, os, sys, httpx, time
from datetime import date
from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(HERE, "..", "..", "downloads")
SAVE_DIR = os.environ.get("MEDIAEXPORT_OUTPUT_DIR", os.path.join(HERE, "..", "..", "output"))

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from dl_utils import artifact_dirs, snapshot_dirs, wait_for_new_file, verify_csv, load_wc_selectors   # noqa: E402
from tool_utils import get_chrome_port

CHROME_PORT = get_chrome_port()
SEL = load_wc_selectors()

# ---------- 工具函数 ----------

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def log_step(step, msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{step}] {msg}", flush=True)

def find_video_page(context):
    for pg in context.pages:
        if 'channels.weixin.qq.com/platform' in pg.url:
            return pg
    log("✗ 未找到视频号后台页面")
    log("请按顺序操作：")
    log("  1. 双击桌面「视频号助手登录.bat」启动Chrome")
    log("  2. 在Chrome中手动打开 https://channels.weixin.qq.com 并登录")
    log("  3. 确认页面显示「视频号助手」后台后，再运行此脚本")
    sys.exit(1)

def wait_for_frame(page, keyword, timeout=8, name="frame"):
    for i in range(timeout):
        frames = [f for f in page.frames if keyword in f.url]
        if frames:
            log(f"  ✓ [{name}] iframe 已加载")
            return frames[0]
        log(f"  等待{name}加载... ({i+1}/{timeout}秒)")
        time.sleep(1)
    log(f"✗ 等待{name}超时（{keyword}）")
    log("可能原因：网络较慢或页面未完全加载")
    log("建议：稍后重试，或检查Chrome中视频号后台是否显示正常")
    sys.exit(1)

async def wait_for_csv(download_dir, timeout=20):
    existing = set(os.listdir(download_dir))
    for i in range(timeout * 2):
        await asyncio.sleep(0.5)
        files = [f for f in os.listdir(download_dir) if f.endswith('.csv') and f not in existing]
        if files:
            return files[0]
        if i % 4 == 2:
            log(f"  等待下载... ({i//2}/{timeout}秒)")
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
    missing = []
    for t in required:
        if not await page.get_by_text(t, exact=True).count():
            missing.append(t)
    if missing:
        log(f"  ⚠ 疑似页面改版（{key}），缺关键文本: {missing}")


# ---------- 主流程 ----------

async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(SAVE_DIR, exist_ok=True)

    for fn in os.listdir(DOWNLOAD_DIR):
        if fn.endswith('.csv'):
            os.remove(os.path.join(DOWNLOAD_DIR, fn))

    log("=" * 50)
    log("视频号 - 全部视频数据导出")
    log("=" * 50)
    log(f"输出目录: {SAVE_DIR}")

    log_step("1/7", "连接 Chrome CDP...")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{CHROME_PORT}/json/version", timeout=5)
            ws_url = resp.json()["webSocketDebuggerUrl"]
    except Exception as e:
        log(f"✗ 无法连接到Chrome调试端口 ({CHROME_PORT})")
        log(f"  错误: {e}")
        log("请确认「视频号助手登录.bat」已启动并保持运行")
        sys.exit(1)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url)
        context = browser.contexts[0]
        page = find_video_page(context)
        # 下载容错：让 Chrome 直接把文件落到 downloads/，浏览器崩了也能收编 .crdownload
        try:
            cdp = await context.new_cdp_session(page)
            await cdp.send("Browser.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": os.path.abspath(DOWNLOAD_DIR),
                "eventsEnabled": False,
            })
            log(f"  ✓ 下载目录: {os.path.abspath(DOWNLOAD_DIR)}")
        except Exception as e:
            log(f"  ⚠ 指定下载目录失败（改用默认）: {str(e)[:100]}")

        log_step("1/7", "✓ Chrome CDP 连接成功")

        # 导航
        log_step("2/7", "导航到 数据中心 → 视频数据 → 全部视频...")
        await click_menu(page, SEL["top_menu_selector"], SEL["menus"]["数据中心"])
        await asyncio.sleep(0.8)
        await click_menu(page, SEL["sub_menu_selector"], SEL["menus"]["视频数据"])
        await asyncio.sleep(1.5)
        await click_menu(page, SEL["sub_menu_selector"], SEL["menus"]["全部视频"])
        log_step("2/7", "✓ 已进入全部视频页")
        await check_fp(page, "video_all")

        # 等待post frame
        log_step("3/7", "等待数据 iframe 加载...")
        frame = wait_for_frame(page, 'micro/statistic/post', name="数据帧")

        # 找近30天radio
        log_step("4/7", "切换时间范围为「近30天」...")
        r = await frame.evaluate("""
            () => {
                const labels = document.querySelectorAll('label');
                const candidates = [];
                labels.forEach(label => {
                    if (label.innerText.includes('近30天')) {
                        const rect = label.getBoundingClientRect();
                        if (rect.height > 0 && rect.width > 0) {
                            candidates.push({y: Math.round(rect.y), label: label});
                        }
                    }
                });
                // 优先选y最大的（主数据表区域）
                candidates.sort((a, b) => b.y - a.y);
                if (candidates.length === 0) return "未找到可见的「近30天」选项";
                const label = candidates[0].label;
                const inputs = label.querySelectorAll('input[type="radio"]');
                for (const inp of inputs) {
                    if (inp.value === '2') { inp.click(); return "ok"; }
                }
                return "「近30天」选项无radio按钮";
            }
        """)
        if r != "ok":
            log(f"✗ 近30天切换失败: {r}")
            log("提示：视频号后台UI可能已更新，请联系技术支持")
            await browser.close()
            sys.exit(1)
        log_step("4/7", "✓ 近30天已切换")
        await asyncio.sleep(5)

        # 日期范围
        log_step("5/7", "读取日期范围...")
        date_range = await frame.evaluate("""
            () => {
                const iter = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                let node;
                while (node = iter.nextNode()) {
                    const t = node.textContent.trim();
                    if (t.includes('统计时间') && t.length < 30) return t;
                }
                return "未找到";
            }
        """)
        log_step("5/7", f"✓ 日期范围: {date_range}")

        # 下载
        log_step("6/7", "点击「下载表格」按钮，等待CSV下载...")
        dl_dirs = [os.path.abspath(DOWNLOAD_DIR)] + artifact_dirs()
        before = snapshot_dirs(dl_dirs)
        dl = await frame.evaluate("""
            () => {
                const iter = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                let node;
                while (node = iter.nextNode()) {
                    if (node.textContent.trim() === '下载表格') { node.parentElement.click(); return 'ok'; }
                }
                return '未找到「下载表格」按钮';
            }
        """)
        if dl != "ok":
            log(f"✗ 点击下载失败: {dl}")
            await browser.close()
            sys.exit(1)
        log_step("6/7", "✓ 已点击下载，等待文件...")

        csv_path, partial = await wait_for_new_file(dl_dirs, before, timeout=45, log=log)
        if not csv_path:
            log("✗ 等待下载超时，未收到CSV文件")
            log("建议：检查Chrome是否有弹窗阻止了下载，或网络是否稳定")
            await browser.close()
            sys.exit(1)
        if partial:
            log("  ⚠ 浏览器在下载期间崩溃了（本机 Chrome 已知问题），已收编中途文件")
        ok, rows = verify_csv(csv_path)
        if not ok:
            log(f"✗ 收到的文件不完整（{rows} 行），放弃")
            await browser.close()
            sys.exit(1)

        content = open(csv_path, 'r', encoding='utf-8-sig').read()
        lines = [l for l in content.split('\n') if '2026/' in l and l.strip()]

        filename = f"wechat_video_all_{date.today().strftime('%Y%m%d')}.csv"
        save_path = os.path.join(SAVE_DIR, filename)
        with open(save_path, 'w', encoding='utf-8-sig') as out:
            out.write(content)

        await browser.close()

        log_step("7/7", f"✓ 导出成功")
        log(f"  文件名: {filename}")
        log(f"  数据量: {len(lines)}天")
        if lines:
            log(f"  时间范围: {lines[-1][:20]} → {lines[0][:20]}")
        log(f"  保存路径: {save_path}")
        log("=" * 50)

asyncio.run(main())
