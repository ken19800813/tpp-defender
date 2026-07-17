"""社群闢謠工具的留言掃描引擎（Facebook）。

跟 bot_engine.py 的 YouTube 監控共用同一套 CDP 接管模式：接管使用者
自己已登入的工具專用 Chrome（BROWSER_PROFILE_DIR），不用程式自己存
帳密或 session，登入狀態完全由使用者自己在那個 Chrome 視窗維護。

掃描留言故意不用 CSS class selector，因為 Facebook 的 class
名稱是建置時隨機產生的雜湊值，隔幾天改版就全部失效。改用 Playwright
的 accessibility snapshot（跟螢幕報讀軟體看到的樹狀結構一樣，靠 ARIA
role/name，不靠 class），穩定性高很多。
"""

import json
import re
import time
import random
import subprocess
import sys
import os
import urllib.request
from datetime import datetime
from playwright.sync_api import sync_playwright

from bot_engine import (
    CHROME_CDP_PORT,
    BROWSER_PROFILE_DIR,
)


def _find_system_chrome():
    """尋找系統已安裝的『真 Google Chrome』（不是 Chromium）。"""
    if sys.platform == "win32":
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    elif sys.platform == "darwin":
        chrome_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    else:  # linux
        chrome_paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/opt/google/chrome/chrome",
        ]

    for path in chrome_paths:
        if os.path.exists(path):
            return path
    return None


def _cdp_endpoint_ready(port) -> bool:
    """檢查除錯埠是否已就緒（能回應 /json/version）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def _launch_chrome_with_cdp(chrome_path, port, ui_callback) -> bool:
    """開一個『獨立設定檔』的真 Chrome 視窗（帶除錯埠）供 Playwright 接管。"""
    os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)
    ui_callback("SYSTEM", "正在開啟工具專用的 Chrome 視窗（不影響你主要的 Chrome）...")

    try:
        subprocess.Popen(
            [
                chrome_path,
                f"--remote-debugging-port={port}",
                "--remote-allow-origins=*",
                f"--user-data-dir={BROWSER_PROFILE_DIR}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        ui_callback("SYSTEM", f"啟動 Chrome 失敗：{str(e)[:120]}")
        return False

    # 等除錯埠就緒（最多 20 秒）
    for _ in range(40):
        if _cdp_endpoint_ready(port):
            return True
        time.sleep(0.5)
    ui_callback("SYSTEM", "❌ Chrome 除錯埠未就緒，請重試。")
    return False


def _ensure_chrome_ready(ui_callback) -> bool:
    """確保 Chrome 已啟動並就緒。如果還沒啟動就自動啟動。"""
    # 檢查除錯埠是否已就緒（上次啟動留下、且已登入）
    if _cdp_endpoint_ready(CHROME_CDP_PORT):
        ui_callback("SYSTEM", "✓ Chrome 已就緒，準備掃描...")
        return True

    # 需要啟動 Chrome
    chrome_path = _find_system_chrome()
    if not chrome_path:
        ui_callback("SYSTEM", "❌ 系統未安裝 Google Chrome，掃描無法進行。")
        return False

    return _launch_chrome_with_cdp(chrome_path, CHROME_CDP_PORT, ui_callback)


# 診斷 log 檔：掃描時把 🔍 診斷資訊也寫進這裡，避免被 UI 狀態列即時覆蓋
# 而看不到（UI 只有單行狀態列，最後會被「共 N 則」蓋掉）。
_DIAG_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "social_scan_diag.log")


def _diag_log(msg: str):
    """把診斷訊息附加寫進 social_scan_diag.log（含時間戳）。"""
    try:
        os.makedirs(os.path.dirname(_DIAG_LOG), exist_ok=True)
        with open(_DIAG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def detect_platform(url: str) -> str:
    """依網址判斷平台，回傳 'facebook' / '' (無法辨識)"""
    url = url.strip().lower()
    if "facebook.com" in url or "fb.com" in url or "fb.watch" in url:
        return "facebook"
    return ""




# 純導覽列/圖片檢視器/選單字樣。注意：這裡刻意「不」放「查看更多」
# 「看更多」——那是 FB 對過長留言的內文截斷展開連結，不是頁面導覽
# chrome。先前把它放進來，導致所有被截斷的長留言內文都被誤判成 UI
# 雜訊整則丟掉（16→4 裡 6→4 的真因）。長留言的「查看更多」尾綴改在
# _extract_comment_lines 內從內文剝除，而不是拿來否決整則留言。
_UI_CHROME_PATTERNS = (
    "Facebook 功能表", "Messenger", "個人檔案", "進入全螢幕模式", "放大", "縮小",
    "關閉檢視工具", "返回動態消息", "下一張相片", "上一張相片",
    "通知", "遊戲",
)


def _looks_like_ui_chrome(text: str) -> bool:
    """篩掉導覽列/圖片檢視器/選單這類非留言的頁面雜訊。之前用整頁
    accessibility snapshot 硬掃時，這些文字會被誤判成獨立留言。"""
    return any(p in text for p in _UI_CHROME_PATTERNS)


# 互動列/分隔符等非內文行；「·」「・」是作者名與時間戳之間的分隔點，
# 單獨成行時是雜訊要濾掉（否則會被黏進內文變成「· 內文」）。
_NOISE_LINES = ("讚", "回覆", "不喜歡", "隱藏或檢舉此留言", "查看回覆", "·", "・")

# 過長留言被 FB 截斷時，內文尾端會接的展開連結字樣，從內文剝除。
_SEEMORE_SUFFIXES = ("…… 查看更多", "... 查看更多", "查看更多", "看更多")


def _extract_comment_lines(text: str):
    """把一個容器的 inner_text() 拆行，濾掉讚/回覆等互動列字樣跟純時間戳，
    回傳 (作者, 內文) 或 None（判斷這段文字不像一則完整留言時）。"""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if len(lines) < 2:
        return None
    author_line = lines[0]
    author = re.split(r"\s*[·・]\s*", author_line)[0].strip()
    if not author or len(author) > 40:
        return None
    content_lines = []
    for ln in lines[1:]:
        if ln in _NOISE_LINES:
            continue
        if re.fullmatch(r"\d+\s*[天小時分鐘週]前?", ln):
            continue
        # 剝掉過長留言尾端的「查看更多」展開連結，保留真正內文
        for suf in _SEEMORE_SUFFIXES:
            if ln.endswith(suf):
                ln = ln[:-len(suf)].rstrip("… .").rstrip()
                break
        if ln:
            content_lines.append(ln)
    content = " ".join(content_lines).strip()
    if not content or _looks_like_ui_chrome(content):
        return None
    return author, content


def _find_comment_container_text(reply_btn):
    """從『回覆』按鈕往上找最小的、內容完整的祖先容器。

    先前用 role="article" 定位留言區塊，但這個圖片檢視彈窗（photo.php
    這類 URL）裡的留言並沒有標 role="article"，導致抓不到（16則只抓到
    4則的真正原因）。改成用每則留言下方一定有的「回覆」按鈕當錨點——
    這個按鈕在所有留言版面（動態消息貼文、圖片彈窗）都存在，比
    role="article" 更可靠。

    往上找祖先 div 時，從最近的開始試，找到第一個『內容夠完整』
    （作者行 + 至少一行非雜訊內文）的容器就採用，避免抓太高層把
    好幾則留言黏在一起。"""
    for depth in range(2, 8):
        try:
            container = reply_btn.locator(f"xpath=ancestor::div[{depth}]")
            text = container.inner_text(timeout=1500).strip()
        except Exception:
            continue
        if not text:
            continue
        parsed = _extract_comment_lines(text)
        if parsed:
            return parsed
    return None


def _reconstruct_comments_from_articles(page, diag=None):
    """用『回覆』按鈕當作每則留言的錨點來定位留言，取代先前不可靠的
    role="article" 定位法（見 _find_comment_container_text 的說明）。

    diag：可選的診斷字典，若傳入會累加統計數字（找到幾個回覆按鈕、
    解析成功幾個、失敗幾個），用來判斷 16→4 卡在載入還是解析。"""
    try:
        reply_buttons = page.get_by_text("回覆", exact=True).all()
    except Exception:
        return []

    if diag is not None:
        diag["reply_btn_count"] = max(diag.get("reply_btn_count", 0), len(reply_buttons))

    comments = []
    seen = set()
    for idx, btn in enumerate(reply_buttons):
        parsed = _find_comment_container_text(btn)
        if not parsed:
            if diag is not None:
                diag["parse_fail"] = diag.get("parse_fail", 0) + 1
            # 記錄失敗按鈕附近的原始文字，判斷是「容器抓不到作者」還是別的
            try:
                raw = btn.locator("xpath=ancestor::div[5]").inner_text(timeout=1000)[:120].replace("\n", " ⏎ ")
            except Exception:
                raw = "(取不到祖先文字)"
            _diag_log(f"  ✗ 按鈕#{idx} 解析失敗，附近文字: {raw}")
            continue
        author, content = parsed
        key = (author, content[:60])
        if key in seen:
            continue
        seen.add(key)
        comments.append({"author": author, "content": content})

    return comments


def _is_facebook_photo_popup(url: str) -> bool:
    """判斷是不是 Facebook 圖片檢視彈窗網址（photo / photo.php）。
    這種版面的留言塞在右側小面板、虛擬化嚴重、大量折疊，直接抓只能拿到
    少數幾則（實測 16 則只抓到 4 則）。改走底層貼文永久連結的完整版面。"""
    u = url.lower()
    return "facebook.com/photo" in u and ("fbid=" in u or "/photo/" in u)


def _resolve_facebook_permalink(page, ui_callback):
    """在已載入的圖片彈窗頁面裡，找出底層貼文的永久連結（permalink）。

    弹窗里贴文的时间戳是一个指向永久连结的 <a>，href 型如：
      /{user}/posts/{id}    /permalink.php?story_fbid=...    /groups/{gid}/posts/{id}
      /story.php?story_fbid=...
    抓到後回傳完整網址；找不到回傳 None（呼叫端就沿用原網址硬抓）。"""
    # 收集頁面上所有 a[href]，挑出「像貼文永久連結」的那些
    href_js = """
    () => Array.from(document.querySelectorAll('a[href]'))
        .map(a => a.getAttribute('href'))
        .filter(h => h && (
            /\\/posts\\//.test(h) ||
            /permalink\\.php/.test(h) ||
            /story\\.php/.test(h) ||
            /story_fbid=/.test(h) ||
            /\\/groups\\/[^/]+\\/posts\\//.test(h)
        ))
    """
    try:
        hrefs = page.evaluate(href_js)
    except Exception:
        hrefs = []

    # 去重、log 出候選，方便對不上時校準
    seen = []
    for h in hrefs:
        if h not in seen:
            seen.append(h)
    for h in seen[:12]:
        _diag_log(f"  · permalink 候選: {h}")

    if not seen:
        return None

    # 關鍵：排除「通知欄」連結。photo 彈窗頁的 DOM 混了右上角通知鈴鐺的
    # 貼文連結（帶 notif_id / notif_t / ref=notif），那些是別人的貼文，
    # 先前被誤選導致抓到別篇留言（實測導到 MoneyAmber 的貼文）。
    import urllib.parse as _up

    def _is_notif(h):
        return ("notif_id=" in h or "notif_t=" in h or "ref=notif" in h)

    def _post_base(h):
        """把連結正規化成『貼文識別碼』：/posts/ 取 path；permalink/story
        取 story_fbid。用來把同一貼文的多個 comment_id 連結歸成一組。"""
        u = h
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("/"):
            u = "https://www.facebook.com" + u
        parts = _up.urlsplit(u)
        if "/posts/" in parts.path:
            return parts.scheme + "://" + parts.netloc + parts.path
        q = dict(_up.parse_qsl(parts.query))
        sid = q.get("story_fbid")
        if sid:
            return "story::" + sid
        return parts.scheme + "://" + parts.netloc + parts.path

    # 只留非通知連結；正確貼文的多個 comment_id 連結會歸到同一個 base，
    # 用「出現最多次的 base」投票選出當前正在看的貼文，通知那種各不同
    # 貼文只出現一兩次，自然落選。
    non_notif = [h for h in seen if not _is_notif(h)]
    pool = non_notif or seen  # 萬一全是通知連結才退而求其次

    from collections import Counter
    base_counts = Counter(_post_base(h) for h in pool)
    winner_base, win_n = base_counts.most_common(1)[0]
    _diag_log(f"  · permalink 投票: 勝出 base={winner_base}（{win_n} 票），"
              f"排除通知連結 {len(seen) - len(non_notif)} 個")

    # 取勝出 base 的第一個實際連結當 best
    best = next(h for h in pool if _post_base(h) == winner_base)

    # 補成絕對網址
    if best.startswith("//"):
        best = "https:" + best
    elif best.startswith("/"):
        best = "https://www.facebook.com" + best

    # 關鍵：剝掉 ?comment_id=...&__tn__=... 這類會把視圖「錨定到單一留言」
    # 的追蹤參數。帶著 comment_id 時 FB 會聚焦那則留言的上下文、不平鋪
    # 全部留言（實測只載入 10/16）。/posts/pfbid... 這段本身就足以定位
    # 貼文，query 一律去掉，換成乾淨的完整貼文版面。
    # 但 permalink.php / story.php 的 story_fbid 是在 query 裡的必要參數，
    # 那種情況只去掉 comment_id / __tn__，保留其餘。
    if "/posts/" in best:
        best = best.split("?")[0]
    else:
        # 保守處理：只砍掉 comment_id 與 __tn__ 兩個參數
        import urllib.parse as _up
        parts = _up.urlsplit(best)
        kept = [(k, v) for k, v in _up.parse_qsl(parts.query)
                if k not in ("comment_id", "__tn__")]
        best = _up.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, _up.urlencode(kept), "")
        )
    return best


def _to_mbasic_url(url: str) -> str:
    """把任意 facebook 網址換成 mbasic.facebook.com 版本（保留 path/query）。"""
    import urllib.parse as _up
    parts = _up.urlsplit(url)
    return _up.urlunsplit((parts.scheme or "https", "mbasic.facebook.com",
                           parts.path, parts.query, ""))


def _mbasic_candidate_urls(original_url: str, page):
    """產出一組 mbasic 目標網址候選，優先用『純數字 ID』的型態——mbasic
    看不懂新版 pfbid token（會回空殼頁），但吃得下數字 fbid / story_fbid。

    候選來源：
    1. 原始 photo 網址裡的數字 fbid（photo?fbid=<數字>）→ mbasic photo.php
    2. 桌面頁 DOM 內出現的 story_fbid=<數字> / top_level_post_id=<數字>
    3. 最後才退回 pfbid 直轉（大概率失敗，但當保底）
    回傳去重後的候選網址清單，依可靠度排序。"""
    candidates = []

    # 1. 原始網址的數字 fbid（照片本身的 id，就是貼文留言所在）
    m = re.search(r"[?&]fbid=(\d+)", original_url)
    set_m = re.search(r"[?&]set=([^&]+)", original_url)
    if m:
        fbid = m.group(1)
        u = f"https://mbasic.facebook.com/photo.php?fbid={fbid}"
        if set_m:
            u += f"&set={set_m.group(1)}"
        candidates.append(u)

    # 2. 從桌面 DOM 掃數字 story_fbid / top_level_post_id
    try:
        ids = page.evaluate(r"""
        () => {
            const html = document.documentElement.innerHTML;
            const out = new Set();
            let re1 = /story_fbid[=:\\\"]+(\d{6,})/g, mm;
            while ((mm = re1.exec(html)) !== null) out.add(mm[1]);
            let re2 = /top_level_post_id[\\\":=]+(\d{6,})/g;
            while ((mm = re2.exec(html)) !== null) out.add(mm[1]);
            return Array.from(out).slice(0, 5);
        }
        """)
    except Exception:
        ids = []
    for sid in ids or []:
        candidates.append(f"https://mbasic.facebook.com/story.php?story_fbid={sid}")

    # 3. 保底：pfbid 直轉
    candidates.append(_to_mbasic_url(original_url))

    # 去重保序
    seen, uniq = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    for c in uniq:
        _diag_log(f"  · mbasic 候選網址: {c}")
    return uniq


# mbasic 頁面解析：mbasic 是純靜態 HTML，留言不虛擬化、不被「最相關」過濾，
# 每則留言是帶 id 的區塊（作者連結 + 內文），底部有「查看更多留言/更多留言」
# 的真實 <a href> 分頁連結。順著翻頁就能拿到每一則。
# 下面用 JS 在 mbasic DOM 上抽留言與「更多留言」連結，並回傳診斷用結構統計。
_MBASIC_PARSE_JS = r"""
() => {
    const result = {comments: [], moreHref: null, diag: {}};

    // 「更多留言/查看更多留言」分頁連結（mbasic 底部）
    const links = Array.from(document.querySelectorAll('a[href]'));
    for (const a of links) {
        const t = (a.innerText || '').trim();
        if (/更多留言|查看更多留言|View more comments|更多回覆/.test(t)) {
            result.moreHref = a.getAttribute('href');
            break;
        }
    }

    // 留言區塊：mbasic 每則留言常是 div[id] 內含 h3(作者) + 內文。
    // 為了穩健，抓「包含一個指向個人檔案的連結(h3/h3 內 a) 且有文字」的區塊。
    const seen = new Set();
    const divs = Array.from(document.querySelectorAll('div[id]'));
    let idBlocks = 0;
    for (const d of divs) {
        const id = d.getAttribute('id') || '';
        // mbasic 留言 id 多為純數字或以數字為主
        if (!/^\d{5,}$/.test(id)) continue;
        idBlocks++;
        const h3 = d.querySelector('h3');
        if (!h3) continue;
        const authorA = h3.querySelector('a');
        const author = authorA ? (authorA.innerText || '').trim() : (h3.innerText || '').trim();
        if (!author) continue;
        // 內文：h3 之後的第一段文字容器
        let content = '';
        // 取整個 block 的文字、去掉作者行與互動字樣
        let full = (d.innerText || '').trim();
        const lines = full.split('\n').map(s => s.trim()).filter(Boolean);
        const noise = new Set(['讚','回覆','不喜歡','分享','更多','檢舉','隱藏']);
        const body = [];
        for (let i = 0; i < lines.length; i++) {
            if (i === 0 && lines[i] === author) continue;
            if (lines[i] === author) continue;
            if (noise.has(lines[i])) continue;
            if (/^\d+\s*[天小時分鐘週年]前?$/.test(lines[i])) continue;
            body.push(lines[i]);
        }
        content = body.join(' ').trim();
        if (!content) continue;
        const key = author + '|' + content.slice(0, 40);
        if (seen.has(key)) continue;
        seen.add(key);
        result.comments.push({author, content});
    }
    result.diag.idBlocks = idBlocks;
    result.diag.totalDivWithId = divs.length;
    return result;
}
"""


def _mbasic_scrape_one(start_url: str, ui_callback, page, max_pages: int) -> list:
    """從單一 mbasic 起始網址順著『更多留言』分頁抓到底，回傳留言清單。
    抓不到（頁面沒渲染出留言區塊）就回傳空清單，讓呼叫端換下一個候選。"""
    collected = {}
    next_url = start_url
    for pageno in range(max_pages):
        try:
            page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(0.6)
        except Exception as e:
            _diag_log(f"  ✗ mbasic 第{pageno+1}頁載入失敗: {str(e)[:100]}")
            break

        try:
            res = page.evaluate(_MBASIC_PARSE_JS)
        except Exception as e:
            _diag_log(f"  ✗ mbasic 第{pageno+1}頁解析失敗: {str(e)[:100]}")
            break

        found = res.get("comments", []) if isinstance(res, dict) else []
        for c in found:
            key = c["author"] + "|" + c["content"][:40]
            if key not in collected:
                collected[key] = c

        diag = res.get("diag", {}) if isinstance(res, dict) else {}
        _diag_log(f"  · mbasic 第{pageno+1}頁: 本頁 {len(found)} 則, "
                  f"累積 {len(collected)} 則, id區塊={diag.get('idBlocks')}, "
                  f"div[id]總數={diag.get('totalDivWithId')}")
        ui_callback("SYSTEM", f"mbasic 第 {pageno+1} 頁，累積 {len(collected)} 則留言...")

        # 第一頁就完全沒有留言區塊 → 這個候選網址沒渲染出貼文，直接放棄換下一個
        if pageno == 0 and diag.get("idBlocks", 0) == 0 and not found:
            _diag_log("  · 此候選第1頁無任何留言區塊，放棄換下一個候選")
            break

        more = res.get("moreHref") if isinstance(res, dict) else None
        if not more:
            _diag_log(f"  · mbasic 沒有下一頁『更多留言』連結，結束於第{pageno+1}頁")
            break
        if more.startswith("/"):
            more = "https://mbasic.facebook.com" + more
        next_url = more

    return list(collected.values())


def scan_post_comments_mbasic(url: str, ui_callback, page, original_url: str = None,
                              max_pages: int = 40) -> list:
    """走 mbasic.facebook.com 純靜態 HTML 版分頁抓取全部留言。

    依序嘗試多個候選網址（優先純數字 fbid / story_fbid，最後才 pfbid 直轉），
    用第一個真的渲染出留言的候選抓到底。mbasic 不虛擬化、不被「最相關」過濾，
    分頁是真實 <a>，能規模化抓上百上千則。

    url：已解析成乾淨貼文永久連結（www）；original_url：使用者最初貼的網址
    （用來抽數字 fbid，photo?fbid=<數字> 就在裡面）。
    page：已登入的 CDP Chrome 分頁（cookie 在 .facebook.com 網域，mbasic 共用）。"""
    ui_callback("SYSTEM", "改走 mbasic 純文字版，開始分頁抓取全部留言...")
    candidates = _mbasic_candidate_urls(original_url or url, page)

    for cand in candidates:
        _diag_log(f"  → 嘗試 mbasic 候選: {cand}")
        comments = _mbasic_scrape_one(cand, ui_callback, page, max_pages)
        if comments:
            _diag_log(f"🔍 mbasic 總結: 候選 {cand[:60]} 成功，共 {len(comments)} 則")
            _diag_log("=" * 50)
            ui_callback("SYSTEM", f"mbasic 共擷取到 {len(comments)} 則留言")
            return comments

    _diag_log("🔍 mbasic 總結: 所有候選都抓不到留言")
    _diag_log("=" * 50)
    ui_callback("SYSTEM", "mbasic 所有候選都未取得留言")
    return []


def _switch_comment_sort_to_all(page, ui_callback) -> bool:
    """把留言排序從「最相關」切成「所有留言」（最新/最舊），移除 FB 會主動
    隱藏部分留言的相關性過濾。回傳是否成功切換。

    做法：點開留言排序下拉（文字含「最相關」的按鈕）→ 在跳出的選單裡點
    「所有留言／最新留言／最舊」其一。FB 這個選單選項在不同版面文字略有
    差異，故用關鍵字比對而非寫死。"""
    try:
        # 找到排序下拉按鈕：role=button 且文字含「最相關」
        sort_btn = None
        for el in page.get_by_role("button").all():
            try:
                t = (el.inner_text(timeout=500) or "").strip()
            except Exception:
                continue
            if "最相關" in t and len(t) < 12:
                sort_btn = el
                break
        if not sort_btn:
            _diag_log("  · 排序切換: 找不到「最相關」下拉，可能本來就非過濾排序")
            return False

        sort_btn.click(timeout=2000)
        time.sleep(1.0)

        # 選單跳出後，點「所有留言 / 最新 / 最舊」其一（優先所有留言→最新）
        wanted = ("所有留言", "最新", "最舊", "All comments", "Newest")
        for kw in wanted:
            try:
                opt = page.get_by_text(kw, exact=False).first
                if opt and opt.is_visible():
                    opt.click(timeout=2000)
                    _diag_log(f"  · 排序切換: 已點選「{kw}」")
                    ui_callback("SYSTEM", f"已切換留言排序為「{kw}」，載入完整留言...")
                    time.sleep(2.0)
                    return True
            except Exception:
                continue
        _diag_log("  · 排序切換: 選單開了但找不到「所有留言/最新」選項")
        return False
    except Exception as e:
        _diag_log(f"  · 排序切換失敗: {str(e)[:100]}")
        return False


def _extract_comments_from_graphql(payload, sink: dict, diag: dict):
    """從 Facebook GraphQL 回應 payload 遞迴挖出留言節點。

    schema 無關：只憑「dict 同時具備 body（含 text 字串）與 author（含 name 字串）」
    這個穩定特徵來抽取，不硬寫任何路徑，FB 之後改 schema 也不會壞。

    payload 可為 dict / list / str：字串時視為多行 JSON 串流（FB 常
    以一行一個 JSON object 分段回傳），逐行 json.loads 容錯。

    去重 key 為 f"{author}|{content[:60]}"。回傳本次新增的節點數。
    """
    added = 0

    def walk(node):
        nonlocal added
        if isinstance(node, dict):
            body = node.get("body")
            author = node.get("author")
            if isinstance(body, dict) and isinstance(author, dict):
                text = body.get("text")
                name = author.get("name")
                if isinstance(text, str) and isinstance(name, str) and text.strip() and name.strip():
                    diag["graphql_comment_nodes"] = diag.get("graphql_comment_nodes", 0) + 1
                    key = name + "|" + text[:60]
                    if key not in sink:
                        sink[key] = {"author": name, "content": text}
                        added += 1
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8", errors="replace")
        except Exception:
            return added
    if isinstance(payload, str):
        # FB 常回多行 JSON 串流；先試整包，再逐行容錯
        try:
            walk(json.loads(payload))
            return added
        except Exception:
            pass
        for line in payload.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            walk(obj)
    else:
        walk(payload)
    return added


def _find_cursor_candidates(payload) -> list:
    """遞迴走訪 GraphQL 回應，找出所有可能的 Relay 分頁游標欄位。

    收集條件：dict 的 key 名稱（case-insensitive）含 "cursor"，
    或 key 等於 "page_info" / "pageInfo"。回傳
    [{"path": "a.b.c", "key": ..., "value": ...}, ...]。

    payload 支援 dict/list/str（str 時當作 JSON 或多行 JSON 串流）。
    路徑用 dot notation，list 索引用 [i]。
    """
    results: list = []

    def walk(node, path: str):
        if isinstance(node, dict):
            for k, v in node.items():
                sub = f"{path}.{k}" if path else k
                lk = k.lower() if isinstance(k, str) else ""
                if isinstance(k, str) and ("cursor" in lk or lk in ("page_info", "pageinfo")):
                    # value 可能是 dict（page_info 整包）或 str（cursor 本身）
                    preview = v
                    if isinstance(v, str) and len(v) > 120:
                        preview = v[:120] + "...(截斷)"
                    results.append({"path": sub, "key": k, "value": preview})
                walk(v, sub)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    def _try_parse(s: str):
        try:
            walk(json.loads(s), "")
            return True
        except Exception:
            return False

    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8", errors="replace")
        except Exception:
            return results
    if isinstance(payload, str):
        if not _try_parse(payload):
            for line in payload.splitlines():
                line = line.strip()
                if line:
                    _try_parse(line)
    else:
        walk(payload, "")
    return results


def _build_next_page_request(first_post_data: str, next_cursor: str,
                             cursor_field: str = "cursor") -> str:
    """依第一次留言 graphql 的 post_data，產出「下一頁」重放用的 post_data。

    只更動 URL-encoded 內 variables JSON 裡的 cursor_field 欄位（覆寫或新增），
    其餘防偽 token（fb_dtsg / jazoest / lsd / __csr ...）與 doc_id、
    fb_api_req_friendly_name 全部原封不動。cursor_field 目前預設 "cursor"，
    但真實名稱要看實測攔到的分頁請求（也可能是 "after" / "before"）——
    caller 觀察 log 後可傳入正確欄位名。

    參數：
      first_post_data: 第一次留言 graphql 請求的完整 URL-encoded body 字串
      next_cursor: 從上一份回應 comments.page_info.end_cursor 取得的游標值
      cursor_field: variables JSON 內要塞的欄位名（預設 "cursor"）

    回傳：新的 URL-encoded post_data 字串。
    """
    import urllib.parse as _up
    pairs = _up.parse_qsl(first_post_data, keep_blank_values=True)
    new_pairs = []
    touched = False
    for k, v in pairs:
        if k == "variables":
            try:
                obj = json.loads(v)
            except Exception:
                obj = {}
            if not isinstance(obj, dict):
                obj = {}
            obj[cursor_field] = next_cursor
            v = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
            touched = True
        new_pairs.append((k, v))
    if not touched:
        new_pairs.append(("variables",
                          json.dumps({cursor_field: next_cursor},
                                     separators=(",", ":"), ensure_ascii=False)))
    return _up.urlencode(new_pairs)


def scan_post_comments(url: str, ui_callback, max_scroll: int = 15) -> list:
    """接管使用者已登入的工具專用 Chrome，開啟貼文網址、捲動載入留言、
    回傳留言清單 [{author, content}, ...]。

    max_scroll：捲動次數上限，避免留言數異常多時無限捲動卡死；每次
    捲動後等待 1.2 秒讓新內容載入，並偵測『捲動後頁面高度沒再增加』
    就提前結束（已經到底或觸發登入牆）。
    """
    platform = detect_platform(url)
    if not platform:
        ui_callback("SYSTEM", "無法辨識網址平台，僅支援 Facebook 貼文網址")
        return []

    # 確保 Chrome 已啟動
    if not _ensure_chrome_ready(ui_callback):
        return []

    ui_callback("SYSTEM", f"辨識為 {platform} 貼文，準備掃描...")
    _diag_log(f"=== 開始掃描 {platform}: {url[:80]} ===")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CHROME_CDP_PORT}")
        except Exception:
            ui_callback("SYSTEM", "❌ Chrome 連線失敗，請檢查 Chrome 是否正常運行。")
            return []

        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()

        # === Facebook GraphQL 攔截器：必須在 goto 之前掛上 ===
        # FB 桌面版留言區是 React 虛擬化 + 相關性過濾，DOM 永遠不完整。
        # 但 FB 前端自己是 POST /api/graphql/ 取留言，回應 JSON 內含完整資料，
        # 直接攔 XHR 回應才有辦法規模化（幾百則也抓得齊）。DOM 卷動/展開
        # 只是「觸發 FB 去載入」的手段，資料源改為 graphql。排序切換、
        # 卷動、展開都會觸發 graphql，攔截器越早掛上收穫越多。
        graphql_sink: dict = {}
        gql_diag = {"graphql_responses": 0, "graphql_comment_nodes": 0, "graphql_skipped": 0}
        # armed 旗標：導向目標貼文完成前收到的 graphql 一律丟棄，避免收到
        # 圖片彈窗階段、或別篇貼文的留言。導到正確貼文後才設 True。
        gql_state = {"armed": False}
        # 階段 1 診斷用：第一次挖到留言的回應，把 request post_data 與
        # cursor 候選欄位完整印出，供人工核對階段 2 分頁重放所需資訊。
        cursor_probe = {"logged": False}
        # 階段 2 診斷：紀錄第一次留言 graphql 請求的 variables 原字串，
        # 之後每次進 handler 都比對；不同 → 認定是「分頁專用請求」，
        # 完整印一次讓人肉眼比對欄位差異（cursor / after / before 究竟叫什麼）。
        gql_seen = {"first_vars": None, "dumped_diff": False, "first_post_data": None}

        def _on_response(resp):
            try:
                if "/api/graphql" not in resp.url:
                    return
                # 導向目標貼文前的回應全部丟棄（跨頁殘留）
                if not gql_state["armed"]:
                    gql_diag["graphql_skipped"] += 1
                    return
                # 只處理「留言」相關 query：用 request post_data 的
                # fb_api_req_friendly_name 過濾，名稱含 "Comment" 才是留言 query
                # （CommentsListComponentsPaginationQuery / RootQuery 等）。
                # 這道濾網擋掉卷動時 FB「建議貼文」等別篇內容的 graphql。
                try:
                    post_data = resp.request.post_data
                except Exception:
                    post_data = None
                if not post_data or "Comment" not in post_data:
                    gql_diag["graphql_skipped"] += 1
                    return
                try:
                    body = resp.text()
                except Exception:
                    # redirect/空 body / 已被 GC 都會拋，忽略
                    return
                if not body:
                    return
                gql_diag["graphql_responses"] += 1
                before = len(graphql_sink)
                _extract_comments_from_graphql(body, graphql_sink, gql_diag)
                added = len(graphql_sink) - before

                # === 階段 2：每個留言 graphql 請求都印一行精簡摘要 ===
                # 目的：捕捉「首次載入」vs「下一頁載入」的 friendly_name / variables 差異。
                try:
                    import urllib.parse as _up2
                    pd_pairs = dict(_up2.parse_qsl(post_data or "", keep_blank_values=True))
                    friendly = pd_pairs.get("fb_api_req_friendly_name", "?")
                    doc_id = pd_pairs.get("doc_id", "?")
                    vars_raw = pd_pairs.get("variables", "")
                    try:
                        vars_obj = json.loads(vars_raw) if vars_raw else {}
                    except Exception:
                        vars_obj = {}
                    vk = list(vars_obj.keys()) if isinstance(vars_obj, dict) else []
                    vk_lc = " ".join(k.lower() for k in vk)
                    has_cur = "cursor" in vk_lc
                    has_after = "after" in vk_lc
                    has_before = "before" in vk_lc
                    # 從回應找 comments 主體的 page_info 摘要
                    pi_summary = "-"
                    try:
                        cc = _find_cursor_candidates(body)
                        # 找 path 尾端是 comments.page_info 的（非 replies）
                        best = None
                        for c in cc:
                            if c["key"] in ("page_info", "pageInfo") and "replies" not in c["path"] and isinstance(c["value"], dict):
                                best = c
                                break
                        if best is not None:
                            v = best["value"]
                            ec = v.get("end_cursor") or v.get("endCursor")
                            hn = v.get("has_next_page") if "has_next_page" in v else v.get("hasNextPage")
                            ec_s = (ec[:24] + "…") if isinstance(ec, str) and len(ec) > 24 else ec
                            pi_summary = f"has_next={hn} end={ec_s}"
                    except Exception:
                        pass
                    _diag_log(
                        f"  · [gql] fn={friendly} doc={doc_id} "
                        f"vars_keys={vk} has_cursor={has_cur} has_after={has_after} has_before={has_before} "
                        f"added={added} page_info={pi_summary}"
                    )
                    # 記錄首次 variables；後續若不同（可能就是分頁專用 query），
                    # 完整分段印出讓人肉眼比對「多了 / 少了 / 改了哪個欄位」。
                    if gql_seen["first_vars"] is None:
                        gql_seen["first_vars"] = vars_raw
                        gql_seen["first_post_data"] = post_data
                    elif vars_raw and vars_raw != gql_seen["first_vars"] and not gql_seen["dumped_diff"]:
                        gql_seen["dumped_diff"] = True
                        _diag_log("  · 【階段2】偵測到 variables 與首次不同的 graphql 請求，完整印 post_data：")
                        pd_full = post_data or ""
                        for i in range(0, len(pd_full), 800):
                            _diag_log(f"      diff_post_data[{i}:{i+800}] {pd_full[i:i+800]}")
                except Exception as _e:
                    _diag_log(f"      （階段2摘要 log 失敗: {_e}）")

                if added > 0:
                    _diag_log(f"  · graphql 回應挖到 {added} 則（累積 {len(graphql_sink)}）")
                    # === 階段 1 診斷：只在第一次挖到留言時印一次 ===
                    if not cursor_probe["logged"]:
                        cursor_probe["logged"] = True
                        try:
                            _diag_log("  · 【游標探測】首次命中留言回應 - request post_data:")
                            pd = post_data or ""
                            # 分段印避免 log 單行過長被截
                            for i in range(0, len(pd), 800):
                                _diag_log(f"      post_data[{i}:{i+800}] {pd[i:i+800]}")
                        except Exception as _e:
                            _diag_log(f"      （印 post_data 失敗: {_e}）")
                        try:
                            cands = _find_cursor_candidates(body)
                            # 路徑正規化去重：把 edges[0]、edges[1]...同一種路徑
                            # 形狀只留一個代表範例。先前直接列前 40 個，10 則
                            # 留言各自的 replies_connection.page_info（回覆的
                            # 分頁游標，值恆為 None）就佔滿全部 40 格，把真正
                            # 要找的「留言列表本身」comments.page_info 埋掉、
                            # 落在被截斷的「另有 N 個未列出」裡看不到。
                            norm_re = re.compile(r"\[\d+\]")
                            by_shape = {}
                            for c in cands:
                                shape = norm_re.sub("[*]", c["path"])
                                if shape not in by_shape:
                                    by_shape[shape] = c
                            _diag_log(
                                f"  · 【游標探測】cursor 候選欄位共 {len(cands)} 個"
                                f"（正規化去重後 {len(by_shape)} 種路徑形狀）:"
                            )
                            for shape, c in by_shape.items():
                                v = c["value"]
                                if isinstance(v, dict):
                                    v = {kk: (vv[:80] + "…") if isinstance(vv, str) and len(vv) > 80 else vv
                                         for kk, vv in v.items()}
                                _diag_log(f"      ⇢ [形狀]{shape}  例:{c['path']}  key={c['key']}  value={v}")
                        except Exception as _e:
                            _diag_log(f"      （探測 cursor 失敗: {_e}）")
            except Exception:
                # handler 內拋錯會干擾主流程，全程靜默容錯
                pass

        if platform == "facebook":
            page.on("response", _on_response)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            ui_callback("SYSTEM", f"開啟網址失敗：{str(e)[:120]}")
            page.close()
            return []

        # === Facebook 主路徑：桌面完整版面 + 切「所有留言」排序 ===
        # mbasic 已被 Meta 封鎖（對登入態貼文只回空殼頁，實測數字 fbid /
        # story_fbid 候選全部 id區塊=0），故不再走 mbasic。改用桌面完整版面，
        # 並把留言排序從「最相關」切成「所有留言」，移除 FB 主動隱藏部分
        # 留言的相關性過濾——這是留言數在 10~19 間跳動、抓不齊的真凶。
        if platform == "facebook":
            fb_permalink = url
            if _is_facebook_photo_popup(url):
                ui_callback("SYSTEM", "偵測到圖片彈窗版面，先解析底層貼文完整連結...")
                time.sleep(2)  # 等彈窗內時間戳等連結渲染出來
                resolved = _resolve_facebook_permalink(page, ui_callback)
                if resolved:
                    _diag_log(f"  → 解析到永久連結: {resolved}")
                    fb_permalink = resolved
                    try:
                        page.goto(fb_permalink, wait_until="domcontentloaded", timeout=30000)
                        time.sleep(1.5)
                    except Exception as e:
                        ui_callback("SYSTEM", f"導向永久連結失敗，沿用原網址：{str(e)[:80]}")
                else:
                    ui_callback("SYSTEM", "找不到貼文永久連結，沿用原網址")

            # 已停在正確的目標貼文頁：解除攔截器保險，開始收留言 graphql。
            # 在此之前（圖片彈窗、permalink 導向途中）的 graphql 全被丟棄。
            gql_state["armed"] = True
            _diag_log("  · GraphQL 攔截器 armed（開始收目標貼文留言）")

            # 關鍵：切成「所有留言」排序，去掉「最相關」過濾
            _switch_comment_sort_to_all(page, ui_callback)

        ui_callback("SYSTEM", "頁面載入完成，開始捲動並邊滾邊擷取留言...")

        # Facebook 留言區有 DOM 虛擬化：滑出畫面的留言會被移除，不是留著。
        # 邊滾邊抓、用內容去重累積，才能把整個過程出現過的留言都留住。
        collected = {}
        diag = {"reply_btn_count": 0, "parse_fail": 0}

        def collect_visible():
            try:
                found = _reconstruct_comments_from_articles(page, diag)
            except Exception as e:
                ui_callback("SYSTEM", f"⚠️ 這輪擷取失敗：{str(e)[:150]}")
                return
            for c in found:
                key = c["content"][:60]
                if key not in collected:
                    collected[key] = c

        # 關鍵修正：photo.php 這類圖片彈窗的留言在右側一個「自己會捲動」的
        # 內層容器裡（scrollHeight > clientHeight），跟整頁捲軸是分開的。
        # 先前不管抓取邏輯怎麼改都只有 4 則，真正原因是「捲動根本沒觸發
        # Facebook 載入更多留言」——DOM 裡自始至終只有那幾則。
        # 這裡用 JS 在頁面內找出「真正可捲動、且面積最大」的那個容器
        # （就是留言面板），直接對它設 scrollTop 往下捲，才會觸發載入。
        _find_and_scroll_js = """
        () => {
            // 偵測留言輸入框（composer）是否已在可視範圍內——它位於留言串
            // 最底下，看到它就是「捲到底、沒有更多留言」的鐵證。用它當主要
            // 結束信號，比「連續幾輪沒新增」的啟發式可靠。
            // 特徵：contenteditable 或 role=textbox，且 aria-label/placeholder
            // 帶「留言 / comment」字樣。
            function composerAtBottom() {
                const vh = window.innerHeight || document.documentElement.clientHeight;
                const nodes = document.querySelectorAll(
                    '[contenteditable="true"],[role="textbox"],textarea');
                for (const n of nodes) {
                    const lab = (n.getAttribute('aria-label') || '') +
                                (n.getAttribute('placeholder') || '');
                    if (!/留言|comment/i.test(lab)) continue;
                    const r = n.getBoundingClientRect();
                    // 必須真的在畫面內（不是 DOM 存在但在畫面外/被摺疊）
                    if (r.height > 0 && r.top < vh && r.bottom > 0) return true;
                }
                return false;
            }

            let best = null, bestArea = 0;
            const all = document.querySelectorAll('div');
            for (const el of all) {
                const style = getComputedStyle(el);
                const oy = style.overflowY;
                if ((oy === 'auto' || oy === 'scroll') &&
                    el.scrollHeight > el.clientHeight + 200) {
                    const rect = el.getBoundingClientRect();
                    const area = rect.width * rect.height;
                    if (area > bestArea) { bestArea = area; best = el; }
                }
            }
            if (best) {
                const r = best.getBoundingClientRect();
                best.scrollTop = best.scrollHeight;
                const atBottom = best.scrollTop + best.clientHeight >= best.scrollHeight - 4;
                return {mode: 'inner', w: Math.round(r.width), h: Math.round(r.height),
                        x: Math.round(r.left), y: Math.round(r.top), sh: best.scrollHeight,
                        atBottom: atBottom, composer: composerAtBottom()};
            }
            // 找不到內層可捲動容器（一般動態消息貼文），就捲整頁
            window.scrollTo(0, document.body.scrollHeight);
            const de = document.documentElement;
            const atBottom = window.scrollY + window.innerHeight >= de.scrollHeight - 4;
            return {mode: 'page', sh: document.body.scrollHeight,
                    atBottom: atBottom, composer: composerAtBottom()};
        }
        """

        # 探測＋展開：FB 圖片彈窗預設只載入少數留言，其餘折疊在
        # 「查看更多留言」「檢視更多留言」「查看 N 則回覆」等按鈕底下。
        # 光靠捲動不會展開這些，必須主動點擊。第一輪先把頁面上所有含
        # 「更多／留言／回覆」的可點元素文字 dump 到 log，確認真實標籤，
        # 之後幾輪照 pattern 實際點擊展開。
        _EXPAND_PATTERNS = ("查看更多留言", "檢視更多留言", "更多留言", "查看更多回覆",
                            "檢視更多回覆", "則回覆", "查看全部")
        # 巢狀回覆常見顯示為「3 則回覆」「12 則回覆」等；用正則精準比對，
        # 點下去會觸發 graphql 載回覆，正好被上面的攔截器收走。
        _EXPAND_REPLY_RE = re.compile(r"\d+\s*則回覆")

        # 首輪全頁掃描：不管 role，把任何文字含「則回覆／更多留言／查看更多」
        # 的元素 dump 出來，確認那些沒抓到的留言是不是折疊在這類控制底下、
        # 以及它們在 DOM 裡的真實標籤與 tag，供校準展開邏輯。
        _probe_expand_js = r"""
        () => {
            const out = [];
            const re = /則回覆|更多留言|查看更多|檢視更多|查看全部|最相關|所有留言/;
            const els = document.querySelectorAll('div[role=button],span[role=button],a,div');
            for (const el of els) {
                const t = (el.innerText || '').trim();
                if (t && t.length <= 25 && re.test(t)) {
                    out.push(el.tagName.toLowerCase() + '[' + (el.getAttribute('role')||'-') + ']:' + t);
                }
            }
            return Array.from(new Set(out)).slice(0, 20);
        }
        """

        # 診斷探針：把留言區所有『可點且文字含留言/更多/comment』的元素真實
        # 文字全 dump 出來（不預設按鈕叫什麼）。用來找出 FB 這篇貼文的
        # 「查看更多留言」分頁按鈕真名——479 則只抓到 17 就是因為這顆按鈕
        # 一次都沒被點到、FB 沒被觸發去載入後續分頁。
        _probe_all_clickables_js = r"""
        () => {
            const out = [];
            const els = document.querySelectorAll('div[role=button],span[role=button],a[role=button],a');
            for (const el of els) {
                let t = (el.innerText || '').trim();
                if (!t || t.length > 40) continue;
                if (t === '回覆' || t === '讚' || t === '分享') continue;
                if (/留言|更多|comment|回覆|View|more|所有|檢視|查看/i.test(t)) {
                    out.push(el.tagName.toLowerCase() + '[' + (el.getAttribute('role')||'-') + ']:' + t);
                }
            }
            return Array.from(new Set(out)).slice(0, 40);
        }
        """

        # JS 原生點擊分頁鈕：Playwright get_by_role("button") 對這批
        # div[role=button] 抓不到（實測：偵察用 querySelectorAll 找到
        # 「顯示更多」，但角色查詢的候選清單裡完全沒有它——accessibility
        # tree 對這類巢狀 div 按鈕的角色/名稱轉譯跟原生 DOM 對不上）。
        # 改成跟偵察同一套機制：直接原生 querySelectorAll 找、原生
        # el.click()，跳過 Playwright 那層可能失準的轉譯。
        # 每次只點「目前找到的第一顆」就回傳，因為點擊後 DOM 會重排、
        # 舊的 element handle 可能失效，呼叫端要重新查詢再點下一顆。
        _click_one_pager_js = r"""
        (patterns) => {
            const els = document.querySelectorAll(
                'div[role=button],span[role=button],a[role=button]');
            for (const el of els) {
                const t = (el.innerText || '').trim();
                if (!t || t.length > 20) continue;
                for (const p of patterns) {
                    if (t.includes(p)) {
                        el.click();
                        return t;
                    }
                }
            }
            return null;
        }
        """

        def _click_pager_buttons_js(patterns, max_clicks=8):
            """用原生 JS 反覆找並點分頁按鈕，回傳 (點擊次數, 最後一次點到的文字)。
            每點一次就重新查詢（避免點擊後 DOM 重排導致 stale element）。"""
            n = 0
            last_txt = None
            for _ in range(max_clicks):
                try:
                    txt = page.evaluate(_click_one_pager_js, list(patterns))
                except Exception:
                    break
                if not txt:
                    break
                n += 1
                last_txt = txt
                time.sleep(1.2)
            return n, last_txt

        def expand_more(first_round=False):
            clicked = 0
            if first_round:
                try:
                    hits = page.evaluate(_probe_expand_js)
                    for h in hits:
                        _diag_log(f"  · 全頁展開控制: {h}")
                    if not hits:
                        _diag_log("  · 全頁掃描: 沒有任何『則回覆/更多留言』類控制（10 則可能就是全部頂層留言）")
                    # 廣掃：dump 留言區所有可點元素真實文字，找分頁按鈕真名
                    allc = page.evaluate(_probe_all_clickables_js)
                    _diag_log(f"  · 【分頁按鈕偵察】留言區可點元素共 {len(allc)} 個：")
                    for c in allc:
                        _diag_log(f"      ⇢ {c}")
                except Exception:
                    pass

            # 先用 JS 原生點擊處理分頁鈕（顯示更多／更多留言等）——這批
            # Playwright role 查詢抓不到，必須繞過去原生點。
            pager_patterns = ("顯示更多", "更多留言", "檢視更多留言", "查看更多留言", "更多")
            before_gql = len(graphql_sink)
            n_pager, last_pager_txt = _click_pager_buttons_js(pager_patterns)
            if n_pager:
                delta = len(graphql_sink) - before_gql
                clicked += n_pager
                if delta > 0:
                    _diag_log(f"  ✅ JS 點擊分頁鈕 {n_pager} 次（最後「{last_pager_txt}」），"
                              f"graphql +{delta} 則（累積 {len(graphql_sink)}）")
                else:
                    _diag_log(f"  ⚠️ JS 點擊分頁鈕 {n_pager} 次（最後「{last_pager_txt}」），"
                              f"graphql 沒增加")

            try:
                candidates = page.get_by_role("button").all() + page.get_by_role("link").all()
            except Exception:
                return 0
            # 留言分頁按鈕候選文字（含這種短版面實測抓到的「顯示更多」）。
            # 點這些會觸發 FB 載入後續分頁的 graphql，被攔截器收走。
            pager_texts = ("顯示更多", "更多留言", "檢視更多留言", "查看更多留言")
            for el in candidates:
                try:
                    txt = (el.inner_text(timeout=500) or "").strip()
                except Exception:
                    continue
                if not txt or len(txt) > 30:
                    continue
                if first_round and any(k in txt for k in ("更多", "留言", "回覆", "展開", "全部")):
                    _diag_log(f"  · 可展開候選: {txt!r}")
                is_pager = any(p in txt for p in pager_texts)
                if any(p in txt for p in _EXPAND_PATTERNS) or _EXPAND_REPLY_RE.search(txt) or is_pager:
                    try:
                        before = len(graphql_sink)
                        el.click(timeout=1000)
                        clicked += 1
                        time.sleep(1.2)
                        delta = len(graphql_sink) - before
                        # 記錄「點了哪顆按鈕、graphql 長了幾則」——一次跑就能
                        # 認出哪顆是真正的分頁鈕（delta>0），哪顆是雜訊。
                        if delta > 0:
                            _diag_log(f"  ✅ 點「{txt}」後 graphql +{delta} 則（累積 {len(graphql_sink)}）")
                        elif is_pager:
                            _diag_log(f"  ⚠️ 點分頁鈕「{txt}」後 graphql 沒增加")
                    except Exception:
                        pass
            if clicked:
                _diag_log(f"  → 本輪點開 {clicked} 個展開/分頁按鈕")
            return clicked

        collect_visible()
        expand_more(first_round=True)
        collect_visible()

        # 進度尺：Facebook 用 GraphQL 攔到的則數當進度（DOM 因虛擬化永遠卡在
        # ~11 則，用它判斷會「還有 460 則沒載卻以為到底」提早熄火——這正是
        # 479 則只抓到 17 的真因）。只要 GraphQL 還在增長就繼續卷。
        # 上限也大幅拉高：幾百則留言需要卷幾十次分頁。
        def _progress():
            return len(graphql_sink) if platform == "facebook" else len(collected)

        # 規模化策略：不設「固定卷幾次」的死上限（那會把上千則的貼文硬砍在
        # 800 則）。改成「只要還在收到新留言就一直卷」，由三道閘門收斂：
        #   1. stable_limit：連續這麼多輪都沒新留言 → 判定到底/卡住（跟總數
        #      無關，資料還在來就一直歸零重算，上千則也擋不到它）
        #   2. hard_cap：純防呆的極寬安全上限，避免程式異常時無限迴圈
        #   3. time_budget_s：牆鐘時間預算，超過就收工回報目前成果
        # 三者任一觸發就停。正常情況是 stable_limit 先到（真的沒留言了）。
        effective_scroll = max(max_scroll, 500) if platform == "facebook" else max_scroll
        stable_limit = 6 if platform == "facebook" else 3  # FB 分頁偶有停頓，多容忍幾輪
        time_budget_s = 600 if platform == "facebook" else 120  # FB 最多跑 10 分鐘
        loop_start = time.time()

        stable_rounds = 0
        prev_count = _progress()
        for i in range(effective_scroll):
            # 時間預算閘門：上千則可能要跑好幾分鐘，但不能無上限拖住 UI
            if time.time() - loop_start > time_budget_s:
                _diag_log(f"  · 達時間預算 {time_budget_s}s，於第 {i+1} 次卷動收工，"
                          f"GraphQL 累積 {len(graphql_sink)} 則")
                ui_callback("SYSTEM", f"已達時間上限，先回報目前 {len(graphql_sink)} 則")
                break
            try:
                expand_more()
                scroll_info = page.evaluate(_find_and_scroll_js)
                if i == 0:
                    # 第一次捲動就把挑中的容器資訊 log 出來，判斷是否卷錯容器
                    if isinstance(scroll_info, dict) and scroll_info.get("mode") == "inner":
                        msg = (
                            f"🔍 卷動容器: 內層面板 {scroll_info.get('w')}x{scroll_info.get('h')} "
                            f"@({scroll_info.get('x')},{scroll_info.get('y')}) "
                            f"scrollHeight={scroll_info.get('sh')}"
                        )
                    else:
                        msg = "🔍 卷動容器: 整頁（沒找到內層可卷容器）"
                    ui_callback("SYSTEM", msg)
                    _diag_log(msg)

                # 真滾輪事件：先前的 el.scrollTop=... 是 JS 直接改屬性，
                # 瀏覽器會標成非真人觸發；某些用「監聽滾輪事件」而非單純
                # 捲動位置的虛擬化清單只吃這種才會觸發載入。Playwright 的
                # mouse.wheel() 是透過瀏覽器輸入管線送出，比 JS 改屬性更接近
                # 真人操作。滑鼠先移到容器中心再送滾輪，避免滾到別的區塊。
                if platform == "facebook" and isinstance(scroll_info, dict):
                    try:
                        if scroll_info.get("mode") == "inner":
                            cx = scroll_info.get("x", 0) + scroll_info.get("w", 300) / 2
                            cy = scroll_info.get("y", 0) + scroll_info.get("h", 300) / 2
                        else:
                            cx, cy = 640, 400
                        page.mouse.move(cx, cy)
                        before_wheel = len(graphql_sink)
                        for _ in range(6):
                            page.mouse.wheel(0, 1200)
                            time.sleep(0.35)
                        wheel_gain = len(graphql_sink) - before_wheel
                        if i == 0 or wheel_gain > 0:
                            _diag_log(f"  · 真滾輪事件(mouse.wheel) 第{i+1}輪: graphql +{wheel_gain}")
                    except Exception as e:
                        if i == 0:
                            _diag_log(f"  · 真滾輪事件失敗: {str(e)[:100]}")
            except Exception as e:
                ui_callback("SYSTEM", f"⚠️ 捲動失敗：{str(e)[:150]}")

            time.sleep(1.5)
            collect_visible()

            cur = _progress()

            # 結束信號：卷到底 + 輸入框可見 + 這輪無新留言。
            # 但「輸入框可見」在這種短版面一開始就成立（輸入框在留言串上方
            # 就看得到，不必卷到底），會第 1 輪就假觸發。因此加兩道保險：
            #   (a) 至少已卷 stable_limit 輪，給分頁按鈕點擊足夠機會觸發載入
            #   (b) 連續 stable_limit 輪都沒新留言才認帳
            # 真正判斷「到底」還是交給下面 stable_rounds 累積，這裡只在
            # 「已卷夠久且 composer+atBottom」時提早收尾，避免無謂空轉。
            reached_end = (isinstance(scroll_info, dict)
                           and scroll_info.get("composer")
                           and scroll_info.get("atBottom")
                           and cur == prev_count
                           and stable_rounds >= stable_limit - 1
                           and i >= stable_limit)
            if reached_end:
                _diag_log(f"  · 已捲到底且看到留言輸入框，判定結束於第 {i+1} 次卷動，"
                          f"GraphQL 累積 {len(graphql_sink)} 則")
                break

            # 後備結束信號：連續 stable_limit 輪都沒新留言（composer 偵測失敗
            # 或版面沒 composer 時的保底，避免無限迴圈）。
            if cur == prev_count:
                stable_rounds += 1
                if stable_rounds >= stable_limit:
                    _diag_log(f"  · 連續 {stable_limit} 輪無新留言（未偵測到 composer），"
                              f"結束於第 {i+1} 次卷動，GraphQL 累積 {len(graphql_sink)} 則")
                    break
            else:
                stable_rounds = 0
            prev_count = cur
            ui_callback("SYSTEM",
                        f"捲動載入中... ({i + 1}/{effective_scroll})，"
                        f"GraphQL 累積 {len(graphql_sink)} 則 / DOM {len(collected)} 則")

        # 重要修正：先前這裡自動迴圈一放棄就立刻 page.close()，導致「請使用者
        # 手動捲動測試」這個診斷步驟根本不可能被執行——分頁在使用者來得及
        # 動手前就已經關閉了。這是先前診斷請求落空的真因，不是使用者沒做。
        # 現在改成：若 Facebook 自動流程收工時攔到的則數明顯少（判斷依據：
        # 有偵測到「所有留言」的排序但完全沒抓滿），先暫停一段時間、留一個
        # 視窗讓使用者能在畫面上真的用滑鼠捲動，捲動期間攔截器仍在運作，
        # 若因此抓到新留言，log 會顯示，最後仍會被收進結果。
        if platform == "facebook" and not gql_state.get("_manual_window_done"):
            gql_state["_manual_window_done"] = True
            manual_wait_s = 25
            _diag_log(f"  · 自動流程收工，開放 {manual_wait_s} 秒視窗給真人手動捲動測試"
                      f"（目前 GraphQL 累積 {len(graphql_sink)} 則）")
            ui_callback("SYSTEM",
                        f"自動掃描告一段落。接下來 {manual_wait_s} 秒，"
                        f"請直接在剛才那個 Chrome 視窗用滑鼠把留言區捲到底，"
                        f"系統會持續在背景收集捲動時載入的留言。")
            before_manual = len(graphql_sink)
            manual_start = time.time()
            while time.time() - manual_start < manual_wait_s:
                time.sleep(1.0)
            manual_gain = len(graphql_sink) - before_manual
            _diag_log(f"  · 手動視窗結束，期間 GraphQL 增加 {manual_gain} 則"
                      f"（{before_manual} → {len(graphql_sink)}）")
            if manual_gain > 0:
                ui_callback("SYSTEM", f"手動捲動期間收到 {manual_gain} 則新留言，已併入結果")
                collect_visible()

        page.close()

        # === 合併資料源：GraphQL 優先，DOM 降級補充 ===
        # graphql_sink 是 XHR 攔截來的完整資料（規模化主路徑）。
        # collected 是 DOM 解析（虛擬化+相關性過濾，最多 10~19 則），
        # 只在 graphql 一則都沒收到時當降級備援，避免整趟白跑。
        if platform == "facebook":
            merged: dict = {}
            # 兩邊統一用 author|content[:60] 當 key，才能真正去重。
            # （先前 DOM 用 "DOM|content" 前綴，跟 GraphQL 的 key 空間不相交，
            #  同一則被 GraphQL 與 DOM 各收一次 → 整數相加虛胖，23=13+10。）
            for v in graphql_sink.values():
                key = v.get("author", "") + "|" + v.get("content", "")[:60]
                merged.setdefault(key, v)
            if graphql_sink:
                # GraphQL 有貨：把 DOM 抓到的用同一 key 補上（同 key 不覆蓋）
                for v in collected.values():
                    key = v.get("author", "") + "|" + v.get("content", "")[:60]
                    merged.setdefault(key, v)
                comments = list(merged.values())
            else:
                _diag_log("  · GraphQL 未收到任何留言節點，降級沿用 DOM 結果")
                comments = list(collected.values())

            _diag_log(
                f"🔍 GraphQL 總結: {gql_diag['graphql_responses']} 個回應, "
                f"共挖到 {len(graphql_sink)} 則; DOM 法 {len(collected)} 則; "
                f"跳過(未armed/非留言) {gql_diag['graphql_skipped']} 個; "
                f"合併後 {len(comments)} 則"
            )
        else:
            comments = list(collected.values())
        # 診斷總結：這行是判斷 16→4 卡在哪的關鍵
        #   reply_btn_count 少（如 4）→ 情境A 載入問題（卷動沒觸發 FB 加載）
        #   reply_btn_count 多（如 16）但 parse_fail 高 → 情境B 解析問題
        summary = (
            f"🔍 診斷: 頁面最多偵測到 {diag['reply_btn_count']} 個「回覆」按鈕，"
            f"解析失敗 {diag['parse_fail']} 次，最終去重後 {len(comments)} 則"
        )
        ui_callback("SYSTEM", summary)
        _diag_log(summary)
        _diag_log("=" * 50)
        ui_callback("SYSTEM", f"共擷取到 {len(comments)} 則留言")
        return comments


def send_replies(url: str, reply_jobs: list, ui_callback, per_reply_delay=(1.5, 3.0)) -> list:
    """對一批 Facebook 留言逐則送出回覆。

    reply_jobs: [{"content": 留言原文, "reply_text": 要送出的回覆, "author": 作者名稱}, ...]

    流程：切「所有留言」排序 → 真滾輪捲動至目標留言出現 → 用作者名稱
    （找不到才退回內文比對）定位留言容器 → 點「回覆」展開行內輸入框
    → insertText 塞入文字並驗證內容無誤 → Enter 送出。

    回傳每筆的結果：[{"content": ..., "success": bool, "error": str|None}, ...]
    重新載入頁面而非沿用掃描時的 page，因為使用者編輯清單可能花好幾分鐘，
    留著同一個 page 物件跨這麼久的間隔容易失效，重新 goto 更可靠。"""
    platform = detect_platform(url)
    results = []

    if platform != "facebook":
        ui_callback("SYSTEM", "無法辨識網址平台，僅支援 Facebook 貼文網址")
        return [{"content": j["content"], "success": False, "error": "不支援的平台"} for j in reply_jobs]

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CHROME_CDP_PORT}")
        except Exception:
            ui_callback("SYSTEM", "❌ 尚未開啟工具專用 Chrome，請先掃描一次確認已登入")
            return [{"content": j["content"], "success": False, "error": "Chrome 未連上"} for j in reply_jobs]

        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)
        except Exception as e:
            ui_callback("SYSTEM", f"重新開啟貼文失敗：{str(e)[:120]}")
            page.close()
            return [{"content": j["content"], "success": False, "error": str(e)[:120]} for j in reply_jobs]

        # 關鍵修正：這裡的 url 是使用者原始貼上的網址（main.py 的
        # self.social_url_entry.get()，從未被改寫成解析後的永久連結）。
        # 若使用者當初貼的是圖片檢視彈窗網址（photo?fbid=...），彈窗版面
        # 留言虛擬化嚴重，不管怎麼捲都只會有小貓幾隻（scan_post_comments
        # 那邊耗費大量診斷才驗證出這點，並靠 _resolve_facebook_permalink
        # 轉往完整版面解決）。send_replies 先前完全沒做這步，導致重新
        # 開啟的分頁停在彈窗版面，捲 25 輪也找不到目標留言（實測發生）。
        if _is_facebook_photo_popup(url):
            ui_callback("SYSTEM", "偵測到圖片彈窗版面，先解析底層貼文完整連結...")
            page.wait_for_timeout(1500)
            resolved = _resolve_facebook_permalink(page, ui_callback)
            if resolved:
                _diag_log(f"  · [送出回覆] 解析到永久連結，導向: {resolved}")
                try:
                    page.goto(resolved, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1500)
                except Exception as e:
                    ui_callback("SYSTEM", f"導向永久連結失敗，沿用原網址：{str(e)[:80]}")
            else:
                _diag_log("  · [送出回覆] 找不到永久連結，沿用彈窗版面（大機率找不到留言）")

        # 關鍵修正：先前這裡只 goto + 等 1.5 秒就直接找留言，完全沒有套用
        # scan_post_comments 那邊耗費大量診斷才換來的經驗——FB 留言預設
        # 「最相關」排序 + 虛擬化，很多留言根本沒被渲染進 DOM，直接找
        # 一定找不到（實測：作者名稱 0 個節點）。這裡補上同一套「切所有
        # 留言排序」，讓待回覆的留言有機會先被載入。
        _switch_comment_sort_to_all(page, ui_callback)

        _find_scroll_container_js = """
        () => {
            let best = null, bestArea = 0;
            for (const el of document.querySelectorAll('div')) {
                const style = getComputedStyle(el);
                if ((style.overflowY === 'auto' || style.overflowY === 'scroll') &&
                    el.scrollHeight > el.clientHeight + 200) {
                    const rect = el.getBoundingClientRect();
                    const area = rect.width * rect.height;
                    if (area > bestArea) { bestArea = area; best = el; }
                }
            }
            if (best) {
                const r = best.getBoundingClientRect();
                return {x: r.left + r.width/2, y: r.top + r.height/2};
            }
            return {x: 640, y: 400};
        }
        """

        def _scroll_until_present(author, content_short, max_rounds=25):
            """用真滾輪事件（跟 scan_post_comments 驗證過有效的同一招）反覆
            捲動，直到目標留言的作者名稱出現在 DOM 裡，或達卷動上限。
            回傳 True/False 代表最終有沒有找到。"""
            try:
                pt = page.evaluate(_find_scroll_container_js)
                page.mouse.move(pt.get("x", 640), pt.get("y", 400))
            except Exception:
                pass
            for i in range(max_rounds):
                if author and page.get_by_text(author, exact=True).count() > 0:
                    return True
                if not author and content_short and page.get_by_text(content_short, exact=False).count() > 0:
                    return True
                try:
                    page.mouse.wheel(0, 1200)
                except Exception:
                    break
                page.wait_for_timeout(400)
            # 最後再檢查一次（最後一輪滾動後可能剛好載入）
            if author:
                return page.get_by_text(author, exact=True).count() > 0
            return bool(content_short) and page.get_by_text(content_short, exact=False).count() > 0

        for job in reply_jobs:
            content_snippet = job["content"][:15].strip()
            content_short = job["content"][:6].strip()  # 短片段當消歧義用，較不受換行/空白差異影響
            author = (job.get("author") or "").strip()
            reply_text = job["reply_text"]
            try:
                found = _scroll_until_present(author, content_short)
                _diag_log(f"  · [送出回覆] 捲動載入後，目標留言{'已' if found else '仍未'}出現於 DOM"
                          f"（author={author!r}）")
                if not found:
                    raise RuntimeError("捲動多輪仍找不到該留言（可能已被刪除、或載入異常），未送出")

                # 優先用「作者名稱」定位（短字串、exact match，不受掃描時
                # 內文處理跟目前頁面顯示之間的空白/換行/展開狀態差異影響）。
                # 先前純用留言前 15 字比對，掃描存的 content 跟頁面當下顯示
                # 的文字若有一絲差異（例如「查看更多」展開狀態不同）就整個
                # 比對失敗、8 秒逾時（實測發生）。作者名同時出現多則留言時，
                # 用 content_short 在候選容器裡二次確認選對哪一則。
                container = None
                if author:
                    author_nodes = page.get_by_text(author, exact=True).all()
                    _diag_log(f"  · [送出回覆] 作者「{author}」在頁面找到 {len(author_nodes)} 個節點")
                    candidates = []
                    for node in author_nodes:
                        try:
                            cand = node.locator(
                                "xpath=ancestor::div[.//*[normalize-space(text())='回覆']][1]"
                            )
                            if cand.count() == 0:
                                continue
                            candidates.append(cand.first)
                        except Exception:
                            continue
                    if len(candidates) == 1:
                        container = candidates[0]
                    elif len(candidates) > 1 and content_short:
                        # 同作者多則留言：用內文短片段挑出對的那個容器
                        for cand in candidates:
                            try:
                                txt = cand.inner_text(timeout=1500)
                            except Exception:
                                continue
                            if content_short in txt:
                                container = cand
                                break
                        if container is None:
                            _diag_log(f"  ✗ [送出回覆] 作者「{author}」有 {len(candidates)} 則留言，"
                                      f"內文短片段「{content_short}」都對不上")

                if container is None:
                    # 退回內文比對（舊策略，作者定位失敗或未提供 author 時保底）
                    _diag_log(f"  · [送出回覆] 改用內文比對定位：「{content_snippet}」")
                    comment_locator = page.get_by_text(content_snippet, exact=False).first
                    comment_locator.scroll_into_view_if_needed(timeout=8000)
                    container = comment_locator.locator(
                        "xpath=ancestor::div[.//*[normalize-space(text())='回覆']][1]"
                    )

                container.scroll_into_view_if_needed(timeout=8000)
                reply_btn = container.get_by_text("回覆", exact=True).first
                if reply_btn.count() == 0:
                    raise RuntimeError("找不到該留言的「回覆」按鈕，容器定位失敗")
                _diag_log(f"  · [送出回覆] 定位到「{author or content_snippet}」的回覆按鈕，點擊中")
                reply_btn.click(timeout=3000)
                page.wait_for_timeout(700)

                # 關鍵防呆：先前直接抓「整頁最後一個 contenteditable」，若上面
                # 的「回覆」點擊沒有真的展開行內回覆框（例如按鈕定位失敗但
                # click() 沒拋錯），會誤抓到主留言輸入框，導致文字被送成
                # 一則新的頂層留言而非掛在該則留言下（實測發生：Ken Huang
                # 帳號的回覆被誤發成新留言）。現在改成：只認 placeholder/
                # aria-label 含「回覆」字樣的輸入框，且優先在剛才點擊的
                # container 範圍內找；範圍內找不到才退而在全頁找，但仍要求
                # placeholder 含「回覆」——兩個條件都不滿足就判定失敗，
                # 絕不落回「抓最後一個」這種可能誤發的行為。
                def _find_reply_textbox(scope):
                    boxes = scope.locator(
                        'div[contenteditable="true"][role="textbox"]'
                    ).all()
                    for b in boxes:
                        try:
                            label = (b.get_attribute("aria-label") or "") + \
                                     (b.get_attribute("data-placeholder") or "")
                        except Exception:
                            continue
                        if "回覆" in label:
                            return b
                    return None

                textbox = _find_reply_textbox(container)
                if textbox is None:
                    textbox = _find_reply_textbox(page)
                if textbox is None:
                    _diag_log(f"  ✗ [送出回覆] 找不到含「回覆」placeholder 的輸入框，"
                              f"為避免誤發到主留言框，中止此則")
                    raise RuntimeError("找不到行內回覆輸入框（可能「回覆」按鈕未成功展開），"
                                       "為避免誤發已中止，未送出")

                textbox.click()
                page.wait_for_timeout(150)
                # 改用 insertText 一次性塞入整段文字（模擬「貼上」），取代逐字
                # type()。FB 留言框是即時攔截鍵盤事件做提及/表情符號建議的
                # 編輯器，逐字送很容易被它自己的處理邏輯打亂、字元錯位或
                # 順序錯亂（實測發生：送出「回覆他們的話...」卻變成亂碼
                # 「諛リー」）。insertText 觸發的是 input 事件而非逐鍵事件，
                # 現代 React/contenteditable 編輯器對這種方式的相容性更好。
                page.keyboard.insert_text(reply_text)
                page.wait_for_timeout(500)

                # 送出前先驗證輸入框內容是否等於預期文字，不等就中止、
                # 不要在文字錯誤的狀態下仍按 Enter 送出公開留言。
                try:
                    actual_text = textbox.inner_text(timeout=1500).strip()
                except Exception:
                    actual_text = None
                if actual_text is not None and reply_text.strip() not in actual_text:
                    _diag_log(f"  ✗ [送出回覆] 輸入框內容跟預期不符，"
                              f"預期「{reply_text}」實際「{actual_text}」，中止不送出")
                    raise RuntimeError(f"輸入框內容跟預期不符（可能打字過程被 FB 編輯器改動），"
                                       f"為避免送出錯誤內容已中止：實際內容「{actual_text}」")

                page.keyboard.press("Enter")
                page.wait_for_timeout(1000)

                results.append({"content": job["content"], "success": True, "error": None})
                ui_callback("SYSTEM", f"✓ 已回覆：{content_snippet}...")
            except Exception as e:
                results.append({"content": job["content"], "success": False, "error": str(e)[:150]})
                ui_callback("SYSTEM", f"✗ 回覆失敗：{content_snippet}... ({str(e)[:80]})")

            time.sleep(random.uniform(*per_reply_delay))

        page.close()

    return results
