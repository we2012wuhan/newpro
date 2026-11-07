# -*- coding: utf-8 -*-
"""国家博物馆预约抢票工具。

通过 Playwright 操作用户提供的官方预约页面：到点刷新、填写公开表单并提交。
登录、短信/图形验证码、滑块等人工验证会暂停任务，绝不尝试绕过。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import storage
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()
TOOL = "museum-ticket"
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_FILE = BASE_DIR / "templates" / "museum-ticket.html"
DEFAULT_URL = "https://www.chnmuseum.cn/"
MAX_VISITORS = 6
MAX_RUNTIME_SECONDS = 45 * 60
_TASKS: dict[str, dict[str, Any]] = {}
_TASKS_LOCK = threading.Lock()


def _user(request: Request) -> str:
    logged_in = getattr(request.state, "user", "") or ""
    if logged_in:
        return logged_in
    host = getattr(getattr(request, "client", None), "host", "") or "unknown"
    return "anon:" + _clean(host, 80)

def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _valid_url(value: Any) -> str:
    url = _clean(value, 500) or DEFAULT_URL
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("官方预约地址必须是 http(s) URL")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host != "chnmuseum.cn" and not host.endswith(".chnmuseum.cn"):
        raise ValueError("为安全起见，只允许打开国家博物馆官方域名（*.chnmuseum.cn）")
    return url


def normalize_visitors(raw: Any) -> list[dict[str, str]]:
    """校验访客资料；不会被写入 SQLite 或任务状态接口。"""
    if not isinstance(raw, list):
        raw = []
    visitors: list[dict[str, str]] = []
    for item in raw[:MAX_VISITORS]:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("name"), 40)
        id_number = re.sub(r"\s+", "", str(item.get("id_number") or "")).upper()[:30]
        phone = re.sub(r"\s+", "", str(item.get("phone") or ""))[:20]
        if not name or not id_number:
            continue
        if len(id_number) < 6:
            raise ValueError("证件号码看起来太短，请检查后再试")
        visitors.append({"name": name, "id_number": id_number, "phone": phone})
    if not visitors:
        raise ValueError("至少填写一位访客的姓名和证件号码")
    return visitors


def _parse_release(value: Any) -> float:
    text = _clean(value, 40)
    if not text:
        return time.time()
    try:
        point = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if point.tzinfo:
            return point.timestamp()
        return point.timestamp()
    except ValueError as exc:
        raise ValueError("放票时间格式不正确") from exc


def _public_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "official_url": config.get("official_url", DEFAULT_URL),
        "target_date": config.get("target_date", ""),
        "time_slot": config.get("time_slot", ""),
        "release_at": config.get("release_at", ""),
        "refresh_seconds": config.get("refresh_seconds", 2.5),
        "visitor_count": len(config.get("visitors") or []),
        "headless": bool(config.get("headless")),
    }


def _snapshot(task: dict[str, Any]) -> dict[str, Any]:
    with task["lock"]:
        return {
            "ok": True,
            "task_id": task["id"],
            "status": task["status"],
            "message": task.get("message", ""),
            "url": task.get("page_url", ""),
            "started_at": task.get("started_at", ""),
            "release_at": task.get("release_text", ""),
            "visitor_count": len(task.get("visitors") or []),
            "history_id": task.get("history_id"),
        }


def _set_status(task: dict[str, Any], status: str, message: str = "", **extra: Any) -> None:
    with task["lock"]:
        task["status"] = status
        task["message"] = _clean(message, 500)
        task.update(extra)
        page = task.get("page")
        if page is not None:
            try:
                task["page_url"] = page.url
            except Exception:
                pass


def _body_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=2500).lower()
    except Exception:
        return ""


def _human_gate(page) -> str:
    text = _body_text(page)
    try:
        inputs = page.locator("input").count()
        has_password = any(
            (page.locator("input").nth(i).get_attribute("type") or "").lower() == "password"
            for i in range(min(inputs, 30))
        )
    except Exception:
        has_password = False
    if any(word in text for word in ("图形验证码", "滑块验证", "人机验证", "安全验证", "验证码")):
        return "captcha"
    if has_password and any(word in text for word in ("登录", "手机号", "账号")):
        return "login"
    return ""


def _visible(locator) -> bool:
    try:
        return locator.is_visible() and locator.is_enabled()
    except Exception:
        return False


def _click_first(page, patterns: list[str], timeout: int = 1200) -> bool:
    for pattern in patterns:
        loc = page.get_by_text(re.compile(pattern, re.I)).all()
        for item in loc[:8]:
            if _visible(item):
                try:
                    item.click(timeout=timeout)
                    return True
                except Exception:
                    continue
    return False


def _fill_date(page, target_date: str) -> bool:
    if not target_date:
        return False
    loc = page.locator("input[type=date]")
    if loc.count():
        for i in range(min(loc.count(), 4)):
            if _visible(loc.nth(i)):
                try:
                    loc.nth(i).fill(target_date)
                    return True
                except Exception:
                    pass
    inputs = page.locator("input").all()
    for item in inputs[:40]:
        attrs = " ".join(filter(None, [
            item.get_attribute("name"), item.get_attribute("id"),
            item.get_attribute("placeholder"), item.get_attribute("aria-label"),
        ])).lower()
        if any(word in attrs for word in ("日期", "date", "visit")) and _visible(item):
            try:
                item.fill(target_date)
                return True
            except Exception:
                pass
    return False


def _fill_slot(page, slot: str) -> bool:
    if not slot:
        return False
    selects = page.locator("select").all()
    for select in selects[:12]:
        if not _visible(select):
            continue
        try:
            options = select.locator("option").all()
            for option in options:
                label = option.inner_text().strip()
                value = option.get_attribute("value") or label
                if slot.lower() in (label + " " + value).lower():
                    select.select_option(value=value)
                    return True
        except Exception:
            continue
    return _click_first(page, [re.escape(slot), r"上午|下午|时段|场次"])


def _field_kind(item) -> str:
    attrs = " ".join(filter(None, [
        item.get_attribute("name"), item.get_attribute("id"),
        item.get_attribute("placeholder"), item.get_attribute("aria-label"),
    ])).lower()
    if any(x in attrs for x in ("证件", "身份证", "idcard", "identity", "id_number", "certificate")):
        return "id_number"
    if any(x in attrs for x in ("手机", "电话", "phone", "mobile", "tel")):
        return "phone"
    if any(x in attrs for x in ("姓名", "名字", "name", "visitor")):
        return "name"
    return ""


def _fill_visitors(page, visitors: list[dict[str, str]]) -> int:
    fields = []
    for item in page.locator("input").all()[:100]:
        if not _visible(item):
            continue
        kind = _field_kind(item)
        input_type = (item.get_attribute("type") or "text").lower()
        if kind and input_type not in ("hidden", "checkbox", "radio", "submit", "button"):
            fields.append((kind, item))
    filled = 0
    for index, visitor in enumerate(visitors):
        group = fields[index * 3:] if index else fields
        used = set()
        for kind in ("name", "id_number", "phone"):
            if not visitor.get(kind):
                continue
            for field_kind, item in group:
                if field_kind == kind and id(item) not in used:
                    try:
                        item.fill(visitor[kind])
                        used.add(id(item))
                        filled += 1
                        break
                    except Exception:
                        pass
    return filled


def _click_booking_actions(page) -> bool:
    # 这些词只用于公开预约流程，不点击支付、充值或购买第三方商品。
    return _click_first(page, [r"下一步", r"确认预约", r"提交预约", r"预约参观", r"立即预约", r"预约"])


def _click_submit(page) -> bool:
    return _click_first(page, [r"确认预约", r"提交预约", r"确认提交", r"提交订单", r"预约完成"])


def _success(page) -> bool:
    text = _body_text(page)
    return any(word in text for word in ("预约成功", "预约完成", "提交成功", "订单成功", "恭喜"))


def _wait_for_resume(task: dict[str, Any], reason: str) -> bool:
    _set_status(task, "waiting_user", reason)
    while not task["stop_event"].is_set():
        if task["resume_event"].wait(timeout=1.0):
            task["resume_event"].clear()
            return True
    return False


def _browser_worker(task: dict[str, Any]) -> None:
    context = None
    playwright = None
    result = "任务结束"
    try:
        from playwright.sync_api import sync_playwright
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        user_dir = BASE_DIR / "data" / "museum-ticket-browser" / re.sub(r"[^a-zA-Z0-9_.-]", "_", task["user"] or "default")
        user_dir.mkdir(parents=True, exist_ok=True)
        playwright = sync_playwright().start()
        _set_status(task, "opening", "正在启动浏览器并打开官方预约页面…")
        try:
            context = playwright.chromium.launch_persistent_context(
                str(user_dir), headless=task["headless"], viewport={"width": 1440, "height": 960}
            )
        except Exception as exc:
            if not task["headless"]:
                _set_status(task, "opening", "当前环境无法显示窗口，改用无头浏览器继续；遇到验证码时请停止并改在本机运行。")
                context = playwright.chromium.launch_persistent_context(
                    str(user_dir), headless=True, viewport={"width": 1440, "height": 960}
                )
            else:
                raise exc
        page = context.pages[0] if context.pages else context.new_page()
        task["page"] = page
        page.goto(task["config"]["official_url"], wait_until="domcontentloaded", timeout=30000)
        _set_status(task, "waiting", "浏览器已打开。请确认页面是官方预约页；若需要登录或验证码，直接在浏览器中完成后点击“继续”。")
        if _human_gate(page):
            if not _wait_for_resume(task, "请在浏览器中完成登录/验证码，然后点击“继续抢票”。"):
                return

        release_at = task["release_ts"]
        while time.time() < release_at:
            if task["stop_event"].is_set():
                return
            left = max(0, int(release_at - time.time()))
            _set_status(task, "countdown", f"等待放票时间，还剩 {left} 秒…")
            time.sleep(min(1.0, max(0.1, release_at - time.time())))

        _set_status(task, "running", "到点，开始刷新官方预约页并尝试提交…")
        deadline = time.time() + MAX_RUNTIME_SECONDS
        attempted_form = False
        while time.time() < deadline and not task["stop_event"].is_set():
            try:
                if not attempted_form:
                    page.reload(wait_until="domcontentloaded", timeout=25000)
                else:
                    page.reload(wait_until="domcontentloaded", timeout=25000)
                task["page_url"] = page.url
            except PlaywrightTimeoutError:
                _set_status(task, "running", "页面响应较慢，继续等待下一轮…")
            gate = _human_gate(page)
            if gate:
                if not _wait_for_resume(task, "检测到登录或验证码，请在浏览器中完成后点击“继续抢票”。"):
                    return
                continue
            _fill_date(page, task["config"]["target_date"])
            _fill_slot(page, task["config"]["time_slot"])
            filled = _fill_visitors(page, task["visitors"])
            if filled:
                attempted_form = True
            if not attempted_form:
                _click_booking_actions(page)
                _set_status(task, "running", "正在寻找可预约日期/时段…")
            else:
                _click_submit(page)
                time.sleep(1.2)
                if _success(page):
                    result = "预约提交成功，请以官网订单/短信为准。"
                    _set_status(task, "success", result)
                    return
                _set_status(task, "running", "已填写当前页面，等待可用名额或继续下一步…")
            time.sleep(float(task["config"]["refresh_seconds"]))
        if task["stop_event"].is_set():
            result = "已由用户停止"
            _set_status(task, "stopped", result)
        else:
            result = "达到最长运行时间，未确认预约成功"
            _set_status(task, "timeout", result)
    except Exception as exc:
        result = "浏览器任务失败：" + _clean(exc, 260)
        _set_status(task, "error", result)
    finally:
        try:
            if task.get("history_id"):
                storage.update_record(task["user"], task["history_id"], payload={
                    "config": _public_config(task["config"]),
                    "status": task.get("status", "unknown"),
                    "result": result,
                })
        except Exception:
            pass
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if playwright:
                playwright.stop()
        except Exception:
            pass


def _start_task(user: str, config: dict[str, Any], visitors: list[dict[str, str]]) -> dict[str, Any]:
    with _TASKS_LOCK:
        for task in _TASKS.values():
            if task["user"] == user and task["status"] in {"opening", "waiting", "countdown", "running", "waiting_user"}:
                raise ValueError("你已经有一个抢票任务在运行，请先停止它")
        task_id = uuid.uuid4().hex[:12]
        task = {
            "id": task_id, "user": user, "config": config, "visitors": visitors,
            "status": "queued", "message": "任务已排队", "started_at": datetime.now().isoformat(timespec="seconds"),
            "release_ts": _parse_release(config.get("release_at")), "release_text": config.get("release_at", ""),
            "headless": bool(config.get("headless")), "stop_event": threading.Event(),
            "resume_event": threading.Event(), "lock": threading.Lock(), "page": None,
        }
        title = "%s · %s · %s" % (config["target_date"] or "待选日期", config["time_slot"] or "任意时段", len(visitors))
        task["history_id"] = None
        if not user.startswith("anon:"):
            try:
                row = storage.add_record(TOOL, user, title[:200], {"config": _public_config(config), "status": "queued"})
                task["history_id"] = row.get("id")
            except storage.StorageUnavailable:
                pass
        _TASKS[task_id] = task
        thread = threading.Thread(target=_browser_worker, args=(task,), name="museum-ticket-" + task_id, daemon=True)
        task["thread"] = thread
        thread.start()
        return _snapshot(task)


@router.get("/museum-ticket", response_class=HTMLResponse)
def museum_ticket_page():
    html = TEMPLATE_FILE.read_text(encoding="utf-8") if TEMPLATE_FILE.exists() else "<h1>模板缺失</h1>"
    return HTMLResponse(html)


@router.get("/museum-ticket/api/status")
def museum_ticket_status(request: Request) -> JSONResponse:
    user = _user(request)
    with _TASKS_LOCK:
        tasks = [_snapshot(t) for t in _TASKS.values() if t["user"] == user]
    current = next((x for x in tasks if x["status"] in {"opening", "waiting", "countdown", "running", "waiting_user"}), None)
    return JSONResponse({"ok": True, "current": current, "tasks": tasks[-10:]})


@router.post("/museum-ticket/api/start")
async def museum_ticket_start(request: Request) -> JSONResponse:
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
        visitors = normalize_visitors(body.get("visitors"))
        official_url = _valid_url(body.get("official_url"))
        target_date = _clean(body.get("target_date"), 20)
        refresh = float(body.get("refresh_seconds") or 2.5)
        if refresh < 1.0 or refresh > 30:
            raise ValueError("刷新间隔请设置在 1 到 30 秒之间")
        config = {
            "official_url": official_url,
            "target_date": target_date,
            "time_slot": _clean(body.get("time_slot"), 80),
            "release_at": _clean(body.get("release_at"), 40),
            "refresh_seconds": refresh,
            "headless": bool(body.get("headless")),
            "visitors": visitors,
        }
        return JSONResponse(_start_task(_user(request), config, visitors))
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "message": _clean(exc, 300)}, status_code=500)


@router.post("/museum-ticket/api/resume")
async def museum_ticket_resume(request: Request) -> JSONResponse:
    body = await request.json()
    task_id = _clean(body.get("task_id") if isinstance(body, dict) else "", 40)
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
    if not task or task["user"] != _user(request):
        return JSONResponse({"ok": False, "message": "找不到这个任务"}, status_code=404)
    task["resume_event"].set()
    return JSONResponse({"ok": True, "message": "已通知任务继续"})


@router.post("/museum-ticket/api/stop")
async def museum_ticket_stop(request: Request) -> JSONResponse:
    body = await request.json()
    task_id = _clean(body.get("task_id") if isinstance(body, dict) else "", 40)
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
    if not task or task["user"] != _user(request):
        return JSONResponse({"ok": False, "message": "找不到这个任务"}, status_code=404)
    task["stop_event"].set()
    task["resume_event"].set()
    _set_status(task, "stopped", "正在停止浏览器任务…")
    return JSONResponse({"ok": True})


@router.get("/museum-ticket/api/history")
def museum_ticket_history(request: Request) -> JSONResponse:
    if _user(request).startswith("anon:"):
        return JSONResponse({"ok": True, "items": []})
    try:
        rows = storage.list_records(TOOL, _user(request), 20)
    except storage.StorageUnavailable as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)
    return JSONResponse({"ok": True, "items": [
        {"id": row.get("id"), "title": row.get("title", ""), "created_at": row.get("created_at", ""),
         "status": (row.get("payload") or {}).get("status", "")}
        for row in rows
    ]})


if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    app = FastAPI(title="国家博物馆抢票工具")
    app.include_router(router)
    uvicorn.run(app, host="127.0.0.1", port=8040)