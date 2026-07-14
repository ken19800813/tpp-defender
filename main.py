import customtkinter as ctk
from tkinter import messagebox, scrolledtext
import pyperclip
import uuid
from config import ConfigManager, Rule
from logger_thread import BotThreadManager


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
        w, h = 340, 160
        self.geometry(f"{w}x{h}+{sw-w-20}+{sh-h-60}")
        self.configure(fg_color="#1F1F1F", border_color="#FF8C00", border_width=2)

        lbl_title = ctk.CTkLabel(
            self, text="🚨 偵測到網軍留言！",
            font=("Arial", 13, "bold"), text_color="red"
        )
        lbl_title.pack(pady=5)

        lbl_author = ctk.CTkLabel(
            self, text=f"🎤 {author}",
            font=("Arial", 10, "bold"), text_color="#FFA500"
        )
        lbl_author.pack(pady=2)

        lbl_info = ctk.CTkLabel(
            self, text=content,
            font=("Arial", 10), wraplength=300, justify="left"
        )
        lbl_info.pack(pady=3)

        # 核心發言出口：僅執行複製到剪貼簿，完全不代為操作網頁
        self.btn_copy = ctk.CTkButton(
            self, text=f"📋 複製: {reply[:30]}...",
            fg_color="#FF8C00", hover_color="#CD853F",
            command=self.on_click_copy, height=32
        )
        self.btn_copy.pack(pady=5, padx=5, fill="x")
        self.bind("<Space>", lambda e: self.on_click_copy())
        self.btn_copy.focus_set()

    def on_click_copy(self):
        pyperclip.copy(self.reply_text)
        self.ui_log_callback("SYSTEM", f"📋 草稿已複製到系統剪貼簿: {self.reply_text}")
        self.destroy()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("阿北直播小助手 v1.0 - 真人輔助版")
        self.geometry("1000x700")
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")

        self.config_mgr = ConfigManager()
        self.bot_manager = BotThreadManager(self.config_mgr, self.handle_bot_signal)
        self.setup_ui()

    def setup_ui(self):
        """使用 CustomTkinter 繪製黑系科技感頁籤"""
        # 頂部狀態列
        top_frame = ctk.CTkFrame(self)
        top_frame.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(
            top_frame, text="🇹🇼 阿北直播小助手：真人反擊輔助版",
            font=("Arial", 18, "bold"), text_color="#FF8C00"
        ).pack(side="left")

        ctk.CTkLabel(
            top_frame, text="狀態: 待命",
            font=("Arial", 12), text_color="#888"
        ).pack(side="right")

        # 分頁
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=10, pady=10)

        self.tabview.add("直播監控")
        self.tabview.add("防禦規則設定")
        self.tabview.add("挺台言論自訂")

        self.init_monitor_tab()
        self.init_rules_tab()
        self.init_poison_pill_tab()

    def init_monitor_tab(self):
        """直播監控頁籤"""
        tab = self.tabview.tab("直播監控")

        frame_url = ctk.CTkFrame(tab)
        frame_url.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(frame_url, text="YouTube 直播網址:", font=("Arial", 12, "bold")).pack(side="left", padx=5)
        self.entry_url = ctk.CTkEntry(frame_url, placeholder_text="https://www.youtube.com/watch?v=...", width=400)
        self.entry_url.pack(side="left", padx=5, fill="x", expand=True)

        self.btn_start = ctk.CTkButton(frame_url, text="🚀 啟動雷達", command=self.start_monitoring, fg_color="#FF8C00")
        self.btn_start.pack(side="left", padx=5)

        self.btn_stop = ctk.CTkButton(frame_url, text="⏹️ 停止", command=self.stop_monitoring, fg_color="#666", state="disabled")
        self.btn_stop.pack(side="left", padx=5)

        # 日誌顯示區
        frame_log = ctk.CTkFrame(tab)
        frame_log.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(frame_log, text="🔔 實時日誌", font=("Arial", 12, "bold")).pack(anchor="w", padx=5, pady=5)

        self.log_text = scrolledtext.ScrolledText(
            frame_log, height=20, width=100, bg="#0a0a0a", fg="#00ff00",
            insertbackground="#00ff00", font=("Courier", 10)
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.config(state="disabled")

        # 標籤設定
        self.log_text.tag_config("ALERT", foreground="#ff0000", background="#1a0000")
        self.log_text.tag_config("SYSTEM", foreground="#888888")
        self.log_text.tag_config("NORMAL", foreground="#00ff00")

    def init_rules_tab(self):
        """防禦規則設定頁籤"""
        tab = self.tabview.tab("防禦規則設定")

        frame_buttons = ctk.CTkFrame(tab)
        frame_buttons.pack(fill="x", padx=10, pady=10)

        ctk.CTkButton(frame_buttons, text="➕ 新增規則", command=self.add_rule_dialog).pack(side="left", padx=5)
        ctk.CTkButton(frame_buttons, text="❌ 刪除規則", command=self.delete_rule_dialog).pack(side="left", padx=5)
        ctk.CTkButton(frame_buttons, text="✏️ 編輯規則", command=self.edit_rule_dialog).pack(side="left", padx=5)

        # 規則列表
        frame_list = ctk.CTkFrame(tab)
        frame_list.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(frame_list, text="📋 已建立規則", font=("Arial", 12, "bold")).pack(anchor="w", padx=5, pady=5)

        self.rules_text = scrolledtext.ScrolledText(
            frame_list, height=15, width=100, bg="#0a0a0a", fg="#00ff00",
            font=("Courier", 10)
        )
        self.rules_text.pack(fill="both", expand=True)
        self.rules_text.config(state="disabled")

        self.refresh_rules_display()

    def init_poison_pill_tab(self):
        """挺台言論自訂頁籤"""
        tab = self.tabview.tab("挺台言論自訂")

        ctk.CTkLabel(
            tab, text="💪 毒丸機制自訂基礎言論",
            font=("Arial", 12, "bold"), text_color="#FF8C00"
        ).pack(anchor="w", padx=10, pady=10)

        ctk.CTkLabel(
            tab, text="當檢測到網軍時，有 20% 機率以下列言論取代預設回覆。每行一句。",
            font=("Arial", 10), text_color="#888"
        ).pack(anchor="w", padx=10, pady=5)

        self.poison_pill_text = scrolledtext.ScrolledText(
            tab, height=12, width=100, bg="#0a0a0a", fg="#ffff00",
            font=("Courier", 10)
        )
        self.poison_pill_text.pack(fill="both", expand=True, padx=10, pady=10)

        frame_buttons = ctk.CTkFrame(tab)
        frame_buttons.pack(fill="x", padx=10, pady=10)

        ctk.CTkButton(frame_buttons, text="💾 儲存毒丸言論", command=self.save_poison_pills, fg_color="#FF8C00").pack(side="left", padx=5)

        # 預覽雲端毒丸
        ctk.CTkLabel(
            tab, text="📥 雲端預設毒丸言論 (唯讀)",
            font=("Arial", 12, "bold"), text_color="#888"
        ).pack(anchor="w", padx=10, pady=10)

        self.cloud_pills_text = scrolledtext.ScrolledText(
            tab, height=3, width=100, bg="#1a1a1a", fg="#888888",
            font=("Courier", 9), state="disabled"
        )
        self.cloud_pills_text.pack(fill="x", padx=10)

        self.refresh_cloud_pills_display()

    def start_monitoring(self):
        """啟動監聽"""
        url = self.entry_url.get().strip()
        if not url:
            messagebox.showerror("錯誤", "請輸入 YouTube 直播網址")
            return

        if "youtube.com" not in url and "youtu.be" not in url:
            messagebox.showerror("錯誤", "請輸入有效的 YouTube 網址")
            return

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.bot_manager.start(url)

    def stop_monitoring(self):
        """停止監聽"""
        self.bot_manager.stop()
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def handle_bot_signal(self, msg_type, data):
        """處理機器人訊號"""
        if msg_type == "ALERT":
            self.append_log_highlight(f"[🚨網軍] {data['author']}: {data['content']}")
            popup = NotificationPopUp(self, data['author'], data['content'], data['reply'], self.handle_bot_signal)
        elif msg_type == "NORMAL":
            self.append_log_normal(f"{data['author']}: {data['content']}")
        elif msg_type == "SYSTEM":
            self.append_log_system(data)

    def append_log(self, text, tag="NORMAL"):
        """附加日誌"""
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

    def add_rule_dialog(self):
        """新增規則對話框"""
        dlg = ctk.CTkToplevel(self)
        dlg.title("新增規則")
        dlg.geometry("500x400")
        dlg.attributes("-topmost", True)

        ctk.CTkLabel(dlg, text="關鍵字 (用逗號分隔):", font=("Arial", 11)).pack(anchor="w", padx=10, pady=5)
        entry_keywords = ctk.CTkEntry(dlg, width=400, placeholder_text="例: 檳榔,哭文哲")
        entry_keywords.pack(padx=10, pady=5, fill="x")

        ctk.CTkLabel(dlg, text="回覆草稿 (用逗號分隔):", font=("Arial", 11)).pack(anchor="w", padx=10, pady=5)
        text_replies = scrolledtext.ScrolledText(dlg, height=6, width=50, font=("Courier", 10))
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

        ctk.CTkButton(dlg, text="💾 儲存規則", command=save_rule, fg_color="#FF8C00").pack(pady=10)

    def delete_rule_dialog(self):
        """刪除規則"""
        if not self.config_mgr.rules:
            messagebox.showwarning("警告", "目前沒有規則")
            return

        dlg = ctk.CTkToplevel(self)
        dlg.title("刪除規則")
        dlg.geometry("400x300")
        dlg.attributes("-topmost", True)

        ctk.CTkLabel(dlg, text="選擇要刪除的規則:", font=("Arial", 11)).pack(padx=10, pady=5)

        listbox = ctk.CTkTextbox(dlg, height=10, width=40, font=("Courier", 10))
        listbox.pack(fill="both", expand=True, padx=10, pady=5)
        listbox.configure(state="disabled")

        def delete_selected():
            # 簡單起見，刪除第一個
            if self.config_mgr.rules:
                self.config_mgr.delete_rule(self.config_mgr.rules[0].id)
                self.refresh_rules_display()
                messagebox.showinfo("成功", "規則已刪除")
                dlg.destroy()

        ctk.CTkButton(dlg, text="❌ 刪除", command=delete_selected, fg_color="#ff4444").pack(pady=10)

    def edit_rule_dialog(self):
        """編輯規則"""
        if not self.config_mgr.rules:
            messagebox.showwarning("警告", "目前沒有規則")
            return
        messagebox.showinfo("提示", "請先刪除舊規則後新增更新的規則")

    def refresh_rules_display(self):
        """更新規則顯示"""
        self.rules_text.config(state="normal")
        self.rules_text.delete("1.0", "end")
        for rule in self.config_mgr.rules:
            keywords_str = ", ".join(rule.trigger_keywords)
            replies_str = " | ".join(rule.reply_pool[:2])
            self.rules_text.insert("end", f"🔹 {keywords_str}\n   → {replies_str}...\n\n")
        self.rules_text.config(state="disabled")

    def save_poison_pills(self):
        """儲存毒丸言論"""
        pills = [p.strip() for p in self.poison_pill_text.get("1.0", "end").split("\n") if p.strip()]
        if not pills:
            messagebox.showwarning("警告", "請至少輸入一條言論")
            return

        # 簡單起見，存到本機快取
        import json
        with open("security_cache.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        data["poison_pill_replies"] = pills
        with open("security_cache.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        self.config_mgr.poison_pill_base = pills
        messagebox.showinfo("成功", "毒丸言論已儲存")

    def refresh_cloud_pills_display(self):
        """更新雲端毒丸顯示"""
        self.cloud_pills_text.config(state="normal")
        self.cloud_pills_text.delete("1.0", "end")
        for pill in self.config_mgr.poison_pill_base[:3]:
            self.cloud_pills_text.insert("end", f"• {pill}\n")
        self.cloud_pills_text.config(state="disabled")


if __name__ == "__main__":
    app = App()
    app.mainloop()
