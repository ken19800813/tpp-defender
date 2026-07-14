import threading
import time
from bot_engine import YouTubeLiveTacticalBot


class BotThreadManager:
    """背景多執行緒調度，防止 UI 視窗凍結"""
    def __init__(self, config_manager, ui_log_callback):
        self.config = config_manager
        self.ui_log_callback = ui_log_callback
        self.bot = None
        self.thread = None

    def start(self, url: str):
        """在背景執行緒啟動機器人"""
        if self.thread and self.thread.is_alive():
            self.ui_log_callback("SYSTEM", "⚠️ 雷達已在運行中")
            return

        self.bot = YouTubeLiveTacticalBot(self.config, self.ui_log_callback)
        self.thread = threading.Thread(target=self.bot.start_monitor, args=(url,), daemon=True)
        self.thread.start()

    def stop(self):
        """停止機器人"""
        if self.bot:
            self.bot.stop()
        self.ui_log_callback("SYSTEM", "系統：正在中斷監聽核心...")
        if self.thread:
            self.thread.join(timeout=2)

    def is_running(self) -> bool:
        """檢查機器人是否正在運行"""
        return self.bot is not None and self.bot.is_running

    def request_send(self, text: str):
        """從主UI執行緒請求自動送出一則回覆，實際打字動作會在bot自己的
        背景執行緒裡執行(Playwright的頁面物件不能跨執行緒直接呼叫)"""
        if self.bot and self.bot.is_running:
            self.bot.queue_send(text)

    def get_cooldown_remaining(self) -> float:
        """查詢距離下次可送出還要等幾秒，沒有監聽中就回傳0(不擋)"""
        if self.bot and self.bot.is_running:
            return self.bot.get_cooldown_remaining()
        return 0.0
