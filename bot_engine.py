import time
import random
import json
import os
import re
import sys
import queue
import subprocess
import urllib.request
import requests
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

# CDP 接管用的除錯埠：工具會用『你已登入的真實 Chrome 設定檔』重新開一個
# 帶此除錯埠的 Chrome，然後 Playwright 連上去接管。因為登入是你在正常
# Chrome 完成的、不是程式登的，Google 不會觸發「自動測試」拒絕。
CHROME_CDP_PORT = 9222

# 使用用戶主目錄，避免只讀文件系統問題
APP_DATA_DIR = Path.home() / ".tppchat"
LOGS_DIR = str(APP_DATA_DIR / "logs")
BROWSER_PROFILE_DIR = str(APP_DATA_DIR / "browser_profile")

# 送出冷卻秒數：不管是手動點擊送出還是全自動送出模式，兩次實際送出
# 之間至少要間隔這麼多秒，嚴禁被拿來洗版聊天室。
SEND_COOLDOWN_SECONDS = 10


def extract_video_id(video_url: str) -> str:
    """從各種 YouTube 網址格式中取出 11 碼影片 ID。
    之前只用 split("v=") 抓 watch?v= 格式，遇到沒有 v= 參數的
    youtube.com/live/VIDEO_ID 這種格式時，會把整串網址誤判成 ID，
    導致組出來的聊天室網址完全錯誤（Windows 使用者回報的卡住問題根因）。
    支援：watch?v=ID、youtu.be/ID、live/ID、shorts/ID、embed/ID。"""
    video_url = video_url.strip()
    match = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", video_url)
    if match:
        return match.group(1)
    match = re.search(r"(?:youtu\.be/|/live/|/shorts/|/embed/)([A-Za-z0-9_-]{11})", video_url)
    if match:
        return match.group(1)
    # 最後保險：直接拿掉查詢字串後的最後一段路徑
    return video_url.split("?")[0].rstrip("/").split("/")[-1]

# YouTube 直播聊天室輸入框的常見選擇器，隨頁面版本可能略有差異，依序嘗試
CHAT_INPUT_SELECTORS = [
    "yt-live-chat-message-input-renderer #input",
    "div#input.yt-live-chat-text-input-field-renderer",
    "#input.yt-live-chat-text-input-field-renderer",
]


class YouTubeLiveTacticalBot:
    def __init__(self, config_manager, ui_callback):
        self.config = config_manager
        self.ui_callback = ui_callback
        self.is_running = False
        self.page = None
        self.session_log = None
        self.send_queue = queue.Queue()
        self.ban_queue = queue.Queue()
        self.last_send_time = 0.0
        # CDP 接管模式下，保存 Playwright 連上的 browser 物件；收尾時只
        # 「斷開連線」不「關閉瀏覽器」，避免關掉使用者自己的真實 Chrome。
        self._cdp_browser = None
        self._chrome_proc = None

    def queue_send(self, text: str):
        """從其他執行緒(主UI執行緒)排入一則要送出的回覆文字。
        Playwright的page物件只能在建立它的執行緒(本bot的背景執行緒)操作，
        所以用queue把指令帶進start_monitor()的迴圈裡執行，而不是直接呼叫。"""
        self.send_queue.put(text)

    def queue_ban(self, msg_id: str, author: str):
        """從主UI執行緒排入一則封鎖指令，同理必須丟進背景執行緒的
        Playwright page操作，不能跨執行緒直接呼叫。"""
        self.ban_queue.put((msg_id, author))

    def _try_ban(self, msg_id: str, author: str):
        """嘗試封鎖留言者（隱藏該使用者在本場直播的所有留言）。
        這個操作只有頻道主/版主的YouTube帳號才有權限執行——一般觀眾
        帳號的留言選單完全不會出現「封鎖使用者」這個選項，所以這裡
        不用另外做「是不是頻道主」的預先判斷，直接嘗試操作，選單裡
        找不到封鎖選項就直接回報「沒有權限」，這就是最準確的權限偵測
        方式（比起猜測帳號身份，直接試一次動作更可靠）。

        刻意不套用 SEND_COOLDOWN_SECONDS 送出冷卻機制：冷卻是為了防止
        「發言洗版」，封鎖是隱藏對方帳號的單向管理動作、不會產生任何
        新留言，兩者風險性質不同，確認是頻道主本人操作時應該讓他能
        連續處理多個側翼攻擊者，不該被送出冷卻卡住。

        回傳 (success: bool, message: str)。"""
        try:
            msg_el = self.page.query_selector(f'[id="{msg_id}"]')
            if not msg_el:
                return False, "留言已消失（可能已被捲出畫面或刪除），無法封鎖"

            menu_btn = msg_el.query_selector("#menu-button, yt-icon-button#menu-button")
            if not menu_btn:
                return False, "找不到留言選單按鈕，可能是YouTube介面版本差異"
            menu_btn.click()

            try:
                self.page.wait_for_selector(
                    "tp-yt-paper-listbox #items, ytd-menu-popup-renderer tp-yt-paper-item, tp-yt-paper-item",
                    timeout=2000
                )
            except Exception:
                return False, "選單未彈出，可能是網路延遲，請重試"

            menu_items = self.page.query_selector_all("tp-yt-paper-item, ytd-menu-service-item-renderer")
            ban_item = None
            for item in menu_items:
                try:
                    item_text = item.inner_text().strip()
                except Exception:
                    continue
                if any(kw in item_text for kw in ["封鎖", "隱藏", "Hide user", "Block"]):
                    ban_item = item
                    break

            if not ban_item:
                self.page.keyboard.press("Escape")
                return False, "您沒有本頻道的板主/主持人權限，無法封鎖使用者"

            ban_item.click()

            # YouTube通常會再彈一次確認對話框(「隱藏這位使用者的所有訊息？」)，
            # 有的話要再點一次確認按鈕；沒有彈出就當作已經直接生效。
            try:
                self.page.wait_for_selector(
                    "yt-confirm-dialog-renderer, tp-yt-paper-dialog", timeout=1500
                )
                confirm_buttons = self.page.query_selector_all(
                    "yt-confirm-dialog-renderer #confirm-button, tp-yt-paper-dialog #confirm-button"
                )
                if confirm_buttons:
                    confirm_buttons[0].click()
            except Exception:
                pass  # 沒有二次確認視窗，代表已直接生效

            return True, f"已封鎖 {author}，該使用者在本場直播的留言將不再顯示"
        except Exception as e:
            return False, f"封鎖操作失敗：{e}"

    def get_cooldown_remaining(self) -> float:
        """回傳距離下次可以送出還要等幾秒，0表示現在就可以送。"""
        elapsed = time.time() - self.last_send_time
        return max(0.0, SEND_COOLDOWN_SECONDS - elapsed)

    def _try_send(self, text: str) -> bool:
        """統一送出閘門：不管是使用者手動點擊還是全自動送出模式，
        兩次實際送出中間都必須間隔 SEND_COOLDOWN_SECONDS 秒，
        嚴禁被拿來洗版聊天室。回傳True代表真的送出了，False代表被冷卻擋下。"""
        if self.get_cooldown_remaining() > 0:
            return False
        self._send_chat_message(text)
        self.last_send_time = time.time()
        return True

    def _send_chat_message(self, text: str):
        """自動把文字打進YouTube直播聊天室輸入框並送出。
        這一步會真的公開發言，只透過 _try_send() 呼叫，確保一定經過冷卻檢查。"""
        try:
            input_box = None
            for selector in CHAT_INPUT_SELECTORS:
                input_box = self.page.query_selector(selector)
                if input_box:
                    break
            if not input_box:
                self.ui_callback("SYSTEM", "自動發送失敗：找不到聊天室輸入框，請手動貼上剪貼簿內容。")
                return

            input_box.click()
            input_box.type(text)
            self.page.keyboard.press("Enter")
            self.ui_callback("SYSTEM", f"已自動送出回覆：{text}")
        except Exception as e:
            self.ui_callback("SYSTEM", f"自動發送失敗：{e}，請手動貼上剪貼簿內容。")

    def check_channel_lock(self, video_url: str) -> bool:
        """透過 YouTube 公開 OEmbed API 檢查該直播頻道是否屬於硬性鎖定黑名單
        支援 channel_id (UC...) 與 handle (@xxx) 雙重比對"""
        try:
            video_id = extract_video_id(video_url)
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            res = requests.get(oembed_url, timeout=4)
            if res.status_code == 200:
                data = res.json()
                author_url = data.get("author_url", "")
                author_name = data.get("author_name", "")

                # 比對方式 1: Channel ID (UC...)
                channel_id = author_url.split("/channel/")[-1] if "/channel/" in author_url else ""
                if channel_id and channel_id in self.config.locked_channels:
                    return True

                # 比對方式 2: Handle (@xxx) - 來自 author_name
                if author_name:
                    handle = f"@{author_name}" if not author_name.startswith("@") else author_name
                    if handle in self.config.locked_channels:
                        return True
                    # 支援 URL encode 的 handle
                    import urllib.parse
                    handle_encoded = urllib.parse.quote(handle.encode("utf-8")).replace("%40", "%40")
                    if handle_encoded in self.config.locked_channels:
                        return True
        except Exception:
            pass
        return False

    def _fetch_video_title(self, video_id: str) -> str:
        """取得直播節目名稱，供歷史記錄存檔使用"""
        try:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            res = requests.get(oembed_url, timeout=4)
            if res.status_code == 200:
                return res.json().get("title", "未命名直播")
        except Exception:
            pass
        return "未命名直播"

    def _init_session_log(self, video_url: str, video_id: str) -> str:
        """建立本場直播的聊天記錄容器"""
        title = self._fetch_video_title(video_id)
        self.session_log = {
            "title": title,
            "video_url": video_url,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "messages": []
        }
        return title

    def _append_message_log(self, author: str, content: str, flagged: bool, superchat_amount: str = None):
        """記錄單則留言（無論是否被判定為側翼攻擊；抖內另加金額欄位）"""
        if self.session_log is not None:
            entry = {
                "author": author,
                "content": content,
                "flagged": flagged,
                "time": datetime.now().strftime("%H:%M:%S")
            }
            if superchat_amount:
                entry["superchat_amount"] = superchat_amount
            self.session_log["messages"].append(entry)

    def _save_session_log(self):
        """直播結束後將完整聊天記錄存檔到 logs/ 目錄"""
        if not self.session_log or not self.session_log["messages"]:
            return
        os.makedirs(LOGS_DIR, exist_ok=True)
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", self.session_log["title"]).strip()[:60] or "未命名直播"
        date_str = self.session_log["started_at"].split(" ")[0]
        filename = f"{date_str}_{safe_title}_{int(time.time())}.json"
        path = os.path.join(LOGS_DIR, filename)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.session_log, f, ensure_ascii=False, indent=2)
            flagged_count = sum(1 for m in self.session_log["messages"] if m["flagged"])
            self.ui_callback(
                "SYSTEM",
                f"本場直播記錄已存檔（共 {len(self.session_log['messages'])} 則留言，"
                f"{flagged_count} 則側翼標記）：{filename}"
            )
        except Exception as e:
            self.ui_callback("SYSTEM", f"記錄存檔失敗：{str(e)}")

    def _find_system_chrome(self):
        """尋找系統已安裝的『真 Google Chrome』（不是 Chromium）。
        用真 Chrome 才能通過 Google 的『受支援瀏覽器』檢查。
        也一併搜尋 Playwright 透過 `playwright install chrome` 安裝的
        Google Chrome（放在使用者的 ms-playwright 快取內）。"""
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

    def _ensure_chrome_installed(self) -> bool:
        """偵測到使用者沒裝 Google Chrome 時，用 Playwright 幫他自動下載安裝
        『真的 Google Chrome』（不是 Chromium）。使用者完全不需要懂技術名詞，
        下載期間透過進度訊息告知。安裝完成後再用 _find_system_chrome() 找路徑。"""
        self.ui_callback("SYSTEM", "偵測到未安裝 Google Chrome，正在自動下載安裝（僅第一次需要，需網路連線，約 3-10 分鐘，請耐心等候）...")
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "playwright", "install", "chrome"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )

            last_update = time.time()
            output_lines = []

            for line in iter(process.stdout.readline, ''):
                if line:
                    output_lines.append(line.strip())
                    if time.time() - last_update > 2:
                        last_update = time.time()
                        msg = output_lines[-1] if output_lines else "下載中..."
                        if len(msg) > 60:
                            msg = msg[:57] + "..."
                        self.ui_callback("SYSTEM", f"⬇️  {msg}")

            process.wait(timeout=900)

            if process.returncode == 0:
                self.ui_callback("SYSTEM", "✅ Google Chrome 安裝完成，繼續啟動監看。")
                return True

            error_msg = "\n".join(output_lines[-10:]) if output_lines else "未知錯誤"
            self.ui_callback("SYSTEM", f"❌ Chrome 安裝失敗：{error_msg[-200:]}")
            self.ui_callback("SYSTEM", "請手動前往 https://www.google.com/chrome 下載安裝 Chrome 後重試。")
            return False
        except subprocess.TimeoutExpired:
            process.kill()
            self.ui_callback("SYSTEM", "❌ Chrome 安裝逾時（超過 15 分鐘），請檢查網路連線後重試。")
            return False
        except Exception as e:
            self.ui_callback("SYSTEM", f"❌ Chrome 安裝失敗：{str(e)[:100]}")
            self.ui_callback("SYSTEM", "請手動前往 https://www.google.com/chrome 下載安裝 Chrome 後重試。")
            return False

    def _cdp_endpoint_ready(self, port) -> bool:
        """檢查除錯埠是否已就緒（能回應 /json/version）。"""
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
                return r.status == 200
        except Exception:
            return False

    def _launch_chrome_with_cdp(self, chrome_path, port) -> bool:
        """開一個『獨立設定檔』的真 Chrome 視窗（帶除錯埠）供 Playwright 接管。

        用獨立的 BROWSER_PROFILE_DIR，不碰使用者主要的 Chrome——兩者各用各
        的 user-data-dir，可同時執行、互不鎖定。使用者只需在這個視窗登入
        YouTube 一次，登入狀態會永久保存在此設定檔。

        關鍵旗標：
        - --remote-allow-origins=*：Chrome 111+ 允許 CDP websocket 連線的必要
          旗標，缺了會 403 連不上。
        - 不加任何自動化旗標（如 --enable-automation），所以沒有「受自動測試
          軟體控制」橫幅，navigator.webdriver 也是 false，Google 登入不會被擋。
        """
        os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)
        self.ui_callback("SYSTEM", "正在開啟工具專用的 Chrome 視窗（不影響你主要的 Chrome）...")

        try:
            self._chrome_proc = subprocess.Popen(
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
            self.ui_callback("SYSTEM", f"啟動 Chrome 失敗：{str(e)[:120]}")
            return False

        # 等除錯埠就緒（最多 20 秒）
        for _ in range(40):
            if self._cdp_endpoint_ready(port):
                return True
            time.sleep(0.5)
        self.ui_callback("SYSTEM", "❌ Chrome 除錯埠未就緒，請重試。")
        return False

    def start_monitor(self, video_url: str):
        """啟動唯讀雷達監聽（接管使用者已登入的真實 Chrome）"""
        if self.check_channel_lock(video_url):
            self.ui_callback("SYSTEM", "安全機制判定：本工具不支援此非授權親綠陣營頻道運作！")
            return

        self.is_running = True
        self._cdp_browser = None
        try:
            with sync_playwright() as p:
                # 只用真 Google Chrome。沒裝就自動下載安裝，裝完再找一次路徑。
                chrome_path = self._find_system_chrome()
                if not chrome_path:
                    if not self._ensure_chrome_installed():
                        self.ui_callback("SYSTEM", "無法啟動：缺少 Google Chrome。")
                        return
                    chrome_path = self._find_system_chrome()
                    if not chrome_path:
                        self.ui_callback("SYSTEM", "無法啟動：安裝後仍找不到 Chrome。")
                        return

                # === 開工具專用 Chrome 視窗並用 CDP 接管 ===
                # 若除錯埠已就緒（上次啟動留下、且已登入），直接連；否則開一個
                # 獨立設定檔的 Chrome 視窗再連。不碰使用者主要的 Chrome。
                if not self._cdp_endpoint_ready(CHROME_CDP_PORT):
                    if not self._launch_chrome_with_cdp(chrome_path, CHROME_CDP_PORT):
                        self.ui_callback("SYSTEM", "無法啟動：無法開啟 Chrome。")
                        return

                self.ui_callback("SYSTEM", "✓ Chrome 已就緒（首次使用請在這個視窗登入 YouTube 一次，之後永久記住）")
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CHROME_CDP_PORT}")
                self._cdp_browser = browser
                context = browser.contexts[0] if browser.contexts else browser.new_context()

                # 一律開新分頁，不霸佔使用者現有分頁（避免把他正在看的頁面導走）
                self.page = context.new_page()
                video_id = extract_video_id(video_url)

                title = self._init_session_log(video_url, video_id)
                self.ui_callback("SYSTEM", f"系統：正在部署唯讀防禦雷達... 節目：{title}")
                self.ui_callback(
                    "SYSTEM",
                    "提示：這是工具專用的 Chrome 視窗，不影響你原本的 Chrome。"
                    "首次使用若聊天室要求登入，請在此視窗登入 YouTube 一次，之後永久記住。"
                )
                self.page.goto(f"https://www.youtube.com/live_chat?v={video_id}")

                # 等待「聊天室容器」出現即可，不等「至少一則留言」——
                # 直播還沒開始（例如預告網址、晚上八點才開播）時聊天室
                # 頁面本身已經載入，只是還沒有人留言，用訊息當判斷標準
                # 會誤判成連線失敗。改成耐心等待聊天室容器出現，讓使用者
                # 可以提前貼上網址、開播後自動接手監看，不用一直手動重試。
                chat_ready = False
                wait_attempt = 0
                while self.is_running and not chat_ready:
                    wait_attempt += 1
                    try:
                        self.page.wait_for_selector("yt-live-chat-renderer", timeout=15000)
                        chat_ready = True
                    except Exception:
                        if not self.is_running:
                            break
                        self.ui_callback(
                            "SYSTEM",
                            f"尚未偵測到聊天室（可能直播尚未開始），持續等待中...（第{wait_attempt}次嘗試）"
                        )
                        if wait_attempt % 4 == 0:
                            try:
                                self.page.reload()
                            except Exception:
                                pass

                if not self.is_running:
                    self._teardown_browser()
                    return

                if not chat_ready:
                    self.ui_callback("SYSTEM", "錯誤：無法加載聊天室，請確認網址正確。")
                    self._teardown_browser()
                    return

                self.ui_callback("SYSTEM", "雷達運作中... 靜態過濾已就緒。若直播尚未開始，會持續監看至開播。")
                processed_msg_ids = set()
                # YouTube 聊天室的 DOM 節點在捲動/虛擬化重新渲染時，同一則留言
                # 有時會被拿到不同的 id，導致只靠 id 去重會讓同一則訊息（尤其是
                # 機器人自己剛送出、馬上又被讀回來的那則）被當成新留言重複處理。
                # 用 (作者, 內容) 加短時間窗口再做一層保險去重。
                recent_content_seen = {}
                CONTENT_DEDUP_WINDOW = 6.0

                # 追蹤上次檢查直播是否結束的時間（每 5 秒檢查一次，省 DOM 操作）
                last_stream_check = 0.0
                check_stream_interval = 5.0
                consecutive_no_chat_checks = 0

                while self.is_running:
                    try:
                        # 每 5 秒檢查一次聊天室容器是否還存在（直播結束時容器會消失）
                        now = time.time()
                        if now - last_stream_check >= check_stream_interval:
                            last_stream_check = now
                            try:
                                chat_container = self.page.query_selector("yt-live-chat-renderer")
                                if not chat_container:
                                    consecutive_no_chat_checks += 1
                                    if consecutive_no_chat_checks >= 2:  # 連續 2 次（10 秒）找不到，判定直播已結束
                                        self.ui_callback("SYSTEM", "偵測到直播已結束，正在保存記錄...")
                                        self.is_running = False
                                        break
                                else:
                                    consecutive_no_chat_checks = 0
                            except Exception:
                                pass

                        while not self.send_queue.empty():
                            queued_text = self.send_queue.get()
                            if not self._try_send(queued_text):
                                self.ui_callback(
                                    "SYSTEM",
                                    f"送出被冷卻機制擋下（{SEND_COOLDOWN_SECONDS}秒內僅能送出一次，"
                                    f"避免洗版），已跳過：{queued_text[:30]}"
                                )

                        while not self.ban_queue.empty():
                            ban_msg_id, ban_author = self.ban_queue.get()
                            success, message = self._try_ban(ban_msg_id, ban_author)
                            self.ui_callback("BAN_RESULT", {
                                "author": ban_author, "success": success, "message": message
                            })

                        # 抖內（Super Chat / Super Sticker）：跟一般留言是不同的
                        # DOM 元件，要分開抓，才能在日誌裡標示鵝黃色底提醒使用者。
                        superchats = self.page.query_selector_all(
                            "yt-live-chat-paid-message-renderer, yt-live-chat-paid-sticker-renderer"
                        )
                        for sc in superchats:
                            sc_id = sc.get_attribute("id") or sc.get_attribute("data-message-id")
                            if not sc_id or sc_id in processed_msg_ids:
                                continue
                            processed_msg_ids.add(sc_id)

                            author_el = sc.query_selector("#author-name")
                            amount_el = sc.query_selector("#purchase-amount, #purchase-amount-chip")
                            content_el = sc.query_selector("#message")
                            if not author_el or not amount_el:
                                continue

                            sc_author = author_el.inner_text()
                            sc_amount = amount_el.inner_text()
                            sc_content = content_el.inner_text() if content_el else ""
                            self._append_message_log(sc_author, sc_content, flagged=False, superchat_amount=sc_amount)
                            self.ui_callback("SUPERCHAT", {
                                "author": sc_author,
                                "amount": sc_amount,
                                "content": sc_content,
                            })

                        messages = self.page.query_selector_all("yt-live-chat-text-message-renderer")
                        for msg in messages:
                            msg_id = msg.get_attribute("id")
                            if msg_id in processed_msg_ids:
                                continue
                            processed_msg_ids.add(msg_id)

                            author_el = msg.query_selector("#author-name")
                            content_el = msg.query_selector("#message")
                            if not author_el or not content_el:
                                continue

                            author = author_el.inner_text()
                            content = content_el.inner_text()

                            content_key = (author, content)
                            now = time.time()
                            last_seen = recent_content_seen.get(content_key)
                            recent_content_seen[content_key] = now
                            if last_seen is not None and now - last_seen < CONTENT_DEDUP_WINDOW:
                                continue
                            # 順手清掉過期項目，避免這個 dict 隨直播時間無限長大
                            if len(recent_content_seen) > 500:
                                recent_content_seen = {
                                    k: v for k, v in recent_content_seen.items()
                                    if now - v < CONTENT_DEDUP_WINDOW
                                }

                            matched_rule = None
                            for rule in self.config.rules:
                                if rule.is_enabled and any(kw in content for kw in rule.trigger_keywords):
                                    matched_rule = rule
                                    break

                            if matched_rule:
                                self._append_message_log(author, content, flagged=True)

                                # 1. 命中任何關鍵字規則就算偵測到側翼攻擊；但實際
                                # 要用哪個回覆池，如果使用者設定了「最優先規則」，
                                # 一律改用該規則的回覆內容，不管實際命中的是哪一條
                                # 關鍵字規則——關鍵字偵測仍然是「有沒有被判定為
                                # 攻擊」的前提，只是回覆內容的選擇被優先規則取代。
                                priority_rule = next(
                                    (r for r in self.config.rules if r.is_enabled and getattr(r, "is_priority", False)),
                                    None
                                )
                                reply_source = priority_rule if priority_rule else matched_rule
                                suggested_reply = random.choice(reply_source.reply_pool)

                                # 2. 自動加上@留言者，讓回覆明確是針對這則攻擊留言。
                                # YouTube 頻道名稱本身有時就以 @ 開頭（例如
                                # "@H_Minnie_米妮"），無條件加 @ 會變成 "@@..."。
                                handle = author if author.startswith("@") else f"@{author}"
                                mention_reply = f"{handle} {suggested_reply}"

                                # 4. 全自動送出模式：偵測到側翼攻擊且已有對應回覆時，
                                # 直接嘗試送出（一樣要經過冷卻閘門，不管同時有多少
                                # 則留言或多少條規則命中，都只受同一個冷卻限制）。
                                # 不管有沒有真的送出，都要彈出告知視窗讓使用者知道
                                # 發生了側翼攻擊——這裡只是不需要使用者動手確認。
                                if getattr(self.config, "auto_send_enabled", False):
                                    sent = self._try_send(mention_reply)
                                    self.ui_callback("AUTO_SENT", {
                                        "author": author,
                                        "content": content,
                                        "reply": mention_reply,
                                        "sent": sent,
                                    })
                                else:
                                    self.ui_callback("ALERT", {
                                        "author": author,
                                        "content": content,
                                        "reply": mention_reply,
                                        "msg_id": msg_id
                                    })
                            else:
                                self._append_message_log(author, content, flagged=False)
                                self.ui_callback("NORMAL", {
                                    "author": author,
                                    "content": content
                                })
                    except Exception:
                        pass
                    time.sleep(0.4)
                self._teardown_browser()
        except Exception as e:
            self.ui_callback("SYSTEM", f"錯誤：{str(e)}")
        finally:
            self._save_session_log()

    def _teardown_browser(self):
        """收尾。CDP 接管模式下只『斷開 Playwright 連線』，保留使用者自己的
        真實 Chrome（不關視窗、不關分頁）；非接管模式才真的關閉 context。"""
        try:
            if self._cdp_browser is not None:
                self._cdp_browser.close()  # 只斷線，Chrome 進程續留
                self._cdp_browser = None
            elif self.page:
                self.page.context.close()
        except Exception:
            pass

    def stop(self):
        """停止監聽"""
        self.is_running = False
        self._teardown_browser()
