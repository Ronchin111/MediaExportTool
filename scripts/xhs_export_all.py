"""小红书创作者中心 - 一键数据导出 v1
一次浏览器会话完成3大任务（账号概览/内容分析/粉丝数据），每项独立容错
"""

import asyncio
import os
import random
import traceback
from datetime import datetime

from playwright.async_api import async_playwright

# ── 常量 ──────────────────────────────────────────────
from tool_utils import get_platform_profile_dir, find_chrome
DEDICATED_USER_DATA = get_platform_profile_dir('xhs')
CHROME_PATH = find_chrome() or r'C:\Program Files\Google\Chrome\Application\chrome.exe'
OUTPUT_DIR = os.environ.get("MEDIAEXPORT_OUTPUT_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output"))
DOWNLOAD_TIMEOUT = 60000
HOME_URL = "https://creator.xiaohongshu.com/new/home"

TAB_NAMES = ["观看数据", "互动数据", "涨粉数据", "发布数据"]
TAB_FILE_KEYS = ["watch", "interact", "fans_growth", "publish"]


# ── 工具函数 ──────────────────────────────────────────
def log_step(step, msg):
    """带步骤编号的日志输出"""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{step}] {msg}")

def log_info(msg):
    """带时间戳的日志输出"""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def ts_now():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def date_str():
    return datetime.now().strftime("%Y%m%d")


def sleep_random(min_s=1.0, max_s=3.0):
    """随机延迟，模仿人类操作节奏"""
    return asyncio.sleep(random.uniform(min_s, max_s))


async def click_sidebar_submenu(page, parent_menu, submenu):
    """点击侧边栏子菜单，必要时先展开父菜单（复刻抖音模式）"""
    try:
        sub = page.get_by_text(submenu, exact=True)
        if not await sub.is_visible():
            parent = page.get_by_text(parent_menu, exact=True)
            if await parent.is_visible():
                await parent.click()
                log_info(f"  展开父菜单: {parent_menu}")
                await sleep_random(1, 2)
                await sub.wait_for(state="visible", timeout=5000)
        await sub.click()
        log_info(f"  点击子菜单: {submenu}")
        await sleep_random(2, 4)
        return True
    except Exception as e:
        log_info(f"  点击子菜单失败: {e}")
        return False


async def robust_click(page, text, exact=True):
    """多重策略点击元素，逐步升级"""

    # 策略1: Playwright 原生 get_by_text
    for exact_flag in [exact, False]:
        try:
            item = page.get_by_text(text, exact=exact_flag)
            if await item.is_visible() and await item.is_enabled():
                await item.click()
                log_info(f"  → 点击「{text}」(get_by_text exact={exact_flag})")
                await sleep_random(1, 2)
                return True
        except Exception:
            pass

    # 策略2: get_by_role button
    try:
        item = page.get_by_role("button", name=text)
        if await item.is_visible() and await item.is_enabled():
            await item.click()
            log_info(f"  → 点击「{text}」(get_by_role button)")
            await sleep_random(1, 2)
            return True
    except Exception:
        pass

    # 策略3: locator text= 模糊匹配
    try:
        item = page.locator(f"text={text}").first
        if await item.is_visible() and await item.is_enabled():
            await item.click()
            log_info(f"  → 点击「{text}」(locator text=)")
            await sleep_random(1, 2)
            return True
    except Exception:
        pass

    # 策略4: JS 注入（include 匹配，点击可点击的父级）
    try:
        clicked = await page.evaluate(f"""() => {{
            // 查找所有文本节点，用 includes 匹配
            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_TEXT, null, false
            );
            let node;
            while ((node = walker.nextNode())) {{
                if (node.textContent.trim().includes({repr(text)})) {{
                    let el = node.parentElement;
                    // 向上找可点击的元素
                    while (el && el !== document.body) {{
                        if (el.tagName === 'BUTTON' || el.tagName === 'A' ||
                            el.tagName === 'SPAN' || el.tagName === 'DIV' ||
                            el.onclick || el.getAttribute('role') === 'button' ||
                            el.style.cursor === 'pointer') {{
                            el.click();
                            return true;
                        }}
                        el = el.parentElement;
                    }}
                    // 如果没找到可点击父级，点击 text 节点的父元素
                    node.parentElement?.click();
                    return true;
                }}
            }}
            return false;
        }}""")
        if clicked:
            log_info(f"  → [JS] 点击「{text}」(includes 匹配)")
            await sleep_random(1, 2)
            return True
    except Exception as e:
        log_info(f"  JS fallback 异常: {e}")

    log_info(f"  [!!] 未找到 [{text}] 按钮")
    return False


async def click_text(page, text, exact=True):
    """向下兼容包装，实际使用 robust_click"""
    return await robust_click(page, text, exact=exact)


async def export_download(page, filename, wait_for_text="导出数据"):
    """封装 expect_download 流程：点击「导出数据」→ 等待下载 → 保存到输出目录"""
    try:
        btn = page.get_by_text(wait_for_text, exact=True)
        if not await btn.is_visible():
            log_info(f"  未找到「{wait_for_text}」按钮")
            return None

        async with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as dl:
            await btn.click()
            await sleep_random(1, 2)

        download = await dl.value
        path = os.path.join(OUTPUT_DIR, filename)
        await download.save_as(path)
        log_info(f"  -> {path}")
        return path
    except Exception as e:
        log_info(f"  下载失败: {e}")
        return None


# ── 专用函数：点击时间范围「近30天」────────────────

async def select_30day_range(page):
    """强力点击「近30天」按钮，多重策略 + 长等待"""
    texts_to_try = ["近30日", "近30天", "30天", "30日", "近 30 天"]
    for t in texts_to_try:
        log_info(f"  尝试选择时间范围: [{t}]")
        ok = await robust_click(page, t)
        if ok:
            await sleep_random(2, 3)
            return True

    # 最终手段：JS 遍历所有可点击元素，find 包含 30 的按钮
    log_info("  最终手段: JS 遍历按钮查找 30 天范围...")
    try:
        clicked = await page.evaluate("""() => {
            const allEls = document.querySelectorAll('button, [role="button"], [role="tab"], span, div');
            for (const el of allEls) {
                const t = el.textContent.trim();
                if ((t.includes('30') || t.includes('三十')) &&
                    (t.includes('天') || t.includes('日')) &&
                    !t.includes('7') && !t.includes('七')) {
                    el.scrollIntoView({behavior:'instant', block:'center'});
                    el.click();
                    return t;
                }
            }
            // 如果上面没找到，找第二个时间范围按钮（通常第一个是7天，第二个是30天）
            const timeBtns = Array.from(allEls).filter(el => {
                const t = el.textContent.trim();
                return (t.includes('天') || t.includes('日')) &&
                       (t.includes('近') || t.includes('7') || t.includes('30'));
            });
            if (timeBtns.length >= 2) {
                timeBtns[1].scrollIntoView({behavior:'instant', block:'center'});
                timeBtns[1].click();
                return timeBtns[1].textContent.trim();
            }
            return null;
        }""")
        if clicked:
            log_info(f"  -> [JS-最终] 点击了: [{clicked}]")
            await sleep_random(2, 3)
            return True
    except Exception:
        pass

    log_info("  [!!] 所有策略均未能点击 30 天范围")
    return False
# ── Task 1: 账号概览（4个选项卡导出 xlsx）─────────────

async def task1_overview(page):
    """账号概览 → 笔记数据下4个选项卡分别导出"""
    log_info("=" * 50)
    log_step("1/3", "账号概览 — 笔记数据（4个选项卡）")
    log_info("=" * 50)

    ok = await click_sidebar_submenu(page, "数据看板", "账号概览")
    if not ok:
        log_info("  导航到账号概览失败")
        await page.screenshot(path=os.path.join(OUTPUT_DIR, f"err_task1_nav_{ts_now()}.png"))
        return []

    await sleep_random(2, 4)

    results = []
    for tab_name, file_key in zip(TAB_NAMES, TAB_FILE_KEYS):
        log_info(f"  [{tab_name}]")
        try:
            # 先进入笔记数据区域
            note_section = page.get_by_text("笔记数据", exact=True)
            if await note_section.is_visible():
                log_info("  已进入笔记数据区域")

            # 点击选项卡
            await click_text(page, tab_name, exact=True)
            await sleep_random(2, 3)

            # 点击「近30天」（确保时间范围）— 使用专用强力函数
            await select_30day_range(page)

            # 导出
            fname = f"xhs_overview_{file_key}_{date_str()}.xlsx"
            path = await export_download(page, fname)
            if path:
                results.append((tab_name, path))
            else:
                # 尝试第二遍 JS 点击导出按钮
                fallback_ok = await click_text(page, "导出数据", exact=True)
                if fallback_ok:
                    await sleep_random(2, 3)
                    path = await export_download(page, fname, wait_for_text="导出数据")
                    if path:
                        results.append((tab_name, path))
        except Exception as e:
            log_info(f"  [{tab_name}] 异常: {e}")
            traceback.print_exc()
            await page.screenshot(
                path=os.path.join(OUTPUT_DIR, f"err_task1_{file_key}_{ts_now()}.png")
            )

    return results


# ── Task 2: 内容分析（导出 xlsx）──────────────────────

async def task2_content_analysis(page):
    """数据看板 → 内容分析 → 导出数据（默认近30天）"""
    log_info("=" * 50)
    log_step("2/3", "内容分析 — 导出数据")
    log_info("=" * 50)

    ok = await click_sidebar_submenu(page, "数据看板", "内容分析")
    if not ok:
        log_info("  导航到内容分析失败")
        await page.screenshot(path=os.path.join(OUTPUT_DIR, f"err_task2_nav_{ts_now()}.png"))
        return None

    await sleep_random(2, 4)

    try:
        fname = f"xhs_content_analysis_{date_str()}.xlsx"
        path = await export_download(page, fname)
        if path:
            return path

        # Fallback: 等待一下再试
        log_info("  首次尝试失败，等待2秒重试...")
        await sleep_random(2, 3)
        path = await export_download(page, fname)
        return path
    except Exception as e:
        log_info(f"  内容分析导出异常: {e}")
        traceback.print_exc()
        await page.screenshot(
            path=os.path.join(OUTPUT_DIR, f"err_task2_{ts_now()}.png")
        )
        return None


# ── Task 3: 粉丝数据（抓取概览 + 画像 txt）────────────

async def task3_fans_data(page):
    """数据看板 → 粉丝数据 → 抓取粉丝概览数字 + 粉丝画像文本"""
    log_info("=" * 50)
    log_step("3/3", "粉丝数据 — 概览抓取 + 画像")
    log_info("=" * 50)

    ok = await click_sidebar_submenu(page, "数据看板", "粉丝数据")
    if not ok:
        log_info("  导航到粉丝数据失败")
        await page.screenshot(path=os.path.join(OUTPUT_DIR, f"err_task3_nav_{ts_now()}.png"))
        return None, None

    await sleep_random(3, 5)

    # ── 3a: 粉丝概览（简单innerText抓取，确保有数据） ──
    overview_path = None
    try:
        log_info(f"  [粉丝概览]")
        fans_data = await page.evaluate("""() => {
            // 简单 innerText 抓取，获取页面所有文本
            const allText = document.body.innerText;
            const lines = allText.split("\\n").map(l => l.trim()).filter(Boolean);
            // 筛选含粉丝相关关键词的行
            const keywords = ["总粉丝数", "新增粉丝", "流失粉丝", "粉丝总数", "粉丝数据", "粉丝"];
            const relevant = lines.filter(l =>
                keywords.some(k => l.includes(k)) || /[0-9]{3,}/.test(l)
            );
            return relevant.length > 0 ? relevant : lines.slice(0, 50);
        }""")

        ts = ts_now()
        overview_lines = [
            f"粉丝概览数据",
            f"采集时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        for line in fans_data:
            overview_lines.append(line)

        overview_content = "\n".join(overview_lines)
        overview_path = os.path.join(OUTPUT_DIR, f"xhs_fans_overview_{ts}.txt")
        with open(overview_path, "w", encoding="utf-8") as f:
            f.write(overview_content)
        log_info(f"  -> {overview_path}")
        for line in overview_lines:
            log_info(f"    {line}")

    except Exception as e:
        log_info(f"  粉丝概览抓取异常: {e}")
        traceback.print_exc()
        await page.screenshot(
            path=os.path.join(OUTPUT_DIR, f"err_task3_overview_{ts_now()}.png")
        )
    # ── 3b: 粉丝画像（检查是否有数据，有则抓取） ──
    portrait_path = None
    try:
        log_info(f"  [粉丝画像]")

        # 检查是否有"粉丝数过少"提示
        too_few = await page.evaluate("""() => {
            const body = document.body.innerText;
            // 检查页面是否包含「粉丝数过少」或类似提示
            return body.includes('粉丝数过少') || body.includes('粉丝数量较少');
        }""")

        if too_few:
            log_info("  粉丝数过少（<100），跳过画像抓取")
        else:
            # 尝试抓取粉丝画像数据（性别/年龄分布）
            portrait_data = await page.evaluate("""() => {
                // 查找包含「粉丝画像」的区块
                const allText = document.body.innerText;
                const lines = allText.split('\\n').map(l => l.trim()).filter(Boolean);

                // 找到「粉丝画像」或「画像」开始的区域
                const portraitStart = lines.findIndex(l =>
                    l.includes('粉丝画像') || l.includes('画像分析') || l.includes('受众画像')
                );

                // 查找性别/年龄关键词
                const portraitLines = [];
                let capturing = false;
                for (let i = 0; i < lines.length; i++) {
                    const l = lines[i];
                    if (l.includes('粉丝画像') || l.includes('画像分析') || l.includes('受众画像')) {
                        capturing = true;
                    }
                    if (capturing) {
                        portraitLines.push(l);
                        // 连续5行以上非画像内容则停止
                        if (i > portraitStart + 30) break;
                    }
                }

                if (capturing) {
                    return portraitLines;
                }

                // 如果没找到画像区块，尝试找性别/年龄分布
                const genderAge = [];
                for (const l of lines) {
                    if (l.includes('性别') || l.includes('年龄') || l.includes('男性') ||
                        l.includes('女性') || l.includes('分布') || l.includes('%')) {
                        genderAge.push(l);
                    }
                }
                return genderAge.length > 0 ? genderAge : null;
            }""")

            if portrait_data and len(portrait_data) > 0:
                ts2 = ts_now()
                portrait_lines = [
                    f"粉丝画像数据",
                    f"采集时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    "",
                ]
                for line in portrait_data:
                    portrait_lines.append(line)

                portrait_content = "\n".join(portrait_lines)
                portrait_path = os.path.join(OUTPUT_DIR, f"xhs_fans_portrait_{ts2}.txt")
                with open(portrait_path, "w", encoding="utf-8") as f:
                    f.write(portrait_content)
                log_info(f"  -> {portrait_path}")
                for line in portrait_lines:
                    log_info(f"    {line}")
            else:
                log_info("  未找到粉丝画像数据")

    except Exception as e:
        log_info(f"  粉丝画像抓取异常: {e}")
        traceback.print_exc()
        await page.screenshot(
            path=os.path.join(OUTPUT_DIR, f"err_task3_portrait_{ts_now()}.png")
        )

    return overview_path, portrait_path


# ── 截图辅助 ──────────────────────────────────────────

async def save_error_screenshot(page, label):
    """保存错误截图到桌面"""
    try:
        path = os.path.join(OUTPUT_DIR, f"crash_{label}_{ts_now()}.png")
        await page.screenshot(path=path, full_page=True)
        log_info(f"  崩溃截图: {path}")
    except Exception:
        pass


# ── Main ──────────────────────────────────────────────

async def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    results = {}

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=DEDICATED_USER_DATA,
            executable_path=CHROME_PATH,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1920, "height": 1080},
            accept_downloads=True,
        )

        page = context.pages[0] if context.pages else await context.new_page()

        log_info("=" * 50)
        log_info('小红书创作者中心 - 一键数据导出 v1')
        log_info("=" * 50)

        # ── 登录检查 ──
        log_info('[登录检查]')
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        await asyncio.sleep(5)

        # 检测是否处于登录页面
        page_url_lower = page.url.lower()
        if "login" in page_url_lower or "passport" in page_url_lower or "sign" in page_url_lower:
            log_info("=" * 50)
            log_info('  【需要扫码】小红书创作者中心登录已过期')
            log_info('  请在弹出的浏览器窗口中扫码重新登录')
            log_info('  等待自动检测登录...')
            log_info("=" * 50)

            for i in range(180):
                await asyncio.sleep(1)
                current_url = page.url.lower()
                if "login" not in current_url and "passport" not in current_url and "sign" not in current_url:
                    log_info('  登录检测成功，继续执行...')
                    break
                if i % 15 == 0 and i > 0:
                    log_info(f"  等待登录... ({i}秒)")
            else:
                log_info('登录超时（3分钟），退出。')
                await context.close()
                return

            await asyncio.sleep(3)

        log_info('已登录，开始导出...')
        await sleep_random(3, 5)

        # ── 依次执行3个任务，每个独立容错 ──
        tasks = [
            ("账号概览", lambda: task1_overview(page)),
            ("内容分析", lambda: task2_content_analysis(page)),
            ("粉丝数据", lambda: task3_fans_data(page)),
        ]

        for name, task_fn in tasks:
            try:
                result = await task_fn()
                results[name] = result
            except Exception as e:
                log_info(f"[错误] {name} 任务异常: {e}")
                traceback.print_exc()
                await save_error_screenshot(page, name)
                results[name] = None

        # ── 汇总 ──
        log_info("=" * 50)
        log_info('导出完成 - 结果汇总')
        log_info("=" * 50)

        success_count = 0
        total_count = 0

        # Task 1: 账号概览 (4个子任务)
        t1_results = results.get("账号概览", [])
        if t1_results:
            for tab_name, path in t1_results:
                status = "OK" if path else "FAIL"
                if path:
                    success_count += 1
                total_count += 1
                log_info(f"  [{status}] 账号概览-{tab_name}: {path}")
        else:
            log_info(f"  [FAIL] 账号概览: 无结果")

        # Task 2: 内容分析
        total_count += 1
        t2_path = results.get("内容分析")
        if t2_path:
            success_count += 1
            log_info(f"  [OK] 内容分析: {t2_path}")
        else:
            log_info(f"  [FAIL] 内容分析: 无结果")

        # Task 3: 粉丝数据
        total_count += 1
        t3_result = results.get("粉丝数据")
        if t3_result:
            overview_path, portrait_path = t3_result
            if overview_path:
                success_count += 1
                log_info(f"  [OK] 粉丝概览: {overview_path}")
            else:
                log_info(f"  [FAIL] 粉丝概览: 无结果")
            if portrait_path:
                success_count += 1
                log_info(f"  [OK] 粉丝画像: {portrait_path}")
            else:
                # 粉丝画像可能因粉丝数过少跳过，不算失败
                log_info(f"  [--] 粉丝画像: 跳过（无数据或粉丝数过少）")
        else:
            log_info(f"  [FAIL] 粉丝数据: 无结果")

        log_info(f"成功 {success_count}/{total_count} | 输出目录: {OUTPUT_DIR}")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())







