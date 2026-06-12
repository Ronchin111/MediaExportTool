"""抖音创作者中心 - 一键数据导出（合并版 v2）
一次浏览器会话完成4项输出，每项独立容错
"""
import asyncio
import os
import json
import traceback
from datetime import datetime
from playwright.async_api import async_playwright

from tool_utils import get_platform_profile_dir, find_chrome
DEDICATED_USER_DATA = get_platform_profile_dir('douyin')
CHROME_PATH = find_chrome() or r'C:\Program Files\Google\Chrome\Application\chrome.exe'
OUTPUT_DIR = os.environ.get("MEDIAEXPORT_OUTPUT_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output"))
DOWNLOAD_TIMEOUT = 60000
TARGET_API = "/janus/douyin/creator/bff/data/fans/summary/v2"
TARGET_METRICS = ["主页访问", "作品点赞", "作品分享", "作品评论", "封面点击率", "总粉丝量"]


def ts_now():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log_step(step, msg):
    """带步骤编号的日志输出"""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{step}] {msg}")


def log_info(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


async def click_sidebar_submenu(page, parent_menu, submenu):
    try:
        sub = page.get_by_text(submenu, exact=True)
        if not await sub.is_visible():
            parent = page.get_by_text(parent_menu, exact=True)
            if await parent.is_visible():
                await parent.click()
                log_info(f"  → 展开父菜单「{parent_menu}」")
                await sub.wait_for(state="visible", timeout=5000)
        await sub.click()
        log_info(f"  → 点击子菜单「{submenu}」")
        await asyncio.sleep(3)
        return True
    except Exception as e:
        log_info(f"  ✗ 点击子菜单失败: {e}")
        return False


async def click_text(page, text, exact=False):
    try:
        item = page.get_by_text(text, exact=exact)
        if await item.is_visible():
            await item.click()
            await asyncio.sleep(2)
            return True
    except:
        pass
    return False


# ---- Task 1: Export Play Data xlsx ----

async def task1_export_play_data(page):
    print("\n" + "=" * 50)
    print("[1/4] 表格导出：播放量数据")
    print("=" * 50)

    log_step("1/4", "导航到 数据中心 → 账号总览...")
    await click_sidebar_submenu(page, "数据中心", "账号总览")
    await asyncio.sleep(3)
    log_step("1/4", "切换时间范围为「近30天」...")
    clicked = await click_text(page, "近30天", exact=True)
    if clicked:
        log_info("  ✓ 已切换到近30天")
    else:
        log_info("  ⚠ 未找到「近30天」选项，使用默认范围")

    log_step("1/4", "查找「导出数据」按钮...")
    try:
        export_btn = page.get_by_text("导出数据", exact=True)
        if await export_btn.is_visible():
            log_info("  → 点击「导出数据」，等待下载...")
            async with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as dl:
                await export_btn.click()
            download = await dl.value
            path = os.path.join(OUTPUT_DIR, f"douyin_play_data_{datetime.now().strftime('%Y%m%d')}.xlsx")
            await download.save_as(path)
            log_info(f"  ✓ 下载完成: {os.path.basename(path)}")
            log_info(f"  → 保存路径: {path}")
            return path
        else:
            log_info("  ✗ 「导出数据」按钮不可见")
    except Exception as e:
        log_info(f"  ✗ 导出失败: {e}")
        await page.screenshot(path=os.path.join(OUTPUT_DIR, f"err_task1_{ts_now()}.png"))
        log_info(f"  → 错误截图已保存")
    return None


# ---- Task 2: Export Video List xlsx ----

async def task2_export_video_list(page):
    print("\n" + "=" * 50)
    print("[2/4] 表格导出：作品列表")
    print("=" * 50)

    log_step("2/4", "导航到 数据中心 → 作品分析...")
    await click_sidebar_submenu(page, "数据中心", "作品分析")
    await asyncio.sleep(3)
    log_step("2/4", "切换到「投稿作品」→「投稿列表」...")
    c1 = await click_text(page, "投稿作品", exact=True)
    if c1:
        log_info("  ✓ 已切换到「投稿作品」")
    c2 = await click_text(page, "投稿列表", exact=True)
    if c2:
        log_info("  ✓ 已切换到「投稿列表」")
    await asyncio.sleep(3)

    log_step("2/4", "查找「导出数据」按钮...")
    try:
        for btn in await page.get_by_text("导出数据", exact=True).all():
            if await btn.is_visible():
                log_info("  → 点击「导出数据」，等待下载...")
                async with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as dl:
                    await btn.click()
                download = await dl.value
                path = os.path.join(OUTPUT_DIR, f"douyin_video_list_{datetime.now().strftime('%Y%m%d')}.xlsx")
                await download.save_as(path)
                log_info(f"  ✓ 下载完成: {os.path.basename(path)}")
                log_info(f"  → 保存路径: {path}")
                return path
        log_info("  ✗ 未找到可见的「导出数据」按钮")
    except Exception as e:
        log_info(f"  ✗ 导出失败: {e}")
        await page.screenshot(path=os.path.join(OUTPUT_DIR, f"err_task2_{ts_now()}.png"))
        log_info(f"  → 错误截图已保存")
    return None


# ---- Task 3: Scrape Overview Metrics txt ----

async def task3_scrape_overview(page):
    print("\n" + "=" * 50)
    print("[3/4] 页面抓取：账号总览近7天指标")
    print("=" * 50)

    # Navigate
    log_step("3/4", "导航到 数据中心 → 账号总览...")
    ok = await click_sidebar_submenu(page, "数据中心", "账号总览")
    if not ok:
        log_info("  ✗ 导航失败，截图保存")
        await page.screenshot(path=os.path.join(OUTPUT_DIR, f"err_task3_nav_{ts_now()}.png"))
    await asyncio.sleep(3)

    # Click 近7天 tab
    log_step("3/4", "切换时间范围为「近7天」...")
    clicked = await click_text(page, "近7天", exact=True)
    if clicked:
        log_info("  ✓ 已切换到近7天")
    else:
        log_info("  ⚠ 未找到「近7天」选项卡，尝试截图")
        await page.screenshot(path=os.path.join(OUTPUT_DIR, f"err_task3_tab_{ts_now()}.png"))
    await asyncio.sleep(3)

    # Extract metrics via JS
    log_step("3/4", "提取页面指标数据...")
    results = {}
    for metric in TARGET_METRICS:
        try:
            value = await page.evaluate("""
                (metricName) => {
                    const walker = document.createTreeWalker(
                        document.body, NodeFilter.SHOW_TEXT, null, false
                    );
                    let node;
                    while ((node = walker.nextNode())) {
                        if (node.textContent.trim() === metricName) {
                            let el = node.parentElement;
                            for (let i = 0; i < 5; i++) {
                                if (!el) break;
                                const sibling = el.nextElementSibling;
                                if (sibling && sibling.textContent.trim()) {
                                    return sibling.textContent.trim();
                                }
                                el = el.parentElement;
                            }
                            return null;
                        }
                    }
                    return null;
                }
            """, metric)
            results[metric] = value
            if value:
                log_info(f"  ✓ {metric}: {value}")
            else:
                log_info(f"  ⚠ {metric}: 未提取到")
        except Exception as e:
            results[metric] = None
            log_info(f"  ✗ {metric}: 异常 - {e}")

    # Write to txt
    lines = ["抖音账号总览（近7天）", ""]
    for k, v in results.items():
        lines.append(f"{k}: {v or 'N/A'}")
    txt_content = "\n".join(lines)
    ts = ts_now()
    txt_path = os.path.join(OUTPUT_DIR, f"douyin_account_overview_7days_{ts}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt_content)

    log_info(f"  ✓ 指标已保存: {os.path.basename(txt_path)}")
    log_info(f"  → 保存路径: {txt_path}")
    return txt_path


# ---- Task 4: Scrape Fan Portrait via API ----

async def task4_scrape_fans(page):
    print("\n" + "=" * 50)
    print("[4/4] API抓取：粉丝画像")
    print("=" * 50)

    captured = {}

    async def on_response(response):
        if TARGET_API in response.url:
            try:
                body = await response.text()
                captured["body"] = body
            except:
                pass

    page.on("response", on_response)

    log_step("4/4", "导航到 数据中心 → 粉丝画像...")
    ok = await click_sidebar_submenu(page, "数据中心", "粉丝画像")
    if not ok:
        log_info("  ✗ 导航失败")
    await asyncio.sleep(5)

    if "body" in captured:
        log_info(f"  ✓ API响应已捕获: {len(captured['body'])} bytes")
    else:
        log_info("  ⚠ 未捕获到API响应，截图保存")
        await page.screenshot(path=os.path.join(OUTPUT_DIR, f"err_task4_api_{ts_now()}.png"))

    if "body" not in captured:
        return None

    # Parse
    log_step("4/4", "解析粉丝画像数据...")
    try:
        api_data = json.loads(captured["body"])
        if "data" in api_data and "summary" in api_data["data"]:
            summary = api_data["data"]["summary"]
            log_info(f"  → 总粉丝: {summary.get('total_fans', 'N/A')}")
            log_info(f"  → 新增粉丝: {summary.get('new_fans', 'N/A')}")
    except Exception as e:
        log_info(f"  ✗ JSON解析失败: {e}")

    # Format output
    def format_section(title, items):
        if not items:
            return f"\n{title}\n  无数据"
        lines = [f"\n{title}"]
        for item in items:
            name = item.get("name", item.get("key", "?"))
            value = item.get("value", item.get("count", "?"))
            lines.append(f"  {name}: {value}")
        return "\n".join(lines)

    lines = ["抖音粉丝画像", ""]
    age = api_data.get("age_distribution", [])
    lines.append(format_section("年龄分布", age) if age else "\n[年龄分布] 无数据")

    city = api_data.get("city_distribution", [])
    province = api_data.get("province_distribution", [])
    regional = city if city else province
    lines.append(format_section("地域分布", regional) if regional else "\n[地域分布] 无数据")

    device = api_data.get("device_brand_distribution", []) or api_data.get("device_distribution", [])
    lines.append(format_section("设备分布", device) if device else "\n[设备分布] 无数据")

    txt_content = "\n".join(lines)
    ts = ts_now()
    txt_path = os.path.join(OUTPUT_DIR, f"douyin_fan_portrait_{ts}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt_content)

    log_info(f"  ✓ 粉丝画像已保存: {os.path.basename(txt_path)}")
    log_info(f"  → 保存路径: {txt_path}")
    for line in txt_content.split("\n")[:15]:
        log_info(f"    {line}")
    if len(txt_content.split("\n")) > 15:
        log_info(f"    ... (共{len(txt_content.split(chr(10)))}行)")
    return txt_path


# ---- Main ----

async def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    results = []

    async with async_playwright() as p:
        log_info("启动浏览器（抖音专用Profile）...")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=DEDICATED_USER_DATA,
            executable_path=CHROME_PATH,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1920, "height": 1080},
            accept_downloads=True
        )

        page = context.pages[0] if context.pages else await context.new_page()

        print("=" * 60)
        print("抖音创作者中心 - 一键数据导出 v2")
        print("=" * 60)
        log_info(f"输出目录: {OUTPUT_DIR}")
        log_info(f"Chrome Profile: {DEDICATED_USER_DATA}")

        # 登录检查
        print("\n[登录检查]")
        log_info("正在访问抖音创作者中心...")
        try:
            await page.goto(
                "https://creator.douyin.com/creator-micro/home",
                wait_until="domcontentloaded", timeout=60000
            )
        except:
            pass
        await asyncio.sleep(5)

        if "login" in page.url.lower() or "passport" in page.url.lower():
            print("\n" + "=" * 60)
            print("  【需要扫码】抖音创作者中心登录已过期")
            print("  请在弹出的浏览器窗口中扫码重新登录")
            print("  等待自动检测登录...")
            print("=" * 60)
            for i in range(180):
                await asyncio.sleep(1)
                current = page.url.lower()
                if "login" not in current and "passport" not in current:
                    log_info("  ✓ 登录检测成功，继续执行...")
                    break
                if i % 15 == 0 and i > 0:
                    log_info(f"  等待登录... ({i}秒)")
            else:
                log_info("✗ 登录超时（3分钟），退出。")
                await context.close()
                return
            await asyncio.sleep(3)

        log_info("✓ 已登录，开始导出...")
        log_info(f"  共4个任务：播放量数据 → 作品列表 → 账号总览指标 → 粉丝画像")
        await asyncio.sleep(3)  # 等待侧边栏渲染完成

        # 依次执行4个任务，每个独立容错
        tasks = [
            ("播放量数据", task1_export_play_data),
            ("作品列表", task2_export_video_list),
            ("账号总览指标", task3_scrape_overview),
            ("粉丝画像", task4_scrape_fans),
        ]

        for idx, (name, task_fn) in enumerate(tasks):
            try:
                path = await task_fn(page)
                results.append((name, path))
            except Exception as e:
                log_info(f"\n✗ {name} 任务异常: {e}")
                traceback.print_exc()
                # 截图保存现场
                try:
                    err_path = os.path.join(OUTPUT_DIR, f"crash_{name}_{ts_now()}.png")
                    await page.screenshot(path=err_path, full_page=True)
                    log_info(f"  → 崩溃截图已保存: {err_path}")
                except:
                    pass
                results.append((name, None))

        # 汇总
        print("\n" + "=" * 60)
        print("导出完成 - 结果汇总")
        print("=" * 60)
        success = 0
        for name, path in results:
            status = "OK" if path else "FAIL"
            if path:
                success += 1
            icon = "✓" if path else "✗"
            log_info(f"  [{status}] {icon} {name}: {path or '未生成'}")
        log_info(f"\n成功 {success}/{len(results)} | 输出目录: {OUTPUT_DIR}")

        if success == len(results):
            log_info("🎉 抖音全部数据导出成功！")
        elif success > 0:
            log_info(f"⚠ 抖音部分导出成功（{len(results) - success}项失败）")
        else:
            log_info("✗ 抖音全部导出失败")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
