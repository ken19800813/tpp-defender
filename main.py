import customtkinter as ctk
from tkinter import messagebox, scrolledtext
import pyperclip
import uuid
import os
import json
import glob
from config import ConfigManager, Rule
from logger_thread import BotThreadManager
from bot_engine import LOGS_DIR

ACCENT = "#28c8c8"
ACCENT_HOVER = "#1fa3a3"
ACCENT_DIM = "#1c8a8a"
DANGER = "#ff4d4d"
BG_PANEL = "#161b1b"
LOG_WHITE = "#ffffff"

# 全域字體（再放大兩級）
FONT_BRAND = ("Arial", 30, "bold")
FONT_TAGLINE = ("Arial", 18)
FONT_STATUS = ("Arial", 17, "bold")
FONT_TAB = ("Arial", 18, "bold")
FONT_SECTION = ("Arial", 20, "bold")
FONT_LABEL = ("Arial", 16)
FONT_LABEL_BOLD = ("Arial", 16, "bold")
FONT_BUTTON = ("Arial", 16, "bold")
FONT_ENTRY = ("Arial", 16)
FONT_MONO = ("Courier New", 16)
FONT_MONO_SMALL = ("Courier New", 14)
FONT_TOOLTIP = ("Arial", 14)
FONT_ICON = ("Arial", 16, "bold")
FONT_MARQUEE = ("Courier New", 15, "bold")


class ToolTip:
    """滑入顯示功能說明的輕量提示框"""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        if self.tipwindow or not self.text:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tipwindow = tw = ctk.CTkToplevel(self.widget)
        tw.overrideredirect(True)
        tw.attributes("-topmost", True)
        tw.geometry(f"+{x}+{y}")
        ctk.CTkLabel(
            tw, text=self.text, fg_color="#1f2b2b", text_color="#e8fdfd",
            corner_radius=8, font=FONT_TOOLTIP, wraplength=400, justify="left"
        ).pack(padx=14, pady=12)

    def hide(self, event=None):
        if self.tipwindow:
            self.tipwindow.destroy()
            self.tipwindow = None


def info_icon(parent, text, **pack_kwargs):
    """建立一個ⓘ說明圖示並附加滑入提示，回傳該元件（這是唯一保留的圖示）"""
    icon = ctk.CTkLabel(parent, text="ⓘ", font=FONT_ICON, text_color=ACCENT, cursor="hand2")
    icon.pack(**pack_kwargs)
    ToolTip(icon, text)
    return icon


class Marquee(ctk.CTkFrame):
    """底部跑馬燈：內容來源可由雲端 GitHub 同步的 marquee_messages 更新"""
    def __init__(self, parent, get_messages, **kwargs):
        super().__init__(parent, **kwargs)
        self.get_messages = get_messages
        self._full_text = ""
        self._pos = 0
        self._running = True

        self.label = ctk.CTkLabel(self, text="", font=FONT_MARQUEE, text_color=ACCENT, anchor="w")
        self.label.pack(fill="x", padx=16, pady=8)

        self._tick()

    def _tick(self):
        if not self._running:
            return
        messages = self.get_messages() or []
        joined = "　★　".join(messages) if messages else "（尚無跑馬燈訊息）"
        full = joined + "　★　"

        if full != self._full_text:
            self._full_text = full
            self._pos = 0

        window_len = 70
        source = self._full_text
        if len(source) < window_len:
            source = source * (window_len // max(len(source), 1) + 2)

        doubled = source + source
        text = doubled[self._pos:self._pos + window_len]
        self.label.configure(text=text)
        self._pos = (self._pos + 1) % len(source)

        self.after(160, self._tick)

    def destroy(self):
        self._running = False
        super().destroy()


class NotificationPopUp(ctk.CTkToplevel):
    """置頂警示小彈窗：由真人點擊觸發一鍵複製到剪貼簿"""
    def __init__(self, parent, author, content, reply, ui_log_callback):
        super().__init__(parent)
        self.reply_text = reply
        self.ui_log_callback = ui_log_callback

        self.overrideredirect(True)
        self.attributes("-topmost", True)

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = 420, 220
        self.geometry(f"{w}x{h}+{sw-w-20}+{sh-h-60}")
        self.configure(fg_color="#141a1a", border_color=ACCENT, border_width=2)

        ctk.CTkLabel(
            self, text="偵測到側翼攻擊留言！",
            font=("Arial", 17, "bold"), text_color=DANGER
        ).pack(pady=(14, 6))

        ctk.CTkLabel(
            self, text=author,
            font=("Arial", 15, "bold"), text_color=ACCENT
        ).pack(pady=2)

        ctk.CTkLabel(
            self, text=content,
            font=("Arial", 15), wraplength=380, justify="left"
        ).pack(pady=6)

        self.btn_copy = ctk.CTkButton(
            self, text=f"複製反擊建議：{reply[:20]}...",
            font=FONT_BUTTON,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a",
            command=self.on_click_copy, height=46
        )
        self.btn_copy.pack(pady=10, padx=14, fill="x")
        self.bind("<Space>", lambda e: self.on_click_copy())
        self.btn_copy.focus_set()

    def on_click_copy(self):
        pyperclip.copy(self.reply_text)
        self.ui_log_callback("SYSTEM", f"反擊建議已複製到系統剪貼簿：{self.reply_text}")
        self.destroy()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("直播小幫手：打擊青鳥人人有責")
        self.geometry("1320x920")
        self.minsize(1140, 780)
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")

        self.config_mgr = ConfigManager()
        self.bot_manager = BotThreadManager(self.config_mgr, self.handle_bot_signal)
        self.setup_ui()

    def setup_ui(self):
        """繪製整體介面：頂部品牌列 + 分頁 + 底部跑馬燈"""
        top_frame = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=10)
        top_frame.pack(fill="x", padx=16, pady=(16, 10))

        title_box = ctk.CTkFrame(top_frame, fg_color="transparent")
        title_box.pack(side="left", padx=18, pady=14)

        ctk.CTkLabel(
            title_box, text="直播小幫手",
            font=FONT_BRAND, text_color=ACCENT
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_box, text="打擊青鳥人人有責",
            font=FONT_TAGLINE, text_color="#9fd9d9"
        ).pack(anchor="w")

        self.status_label = ctk.CTkLabel(
            top_frame, text="狀態：待命",
            font=FONT_STATUS, text_color="#888"
        )
        self.status_label.pack(side="right", padx=20)

        # 分頁
        self.tabview = ctk.CTkTabview(
            self, fg_color=BG_PANEL,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color="#202929",
        )
        self.tabview.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self.tabview._segmented_button.configure(font=FONT_TAB, height=50)

        self.tabview.add("直播監控")
        self.tabview.add("防禦規則設定")
        self.tabview.add("反擊建議")
        self.tabview.add("歷史記錄")

        self.init_monitor_tab()
        self.init_rules_tab()
        self.init_poison_pill_tab()
        self.init_history_tab()

        # 底部跑馬燈
        self.marquee = Marquee(
            self, get_messages=lambda: self.config_mgr.marquee_messages,
            fg_color=BG_PANEL, corner_radius=10
        )
        self.marquee.pack(fill="x", padx=16, pady=(0, 16))

    # ------------------------------------------------------------------
    # 分頁 1：直播監控
    # ------------------------------------------------------------------
    def init_monitor_tab(self):
        tab = self.tabview.tab("直播監控")

        frame_url = ctk.CTkFrame(tab, fg_color="transparent")
        frame_url.pack(fill="x", padx=8, pady=(14, 6))

        row1 = ctk.CTkFrame(frame_url, fg_color="transparent")
        row1.pack(fill="x")
        ctk.CTkLabel(row1, text="YouTube 直播網址", font=FONT_SECTION).pack(side="left")
        info_icon(
            row1,
            "貼上你要監看的 YouTube 直播網址（需包含 watch?v=）。\n"
            "啟動後系統會先檢查該頻道是否在黑名單中，\n"
            "若非黑名單頻道，會另開一個瀏覽器視窗顯示聊天室，\n"
            "並開始靜默監看留言、比對防禦關鍵字。\n"
            "第一次使用這個瀏覽器視窗時，若聊天室要求登入，\n"
            "請手動登入一次，之後會記住登入狀態。",
            side="left", padx=(10, 0)
        )

        row2 = ctk.CTkFrame(frame_url, fg_color="transparent")
        row2.pack(fill="x", pady=(10, 0))
        self.entry_url = ctk.CTkEntry(
            row2, placeholder_text="https://www.youtube.com/watch?v=...", height=48, font=FONT_ENTRY
        )
        self.entry_url.pack(side="left", padx=(0, 10), fill="x", expand=True)

        self.btn_start = ctk.CTkButton(
            row2, text="啟動雷達", command=self.start_monitoring, font=FONT_BUTTON,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a", height=48, width=150
        )
        self.btn_start.pack(side="left", padx=4)

        self.btn_stop = ctk.CTkButton(
            row2, text="停止", command=self.stop_monitoring, font=FONT_BUTTON,
            fg_color="#444", hover_color="#555", height=48, width=110, state="disabled"
        )
        self.btn_stop.pack(side="left", padx=4)

        self.btn_test_alert = ctk.CTkButton(
            row2, text="測試彈跳視窗", command=self.trigger_test_alert, font=FONT_BUTTON,
            fg_color="#3a4a4a", hover_color="#4a5a5a", height=48, width=150
        )
        self.btn_test_alert.pack(side="left", padx=4)

        # 日誌顯示區
        frame_log = ctk.CTkFrame(tab, fg_color="transparent")
        frame_log.pack(fill="both", expand=True, padx=8, pady=(16, 8))

        header = ctk.CTkFrame(frame_log, fg_color="transparent")
        header.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(header, text="即時日誌", font=FONT_SECTION).pack(side="left")
        info_icon(
            header,
            "顯示聊天室的即時留言。\n"
            "白字＝一般留言；紅字＝被判定為側翼攻擊的留言（並會彈出反擊建議小窗）；\n"
            "灰字＝系統訊息。\n"
            "直播結束或按下停止後，本場完整記錄會自動存到「歷史記錄」頁籤。\n\n"
            "沒看到彈跳視窗？可以點右邊「測試彈跳視窗」按鈕，\n"
            "先確認彈窗機制本身正常運作，再確認聊天室是否真的出現觸發關鍵字。",
            side="left", padx=(10, 0)
        )

        self.log_text = scrolledtext.ScrolledText(
            frame_log, height=20, bg="#0a0f0f", fg=LOG_WHITE,
            insertbackground=LOG_WHITE, font=FONT_MONO, borderwidth=0, spacing1=4, spacing3=4
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.config(state="disabled")

        self.log_text.tag_config("ALERT", foreground="#ff4444", background="#1a0000")
        self.log_text.tag_config("SYSTEM", foreground="#8fb3b3")
        self.log_text.tag_config("NORMAL", foreground=LOG_WHITE)

    def trigger_test_alert(self):
        """手動觸發一次假的側翼攻擊留言，用來確認彈窗機制本身正常運作"""
        self.handle_bot_signal("ALERT", {
            "author": "測試用假留言者",
            "content": "（這是測試訊息，用來確認彈跳視窗機制正常運作）",
            "reply": "這只是測試，不是真的側翼留言。"
        })

    # ------------------------------------------------------------------
    # 分頁 2：防禦規則設定
    # ------------------------------------------------------------------
    def init_rules_tab(self):
        tab = self.tabview.tab("防禦規則設定")

        header = ctk.CTkFrame(tab, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(14, 6))
        ctk.CTkLabel(header, text="防禦規則", font=FONT_SECTION).pack(side="left")
        info_icon(
            header,
            "「規則」＝一組觸發關鍵字＋對應的建議回覆。\n"
            "當聊天室留言包含任一觸發關鍵字時，系統會判定為側翼攻擊留言，\n"
            "並隨機挑一句建議回覆彈出，供你複製後手動貼到聊天室。\n"
            "標示「雲端」的規則由 GitHub 統一維護、自動同步，無法在本機刪除；\n"
            "標示「自訂」的規則是你自己新增的，可自由編輯或刪除。",
            side="left", padx=(10, 0)
        )

        frame_buttons = ctk.CTkFrame(tab, fg_color="transparent")
        frame_buttons.pack(fill="x", padx=8, pady=8)

        ctk.CTkButton(frame_buttons, text="新增規則", command=self.add_rule_dialog, font=FONT_BUTTON,
                       fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a", height=44, width=150).pack(side="left", padx=4)
        ctk.CTkButton(frame_buttons, text="編輯規則", command=self.edit_rule_dialog, font=FONT_BUTTON,
                       fg_color="#444", hover_color="#555", height=44, width=150).pack(side="left", padx=4)
        ctk.CTkButton(frame_buttons, text="刪除規則", command=self.delete_rule_dialog, font=FONT_BUTTON,
                       fg_color="#444", hover_color="#555", height=44, width=150).pack(side="left", padx=4)

        frame_list = ctk.CTkFrame(tab, fg_color="transparent")
        frame_list.pack(fill="both", expand=True, padx=8, pady=(10, 8))

        ctk.CTkLabel(frame_list, text="已建立規則", font=FONT_LABEL_BOLD).pack(anchor="w", pady=(0, 8))

        self.rules_text = scrolledtext.ScrolledText(
            frame_list, height=15, bg="#0a0f0f", fg=LOG_WHITE, font=FONT_MONO,
            borderwidth=0, spacing1=6, spacing3=6
        )
        self.rules_text.pack(fill="both", expand=True)
        self.rules_text.config(state="disabled")
        self.rules_text.tag_config("CLOUD", foreground="#8fb3b3")
        self.rules_text.tag_config("USER", foreground=ACCENT)

        self.refresh_rules_display()

    # ------------------------------------------------------------------
    # 分頁 3：反擊建議
    # ------------------------------------------------------------------
    def init_poison_pill_tab(self):
        tab = self.tabview.tab("反擊建議")

        header = ctk.CTkFrame(tab, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(14, 4))
        ctk.CTkLabel(
            header, text="自訂反擊建議語句庫",
            font=FONT_SECTION, text_color=ACCENT
        ).pack(side="left")
        info_icon(
            header,
            "當偵測到側翼攻擊留言時，系統有 20% 機率會從這份語句庫中\n"
            "隨機挑一句取代原本的建議回覆（機制完全透明，取代前你都能在\n"
            "彈出小窗看到實際要複製的文字，再自行決定要不要送出）。\n"
            "每行一句，儲存後立即生效。",
            side="left", padx=(10, 0)
        )

        ctk.CTkLabel(
            tab, text="每行一句，儲存後立即套用到反擊建議語句庫。",
            font=FONT_LABEL, text_color="#8fb3b3"
        ).pack(anchor="w", padx=8, pady=(0, 8))

        self.poison_pill_text = scrolledtext.ScrolledText(
            tab, height=6, bg="#0a0f0f", fg="#ffe066", font=FONT_MONO, borderwidth=0,
            spacing1=4, spacing3=4
        )
        self.poison_pill_text.pack(fill="x", padx=8, pady=4)

        frame_buttons = ctk.CTkFrame(tab, fg_color="transparent")
        frame_buttons.pack(fill="x", padx=8, pady=12)

        ctk.CTkButton(
            frame_buttons, text="儲存反擊建議", command=self.save_poison_pills, font=FONT_BUTTON,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a", height=44, width=180
        ).pack(side="left", padx=4)

        cloud_header = ctk.CTkFrame(tab, fg_color="transparent")
        cloud_header.pack(fill="x", padx=8, pady=(10, 4))
        ctk.CTkLabel(
            cloud_header, text="雲端預設反擊建議（唯讀預覽）",
            font=FONT_LABEL_BOLD, text_color="#888"
        ).pack(side="left")
        info_icon(
            cloud_header,
            "這是目前從雲端（GitHub）同步下來的預設反擊建議語句庫預覽，\n"
            "此區塊僅供參考，無法在此編輯；\n"
            "上方文字框儲存後會覆蓋你本機使用的語句庫。",
            side="left", padx=(10, 0)
        )

        self.cloud_pills_text = scrolledtext.ScrolledText(
            tab, height=14, bg="#121818", fg="#cfcfcf",
            font=FONT_MONO, borderwidth=0, state="disabled", spacing1=5, spacing3=5
        )
        self.cloud_pills_text.pack(fill="both", expand=True, padx=8, pady=(0, 10))

        self.refresh_cloud_pills_display()

    # ------------------------------------------------------------------
    # 分頁 4：歷史記錄
    # ------------------------------------------------------------------
    def init_history_tab(self):
        tab = self.tabview.tab("歷史記錄")

        header = ctk.CTkFrame(tab, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(14, 6))
        ctk.CTkLabel(header, text="歷史直播記錄", font=FONT_SECTION).pack(side="left")
        info_icon(
            header,
            "每次直播監控結束（按停止或直播結束）後，\n"
            "系統會把該場所有聊天留言完整存檔在本機 logs/ 資料夾。\n"
            "下方列出過去所有記錄的節目名稱與日期，點擊即可查看完整留言，\n"
            "被判定為側翼攻擊的留言會以紅字標註。",
            side="left", padx=(10, 0)
        )

        ctk.CTkButton(
            header, text="重新整理", command=self.refresh_history_list, font=FONT_BUTTON,
            fg_color="#444", hover_color="#555", height=42, width=130
        ).pack(side="right")

        self.history_scroll = ctk.CTkScrollableFrame(tab, fg_color="#0f1515")
        self.history_scroll.pack(fill="both", expand=True, padx=8, pady=(6, 12))

        self.refresh_history_list()

    def refresh_history_list(self):
        """掃描 logs/ 目錄，列出所有歷史直播記錄"""
        for widget in self.history_scroll.winfo_children():
            widget.destroy()

        files = []
        if os.path.isdir(LOGS_DIR):
            files = sorted(glob.glob(os.path.join(LOGS_DIR, "*.json")), reverse=True)

        if not files:
            ctk.CTkLabel(
                self.history_scroll, text="目前尚無歷史記錄，直播監控結束後會自動出現在這裡。",
                font=FONT_LABEL, text_color="#888"
            ).pack(anchor="w", padx=12, pady=12)
            return

        for path in files:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue

            title = data.get("title", "未命名直播")
            date = data.get("started_at", "").split(" ")[0]
            messages = data.get("messages", [])
            flagged_count = sum(1 for m in messages if m.get("flagged"))

            row = ctk.CTkFrame(self.history_scroll, fg_color=BG_PANEL, corner_radius=10)
            row.pack(fill="x", padx=6, pady=6)

            text_box = ctk.CTkFrame(row, fg_color="transparent")
            text_box.pack(side="left", fill="x", expand=True, padx=14, pady=12)

            ctk.CTkLabel(
                text_box, text=title, font=FONT_LABEL_BOLD,
                text_color="#e8fdfd", anchor="w"
            ).pack(anchor="w")
            ctk.CTkLabel(
                text_box, text=f"{date}　留言數：{len(messages)}　側翼標記：{flagged_count}",
                font=FONT_LABEL, text_color="#8fb3b3", anchor="w"
            ).pack(anchor="w")

            ctk.CTkButton(
                row, text="查看完整記錄", width=150, height=44, font=FONT_BUTTON,
                fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a",
                command=lambda p=path: self.open_history_viewer(p)
            ).pack(side="right", padx=14, pady=12)

    def open_history_viewer(self, path):
        """開啟單場直播的完整聊天記錄檢視窗"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("錯誤", f"無法讀取記錄檔：{e}")
            return

        win = ctk.CTkToplevel(self)
        win.title(data.get("title", "直播記錄"))
        win.geometry("880x680")
        win.attributes("-topmost", True)

        ctk.CTkLabel(
            win, text=data.get('title', '未命名直播'),
            font=FONT_SECTION, text_color=ACCENT
        ).pack(anchor="w", padx=18, pady=(16, 0))
        ctk.CTkLabel(
            win, text=f"開始時間：{data.get('started_at', '未知')}　網址：{data.get('video_url', '')}",
            font=FONT_LABEL, text_color="#8fb3b3"
        ).pack(anchor="w", padx=18, pady=(4, 12))

        viewer = scrolledtext.ScrolledText(
            win, bg="#0a0f0f", fg=LOG_WHITE, font=FONT_MONO, borderwidth=0, spacing1=4, spacing3=4
        )
        viewer.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        viewer.tag_config("FLAGGED", foreground="#ff4444")
        viewer.tag_config("NORMAL", foreground=LOG_WHITE)

        for msg in data.get("messages", []):
            line = f"[{msg.get('time', '')}] {msg.get('author', '')}: {msg.get('content', '')}\n"
            viewer.insert("end", line, "FLAGGED" if msg.get("flagged") else "NORMAL")

        viewer.config(state="disabled")

    # ------------------------------------------------------------------
    # 監控控制
    # ------------------------------------------------------------------
    def start_monitoring(self):
        url = self.entry_url.get().strip()
        if not url:
            messagebox.showerror("錯誤", "請輸入 YouTube 直播網址")
            return

        if "youtube.com" not in url and "youtu.be" not in url:
            messagebox.showerror("錯誤", "請輸入有效的 YouTube 網址")
            return

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.status_label.configure(text="狀態：監控中", text_color=ACCENT)
        self.bot_manager.start(url)

    def stop_monitoring(self):
        self.bot_manager.stop()
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.status_label.configure(text="狀態：待命", text_color="#888")
        self.after(500, self.refresh_history_list)

    def handle_bot_signal(self, msg_type, data):
        if msg_type == "ALERT":
            self.append_log_highlight(f"[側翼攻擊] {data['author']}: {data['content']}")
            NotificationPopUp(self, data['author'], data['content'], data['reply'], self.handle_bot_signal)
        elif msg_type == "NORMAL":
            self.append_log_normal(f"{data['author']}: {data['content']}")
        elif msg_type == "SYSTEM":
            self.append_log_system(data)

    def append_log(self, text, tag="NORMAL"):
        self.log_text.config(state="normal")
        self.log_text.insert("end", text + "\n", tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def append_log_highlight(self, text):
        self.append_log(text, "ALERT")

    def append_log_normal(self, text):
        self.append_log(text, "NORMAL")

    def append_log_system(self, text):
        self.append_log(text, "SYSTEM")

    # ------------------------------------------------------------------
    # 規則對話框
    # ------------------------------------------------------------------
    def _rule_editor_dialog(self, title, initial_keywords="", initial_replies="", on_save=None):
        """共用的新增/編輯規則表單"""
        dlg = ctk.CTkToplevel(self)
        dlg.title(title)
        dlg.geometry("620x520")
        dlg.attributes("-topmost", True)

        ctk.CTkLabel(dlg, text="關鍵字 (用逗號分隔):", font=FONT_LABEL).pack(anchor="w", padx=14, pady=(14, 6))
        entry_keywords = ctk.CTkEntry(dlg, height=42, font=FONT_ENTRY, placeholder_text="例: 檳榔,哭文哲")
        entry_keywords.pack(padx=14, pady=4, fill="x")
        entry_keywords.insert(0, initial_keywords)

        ctk.CTkLabel(dlg, text="回覆草稿 (每行一句):", font=FONT_LABEL).pack(anchor="w", padx=14, pady=(10, 6))
        text_replies = scrolledtext.ScrolledText(dlg, height=8, font=FONT_MONO)
        text_replies.pack(padx=14, pady=4, fill="both", expand=True)
        text_replies.insert("1.0", initial_replies)

        def do_save():
            keywords = [k.strip() for k in entry_keywords.get().split(",") if k.strip()]
            replies = [r.strip() for r in text_replies.get("1.0", "end").split("\n") if r.strip()]

            if not keywords or not replies:
                messagebox.showerror("錯誤", "關鍵字和回覆都不能為空")
                return

            if not self.config_mgr.validate_custom_rule(keywords, replies):
                messagebox.showerror("安全性錯誤", "偵測到內容包含禁用詞彙，系統已拒絕儲存！")
                return

            on_save(keywords, replies)
            dlg.destroy()

        ctk.CTkButton(dlg, text="儲存規則", command=do_save, font=FONT_BUTTON, height=44,
                       fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a").pack(pady=14)

    def add_rule_dialog(self):
        def on_save(keywords, replies):
            rule = Rule(
                id=str(uuid.uuid4()),
                trigger_keywords=keywords,
                match_type="contains",
                reply_pool=replies,
                is_enabled=True
            )
            self.config_mgr.add_rule(rule)
            self.refresh_rules_display()
            messagebox.showinfo("成功", "規則已新增")

        self._rule_editor_dialog("新增規則", on_save=on_save)

    def edit_rule_dialog(self):
        """直接編輯既有的自訂規則，不需要先刪除"""
        if not self.config_mgr.user_rules:
            messagebox.showwarning("警告", "目前沒有可編輯的自訂規則（雲端預設規則無法在本機編輯）")
            return

        picker = ctk.CTkToplevel(self)
        picker.title("選擇要編輯的規則")
        picker.geometry("520x420")
        picker.attributes("-topmost", True)

        ctk.CTkLabel(picker, text="選擇要編輯的自訂規則：", font=FONT_LABEL).pack(anchor="w", padx=14, pady=12)

        scroll = ctk.CTkScrollableFrame(picker, fg_color="#0f1515")
        scroll.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        def open_editor_for(rule):
            picker.destroy()

            def on_save(keywords, replies):
                updated = Rule(
                    id=rule.id,
                    trigger_keywords=keywords,
                    match_type="contains",
                    reply_pool=replies,
                    is_enabled=rule.is_enabled
                )
                self.config_mgr.update_rule(rule.id, updated)
                self.refresh_rules_display()
                messagebox.showinfo("成功", "規則已更新")

            self._rule_editor_dialog(
                "編輯規則",
                initial_keywords=", ".join(rule.trigger_keywords),
                initial_replies="\n".join(rule.reply_pool),
                on_save=on_save
            )

        for rule in self.config_mgr.user_rules:
            row = ctk.CTkFrame(scroll, fg_color=BG_PANEL, corner_radius=8)
            row.pack(fill="x", padx=4, pady=4)
            ctk.CTkLabel(
                row, text=", ".join(rule.trigger_keywords), font=FONT_LABEL, anchor="w"
            ).pack(side="left", fill="x", expand=True, padx=12, pady=10)
            ctk.CTkButton(
                row, text="編輯", width=90, height=36, font=FONT_BUTTON,
                fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a",
                command=lambda r=rule: open_editor_for(r)
            ).pack(side="right", padx=12, pady=8)

    def delete_rule_dialog(self):
        if not self.config_mgr.user_rules:
            messagebox.showwarning("警告", "目前沒有可刪除的自訂規則（雲端預設規則無法在本機刪除）")
            return

        dlg = ctk.CTkToplevel(self)
        dlg.title("刪除規則")
        dlg.geometry("520x420")
        dlg.attributes("-topmost", True)

        ctk.CTkLabel(dlg, text="選擇要刪除的自訂規則：", font=FONT_LABEL).pack(anchor="w", padx=14, pady=12)

        scroll = ctk.CTkScrollableFrame(dlg, fg_color="#0f1515")
        scroll.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        def do_delete(rule_id):
            if messagebox.askyesno("確認刪除", "確定要刪除這條規則嗎？"):
                self.config_mgr.delete_rule(rule_id)
                self.refresh_rules_display()
                dlg.destroy()

        for rule in self.config_mgr.user_rules:
            row = ctk.CTkFrame(scroll, fg_color=BG_PANEL, corner_radius=8)
            row.pack(fill="x", padx=4, pady=4)
            ctk.CTkLabel(
                row, text=", ".join(rule.trigger_keywords), font=FONT_LABEL, anchor="w"
            ).pack(side="left", fill="x", expand=True, padx=12, pady=10)
            ctk.CTkButton(
                row, text="刪除", width=90, height=36, font=FONT_BUTTON,
                fg_color=DANGER, hover_color="#cc3333",
                command=lambda rid=rule.id: do_delete(rid)
            ).pack(side="right", padx=12, pady=8)

    def refresh_rules_display(self):
        self.rules_text.config(state="normal")
        self.rules_text.delete("1.0", "end")
        user_rule_ids = {r.id for r in self.config_mgr.user_rules}
        for rule in self.config_mgr.rules:
            is_user = rule.id in user_rule_ids
            tag = "USER" if is_user else "CLOUD"
            label = "自訂" if is_user else "雲端"
            keywords_str = ", ".join(rule.trigger_keywords[:12])
            if len(rule.trigger_keywords) > 12:
                keywords_str += f" ...等共{len(rule.trigger_keywords)}個"
            replies_str = " | ".join(rule.reply_pool[:2])
            self.rules_text.insert("end", f"[{label}] {keywords_str}\n   -> {replies_str}...\n\n", tag)
        self.rules_text.config(state="disabled")

    def save_poison_pills(self):
        pills = [p.strip() for p in self.poison_pill_text.get("1.0", "end").split("\n") if p.strip()]
        if not pills:
            messagebox.showwarning("警告", "請至少輸入一條反擊建議")
            return

        cache_path = "security_cache.json"
        data = {}
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        data["poison_pill_replies"] = pills
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        self.config_mgr.poison_pill_base = pills
        messagebox.showinfo("成功", "反擊建議已儲存")

    def refresh_cloud_pills_display(self):
        self.cloud_pills_text.config(state="normal")
        self.cloud_pills_text.delete("1.0", "end")
        for pill in self.config_mgr.poison_pill_base:
            self.cloud_pills_text.insert("end", f"• {pill}\n")
        self.cloud_pills_text.config(state="disabled")


if __name__ == "__main__":
    app = App()
    app.mainloop()
