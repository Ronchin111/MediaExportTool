"""小红书创作者中心 - 一键数据导出 v3

3 个任务：账号概览（4 个选项卡 xlsx）/ 内容分析（xlsx）/ 粉丝数据（概览 + 画像 txt）。
v3 变更（2026-10-04，阶段二推广）：
  1) 接入公共层 resilience（浏览器会话 / 下载收编 / xlsx 校验 / 导出收口）；
  2) L1 多策略点击 + 记录策略级，非首选命中标 OK(兜底)；
  3) L3 页面指纹 + 漂移告警；
  4) L2 xlsx 契约校验 + manifest。
"""
import asyncio
import json
import os
import random
import sys
import traceback
from datetime import datetime

from playwright.async_api import async_playwright

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (_SCRIPT_DIR, os.path.dirname(_SCRIPT_DIR)):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from tool_utils import get_platform_profile_dir, find_chrome
from adaptive import (
    load_json, is_fallback, check_fingerprint, validate_xlsx_contract, write_manifest, save_diag,
)
from resilience import Session, export_table

DEDICATED_USER_DATA = get_platform_profile_dir('xhs')
CHROME_PATH = find_chrome() or r'C:\Program Files\Google\Chrome\Application\chrome.exe'
OUTPUT_DIR = os.environ.get(
    "MEDIAEXPORT_OUTPUT_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output"),
)
TOOL_ROOT = os.path.dirname(_SCRIPT_DIR)
DL_ROOT = ""

HOME_URL = "https://creator.xiaohongshu.com/new/home"
TAB_NAMES = ["观看数据", "互动数据", "涨粉数据", "发布数据"]
TAB_FILE_KEYS = ["watch", "interact", "fans_growth", "publish"]

SELECTORS = load_json(os.path.join(_SCRIPT_DIR, "xhs_selectors.json"), default={})
FINGERPRINT_PATH = os.path.join(_SCRIPT_DIR, "xhs_fingerprint.json")
CTX = {"fallback": False, "current_task": "", "strategies": [], "fingerprint_found": {}, "fingerprint_missing": {}, "items": []}


def log_step(step, msg):
    print(f"[{datetime.now():%H:%M:%S}] [{step}] {msg}", flush=True)


def log_info(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def ts_now():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def date_str():
    return datetime.now().strftime("%Y%m%d")


def sleep_random(min_s=1.0, max_s=3.0):
    return asyncio.sleep(random.uniform(min_s, max_s))


def record_strategy(target, strategy):
    fb = strategy != "exact"
    if fb:
        CTX["fallback"] = True
    CTX["strategies"].append((CTX["current_task"], target, strategy, fb))
    if fb:
        log_info(f"  ⚠ 兜底策略命中（{target}）: {strategy}")


def task_strategies(task):
    return [s[2] for s in CTX["strategies"] if s[0] == task]


def task_fallback(task):
    return any(s[3] for s in CTX["strategies"] if s[0] == task)


def finalize_item(task, file=None, contract=None, rows=None, period=None, error=None):
    if error is None and contract is not None and not contract.get("ok"):
        error = contract.get("reason")
    checks = {}
    if contract is not None:
        checks = {"openable": True, "required_columns": bool(contract.get("ok")), "min_rows": bool(contract.get("ok"))}
    elif file:
        checks = {"written": True}
    if file is None:
        status = "FAIL"
    elif contract is not None and not contract.get("ok"):
        status = "FAIL"
    elif task_fallback(task):
        status = "OK(兜底)"
    else:
        status = "OK"
    return {
        "task": task, "file": os.path.basename(file) if file else None,
        "status": status, "strategy": task_strategies(task), "checks": checks,
        "rows": rows, "period": period, "error": error,
    }


async def check_login(page):
    log_info("[登录检查]")
    try:
        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass
    await asyncio.sleep(5)
    url = page.url.lower()
    if "login" in url or "passport" in url or "sign" in url:
        try:
            await page.bring_to_front()
        except Exception:
            pass
        log_info("=" * 50)
        log_info('  【需要扫码】小红书创作者中心登录已过期')
        log_info('  请在弹出的浏览器窗口中扫码重新登录')
        log_info("=" * 50)
        for i in range(180):
            await asyncio.sleep(1)
            cur = page.url.lower()
            if "login" not in cur and "passport" not in cur and "sign" not in cur:
                log_info('  登录检测成功，继续执行...')
                break
            if i % 15 == 0 and i > 0:
                log_info(f"  等待登录... ({i}秒)")
        else:
            log_info('登录超时（3分钟）')
            return False
        await asyncio.sleep(3)
    log_info('已登录')
    return True


async def safe_screenshot(page, label):
    try:
        path = os.path.join(OUTPUT_DIR, f"{label}_{ts_now()}.png")
        await page.screenshot(path=path, full_page=True)
        log_info(f"  截图: {os.path.basename(path)}")
    except Exception:
        pass


async def check_fingerprint_now(page, page_name):
    required = SELECTORS.get("fingerprint", {}).get("pages", {}).get(page_name, [])
    if not required:
        log_info(f"  ⚠ L3 指纹配置缺失（{page_name}），漂移检测本次未生效")
        return
    found, missing = await check_fingerprint(page, required)
    CTX["fingerprint_found"][page_name] = found
    CTX["fingerprint_missing"][page_name] = missing
    if missing:
        log_info(f"  ⚠ 疑似页面改版（{page_name}），缺关键文本: {missing}")
        await save_drift_diag(page, page_name, missing)
        return
    baseline = load_fp().get(page_name)
    if baseline and set(found) != set(baseline):
        log_info(f"  ⚠ 页面指纹与上次基线不一致（{page_name}: 本次 {len(found)} 项 / 基线 {len(baseline)} 项）")


async def save_drift_diag(page, page_name, missing):
    try:
        shot = os.path.join(OUTPUT_DIR, f"_diag_shot_{ts_now()}.png")
        await page.screenshot(path=shot)
        text = await page.evaluate("() => document.body.innerText.slice(0, 4000)")
        save_diag(OUTPUT_DIR, "xhs", f"drift_{page_name}", screenshot_path=shot, page_text=text,
                  strategies=[s[2] for s in CTX["strategies"]], error=f"缺关键文本: {missing}")
        log_info("  → 诊断包已保存到 output/diag/")
    except Exception as e:
        log_info(f"  → 诊断包保存失败: {str(e)[:80]}")


def load_fp():
    data = load_json(FINGERPRINT_PATH, default={})
    return data.get("pages", {}) if isinstance(data, dict) else {}


def save_fp(pages):
    with open(FINGERPRINT_PATH, "w", encoding="utf-8") as f:
        json.dump({"pages": pages}, f, ensure_ascii=False, indent=2)


async def read_period(page):
    try:
        return await page.evaluate("""() => {
            const m = document.body.innerText.match(/\\d{4}[./-]\\d{1,2}[./-]\\d{1,2}\\s*[至~-]\\s*\\d{4}[./-]\\d{1,2}[./-]\\d{1,2}/);
            return m ? m[0].trim() : null;
        }""")
    except Exception:
        return None


async def click_sidebar_submenu(page, parent_menu, submenu):
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
        record_strategy(f"子菜单[{submenu}]", "exact")
        log_info(f"  点击子菜单: {submenu}")
        await sleep_random(2, 4)
        return True
    except Exception as e:
        log_info(f"  点击子菜单失败: {str(e)[:120]}")
        return False


async def robust_click(page, text, exact=True):
    strategies = [("exact", exact), ("fuzzy", False)]
    for name, exact_flag in strategies:
        try:
            item = page.get_by_text(text, exact=exact_flag)
            if await item.is_visible() and await item.is_enabled():
                await item.click()
                record_strategy(f"点击[{text}]", name)
                log_info(f"  → 点击「{text}」({name})")
                await sleep_random(1, 2)
                return True
        except Exception:
            pass
    try:
        item = page.get_by_role("button", name=text)
        if await item.is_visible() and await item.is_enabled():
            await item.click()
            record_strategy(f"点击[{text}]", "role")
            log_info(f"  → 点击「{text}」(role)")
            await sleep_random(1, 2)
            return True
    except Exception:
        pass
    try:
        item = page.locator(f"text={text}").first
        if await item.is_visible() and await item.is_enabled():
            await item.click()
            record_strategy(f"点击[{text}]", "locator")
            log_info(f"  → 点击「{text}」(locator)")
            await sleep_random(1, 2)
            return True
    except Exception:
        pass
    try:
        clicked = await page.evaluate("""(target) => {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
            let node;
            while ((node = walker.nextNode())) {
                if (node.textContent.trim().includes(target)) {
                    let el = node.parentElement;
                    while (el && el !== document.body) {
                        if (el.tagName === 'BUTTON' || el.tagName === 'A' || el.tagName === 'SPAN' || el.tagName === 'DIV' ||
                            el.onclick || el.getAttribute('role') === 'button' || el.style.cursor === 'pointer') {
                            el.click(); return true;
                        }
                        el = el.parentElement;
                    }
                    if (node.parentElement) node.parentElement.click();
                    return true;
                }
            }
            return false;
        }""", text)
        if clicked:
            record_strategy(f"点击[{text}]", "js")
            log_info(f"  → [JS] 点击「{text}」")
            await sleep_random(1, 2)
            return True
    except Exception as e:
        log_info(f"  JS fallback 异常: {str(e)[:100]}")
    log_info(f"  [!!] 未找到 [{text}] 按钮")
    return False


async def click_text(page, text, exact=True):
    return await robust_click(page, text, exact=exact)


async def select_30day_range(page):
    for t in SELECTORS.get("time_ranges_30", ["近30日", "近30天", "30天", "30日", "近 30 天"]):
        log_info(f"  尝试选择时间范围: [{t}]")
        if await robust_click(page, t):
            await sleep_random(2, 3)
            return True
    log_info("  [!!] 所有策略均未能点击 30 天范围")
    return False


async def export_overview_tab(session, tab_name, file_key, contract_key):
    period_holder = {}

    async def prepare(page):
        ok = await click_sidebar_submenu(page, "数据看板", "账号概览")
        if not ok:
            raise RuntimeError("导航到账号概览失败")
        await sleep_random(2, 4)
        await check_fingerprint_now(page, "overview")
        if not await click_text(page, tab_name, exact=True):
            raise RuntimeError(f"切换到选项卡「{tab_name}」失败")
        await sleep_random(2, 3)
        if not await select_30day_range(page):
            raise RuntimeError("选择近30天失败")
        period_holder["period"] = await read_period(page)
        return page.get_by_text("导出数据", exact=True).first

    dest, contract = await export_table(
        session, prepare, f"xhs_overview_{file_key}_{date_str()}.xlsx",
        DL_ROOT, OUTPUT_DIR, expect_rows=1, contract_key=contract_key,
        validate_contract=validate_xlsx_contract, log=log_info,
    )
    return dest, contract, period_holder.get("period")


async def task1_overview(session):
    log_info("=" * 50)
    log_step("1/3", "账号概览 — 笔记数据（4个选项卡）")
    log_info("=" * 50)
    items = []
    for tab_name, file_key in zip(TAB_NAMES, TAB_FILE_KEYS):
        CTX["current_task"] = tab_name
        log_info(f"  [{tab_name}]")
        try:
            dest, contract, period = await export_overview_tab(session, tab_name, file_key, f"xhs_overview_{file_key}")
            items.append(finalize_item(tab_name, dest, contract=contract, rows=(contract or {}).get("rows"), period=period))
        except Exception as e:
            log_info(f"  [{tab_name}] 异常: {str(e)[:150]}")
            traceback.print_exc()
            items.append(finalize_item(tab_name, error=f"{type(e).__name__}: {str(e)[:150]}"))
    return items


async def task2_content_analysis(session):
    log_info("=" * 50)
    log_step("2/3", "内容分析 — 导出数据")
    log_info("=" * 50)
    CTX["current_task"] = "内容分析"
    async def prepare(page):
        ok = await click_sidebar_submenu(page, "数据看板", "内容分析")
        if not ok:
            raise RuntimeError("导航到内容分析失败")
        await sleep_random(2, 4)
        await check_fingerprint_now(page, "content")
        return page.get_by_text("导出数据", exact=True).first
    try:
        dest, contract = await export_table(
            session, prepare, f"xhs_content_analysis_{date_str()}.xlsx",
            DL_ROOT, OUTPUT_DIR, expect_rows=1, contract_key="xhs_content_analysis",
            validate_contract=validate_xlsx_contract, log=log_info,
        )
        return finalize_item("内容分析", dest, contract=contract, rows=(contract or {}).get("rows"))
    except Exception as e:
        return finalize_item("内容分析", error=f"{type(e).__name__}: {str(e)[:150]}")


async def task3_fans_data(session):
    log_info("=" * 50)
    log_step("3/3", "粉丝数据 — 概览抓取 + 画像")
    log_info("=" * 50)
    CTX["current_task"] = "粉丝数据"
    page = await session.ensure()
    ok = await click_sidebar_submenu(page, "数据看板", "粉丝数据")
    if not ok:
        log_info("  导航到粉丝数据失败")
        return finalize_item("粉丝数据", error="导航到粉丝数据失败")
    await sleep_random(3, 5)
    await check_fingerprint_now(page, "fans")

    overview_path = None
    portrait_path = None
    try:
        log_info("  [粉丝概览]")
        fans_data = await page.evaluate("""() => {
            const lines = document.body.innerText.split("\\n").map(l => l.trim()).filter(Boolean);
            const keywords = ["总粉丝数", "新增粉丝", "流失粉丝", "粉丝总数", "粉丝数据", "粉丝"];
            const relevant = lines.filter(l => keywords.some(k => l.includes(k)) || /[0-9]{3,}/.test(l));
            return relevant.length > 0 ? relevant : lines.slice(0, 50);
        }""")
        ts = ts_now()
        overview_lines = ["粉丝概览数据", f"采集时间: {datetime.now():%Y-%m-%d %H:%M:%S}", ""] + fans_data
        overview_path = os.path.join(OUTPUT_DIR, f"xhs_fans_overview_{ts}.txt")
        with open(overview_path, "w", encoding="utf-8-sig") as f:
            f.write("\n".join(overview_lines))
        log_info(f"  -> {overview_path}")
    except Exception as e:
        log_info(f"  粉丝概览抓取异常: {str(e)[:120]}")
    try:
        log_info("  [粉丝画像]")
        too_few = await page.evaluate("""() => document.body.innerText.includes('粉丝数过少') || document.body.innerText.includes('粉丝数量较少')""")
        if too_few:
            log_info("  粉丝数过少（<100），跳过画像抓取")
        else:
            portrait_data = await page.evaluate("""() => {
                const lines = document.body.innerText.split('\\n').map(l => l.trim()).filter(Boolean);
                const start = lines.findIndex(l => l.includes('粉丝画像') || l.includes('画像分析') || l.includes('受众画像'));
                if (start >= 0) return lines.slice(start, start + 40);
                const hit = lines.filter(l => l.includes('性别') || l.includes('年龄') || l.includes('男性') || l.includes('女性') || l.includes('%'));
                return hit.length > 0 ? hit : null;
            }""")
            if portrait_data:
                ts2 = ts_now()
                portrait_lines = ["粉丝画像数据", f"采集时间: {datetime.now():%Y-%m-%d %H:%M:%S}", ""] + portrait_data
                portrait_path = os.path.join(OUTPUT_DIR, f"xhs_fans_portrait_{ts2}.txt")
                with open(portrait_path, "w", encoding="utf-8-sig") as f:
                    f.write("\n".join(portrait_lines))
                log_info(f"  -> {portrait_path}")
            else:
                log_info("  未找到粉丝画像数据")
    except Exception as e:
        log_info(f"  粉丝画像抓取异常: {str(e)[:120]}")
    return finalize_item("粉丝数据", overview_path, error=None if overview_path else "粉丝概览未生成")


async def main():
    global DL_ROOT
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    DL_ROOT = os.path.join(TOOL_ROOT, "downloads", f"xhs_{ts_now()}")
    os.makedirs(DL_ROOT, exist_ok=True)

    results = []
    async with async_playwright() as p:
        session = Session(p, DEDICATED_USER_DATA, CHROME_PATH, DL_ROOT, log=log_info, check_login=check_login)
        log_info("=" * 50)
        log_info('小红书创作者中心 - 一键数据导出 v3')
        log_info("=" * 50)
        log_info(f"输出目录: {OUTPUT_DIR}")
        try:
            await session.start()
            if not await check_login(session.page):
                write_manifest(OUTPUT_DIR, "xhs", [], {"ok": 0, "fallback": 0, "fail": 1}, error="登录超时/未登录")
                await session.stop()
                sys.exit(1)
        except Exception as e:
            write_manifest(OUTPUT_DIR, "xhs", [], {"ok": 0, "fallback": 0, "fail": 1}, error=f"浏览器启动失败: {e}")
            sys.exit(1)
        await sleep_random(2, 4)

        for name, fn in [("账号概览", task1_overview), ("内容分析", task2_content_analysis), ("粉丝数据", task3_fans_data)]:
            try:
                res = await fn(session)
                if isinstance(res, list):
                    results.extend(res)
                else:
                    results.append(res)
            except Exception as e:
                log_info(f"[错误] {name} 任务异常: {str(e)[:150]}")
                traceback.print_exc()
                results.append(finalize_item(name, error=f"{type(e).__name__}: {str(e)[:150]}"))

        CTX["items"] = results
        log_info("=" * 50)
        log_info('导出完成 - 结果汇总')
        log_info("=" * 50)
        success = 0
        for item in results:
            status = item["status"]
            if status != "FAIL":
                success += 1
            log_info(f"  [{status}] {item['task']}: {item.get('file') or '未生成'}")
        ok_count = sum(1 for i in results if i["status"] != "FAIL")
        fallback_count = sum(1 for i in results if i["status"] == "OK(兜底)")
        fail_count = sum(1 for i in results if i["status"] == "FAIL")
        manifest_path = write_manifest(OUTPUT_DIR, "xhs", results, {"ok": ok_count, "fallback": fallback_count, "fail": fail_count})
        log_info(f"  → manifest 已保存: {os.path.basename(manifest_path)}")
        log_info(f"成功 {success}/{len(results)} | 输出目录: {OUTPUT_DIR}")

        if fail_count == 0 and not CTX["fallback"] and CTX["fingerprint_found"]:
            save_fp(CTX["fingerprint_found"])
            log_info(f"  → 页面指纹基线已更新: {len(CTX['fingerprint_found'])} 页")
        elif CTX["fallback"]:
            log_info("  → 本次用过兜底策略，指纹基线不更新（待人工确认）")

        await session.stop()
        if fail_count > 0:
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
