import time
import random
import json
import os
import re
import sys
import queue
import subprocess
import requests
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

# 使用用戶主目錄，避免只讀文件系統問題
APP_DATA_DIR = Path.home() / ".tppchat"
LOGS_DIR = str(APP_DATA_DIR / "logs")
BROWSER_PROFILE_DIR = str(APP_DATA_DIR / "browser_profile")

# 送出冷卻秒數：不管是手動點擊送出還是全自動送出模式，兩次實際送出
# 之間至少要間隔這麼多秒，嚴禁被拿來洗版聊天室。
SEND_COOLDOWN_SECONDS = 10

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
        self.last_send_time = 0.0

    def queue_send(self, text: str):
        """從其他執行緒(主UI執行緒)排入一則要送出的回覆文字。
        Playwright的page物件只能在建立它的執行緒(本bot的背景執行緒)操作，
        所以用queue把指令帶進start_monitor()的迴圈裡執行，而不是直接呼叫。"""
        self.send_queue.put(text)

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
            video_id = video_url.split("v=")[-1].split("&")[0]
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

    def _append_message_log(self, author: str, content: str, flagged: bool):
        """記錄單則留言（無論是否被判定為側翼攻擊）"""
        if self.session_log is not None:
            self.session_log["messages"].append({
                "author": author,
                "content": content,
                "flagged": flagged,
                "time": datetime.now().strftime("%H:%M:%S")
            })

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

    def _ensure_chromium_installed(self, p) -> bool:
        """確認 Playwright 的 Chromium 瀏覽器元件已安裝。使用者完全不需要
        知道「Chromium」或「Playwright」這些名詞——全新電腦第一次啟動監看時，
        這裡會自動背景下載，下載期間會透過進度條告知使用者。"""
        try:
            browser = p.chromium.launch(headless=True)
            browser.close()
            return True
        except Exception:
            pass

        self.ui_callback("SYSTEM", "偵測到瀏覽器元件尚未安裝，正在自動下載（僅第一次執行需要，需要網路連線，可能需要 5-15 分鐘，請耐心等候）...")
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )

            last_update = time.time()
            output_lines = []

            for line in iter(process.stdout.readline, ''):
                if line:
                    output_lines.append(line.strip())
                    # 每 2 秒更新一次進度，顯示最後一行訊息
                    if time.time() - last_update > 2:
                        last_update = time.time()
                        msg = output_lines[-1] if output_lines else "下載中..."
                        if len(msg) > 60:
                            msg = msg[:57] + "..."
                        self.ui_callback("SYSTEM", f"⬇️  {msg}")

            process.wait(timeout=600)

            if process.returncode == 0:
                self.ui_callback("SYSTEM", "✅ 瀏覽器元件安裝完成，繼續啟動監看。")
                return True

            error_msg = "\n".join(output_lines[-10:]) if output_lines else "未知錯誤"
            self.ui_callback("SYSTEM", f"❌ 瀏覽器元件安裝失敗：{error_msg[-200:]}")
            return False
        except subprocess.TimeoutExpired:
            process.kill()
            self.ui_callback("SYSTEM", "❌ 瀏覽器元件安裝逾時（超過 10 分鐘），請檢查網路連線後重試。")
            return False
        except Exception as e:
            self.ui_callback("SYSTEM", f"❌ 瀏覽器元件安裝失敗：{str(e)[:100]}")
            return False

    def _find_system_chrome(self):
        """尋找系統已安裝的 Chrome 瀏覽器"""
        chrome_paths = []

        if sys.platform == "win32":
            chrome_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]
        elif sys.platform == "darwin":
            chrome_paths = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ]
        elif sys.platform == "linux":
            chrome_paths = [
                "/usr/bin/google-chrome",
                "/usr/bin/chromium",
            ]

        for path in chrome_paths:
            if os.path.exists(path):
                return path
        return None

    def start_monitor(self, video_url: str):
        """啟動唯讀雷達監聽"""
        if self.check_channel_lock(video_url):
            self.ui_callback("SYSTEM", "安全機制判定：本工具不支援此非授權親綠陣營頻道運作！")
            return

        self.is_running = True
        try:
            with sync_playwright() as p:
                os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)

                # 優先用系統已安裝的 Chrome
                chrome_path = self._find_system_chrome()
                if chrome_path:
                    self.ui_callback("SYSTEM", "✓ 偵測到系統 Chrome，直接使用。")
                    context = p.chromium.launch_persistent_context(
                        BROWSER_PROFILE_DIR,
                        executable_path=chrome_path,
                        headless=False,
                        args=["--disable-blink-features=AutomationControlled"]
                    )
                else:
                    # 找不到 Chrome，自動下載 Chromium
                    if not self._ensure_chromium_installed(p):
                        self.ui_callback("SYSTEM", "無法啟動：缺少瀏覽器元件，請確認網路連線後重試。")
                        return

                    self.ui_callback("SYSTEM", "系統未安裝 Chrome，改用 Chromium。")
                    context = p.chromium.launch_persistent_context(
                        BROWSER_PROFILE_DIR,
                        headless=False,
                        args=["--disable-blink-features=AutomationControlled"]
                    )

                self.page = context.pages[0] if context.pages else context.new_page()
                video_id = video_url.split("v=")[-1].split("&")[0]

                title = self._init_session_log(video_url, video_id)
                self.ui_callback("SYSTEM", f"系統：正在部署唯讀防禦雷達... 節目：{title}")
                self.ui_callback(
                    "SYSTEM",
                    "提示：若聊天室要求登入才能發言，請在這個瀏覽器視窗手動登入一次，"
                    "之後啟動都會記住登入狀態。"
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
                    context.close()
                    return

                if not chat_ready:
                    self.ui_callback("SYSTEM", "錯誤：無法加載聊天室，請確認網址正確。")
                    context.close()
                    return

                self.ui_callback("SYSTEM", "雷達運作中... 靜態過濾已就緒。若直播尚未開始，會持續監看至開播。")
                processed_msg_ids = set()

                while self.is_running:
                    try:
                        while not self.send_queue.empty():
                            queued_text = self.send_queue.get()
                            if not self._try_send(queued_text):
                                self.ui_callback(
                                    "SYSTEM",
                                    f"送出被冷卻機制擋下（{SEND_COOLDOWN_SECONDS}秒內僅能送出一次，"
                                    f"避免洗版），已跳過：{queued_text[:30]}"
                                )

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

                                # 2. 20% 透明反擊建議覆蓋機制（優先規則不受此覆蓋，
                                # 使用者既然設定了優先內容，就該原封不動地送出）
                                if not priority_rule and random.random() < 0.20 and self.config.poison_pill_base:
                                    suggested_reply = random.choice(self.config.poison_pill_base)

                                # 3. 自動加上@留言者，讓回覆明確是針對這則攻擊留言
                                mention_reply = f"@{author} {suggested_reply}"

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
                                        "reply": mention_reply
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
                context.close()
        except Exception as e:
            self.ui_callback("SYSTEM", f"錯誤：{str(e)}")
        finally:
            self._save_session_log()

    def stop(self):
        """停止監聽"""
        self.is_running = False
        if self.page:
            try:
                self.page.context.close()
            except Exception:
                pass
