"""社群闢謠工具的留言掃描引擎（Facebook / Threads）。

跟 bot_engine.py 的 YouTube 監控共用同一套 CDP 接管模式：接管使用者
自己已登入的工具專用 Chrome（BROWSER_PROFILE_DIR），不用程式自己存
帳密或 session，登入狀態完全由使用者自己在那個 Chrome 視窗維護。

掃描留言故意不用 CSS class selector，因為 Facebook/Threads 的 class
名稱是建置時隨機產生的雜湊值，隔幾天改版就全部失效。改用 Playwright
的 accessibility snapshot（跟螢幕報讀軟體看到的樹狀結構一樣，靠 ARIA
role/name，不靠 class），穩定性高很多。

已知限制（尚未完成，需要之後用登入帳號實測才能做）：
- 目前只做「掃描並列出留言」，不含「實際送出回覆」的 DOM 操作。
- 送出回覆需要先找到每則留言的「回覆」按鈕、輸入框、送出按鈕的真實
  結構，這些只有登入後才看得到，還沒有測試帳號驗證過，故意先不做，
  避免用猜的 selector 在正式環境靜默失敗。
"""

import re
import time
from playwright.sync_api import sync_playwright

from bot_engine import (
    CHROME_CDP_PORT,
    BROWSER_PROFILE_DIR,
)


def detect_platform(url: str) -> str:
    """依網址判斷平台，回傳 'facebook' / 'threads' / '' (無法辨識)"""
    url = url.strip().lower()
    if "threads.com" in url or "threads.net" in url:
        return "threads"
    if "facebook.com" in url or "fb.com" in url or "fb.watch" in url:
        return "facebook"
    return ""


# 留言區的「非留言內容」雜訊文字，出現在 accessibility name 裡要濾掉，
# 不然會被誤判成留言內容。兩平台通用（讚、留言、轉發、分享、翻譯等
# 互動列文字，以及純數字的讚數/留言數）。
_NOISE_TEXTS = {
    "讚", "留言", "轉發", "分享", "翻譯", "更多", "已驗證", "回覆",
    "喜歡", "留言","傳送","檢舉","隱藏","封鎖","編輯","刪除",
}


def _is_noise(text: str) -> bool:
    text = text.strip()
    if not text:
        return True
    if text in _NOISE_TEXTS:
        return True
    if re.fullmatch(r"[\d,，.萬千]+", text):  # 純數字/讚數格式
        return True
    return False


def _walk_accessibility_tree(node, out):
    """遞迴走訪 accessibility snapshot，把 (role, name) 攤平成一個列表，
    方便之後用『先出現使用者連結、接著出現內文』的順序規則重組留言。"""
    if node is None:
        return
    role = node.get("role", "")
    name = node.get("name", "")
    if name:
        out.append((role, name))
    for child in node.get("children", []) or []:
        _walk_accessibility_tree(child, out)


def _reconstruct_comments(flat_nodes):
    """從攤平的 (role, name) 序列重組留言清單。

    觀察到的重複規律（Facebook / Threads 皆同）：
    一則留言 = [作者連結(link, name=作者)] → 可能重複一次(頭像+暱稱各一個
    link) → [內文文字(generic/text, 一段或多段)] → 互動列(讚/留言/轉發/分享)

    策略：遇到 link 且 name 是使用者代稱（不含空白、不是純數字），視為
    一則新留言的開始；直到下一個「作者 link」出現前，中間所有非雜訊的
    text/generic 節點都算這則留言的內文。第一個作者 link 是貼文本身
    （原PO），予以排除（第一則留言不算留言，是貼文正文）。
    """
    comments = []
    current_author = None
    current_content_parts = []
    seen_authors_at = 0

    def flush():
        nonlocal current_author, current_content_parts
        if current_author and current_content_parts:
            content = " ".join(current_content_parts).strip()
            if content:
                comments.append({"author": current_author, "content": content})
        current_content_parts = []

    for role, name in flat_nodes:
        name = name.strip()
        if not name:
            continue
        if role == "link" and re.fullmatch(r"[A-Za-z0-9_.一-鿿]{2,40}", name) and " " not in name:
            # 疑似作者代稱連結：新留言開始
            if name != current_author:
                flush()
                seen_authors_at += 1
                current_author = name
            continue
        if _is_noise(name):
            continue
        if current_author:
            current_content_parts.append(name)
    flush()

    # 第一則通常是貼文本身（原 PO 正文），不是留言，排除
    return comments[1:] if len(comments) > 1 else []


def scan_post_comments(url: str, ui_callback, max_scroll: int = 15) -> list:
    """接管使用者已登入的工具專用 Chrome，開啟貼文網址、捲動載入留言、
    回傳留言清單 [{author, content}, ...]。

    max_scroll：捲動次數上限，避免留言數異常多時無限捲動卡死；每次
    捲動後等待 1.2 秒讓新內容載入，並偵測『捲動後頁面高度沒再增加』
    就提前結束（已經到底或觸發登入牆）。
    """
    platform = detect_platform(url)
    if not platform:
        ui_callback("SYSTEM", "無法辨識網址平台，僅支援 Facebook 或 Threads 貼文網址")
        return []

    ui_callback("SYSTEM", f"辨識為 {platform} 貼文，準備接管已登入的 Chrome...")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CHROME_CDP_PORT}")
        except Exception:
            ui_callback("SYSTEM", "❌ 尚未開啟工具專用 Chrome，請先啟動一次直播監控（會自動開啟該 Chrome 視窗），"
                                  "並在該視窗登入你的 Facebook/Threads 帳號後再回來掃描。")
            return []

        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            ui_callback("SYSTEM", f"開啟網址失敗：{str(e)[:120]}")
            page.close()
            return []

        ui_callback("SYSTEM", "頁面載入完成，開始捲動載入留言...")
        last_height = 0
        for i in range(max_scroll):
            page.mouse.wheel(0, 2000)
            time.sleep(1.2)
            height = page.evaluate("document.body.scrollHeight")
            if height == last_height:
                break
            last_height = height
            ui_callback("SYSTEM", f"捲動載入中... ({i + 1}/{max_scroll})")

        ui_callback("SYSTEM", "留言載入完畢，開始解析內容...")
        try:
            snapshot = page.accessibility.snapshot()
        except Exception as e:
            ui_callback("SYSTEM", f"解析頁面結構失敗：{str(e)[:120]}")
            page.close()
            return []

        flat_nodes = []
        _walk_accessibility_tree(snapshot, flat_nodes)
        comments = _reconstruct_comments(flat_nodes)

        page.close()
        ui_callback("SYSTEM", f"共擷取到 {len(comments)} 則留言")
        return comments
