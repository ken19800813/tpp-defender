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
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tipwindow = tw = ctk.CTkToplevel(self.widget)
        tw.overrideredirect(True)
        tw.attributes("-topmost", True)
        tw.geometry(f"+{x}+{y}")
        ctk.CTkLabel(
            tw, text=self.text, fg_color="#1f2b2b", text_color="#e8fdfd",
            corner_radius=8, font=("Arial", 11), wraplength=300, justify="left"
        ).pack(padx=10, pady=8)

    def hide(self, event=None):
        if self.tipwindow:
            self.tipwindow.destroy()
            self.tipwindow = None


def info_icon(parent, text, **pack_kwargs):
    """建立一個ⓘ說明圖示並附加滑入提示，回傳該元件"""
    icon = ctk.CTkLabel(parent, text="ⓘ", font=("Arial", 14, "bold"), text_color=ACCENT, cursor="hand2")
    icon.pack(**pack_kwargs)
    ToolTip(icon, text)
    return icon


class NotificationPopUp(ctk.CTkToplevel):
    """置頂警示小彈窗：由真人點擊觸發一鍵複製到剪貼簿"""
    def __init__(self, parent, author, content, reply, ui_log_callback):
        super().__init__(parent)
        self.reply_text = reply
        self.ui_log_callback = ui_log_callback

        self.overrideredirect(True)
        self.attributes("-topmost", True)

        # 永遠定位於螢幕右下角
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = 340, 170
        self.geometry(f"{w}x{h}+{sw-w-20}+{sh-h-60}")
        self.configure(fg_color="#141a1a", border_color=ACCENT, border_width=2)

        ctk.CTkLabel(
            self, text="🚨 偵測到側翼攻擊留言！",
            font=("Arial", 13, "bold"), text_color=DANGER
        ).pack(pady=(10, 4))

        ctk.CTkLabel(
            self, text=f"🎤 {author}",
            font=("Arial", 10, "bold"), text_color=ACCENT
        ).pack(pady=2)

        ctk.CTkLabel(
            self, text=content,
            font=("Arial", 10), wraplength=300, justify="left"
        ).pack(pady=3)

        self.btn_copy = ctk.CTkButton(
            self, text=f"📋 複製反擊建議: {reply[:26]}...",
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a",
            command=self.on_click_copy, height=34
        )
        self.btn_copy.pack(pady=8, padx=10, fill="x")
        self.bind("<Space>", lambda e: self.on_click_copy())
        self.btn_copy.focus_set()

    def on_click_copy(self):
        pyperclip.copy(self.reply_text)
        self.ui_log_callback("SYSTEM", f"📋 反擊建議已複製到系統剪貼簿: {self.reply_text}")
        self.destroy()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("直播小幫手：打擊青鳥人人有責")
        self.geometry("1080x740")
        self.minsize(940, 640)
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")

        self.config_mgr = ConfigManager()
        self.bot_manager = BotThreadManager(self.config_mgr, self.handle_bot_signal)
        self.setup_ui()

    def setup_ui(self):
        """繪製整體介面：頂部品牌列 + 分頁"""
        top_frame = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=10)
        top_frame.pack(fill="x", padx=14, pady=(14, 8))

        title_box = ctk.CTkFrame(top_frame, fg_color="transparent")
        title_box.pack(side="left", padx=14, pady=10)

        ctk.CTkLabel(
            title_box, text="🛡️ 直播小幫手",
            font=("Arial", 20, "bold"), text_color=ACCENT
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_box, text="打擊青鳥人人有責",
            font=("Arial", 12), text_color="#9fd9d9"
        ).pack(anchor="w")

        self.status_label = ctk.CTkLabel(
            top_frame, text="● 狀態：待命",
            font=("Arial", 12, "bold"), text_color="#888"
        )
        self.status_label.pack(side="right", padx=16)

        # 分頁
        self.tabview = ctk.CTkTabview(
            self, fg_color=BG_PANEL,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color="#202929",
        )
        self.tabview.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self.tabview.add("📡 直播監控")
        self.tabview.add("🛠️ 防禦規則設定")
        self.tabview.add("💬 反擊建議")
        self.tabview.add("🗂️ 歷史記錄")

        self.init_monitor_tab()
        self.init_rules_tab()
        self.init_poison_pill_tab()
        self.init_history_tab()

    # ------------------------------------------------------------------
    # 分頁 1：直播監控
    # ------------------------------------------------------------------
    def init_monitor_tab(self):
        tab = self.tabview.tab("📡 直播監控")

        frame_url = ctk.CTkFrame(tab, fg_color="transparent")
        frame_url.pack(fill="x", padx=6, pady=(10, 4))

        row1 = ctk.CTkFrame(frame_url, fg_color="transparent")
        row1.pack(fill="x")
        ctk.CTkLabel(row1, text="YouTube 直播網址", font=("Arial", 12, "bold")).pack(side="left")
        info_icon(
            row1,
            "貼上你要監看的 YouTube 直播網址（需包含 watch?v=）。\n"
            "啟動後系統會先檢查該頻道是否在黑名單中，\n"
            "若非黑名單頻道，會另開一個唯讀瀏覽器視窗顯示聊天室，\n"
            "並開始靜默監看留言、比對防禦關鍵字。",
            side="left", padx=(6, 0)
        )

        row2 = ctk.CTkFrame(frame_url, fg_color="transparent")
        row2.pack(fill="x", pady=(6, 0))
        self.entry_url = ctk.CTkEntry(row2, placeholder_text="https://www.youtube.com/watch?v=...", height=36)
        self.entry_url.pack(side="left", padx=(0, 8), fill="x", expand=True)

        self.btn_start = ctk.CTkButton(
            row2, text="🚀 啟動雷達", command=self.start_monitoring,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a", height=36, width=120
        )
        self.btn_start.pack(side="left", padx=4)

        self.btn_stop = ctk.CTkButton(
            row2, text="⏹️ 停止", command=self.stop_monitoring,
            fg_color="#444", hover_color="#555", height=36, width=90, state="disabled"
        )
        self.btn_stop.pack(side="left", padx=4)

        # 日誌顯示區
        frame_log = ctk.CTkFrame(tab, fg_color="transparent")
        frame_log.pack(fill="both", expand=True, padx=6, pady=(10, 6))

        header = ctk.CTkFrame(frame_log, fg_color="transparent")
        header.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(header, text="🔔 實時日誌", font=("Arial", 12, "bold")).pack(side="left")
        info_icon(
            header,
            "顯示聊天室的即時留言。\n"
            "綠字＝一般留言；紅字＝被判定為側翼攻擊的留言（並會彈出反擊建議小窗）；\n"
            "灰字＝系統訊息。\n"
            "直播結束或按下停止後，本場完整記錄會自動存到「歷史記錄」頁籤。",
            side="left", padx=(6, 0)
        )

        self.log_text = scrolledtext.ScrolledText(
            frame_log, height=20, bg="#0a0f0f", fg="#33e6c9",
            insertbackground="#33e6c9", font=("Courier", 10), borderwidth=0
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.config(state="disabled")

        self.log_text.tag_config("ALERT", foreground="#ff5555", background="#1a0000")
        self.log_text.tag_config("SYSTEM", foreground="#7fb3b3")
        self.log_text.tag_config("NORMAL", foreground="#33e6c9")

    # ------------------------------------------------------------------
    # 分頁 2：防禦規則設定
    # ------------------------------------------------------------------
    def init_rules_tab(self):
        tab = self.tabview.tab("🛠️ 防禦規則設定")

        header = ctk.CTkFrame(tab, fg_color="transparent")
        header.pack(fill="x", padx=6, pady=(10, 4))
        ctk.CTkLabel(header, text="防禦規則", font=("Arial", 13, "bold")).pack(side="left")
        info_icon(
            header,
            "「規則」＝一組觸發關鍵字＋對應的建議回覆。\n"
            "當聊天室留言包含任一觸發關鍵字時，系統會判定為側翼攻擊留言，\n"
            "並隨機挑一句建議回覆彈出，供你複製後手動貼到聊天室。\n"
            "雲端規則會自動同步，這裡新增的規則只存在你的本機。",
            side="left", padx=(6, 0)
        )

        frame_buttons = ctk.CTkFrame(tab, fg_color="transparent")
        frame_buttons.pack(fill="x", padx=6, pady=4)

        ctk.CTkButton(frame_buttons, text="➕ 新增規則", command=self.add_rule_dialog,
                       fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a", width=110).pack(side="left", padx=4)
        ctk.CTkButton(frame_buttons, text="❌ 刪除規則", command=self.delete_rule_dialog,
                       fg_color="#444", hover_color="#555", width=110).pack(side="left", padx=4)
        ctk.CTkButton(frame_buttons, text="✏️ 編輯規則", command=self.edit_rule_dialog,
                       fg_color="#444", hover_color="#555", width=110).pack(side="left", padx=4)

        frame_list = ctk.CTkFrame(tab, fg_color="transparent")
        frame_list.pack(fill="both", expand=True, padx=6, pady=(8, 6))

        ctk.CTkLabel(frame_list, text="📋 已建立規則", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0, 4))

        self.rules_text = scrolledtext.ScrolledText(
            frame_list, height=15, bg="#0a0f0f", fg="#33e6c9", font=("Courier", 10), borderwidth=0
        )
        self.rules_text.pack(fill="both", expand=True)
        self.rules_text.config(state="disabled")

        self.refresh_rules_display()

    # ------------------------------------------------------------------
    # 分頁 3：反擊建議（原：挺台言論自訂）
    # ------------------------------------------------------------------
    def init_poison_pill_tab(self):
        tab = self.tabview.tab("💬 反擊建議")

        header = ctk.CTkFrame(tab, fg_color="transparent")
        header.pack(fill="x", padx=6, pady=(10, 2))
        ctk.CTkLabel(
            header, text="💬 自訂反擊建議語句庫",
            font=("Arial", 13, "bold"), text_color=ACCENT
        ).pack(side="left")
        info_icon(
            header,
            "當偵測到側翼攻擊留言時，系統有 20% 機率會從這份語句庫中\n"
            "隨機挑一句取代原本的建議回覆（機制完全透明，取代前你都能在\n"
            "彈出小窗看到實際要複製的文字，再自行決定要不要送出）。\n"
            "每行一句，儲存後立即生效。",
            side="left", padx=(6, 0)
        )

        ctk.CTkLabel(
            tab, text="每行一句，儲存後立即套用到反擊建議語句庫。",
            font=("Arial", 10), text_color="#8fb3b3"
        ).pack(anchor="w", padx=6, pady=(0, 6))

        self.poison_pill_text = scrolledtext.ScrolledText(
            tab, height=12, bg="#0a0f0f", fg="#ffe066", font=("Courier", 10), borderwidth=0
        )
        self.poison_pill_text.pack(fill="both", expand=True, padx=6, pady=4)

        frame_buttons = ctk.CTkFrame(tab, fg_color="transparent")
        frame_buttons.pack(fill="x", padx=6, pady=8)

        ctk.CTkButton(
            frame_buttons, text="💾 儲存反擊建議", command=self.save_poison_pills,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a"
        ).pack(side="left", padx=4)

        cloud_header = ctk.CTkFrame(tab, fg_color="transparent")
        cloud_header.pack(fill="x", padx=6, pady=(8, 2))
        ctk.CTkLabel(
            cloud_header, text="📥 雲端預設反擊建議（唯讀預覽）",
            font=("Arial", 12, "bold"), text_color="#888"
        ).pack(side="left")
        info_icon(
            cloud_header,
            "這是目前從雲端（GitHub）同步下來的預設反擊建議語句庫預覽，\n"
            "此區塊僅供參考，無法在此編輯；\n"
            "上方文字框儲存後會覆蓋你本機使用的語句庫。",
            side="left", padx=(6, 0)
        )

        self.cloud_pills_text = scrolledtext.ScrolledText(
            tab, height=4, bg="#121818", fg="#888888",
            font=("Courier", 9), borderwidth=0, state="disabled"
        )
        self.cloud_pills_text.pack(fill="x", padx=6, pady=(0, 6))

        self.refresh_cloud_pills_display()

    # ------------------------------------------------------------------
    # 分頁 4：歷史記錄
    # ------------------------------------------------------------------
    def init_history_tab(self):
        tab = self.tabview.tab("🗂️ 歷史記錄")

        header = ctk.CTkFrame(tab, fg_color="transparent")
        header.pack(fill="x", padx=6, pady=(10, 4))
        ctk.CTkLabel(header, text="歷史直播記錄", font=("Arial", 13, "bold")).pack(side="left")
        info_icon(
            header,
            "每次直播監控結束（按停止或直播結束）後，\n"
            "系統會把該場所有聊天留言完整存檔在本機 logs/ 資料夾。\n"
            "下方列出過去所有記錄的節目名稱與日期，點擊即可查看完整留言，\n"
            "被判定為側翼攻擊的留言會以紅字標註。",
            side="left", padx=(6, 0)
        )

        ctk.CTkButton(
            header, text="🔄 重新整理", command=self.refresh_history_list,
            fg_color="#444", hover_color="#555", width=100
        ).pack(side="right")

        self.history_scroll = ctk.CTkScrollableFrame(tab, fg_color="#0f1515")
        self.history_scroll.pack(fill="both", expand=True, padx=6, pady=(4, 10))

        self.refresh_history_list()

    def refresh_history_list(self):
        """掃描 logs/ 目錄，列出所有歷史直播記錄"""
        for widget in self.history_scroll.winfo_children():
            widget.destroy()

        if not os.path.isdir(LOGS_DIR):
            ctk.CTkLabel(
                self.history_scroll, text="目前尚無歷史記錄，直播監控結束後會自動出現在這裡。",
                text_color="#888"
            ).pack(anchor="w", padx=10, pady=10)
            return

        files = sorted(glob.glob(os.path.join(LOGS_DIR, "*.json")), reverse=True)
        if not files:
            ctk.CTkLabel(
                self.history_scroll, text="目前尚無歷史記錄，直播監控結束後會自動出現在這裡。",
                text_color="#888"
            ).pack(anchor="w", padx=10, pady=10)
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

            row = ctk.CTkFrame(self.history_scroll, fg_color=BG_PANEL, corner_radius=8)
            row.pack(fill="x", padx=4, pady=4)

            text_box = ctk.CTkFrame(row, fg_color="transparent")
            text_box.pack(side="left", fill="x", expand=True, padx=10, pady=8)

            ctk.CTkLabel(
                text_box, text=f"🎬 {title}", font=("Arial", 12, "bold"),
                text_color="#e8fdfd", anchor="w"
            ).pack(anchor="w")
            ctk.CTkLabel(
                text_box, text=f"📅 {date}　💬 {len(messages)} 則留言　🚩 {flagged_count} 則側翼標記",
                font=("Arial", 10), text_color="#8fb3b3", anchor="w"
            ).pack(anchor="w")

            ctk.CTkButton(
                row, text="查看完整記錄", width=110,
                fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a",
                command=lambda p=path: self.open_history_viewer(p)
            ).pack(side="right", padx=10, pady=8)

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
        win.geometry("720x600")
        win.attributes("-topmost", True)

        ctk.CTkLabel(
            win, text=f"🎬 {data.get('title', '未命名直播')}",
            font=("Arial", 14, "bold"), text_color=ACCENT
        ).pack(anchor="w", padx=14, pady=(12, 0))
        ctk.CTkLabel(
            win, text=f"📅 開始時間：{data.get('started_at', '未知')}　🔗 {data.get('video_url', '')}",
            font=("Arial", 10), text_color="#8fb3b3"
        ).pack(anchor="w", padx=14, pady=(2, 8))

        viewer = scrolledtext.ScrolledText(
            win, bg="#0a0f0f", fg="#33e6c9", font=("Courier", 10), borderwidth=0
        )
        viewer.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        viewer.tag_config("FLAGGED", foreground="#ff5555")
        viewer.tag_config("NORMAL", foreground="#33e6c9")

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
        self.status_label.configure(text="● 狀態：監控中", text_color=ACCENT)
        self.bot_manager.start(url)

    def stop_monitoring(self):
        self.bot_manager.stop()
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.status_label.configure(text="● 狀態：待命", text_color="#888")
        self.after(500, self.refresh_history_list)

    def handle_bot_signal(self, msg_type, data):
        if msg_type == "ALERT":
            self.append_log_highlight(f"[🚩側翼] {data['author']}: {data['content']}")
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
    def add_rule_dialog(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title("新增規則")
        dlg.geometry("500x420")
        dlg.attributes("-topmost", True)

        ctk.CTkLabel(dlg, text="關鍵字 (用逗號分隔):", font=("Arial", 11)).pack(anchor="w", padx=10, pady=5)
        entry_keywords = ctk.CTkEntry(dlg, width=400, placeholder_text="例: 檳榔,哭文哲")
        entry_keywords.pack(padx=10, pady=5, fill="x")

        ctk.CTkLabel(dlg, text="回覆草稿 (每行一句):", font=("Arial", 11)).pack(anchor="w", padx=10, pady=5)
        text_replies = scrolledtext.ScrolledText(dlg, height=8, font=("Courier", 10))
        text_replies.pack(padx=10, pady=5, fill="both", expand=True)

        def save_rule():
            keywords = [k.strip() for k in entry_keywords.get().split(",") if k.strip()]
            replies = [r.strip() for r in text_replies.get("1.0", "end").split("\n") if r.strip()]

            if not keywords or not replies:
                messagebox.showerror("錯誤", "關鍵字和回覆都不能為空")
                return

            if not self.config_mgr.validate_custom_rule(keywords, replies):
                messagebox.showerror(
                    "安全性錯誤",
                    "偵測到內容包含禁用詞彙，系統已拒絕儲存！"
                )
                return

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
            dlg.destroy()

        ctk.CTkButton(dlg, text="💾 儲存規則", command=save_rule,
                       fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a").pack(pady=10)

    def delete_rule_dialog(self):
        if not self.config_mgr.rules:
            messagebox.showwarning("警告", "目前沒有規則")
            return

        dlg = ctk.CTkToplevel(self)
        dlg.title("刪除規則")
        dlg.geometry("420x320")
        dlg.attributes("-topmost", True)

        ctk.CTkLabel(dlg, text="選擇要刪除的規則:", font=("Arial", 11)).pack(padx=10, pady=5)

        listbox = ctk.CTkTextbox(dlg, height=200, font=("Courier", 10))
        listbox.pack(fill="both", expand=True, padx=10, pady=5)
        listbox.configure(state="disabled")

        def delete_selected():
            if self.config_mgr.rules:
                self.config_mgr.delete_rule(self.config_mgr.rules[0].id)
                self.refresh_rules_display()
                messagebox.showinfo("成功", "規則已刪除")
                dlg.destroy()

        ctk.CTkButton(dlg, text="❌ 刪除", command=delete_selected, fg_color=DANGER, hover_color="#cc3333").pack(pady=10)

    def edit_rule_dialog(self):
        if not self.config_mgr.rules:
            messagebox.showwarning("警告", "目前沒有規則")
            return
        messagebox.showinfo("提示", "請先刪除舊規則後新增更新的規則")

    def refresh_rules_display(self):
        self.rules_text.config(state="normal")
        self.rules_text.delete("1.0", "end")
        for rule in self.config_mgr.rules:
            keywords_str = ", ".join(rule.trigger_keywords)
            replies_str = " | ".join(rule.reply_pool[:2])
            self.rules_text.insert("end", f"🔹 {keywords_str}\n   → {replies_str}...\n\n")
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
        for pill in self.config_mgr.poison_pill_base[:3]:
            self.cloud_pills_text.insert("end", f"• {pill}\n")
        self.cloud_pills_text.config(state="disabled")


if __name__ == "__main__":
    app = App()
    app.mainloop()
