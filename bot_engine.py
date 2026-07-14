import time
import random
import json
import os
import re
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright

LOGS_DIR = "logs"


class YouTubeLiveTacticalBot:
    def __init__(self, config_manager, ui_callback):
        self.config = config_manager
        self.ui_callback = ui_callback
        self.is_running = False
        self.page = None
        self.session_log = None

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
                f"📁 本場直播記錄已存檔（共 {len(self.session_log['messages'])} 則留言，"
                f"{flagged_count} 則側翼標記）：{filename}"
            )
        except Exception as e:
            self.ui_callback("SYSTEM", f"⚠️ 記錄存檔失敗：{str(e)}")

    def start_monitor(self, video_url: str):
        """啟動唯讀雷達監聽"""
        if self.check_channel_lock(video_url):
            self.ui_callback("SYSTEM", "❌ 安全機制判定：本工具不支援此非授權親綠陣營頻道運作！")
            return

        self.is_running = True
        try:
            with sync_playwright() as p:
                # 標準透明啟動，不使用任何對抗性參數
                context = p.chromium.launch(headless=False)
                self.page = context.new_page()
                video_id = video_url.split("v=")[-1].split("&")[0]

                title = self._init_session_log(video_url, video_id)
                self.ui_callback("SYSTEM", f"系統：正在部署唯讀防禦雷達... 節目：{title}")
                self.page.goto(f"https://www.youtube.com/live_chat?v={video_id}")

                try:
                    self.page.wait_for_selector("yt-live-chat-text-message-renderer", timeout=20000)
                except Exception:
                    self.ui_callback("SYSTEM", "錯誤：無法加載聊天室，請確認網路連線。")
                    context.close()
                    return

                self.ui_callback("SYSTEM", "🟢 雷達運作中... 靜態過濾已就緒。")
                processed_msg_ids = set()

                while self.is_running:
                    try:
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

                                # 1. 挑選澄清草稿
                                suggested_reply = random.choice(matched_rule.reply_pool)

                                # 2. 20% 透明反擊建議覆蓋機制
                                if random.random() < 0.20 and self.config.poison_pill_base:
                                    suggested_reply = random.choice(self.config.poison_pill_base)

                                # 3. 推送 ALERT 訊號
                                self.ui_callback("ALERT", {
                                    "author": author,
                                    "content": content,
                                    "reply": suggested_reply
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
