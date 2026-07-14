import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
import pyperclip
import uuid
import os
import sys
import io
import json
import glob
import subprocess
import threading
import webbrowser
import requests
from PIL import Image
from config import ConfigManager, Rule
from logger_thread import BotThreadManager
from bot_engine import LOGS_DIR

AD_POLL_INTERVAL_MS = 60000

# 廣告彈窗圖片固定寬度（配合彈窗內容寬度），高度依圖片比例自動縮放、
# 不裁切也不設上限——彈窗本身高度會隨圖片+文字內容自動增高。
AD_IMAGE_WIDTH = 380

ACCENT = "#28c8c8"
ACCENT_HOVER = "#1fa3a3"
ACCENT_DIM = "#1c8a8a"
DANGER = "#ff4d4d"
BG_PANEL = "#161b1b"
LOG_WHITE = "#ffffff"
SCROLL_BG = "#1c2626"
SCROLL_TROUGH = "#0a0f0f"

# 全域字體（再放大兩級）
FONT_BRAND = ("Arial", 30, "bold")
FONT_TAGLINE = ("Arial", 18)
FONT_STATUS = ("Arial", 17, "bold")
FONT_TAB = ("Arial", 17, "bold")
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


def style_scrollbar(widget):
    """把 tkinter Text/ScrolledText 內建的淺色捲軸改成深色，避免跟深色主題衝突"""
    try:
        sb = widget.vbar
        sb.configure(
            bg=SCROLL_BG, troughcolor=SCROLL_TROUGH, activebackground=ACCENT_DIM,
            highlightthickness=0, bd=0, elementborderwidth=0, width=14
        )
    except Exception:
        pass


def bring_chat_browser_to_front():
    """把監看用的瀏覽器視窗帶到最前面，方便直接貼上回覆（僅macOS，其他平台靜默跳過）"""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Chromium" to activate'],
            timeout=2, capture_output=True
        )
    except Exception:
        pass


# 基本髒話/粗俗字眼黑名單（本機固定，跟雲端 forbidden_words 疊加檢查）。
# 這裡只抓明顯的髒話本身，不含泛政治攻擊詞彙——那些已由雲端
# forbidden_words 另外把關，避免這份清單過度擴張誤擋正常反擊言論。
#
# 以下詞條刻意沒收錄，因為在正常政治評論/日常對話裡有明確、常見的
# 非髒話用法，單純字串比對(word in text)會讓這些用法被誤判：
#   乾（乾淨/餅乾/乾脆）、滾（翻滾/滾動/熱滾滾）、垃圾（字面上的垃圾，
#   垃圾政策/垃圾車是真實政治議題）、三八（三八婦女節）、
#   老二（排行第二的孩子/副手）、可悲/可憐（單純表達同情惋惜）、
#   38、87（容易出現在日期、百分比、統計數字裡）、
#   G8（八大工業國集團，政治評論常會提到）
LOCAL_PROFANITY_BLOCKLIST = [
    # 直接髒話
    "幹", "肏", "靠", "靠北", "靠夭", "操", "操你", "幹你", "幹你娘", "幹你媽",
    "幹林娘", "幹拎娘", "姦", "雞巴", "雞掰", "機掰", "雞掰人", "機八",
    "JB", "雞雞", "屌", "懶叫", "龜頭", "陰道", "屄", "婊子", "賤貨",
    "淫娃", "騷貨",
    # 問候親屬類
    "去你媽的", "去死", "死全家", "你媽的", "他媽的", "幹你祖宗", "幹你全家",
    "問候祖宗十八代",
    # 智力、人格辱罵
    "白癡", "智障", "腦殘", "低能", "廢物", "人渣", "王八蛋", "混蛋",
    "畜生", "禽獸", "北七", "白目", "白爛", "神經病", "瘋子", "笨蛋",
    "呆子", "蠢貨", "廢咖", "廢柴", "廢渣",
    # 台語常見
    "幹恁娘", "幹恁老師", "幹拎老師", "幹林老師", "肖欸", "肖查某", "肖年欸",
    # 網路常見辱罵
    "狗東西", "狗雜種", "狗娘養的", "賤人", "死屁孩", "垃圾人", "社會敗類",
    "人妖", "臭婊", "臭俗辣", "廢物仔", "滾蛋", "吃屎",
    "吃大便", "吃土",
    # 縮寫、變形、規避審查
    "J8", "MD", "TMD", "NMSL", "SB", "CNM", "WTF", "Fxxk", "F***",
    "fk", "sh*t", "bitch", "asshole", "mf",
    # 常見諧音／替代寫法
    "淦", "榦", "靠邀", "靠妖", "靠腰", "G掰", "北妻", "B7",
    "ㄐㄅ", "ㄐㄅㄌ", "ㄍㄋㄋ", "ㄍㄋㄇ", "ㄎㄅ", "ㄎㄧㄅ", "ㄐㄓ",
]


def find_forbidden_word(text, extra_forbidden_words=None):
    """檢查文字是否包含髒話或雲端禁用詞，找到就回傳該詞，否則回傳None"""
    words = LOCAL_PROFANITY_BLOCKLIST + list(extra_forbidden_words or [])
    for word in words:
        if word and word in text:
            return word
    return None


def read_clipboard(widget):
    """統一的剪貼簿讀取。優先用 Tkinter 原生 clipboard_get()——它不依賴
    pbpaste/pyperclip，在打包成 .app / .exe 後 PATH 被精簡時仍然可用（這是
    先前所有貼上途徑同時失效的真正原因：全都經過 pyperclip → pbpaste，
    一旦環境找不到 pbpaste 就靜默回空）。pyperclip 僅作後備。"""
    text = ""
    try:
        text = widget.clipboard_get()
    except Exception:
        text = ""
    if not text:
        try:
            text = pyperclip.paste()
        except Exception:
            text = ""
    return text or ""


def bind_paste(entry):
    """接管 Ctrl+V / Cmd+V 貼上，綁在輸入框本身（於 class 綁定前執行並
    return 'break'，避免與系統原生貼上重複觸發）。同時綁大小寫與 <<Paste>>
    虛擬事件，涵蓋 Mac/Windows/Linux 各種鍵位。"""
    def do_paste(event=None):
        text = read_clipboard(entry)
        if not text:
            # 我們讀不到 → 不要 return 'break'，放行讓系統原生貼上當最後一道
            return None
        try:
            entry.delete("sel.first", "sel.last")  # 有選取就取代
        except Exception:
            pass
        try:
            entry.insert("insert", text)  # 插在游標處（標準貼上行為）
        except Exception:
            try:
                entry.insert(0, text)
            except Exception:
                pass
        return "break"  # 已貼上，擋掉原生貼上避免重複

    for seq in ("<Control-v>", "<Control-V>", "<Command-v>", "<Command-V>", "<<Paste>>"):
        entry.bind(seq, do_paste)
    return entry


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


def pagination_bar(parent, on_prev, on_next, page_size_options=None, on_size_change=None,
                    default_size=None, **pack_kwargs):
    """建立一組上一頁/頁碼/下一頁(+可選每頁筆數選單)的分頁控制列
    回傳(frame, page_label, prev_btn, next_btn, size_menu)"""
    bar = ctk.CTkFrame(parent, fg_color="transparent")
    bar.pack(**pack_kwargs)

    prev_btn = ctk.CTkButton(
        bar, text="上一頁", command=on_prev, font=FONT_BUTTON,
        fg_color="#444", hover_color="#555", height=38, width=100
    )
    prev_btn.pack(side="left", padx=4)

    page_label = ctk.CTkLabel(bar, text="第 1 / 1 頁", font=FONT_LABEL, text_color="#8fb3b3")
    page_label.pack(side="left", padx=10)

    next_btn = ctk.CTkButton(
        bar, text="下一頁", command=on_next, font=FONT_BUTTON,
        fg_color="#444", hover_color="#555", height=38, width=100
    )
    next_btn.pack(side="left", padx=4)

    size_menu = None
    if page_size_options:
        ctk.CTkLabel(bar, text="每頁顯示：", font=FONT_LABEL, text_color="#8fb3b3").pack(side="left", padx=(20, 4))
        size_var = tk.StringVar(value=str(default_size or page_size_options[0]))
        size_menu = ctk.CTkOptionMenu(
            bar, values=[str(n) for n in page_size_options], variable=size_var,
            width=90, height=38, font=FONT_LABEL,
            fg_color="#444", button_color="#444", button_hover_color="#555",
            dropdown_fg_color="#1c2626", dropdown_hover_color=ACCENT_DIM,
            command=lambda v: on_size_change(int(v)) if on_size_change else None
        )
        size_menu.pack(side="left", padx=4)
        ctk.CTkLabel(bar, text="筆", font=FONT_LABEL, text_color="#8fb3b3").pack(side="left")

    return bar, page_label, prev_btn, next_btn, size_menu


def copy_icon(parent, get_text, log_callback=None, **pack_kwargs):
    """建立一個可點擊的複製圖示，點擊時把get_text()回傳的內容整份複製到剪貼簿"""
    icon = ctk.CTkLabel(parent, text="⧉", font=FONT_ICON, text_color=ACCENT, cursor="hand2")

    def on_click(event=None):
        text = get_text()
        pyperclip.copy(text)
        if log_callback:
            log_callback("已複製雲端預設反擊建議全文到剪貼簿。")

    icon.bind("<Button-1>", on_click)
    icon.pack(**pack_kwargs)
    ToolTip(icon, "點擊複製完整的雲端預設反擊建議清單到剪貼簿")
    return icon


class Marquee(ctk.CTkFrame):
    """底部跑馬燈：文字從視窗最右側進場，往左捲動，內容與捲動速度都由 LINEBOT
    後台的「直播小幫手：跑馬燈設定」同步（speed_level 1~10，數字越大越快，
    直接對應每個tick移動的像素數，固定30ms一個tick）。"""
    def __init__(self, parent, get_messages, get_speed_level=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.get_messages = get_messages
        self.get_speed_level = get_speed_level or (lambda: 4)
        self._running = True
        self._source_text = None
        self._text_id = None
        self._x = None

        self.canvas = tk.Canvas(self, height=44, bg=BG_PANEL, highlightthickness=0)
        self.canvas.pack(fill="x", padx=16, pady=8)

        self._tick()

    def _tick(self):
        if not self._running:
            return

        messages = self.get_messages() or []
        text = ("　★　".join(messages) if messages else "（尚無跑馬燈訊息）") + "　★　"

        canvas_width = self.canvas.winfo_width()
        if canvas_width <= 1:
            # 視窗尚未完成排版，稍後再試
            self.after(50, self._tick)
            return

        if text != self._source_text or self._text_id is None:
            self._source_text = text
            self.canvas.delete("all")
            self._text_id = self.canvas.create_text(
                canvas_width, 22, text=text, anchor="w",
                fill=ACCENT, font=FONT_MARQUEE
            )
            self._x = canvas_width

        bbox = self.canvas.bbox(self._text_id)
        text_width = (bbox[2] - bbox[0]) if bbox else 200

        try:
            speed = int(self.get_speed_level())
        except Exception:
            speed = 4
        speed = max(1, min(10, speed))

        self._x -= speed
        if self._x < -text_width:
            self._x = canvas_width

        self.canvas.coords(self._text_id, self._x, 22)
        self.after(30, self._tick)

    def destroy(self):
        self._running = False
        super().destroy()


class NotificationPopUp(ctk.CTkToplevel):
    """置頂警示小彈窗：顯示系統建議的回覆，但文字是可編輯的——使用者可以先修改
    內容再送出。送出前會即時檢查是否包含髒話/禁用詞，違規時整個擋下、無法送出。
    每個小窗只能送出一次；送出還受全域10秒冷卻限制，倒數期間按鈕會顯示秒數、
    無法點擊，嚴禁被拿來洗版聊天室。點擊送出後，會自動打字進聊天室並直接送出
    （這一步會真的公開發言）。"""
    def __init__(self, parent, author, content, reply, ui_log_callback,
                 on_send=None, forbidden_words=None, cooldown_getter=None):
        super().__init__(parent)
        self.ui_log_callback = ui_log_callback
        self.on_send = on_send
        self.forbidden_words = forbidden_words or []
        self.cooldown_getter = cooldown_getter or (lambda: 0.0)
        self._has_sent = False
        self._alive = True

        self.overrideredirect(True)
        self.attributes("-topmost", True)

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = 480, 300
        self.geometry(f"{w}x{h}+{sw-w-20}+{sh-h-60}")
        self.configure(fg_color="#141a1a", border_color=ACCENT, border_width=2)

        ctk.CTkLabel(
            self, text="偵測到側翼攻擊留言！",
            font=("Arial", 17, "bold"), text_color=DANGER
        ).pack(pady=(14, 6), padx=16, anchor="w")

        ctk.CTkLabel(
            self, text=f"{author}：{content}",
            font=("Arial", 14), text_color="#cfcfcf", wraplength=440, justify="left"
        ).pack(pady=(0, 10), padx=16, anchor="w")

        edit_header = ctk.CTkFrame(self, fg_color="transparent")
        edit_header.pack(fill="x", padx=16)
        ctk.CTkLabel(
            edit_header, text="回覆內容（可自行修改）：",
            font=("Arial", 14, "bold"), text_color=ACCENT
        ).pack(side="left")

        self.reply_box = ctk.CTkTextbox(
            self, font=("Arial", 15), height=80, wrap="word",
            fg_color="#0a0f0f", text_color="#ffffff", border_width=1, border_color="#333"
        )
        self.reply_box.pack(fill="x", padx=16, pady=(4, 4))
        self.reply_box.insert("1.0", reply)
        self.reply_box.bind("<KeyRelease>", self._refresh_button_state)

        self.warning_label = ctk.CTkLabel(
            self, text="", font=("Arial", 13, "bold"), text_color=DANGER
        )
        self.warning_label.pack(padx=16, anchor="w")

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(6, 14))

        self.btn_send = ctk.CTkButton(
            btn_row, text="送出", font=FONT_BUTTON,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a",
            command=self.on_click_send, height=46
        )
        self.btn_send.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            btn_row, text="取消", font=FONT_BUTTON,
            fg_color="#444", hover_color="#555",
            command=self.destroy, height=46, width=100
        ).pack(side="left")

        self.reply_box.focus_set()
        self._refresh_button_state()
        self._tick_cooldown()

    def destroy(self):
        self._alive = False
        super().destroy()

    def _get_text(self) -> str:
        return self.reply_box.get("1.0", "end").strip()

    def _tick_cooldown(self):
        """每秒刷新一次冷卻倒數顯示，冷卻結束前按鈕會一直顯示剩餘秒數"""
        if not self._alive:
            return
        self._refresh_button_state()
        self.after(1000, self._tick_cooldown)

    def _refresh_button_state(self, event=None):
        if self._has_sent:
            return

        text = self._get_text()
        bad_word = find_forbidden_word(text, self.forbidden_words)
        if bad_word:
            self.warning_label.configure(text=f"⚠ 內容包含禁用詞彙「{bad_word}」，無法送出")
            self.reply_box.configure(border_color=DANGER)
            self.btn_send.configure(text="送出", state="disabled", fg_color="#555")
            return

        self.warning_label.configure(text="")
        self.reply_box.configure(border_color="#333")

        remaining = self.cooldown_getter()
        if remaining > 0:
            self.btn_send.configure(
                text=f"送出（冷卻中 {int(remaining) + 1} 秒）",
                state="disabled", fg_color="#555"
            )
        else:
            self.btn_send.configure(text="送出", state="normal", fg_color=ACCENT)

    def on_click_send(self):
        if self._has_sent:
            return
        text = self._get_text()
        if not text:
            return
        if find_forbidden_word(text, self.forbidden_words):
            return  # 按鈕理論上已被停用，這裡再擋一次防止繞過
        if self.cooldown_getter() > 0:
            return  # 同上，冷卻中理論上按鈕已停用

        # 每個小窗只能送出一次：送出瞬間立刻鎖住，不管後續結果如何都不能再按
        self._has_sent = True
        self.btn_send.configure(text="已送出", state="disabled", fg_color="#555")

        pyperclip.copy(text)
        self.ui_log_callback("SYSTEM", f"正在自動送出反擊建議：{text}")
        if self.on_send:
            self.on_send(text)
        bring_chat_browser_to_front()
        self.after(600, self.destroy)


class AdPopUp(ctk.CTkToplevel):
    """廣告推播彈窗：顯示後台設定的圖片、文字與超連結。每支廣告對每台
    電腦只會顯示一次（由 ConfigManager 在本機記錄已看過的廣告id）。
    這是一般視窗（非overrideredirect），使用者可以直接用標題列關閉。"""
    def __init__(self, parent, title_text, image_bytes, link_url):
        super().__init__(parent)
        self.link_url = link_url

        self.title("推播訊息")
        self.attributes("-topmost", True)
        self.configure(fg_color="#141a1a")
        self.resizable(False, False)

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(padx=18, pady=18)

        if image_bytes:
            try:
                pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                pil_img = self._resize_to_fixed_width(pil_img, AD_IMAGE_WIDTH)
                ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=pil_img.size)
                ctk.CTkLabel(content, image=ctk_img, text="").pack(pady=(0, 12))
            except Exception:
                pass

        if title_text:
            ctk.CTkLabel(
                content, text=title_text, font=("Arial", 15), text_color="#e8fdfd",
                wraplength=380, justify="left"
            ).pack(pady=(0, 14))

        btn_row = ctk.CTkFrame(content, fg_color="transparent")
        btn_row.pack(fill="x")

        if link_url:
            ctk.CTkButton(
                btn_row, text="前往連結", font=FONT_BUTTON,
                fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a",
                command=self.on_open_link, height=42
            ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            btn_row, text="關閉", font=FONT_BUTTON,
            fg_color="#444", hover_color="#555",
            command=self.destroy, height=42, width=100
        ).pack(side="left")

    @staticmethod
    def _resize_to_fixed_width(pil_img, target_width):
        """不論原圖尺寸(直圖/橫圖)一律縮放成固定寬度、維持長寬比，高度
        不裁切也不設上限，讓彈窗依實際圖片比例自動增高。"""
        w, h = pil_img.size
        scale = target_width / w
        new_height = max(1, round(h * scale))
        return pil_img.resize((target_width, new_height), Image.LANCZOS)

    def on_open_link(self):
        if self.link_url:
            webbrowser.open(self.link_url)


class AutoSentNotice(ctk.CTkToplevel):
    """全自動送出模式下的告知彈窗：純顯示用途，不需要使用者操作即可送出，
    但為了讓使用者知道確實發生了側翼攻擊（以及系統實際回了什麼、或因冷卻
    中而暫時沒送出），仍會彈出視窗告知，幾秒後自動關閉，也可以手動提早關閉。"""
    AUTO_CLOSE_MS = 8000

    def __init__(self, parent, author, content, reply, sent):
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = 480, 260
        self.geometry(f"{w}x{h}+{sw-w-20}+{sh-h-60}")
        status_color = ACCENT if sent else "#ffb020"
        self.configure(fg_color="#141a1a", border_color=status_color, border_width=2)

        status_text = "已自動送出反擊！" if sent else "偵測到側翼攻擊，冷卻中暫未送出"
        ctk.CTkLabel(
            self, text=status_text, font=("Arial", 17, "bold"), text_color=status_color
        ).pack(pady=(14, 6), padx=16, anchor="w")

        ctk.CTkLabel(
            self, text=f"{author}：{content}",
            font=("Arial", 14), text_color="#cfcfcf", wraplength=440, justify="left"
        ).pack(pady=(0, 8), padx=16, anchor="w")

        ctk.CTkLabel(
            self, text=f"回覆內容：{reply}",
            font=("Arial", 14), text_color=ACCENT, wraplength=440, justify="left"
        ).pack(pady=(0, 10), padx=16, anchor="w")

        ctk.CTkButton(
            self, text="關閉", font=FONT_BUTTON,
            fg_color="#444", hover_color="#555",
            command=self._safe_destroy, height=40
        ).pack(padx=16, pady=(4, 14), fill="x")

        self.after(self.AUTO_CLOSE_MS, self._safe_destroy)

    def _safe_destroy(self):
        try:
            self.destroy()
        except Exception:
            pass


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("直播小幫手：打擊青鳥人人有責")
        self.geometry("1140x760")
        self.minsize(980, 640)
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")

        self._rules_page = 0
        self._rules_page_size = 10
        self._history_page = 0
        self._history_page_size = 6

        self.config_mgr = ConfigManager()
        self.bot_manager = BotThreadManager(self.config_mgr, self.handle_bot_signal)
        self._setup_ttk_styles()
        self._setup_menu()
        self.setup_ui()
        self.after(5000, self._check_ads)

    def _create_context_menu(self, widget):
        """為輸入框建立右鍵菜單"""
        context_menu = tk.Menu(self, tearoff=0, bg="#1c2626", fg="white")
        context_menu.add_command(label="貼上", command=self._paste_url)

        def show_menu(event):
            try:
                context_menu.tk_popup(event.x_root, event.y_root)
            except:
                pass

        widget.bind("<Button-3>", show_menu)  # 右鍵

    def _setup_menu(self):
        """加入菜單欄，提供編輯功能（主要是貼上網址）"""
        menubar = tk.Menu(self, bg="#1c2626", fg="white", relief="flat")
        self.config(menu=menubar)

        edit_menu = tk.Menu(menubar, tearoff=0, bg="#1c2626", fg="white", relief="flat")
        menubar.add_cascade(label="編輯", menu=edit_menu)
        edit_menu.add_command(label="貼上網址 (Ctrl+V / Cmd+V)", command=self._paste_url)

    def _paste_url(self):
        """從剪貼簿貼上網址到「直播網址」輸入框（選單用）"""
        if not hasattr(self, 'entry_url'):
            messagebox.showwarning("提示", "找不到直播網址輸入框")
            return
        url = read_clipboard(self.entry_url)
        if not url:
            messagebox.showwarning("提示", "剪貼簿是空的，請先複製 YouTube 網址")
            return
        self.entry_url.delete(0, "end")
        self.entry_url.insert(0, url)

    def _check_ads(self):
        """定時檢查有沒有新的廣告推播，不管有沒有在監看直播都會執行。
        實際的網路請求(抓廣告清單+抓圖片)丟到背景執行緒，避免卡住UI。"""
        threading.Thread(target=self._check_ads_worker, daemon=True).start()
        self.after(AD_POLL_INTERVAL_MS, self._check_ads)

    def _check_ads_worker(self):
        new_ads = self.config_mgr.fetch_new_ads()
        for ad in new_ads:
            image_bytes = None
            image_url = ad.get("image_url")
            if image_url:
                try:
                    resp = requests.get(image_url, timeout=6)
                    if resp.status_code == 200:
                        image_bytes = resp.content
                except Exception:
                    pass
            self.after(0, lambda a=ad, img=image_bytes: AdPopUp(
                self, a.get("title", ""), img, a.get("link_url", "")
            ))

    def _setup_ttk_styles(self):
        """統一設定深色主題下的Treeview/Scrollbar樣式(ttk預設是淺色，跟整體風格衝突)"""
        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Rules.Treeview",
            background="#0a0f0f", fieldbackground="#0a0f0f", foreground=LOG_WHITE,
            rowheight=36, font=("Arial", 14), borderwidth=0
        )
        style.configure(
            "Rules.Treeview.Heading",
            background=BG_PANEL, foreground=ACCENT, font=("Arial", 15, "bold"), borderwidth=0
        )
        style.map("Rules.Treeview", background=[("selected", ACCENT_DIM)])

        style.configure(
            "Dark.Vertical.TScrollbar",
            background=SCROLL_BG, troughcolor=SCROLL_TROUGH, bordercolor=BG_PANEL,
            arrowcolor=ACCENT, gripcount=0, relief="flat"
        )
        style.map("Dark.Vertical.TScrollbar", background=[("active", ACCENT_DIM)])

    def setup_ui(self):
        """繪製整體介面：頂部品牌列 + 靠左頁籤列 + 內容區 + 底部跑馬燈"""
        top_frame = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=10)
        top_frame.pack(fill="x", padx=16, pady=(16, 8))

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

        # 靠左的頁籤按鈕列（自製，不用CTkTabview內建的置中拉伸樣式）
        tab_bar = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=10)
        tab_bar.pack(fill="x", padx=16, pady=(0, 8))

        btn_row = ctk.CTkFrame(tab_bar, fg_color="transparent")
        btn_row.pack(side="left", padx=8, pady=8)

        self.tab_names = ["直播監控", "防禦規則設定", "反擊建議", "歷史記錄"]
        self.tab_buttons = {}
        self.tab_frames = {}

        for name in self.tab_names:
            btn = ctk.CTkButton(
                btn_row, text=name, font=FONT_TAB, height=46, width=180,
                corner_radius=8, border_width=0,
                fg_color="transparent", hover_color="#243333", text_color="#9fd9d9",
                command=lambda n=name: self.switch_tab(n)
            )
            btn.pack(side="left", padx=4)
            self.tab_buttons[name] = btn

        # 底部跑馬燈：必須在 content_container 之前 pack（且用 side="bottom"），
        # 這樣它會先從視窗底部佔到自己需要的固定高度，視窗縮小時是
        # content_container（可伸縮區）先被壓縮，跑馬燈永遠不會被擠到消失。
        # 如果反過來讓 expand=True 的 content_container 先 pack，它會在
        # 排版當下就把整個剩餘空間吃光，跑馬燈排到後面就完全沒有空間可用。
        self.marquee = Marquee(
            self, get_messages=lambda: self.config_mgr.marquee_messages,
            get_speed_level=lambda: self.config_mgr.marquee_speed_level,
            fg_color=BG_PANEL, corner_radius=10
        )
        self.marquee.pack(side="bottom", fill="x", padx=16, pady=(0, 16))

        content_container = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=10)
        content_container.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        for name in self.tab_names:
            frame = ctk.CTkFrame(content_container, fg_color="transparent")
            self.tab_frames[name] = frame

        self.init_monitor_tab(self.tab_frames["直播監控"])
        self.init_rules_tab(self.tab_frames["防禦規則設定"])
        self.init_poison_pill_tab(self.tab_frames["反擊建議"])
        self.init_history_tab(self.tab_frames["歷史記錄"])

        self.switch_tab(self.tab_names[0])

    def switch_tab(self, name):
        for n, frame in self.tab_frames.items():
            frame.pack_forget()
        self.tab_frames[name].pack(fill="both", expand=True, padx=6, pady=6)
        for n, btn in self.tab_buttons.items():
            if n == name:
                btn.configure(fg_color=ACCENT, text_color="#0a0a0a", hover_color=ACCENT_HOVER)
            else:
                btn.configure(fg_color="transparent", text_color="#9fd9d9", hover_color="#243333")

    # ------------------------------------------------------------------
    # 分頁 1：直播監控
    # ------------------------------------------------------------------
    def init_monitor_tab(self, tab):
        frame_url = ctk.CTkFrame(tab, fg_color="transparent")
        frame_url.pack(fill="x", padx=8, pady=(14, 6))

        row1 = ctk.CTkFrame(frame_url, fg_color="transparent")
        row1.pack(fill="x")
        ctk.CTkLabel(row1, text="YouTube 直播網址", font=FONT_SECTION).pack(side="left")
        info_icon(
            row1,
            "貼上你要監看的 YouTube 直播網址（需包含 watch?v=）。\n"
            "預告直播（例如晚上八點才開播）也可以提前貼上網址啟動，\n"
            "系統會耐心等待直播真正開始，不需要等到開播才手動輸入。\n"
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

        # 貼上：用 Tk 原生剪貼簿的統一綁定（不依賴 pbpaste/pyperclip）
        bind_paste(self.entry_url)

        # 右鍵菜單作為備選
        self._create_context_menu(self.entry_url)

        # 順序：輸入框 → 貼上網址 → 啟動雷達 → 停止監控
        self.btn_paste = ctk.CTkButton(
            row2, text="貼上網址", command=self._paste_url, font=FONT_BUTTON,
            fg_color="#3a4a4a", hover_color="#4a5a5a", height=48, width=120
        )
        self.btn_paste.pack(side="left", padx=4)

        self.btn_start = ctk.CTkButton(
            row2, text="啟動雷達", command=self.start_monitoring, font=FONT_BUTTON,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a", height=48, width=140
        )
        self.btn_start.pack(side="left", padx=4)

        self.btn_stop = ctk.CTkButton(
            row2, text="停止監控", command=self.stop_monitoring, font=FONT_BUTTON,
            fg_color="#444", hover_color="#555", height=48, width=120, state="disabled"
        )
        self.btn_stop.pack(side="left", padx=4)

        row3 = ctk.CTkFrame(frame_url, fg_color="transparent")
        row3.pack(fill="x", pady=(10, 0))

        self.auto_send_var = ctk.BooleanVar(value=self.config_mgr.auto_send_enabled)
        self.auto_send_switch = ctk.CTkSwitch(
            row3, text="全自動送出模式", font=FONT_LABEL_BOLD,
            variable=self.auto_send_var, command=self.on_toggle_auto_send,
            progress_color=DANGER, button_color="#eee", button_hover_color="#fff"
        )
        self.auto_send_switch.pack(side="left")
        info_icon(
            row3,
            "開啟後，偵測到側翼攻擊留言且系統已找到對應的反擊回覆時，\n"
            "會直接自動送出到聊天室，不需要你手動確認——但仍會彈出一個\n"
            "告知視窗（幾秒後自動關閉），讓你知道發生了攻擊、系統回了什麼，\n"
            "回覆內容會自動加上「@留言者」明確指名對象。\n"
            "關閉（預設）時維持原本流程：彈窗顯示、你確認或編輯後手動按送出。\n\n"
            "不管開關與否、不管同時有多少留言或多少條規則命中，\n"
            "送出動作都受同一個10秒冷卻機制限制，不能拿來洗版聊天室。",
            side="left", padx=(10, 0)
        )

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
            "小窗上的回覆內容可以自行修改，按下「送出」後系統會直接打字並\n"
            "送出到聊天室——這是真的公開發言，請留意；若修改後的內容包含\n"
            "髒話或禁用詞，送出鍵會自動被停用、無法送出。\n"
            "直播結束或按下停止後，本場完整記錄會自動存到「歷史記錄」頁籤。\n\n"
            "沒看到彈跳視窗？可以點右邊「測試彈跳視窗」按鈕，\n"
            "先確認彈窗機制本身正常運作，再確認聊天室是否真的出現觸發關鍵字。",
            side="left", padx=(10, 0)
        )

        log_frame = ctk.CTkFrame(frame_log, fg_color="transparent")
        log_frame.pack(fill="both", expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=20, bg="#0a0f0f", fg=LOG_WHITE,
            insertbackground=LOG_WHITE, font=FONT_MONO, borderwidth=0, spacing1=4, spacing3=4
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.config(state="disabled")
        style_scrollbar(self.log_text)

        self.log_text.tag_config("ALERT", foreground="#ff4444", background="#1a0000")
        self.log_text.tag_config("SYSTEM", foreground="#8fb3b3")
        self.log_text.tag_config("NORMAL", foreground=LOG_WHITE)
        self.log_text.tag_config("AUTO_SENT", foreground=ACCENT, background="#0a1a1a")

    def on_toggle_auto_send(self):
        enabled = bool(self.auto_send_var.get())
        self.config_mgr.set_auto_send_enabled(enabled)
        self.append_log_system(
            "全自動送出模式已開啟：偵測到側翼攻擊會直接送出回覆，不再跳出確認小窗。"
            if enabled else "全自動送出模式已關閉：偵測到側翼攻擊會跳出小窗，需手動確認才送出。"
        )

    # ------------------------------------------------------------------
    # 分頁 2：防禦規則設定
    # ------------------------------------------------------------------
    def init_rules_tab(self, tab):
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

        list_header = ctk.CTkFrame(frame_list, fg_color="transparent")
        list_header.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(list_header, text="已建立規則", font=FONT_LABEL_BOLD).pack(side="left")
        info_icon(
            list_header,
            "雙擊任一列可查看該規則完整的關鍵字與反擊內容\n"
            "（表格內容太長時會被截斷顯示）。",
            side="left", padx=(10, 0)
        )

        tree_frame = ctk.CTkFrame(frame_list, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True)

        self.rules_tree = ttk.Treeview(
            tree_frame, columns=("no", "keywords", "replies", "source", "priority"), show="headings",
            style="Rules.Treeview", height=self._rules_page_size
        )
        self.rules_tree.heading("no", text="編號")
        self.rules_tree.heading("keywords", text="偵測關鍵字")
        self.rules_tree.heading("replies", text="反擊內容")
        self.rules_tree.heading("source", text="來源")
        self.rules_tree.heading("priority", text="優先層級")
        self.rules_tree.column("no", width=60, minwidth=50, anchor="center", stretch=False)
        self.rules_tree.column("keywords", width=420, minwidth=200, anchor="w", stretch=True)
        self.rules_tree.column("replies", width=420, minwidth=200, anchor="w", stretch=True)
        self.rules_tree.column("source", width=90, minwidth=80, anchor="center", stretch=False)
        self.rules_tree.column("priority", width=100, minwidth=90, anchor="center", stretch=False)

        scrollbar = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.rules_tree.yview,
            style="Dark.Vertical.TScrollbar"
        )
        self.rules_tree.configure(yscrollcommand=scrollbar.set)
        self.rules_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.rules_tree.tag_configure("cloud", foreground="#8fb3b3")
        self.rules_tree.tag_configure("user", foreground=ACCENT)

        self._rules_by_iid = {}
        self.rules_tree.bind("<Double-1>", self._on_rule_row_double_click)

        _, self.rules_page_label, self.rules_prev_btn, self.rules_next_btn, _ = pagination_bar(
            frame_list, self._rules_prev_page, self._rules_next_page,
            page_size_options=[10, 20, 50], on_size_change=self._rules_change_page_size,
            default_size=self._rules_page_size,
            fill="x", pady=(8, 0)
        )

        self.refresh_rules_display()

    def _rules_change_page_size(self, size):
        self._rules_page_size = size
        self._rules_page = 0
        self.rules_tree.configure(height=size)
        self.refresh_rules_display()

    def _rules_prev_page(self):
        if self._rules_page > 0:
            self._rules_page -= 1
            self.refresh_rules_display()

    def _rules_next_page(self):
        total_pages = max(1, -(-len(self.config_mgr.rules) // self._rules_page_size))
        if self._rules_page < total_pages - 1:
            self._rules_page += 1
            self.refresh_rules_display()

    # ------------------------------------------------------------------
    # 分頁 3：反擊建議
    # ------------------------------------------------------------------
    def init_poison_pill_tab(self, tab):
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
            tab, height=5, bg="#0a0f0f", fg="#ffe066", font=FONT_MONO, borderwidth=0,
            spacing1=4, spacing3=4
        )
        self.poison_pill_text.pack(fill="x", padx=8, pady=4)
        style_scrollbar(self.poison_pill_text)

        frame_buttons = ctk.CTkFrame(tab, fg_color="transparent")
        frame_buttons.pack(fill="x", padx=8, pady=12)

        ctk.CTkButton(
            frame_buttons, text="儲存反擊建議", command=self.save_poison_pills, font=FONT_BUTTON,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a", height=44, width=180
        ).pack(side="left", padx=4)

        cloud_header = ctk.CTkFrame(tab, fg_color="transparent")
        cloud_header.pack(fill="x", padx=8, pady=(10, 4))
        copy_icon(
            cloud_header, get_text=lambda: "\n".join(self.config_mgr.poison_pill_base),
            log_callback=self.append_log_system, side="left", padx=(0, 6)
        )
        ctk.CTkLabel(
            cloud_header, text="雲端預設反擊建議（唯讀預覽）",
            font=FONT_LABEL_BOLD, text_color="#888"
        ).pack(side="left")
        info_icon(
            cloud_header,
            "這是目前從雲端同步下來的預設反擊建議語句庫預覽，\n"
            "此區塊僅供參考，無法在此編輯；\n"
            "上方文字框儲存後會覆蓋你本機使用的語句庫。",
            side="left", padx=(10, 0)
        )

        self.cloud_pills_text = scrolledtext.ScrolledText(
            tab, height=16, bg="#121818", fg="#cfcfcf",
            font=FONT_MONO, borderwidth=0, state="disabled", spacing1=5, spacing3=5
        )
        self.cloud_pills_text.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        style_scrollbar(self.cloud_pills_text)

        self.refresh_cloud_pills_display()

    # ------------------------------------------------------------------
    # 分頁 4：歷史記錄
    # ------------------------------------------------------------------
    def init_history_tab(self, tab):
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

        self.history_scroll = ctk.CTkScrollableFrame(
            tab, fg_color="#0f1515",
            scrollbar_fg_color=SCROLL_TROUGH, scrollbar_button_color=SCROLL_BG,
            scrollbar_button_hover_color=ACCENT_DIM
        )
        self.history_scroll.pack(fill="both", expand=True, padx=8, pady=(6, 8))

        _, self.history_page_label, self.history_prev_btn, self.history_next_btn, _ = pagination_bar(
            tab, self._history_prev_page, self._history_next_page,
            page_size_options=[6, 12, 24], on_size_change=self._history_change_page_size,
            default_size=self._history_page_size,
            fill="x", padx=8, pady=(0, 12)
        )

        self.refresh_history_list()

    def _history_change_page_size(self, size):
        self._history_page_size = size
        self._history_page = 0
        self.refresh_history_list()

    def _history_prev_page(self):
        if self._history_page > 0:
            self._history_page -= 1
            self.refresh_history_list()

    def _history_next_page(self):
        total_pages = max(1, -(-self._history_total_count() // self._history_page_size))
        if self._history_page < total_pages - 1:
            self._history_page += 1
            self.refresh_history_list()

    def _history_total_count(self):
        if not os.path.isdir(LOGS_DIR):
            return 0
        return len(glob.glob(os.path.join(LOGS_DIR, "*.json")))

    def refresh_history_list(self):
        """掃描 logs/ 目錄，列出目前頁碼的歷史直播記錄"""
        for widget in self.history_scroll.winfo_children():
            widget.destroy()

        files = []
        if os.path.isdir(LOGS_DIR):
            files = sorted(glob.glob(os.path.join(LOGS_DIR, "*.json")), reverse=True)

        total = len(files)
        total_pages = max(1, -(-total // self._history_page_size))
        if self._history_page >= total_pages:
            self._history_page = max(0, total_pages - 1)

        start = self._history_page * self._history_page_size
        page_files = files[start:start + self._history_page_size]

        self.history_page_label.configure(text=f"第 {self._history_page + 1} / {total_pages} 頁（共 {total} 場）")
        self.history_prev_btn.configure(state="normal" if self._history_page > 0 else "disabled")
        self.history_next_btn.configure(state="normal" if self._history_page < total_pages - 1 else "disabled")

        if not page_files:
            ctk.CTkLabel(
                self.history_scroll, text="目前尚無歷史記錄，直播監控結束後會自動出現在這裡。",
                font=FONT_LABEL, text_color="#888"
            ).pack(anchor="w", padx=12, pady=12)
            return

        for path in page_files:
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

            btn_box = ctk.CTkFrame(row, fg_color="transparent")
            btn_box.pack(side="right", padx=14, pady=12)

            ctk.CTkButton(
                btn_box, text="刪除", width=90, height=44, font=FONT_BUTTON,
                fg_color=DANGER, hover_color="#cc3333",
                command=lambda p=path, t=title: self.delete_history_record(p, t)
            ).pack(side="right", padx=(8, 0))

            ctk.CTkButton(
                btn_box, text="查看完整記錄", width=150, height=44, font=FONT_BUTTON,
                fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a",
                command=lambda p=path: self.open_history_viewer(p)
            ).pack(side="right")

    def delete_history_record(self, path, title):
        if not messagebox.askyesno("確認刪除", f"確定要刪除這場記錄嗎？\n\n{title}\n\n此動作無法復原。"):
            return
        try:
            os.remove(path)
        except Exception as e:
            messagebox.showerror("錯誤", f"刪除失敗：{e}")
            return
        self.refresh_history_list()

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
        win.geometry("900x720")
        win.minsize(640, 480)
        win.attributes("-topmost", True)

        ctk.CTkLabel(
            win, text=data.get('title', '未命名直播'),
            font=FONT_SECTION, text_color=ACCENT
        ).pack(anchor="w", padx=18, pady=(16, 0))
        ctk.CTkLabel(
            win, text=f"開始時間：{data.get('started_at', '未知')}　網址：{data.get('video_url', '')}",
            font=FONT_LABEL, text_color="#8fb3b3"
        ).pack(anchor="w", padx=18, pady=(4, 10))

        search_row = ctk.CTkFrame(win, fg_color="transparent")
        search_row.pack(fill="x", padx=18, pady=(0, 10))

        search_entry = ctk.CTkEntry(search_row, height=42, font=FONT_ENTRY, placeholder_text="搜尋留言內容或留言者...")
        search_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        bind_paste(search_entry)

        search_state = {"matches": [], "current": -1}

        viewer = scrolledtext.ScrolledText(
            win, bg="#0a0f0f", fg=LOG_WHITE, font=FONT_MONO, borderwidth=0, spacing1=4, spacing3=4
        )
        viewer.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        viewer.tag_config("FLAGGED", foreground="#ff4444")
        viewer.tag_config("NORMAL", foreground=LOG_WHITE)
        viewer.tag_config("SEARCH_HIT", background="#ffe066", foreground="#0a0a0a")
        style_scrollbar(viewer)

        for msg in data.get("messages", []):
            line = f"[{msg.get('time', '')}] {msg.get('author', '')}: {msg.get('content', '')}\n"
            viewer.insert("end", line, "FLAGGED" if msg.get("flagged") else "NORMAL")

        viewer.config(state="disabled")

        result_label = ctk.CTkLabel(search_row, text="", font=FONT_LABEL, text_color="#8fb3b3")
        result_label.pack(side="left", padx=8)

        def run_search(event=None):
            query = search_entry.get().strip()
            viewer.tag_remove("SEARCH_HIT", "1.0", "end")
            search_state["matches"] = []
            search_state["current"] = -1

            if not query:
                result_label.configure(text="")
                return

            content = viewer.get("1.0", "end")
            start = 0
            while True:
                idx = content.find(query, start)
                if idx == -1:
                    break
                line_no = content.count("\n", 0, idx) + 1
                line_start = content.rfind("\n", 0, idx) + 1
                col = idx - line_start
                tk_start = f"{line_no}.{col}"
                tk_end = f"{line_no}.{col + len(query)}"
                viewer.tag_add("SEARCH_HIT", tk_start, tk_end)
                search_state["matches"].append(tk_start)
                start = idx + len(query)

            if search_state["matches"]:
                search_state["current"] = 0
                viewer.see(search_state["matches"][0])
                result_label.configure(text=f"共 {len(search_state['matches'])} 筆符合，第 1 筆")
            else:
                result_label.configure(text="找不到符合的內容")

        def jump_next(event=None):
            if not search_state["matches"]:
                return
            search_state["current"] = (search_state["current"] + 1) % len(search_state["matches"])
            idx = search_state["current"]
            viewer.see(search_state["matches"][idx])
            result_label.configure(text=f"共 {len(search_state['matches'])} 筆符合，第 {idx + 1} 筆")

        search_entry.bind("<Return>", lambda e: (run_search() if not search_state["matches"] else jump_next()))

        ctk.CTkButton(
            search_row, text="搜尋", command=run_search, font=FONT_BUTTON,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a", height=42, width=100
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            search_row, text="下一筆", command=jump_next, font=FONT_BUTTON,
            fg_color="#444", hover_color="#555", height=42, width=100
        ).pack(side="left", padx=4)

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
        # bot_manager.stop() 內含 thread.join(timeout=2)，會等監控執行緒跑完
        # 它的 finally: _save_session_log()——所以本場所有聊天記錄（含一般留言
        # 與側翼標記）在這裡回傳前就已存進歷史記錄。
        self.bot_manager.stop()
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.status_label.configure(text="狀態：待命", text_color="#888")
        # join 已等過存檔，這裡再稍延遲刷新，確保檔案系統寫入完成後列表最新
        self.after(300, self.refresh_history_list)
        self.after(1500, self.refresh_history_list)

    def handle_bot_signal(self, msg_type, data):
        if msg_type == "ALERT":
            self.append_log_highlight(f"[側翼攻擊] {data['author']}: {data['content']}")
            NotificationPopUp(
                self, data['author'], data['content'], data['reply'],
                self.handle_bot_signal, on_send=self.request_auto_send,
                forbidden_words=self.config_mgr.forbidden_words,
                cooldown_getter=self.bot_manager.get_cooldown_remaining
            )
        elif msg_type == "AUTO_SENT":
            sent = data.get("sent", False)
            status_label = "已自動送出" if sent else "冷卻中未送出"
            self.append_log(
                f"[全自動-{status_label}] {data['author']}: {data['content']} -> {data['reply']}",
                "AUTO_SENT"
            )
            AutoSentNotice(self, data['author'], data['content'], data['reply'], sent)
        elif msg_type == "NORMAL":
            self.append_log_normal(f"{data['author']}: {data['content']}")
        elif msg_type == "SYSTEM":
            self.append_log_system(data)

    def request_auto_send(self, text):
        """把使用者確認過的回覆文字交給bot背景執行緒自動打字送出聊天室"""
        self.bot_manager.request_send(text)

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
    def _rule_editor_dialog(self, title, initial_keywords="", initial_replies="",
                             initial_priority=False, on_save=None):
        """共用的新增/編輯規則表單"""
        dlg = ctk.CTkToplevel(self)
        dlg.title(title)
        dlg.geometry("620x560")
        dlg.minsize(480, 440)
        dlg.attributes("-topmost", True)

        priority_var = ctk.BooleanVar(value=initial_priority)

        # === 關鍵字欄位 ===
        label_keywords = ctk.CTkLabel(dlg, text="關鍵字 (用逗號分隔):", font=FONT_LABEL)
        label_keywords.pack(anchor="w", padx=14, pady=(14, 6))

        entry_keywords = ctk.CTkEntry(dlg, height=42, font=FONT_ENTRY, placeholder_text="例: 檳榔,哭文哲")
        entry_keywords.pack(padx=14, pady=4, fill="x")
        bind_paste(entry_keywords)
        entry_keywords.insert(0, initial_keywords)

        def update_keywords_state():
            """最優先規則時，關鍵字欄變只讀+提示"""
            if priority_var.get():
                entry_keywords.configure(state="disabled", text_color="#999999")
                entry_keywords.delete(0, "end")
                entry_keywords.insert(0, "不管什麼垃圾字，所有攻擊都用此訊息回覆")
            else:
                entry_keywords.configure(state="normal", text_color=TEXT_COLOR)
                entry_keywords.delete(0, "end")
                entry_keywords.insert(0, initial_keywords)

        # === 回覆草稿 ===
        ctk.CTkLabel(dlg, text="回覆草稿 (每行一句):", font=FONT_LABEL).pack(anchor="w", padx=14, pady=(10, 6))
        text_replies = scrolledtext.ScrolledText(dlg, height=8, font=FONT_MONO)
        text_replies.pack(padx=14, pady=4, fill="both", expand=True)
        text_replies.insert("1.0", initial_replies)
        style_scrollbar(text_replies)

        # === 最優先規則勾選框 ===
        priority_row = ctk.CTkFrame(dlg, fg_color="transparent")
        priority_row.pack(fill="x", padx=14, pady=(8, 0))
        priority_checkbox = ctk.CTkCheckBox(
            priority_row, text="設為最優先規則", font=FONT_LABEL, variable=priority_var,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, command=update_keywords_state
        )
        priority_checkbox.pack(side="left")
        info_icon(
            priority_row,
            "勾選後：只要偵測到任何側翼攻擊，系統會直接用此訊息回覆\n"
            "（不管命中的是哪條關鍵字規則）。\n"
            "未勾選：需自訂觸發關鍵字。",
            side="left", padx=(8, 0)
        )

        # 初始化狀態
        update_keywords_state()

        def do_save():
            # 若是最優先規則，直接用虛擬關鍵字（系統內部不會看）
            if priority_var.get():
                keywords = ["__priority_rule__"]  # 系統標記，不實際用來匹配
            else:
                keywords = [k.strip() for k in entry_keywords.get().split(",") if k.strip()]

            replies = [r.strip() for r in text_replies.get("1.0", "end").split("\n") if r.strip()]

            if not keywords or not replies:
                messagebox.showerror("錯誤", "回覆內容不能為空")
                return

            if not self.config_mgr.validate_custom_rule(keywords, replies):
                messagebox.showerror("安全性錯誤", "偵測到內容包含禁用詞彙，系統已拒絕儲存！")
                return

            on_save(keywords, replies, bool(priority_var.get()))
            dlg.destroy()

        ctk.CTkButton(dlg, text="儲存規則", command=do_save, font=FONT_BUTTON, height=44,
                       fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0a0a0a").pack(pady=14)

    def add_rule_dialog(self):
        def on_save(keywords, replies, is_priority):
            rule = Rule(
                id=str(uuid.uuid4()),
                trigger_keywords=keywords,
                match_type="contains",
                reply_pool=replies,
                is_enabled=True,
                is_priority=is_priority
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
        picker.minsize(420, 320)
        picker.attributes("-topmost", True)

        ctk.CTkLabel(picker, text="選擇要編輯的自訂規則：", font=FONT_LABEL).pack(anchor="w", padx=14, pady=12)

        scroll = ctk.CTkScrollableFrame(
            picker, fg_color="#0f1515",
            scrollbar_fg_color=SCROLL_TROUGH, scrollbar_button_color=SCROLL_BG,
            scrollbar_button_hover_color=ACCENT_DIM
        )
        scroll.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        def open_editor_for(rule):
            picker.destroy()

            def on_save(keywords, replies, is_priority):
                updated = Rule(
                    id=rule.id,
                    trigger_keywords=keywords,
                    match_type="contains",
                    reply_pool=replies,
                    is_enabled=rule.is_enabled,
                    is_priority=is_priority
                )
                self.config_mgr.update_rule(rule.id, updated)
                self.refresh_rules_display()
                messagebox.showinfo("成功", "規則已更新")

            self._rule_editor_dialog(
                "編輯規則",
                initial_keywords=", ".join(rule.trigger_keywords),
                initial_replies="\n".join(rule.reply_pool),
                initial_priority=getattr(rule, "is_priority", False),
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
        dlg.minsize(420, 320)
        dlg.attributes("-topmost", True)

        ctk.CTkLabel(dlg, text="選擇要刪除的自訂規則：", font=FONT_LABEL).pack(anchor="w", padx=14, pady=12)

        scroll = ctk.CTkScrollableFrame(
            dlg, fg_color="#0f1515",
            scrollbar_fg_color=SCROLL_TROUGH, scrollbar_button_color=SCROLL_BG,
            scrollbar_button_hover_color=ACCENT_DIM
        )
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
        self.rules_tree.delete(*self.rules_tree.get_children())
        self._rules_by_iid = {}
        user_rule_ids = {r.id for r in self.config_mgr.user_rules}

        def truncate(text, limit=60):
            return text if len(text) <= limit else text[:limit] + "..."

        all_rules = self.config_mgr.rules
        total = len(all_rules)
        total_pages = max(1, -(-total // self._rules_page_size))
        if self._rules_page >= total_pages:
            self._rules_page = max(0, total_pages - 1)

        start = self._rules_page * self._rules_page_size
        page_rules = list(enumerate(all_rules, start=1))[start:start + self._rules_page_size]

        for idx, rule in page_rules:
            is_user = rule.id in user_rule_ids
            keywords_str = truncate(", ".join(rule.trigger_keywords))
            replies_str = truncate(" / ".join(rule.reply_pool))
            source_label = "自訂" if is_user else "雲端"
            priority_label = "⭐最優先" if getattr(rule, "is_priority", False) else ""
            iid = self.rules_tree.insert(
                "", "end",
                values=(idx, keywords_str, replies_str, source_label, priority_label),
                tags=("user" if is_user else "cloud",)
            )
            self._rules_by_iid[iid] = rule

        self.rules_page_label.configure(text=f"第 {self._rules_page + 1} / {total_pages} 頁（共 {total} 條規則）")
        self.rules_prev_btn.configure(state="normal" if self._rules_page > 0 else "disabled")
        self.rules_next_btn.configure(state="normal" if self._rules_page < total_pages - 1 else "disabled")

    def _on_rule_row_double_click(self, event):
        iid = self.rules_tree.identify_row(event.y)
        rule = self._rules_by_iid.get(iid)
        if not rule:
            return

        win = ctk.CTkToplevel(self)
        win.title("規則詳細內容")
        win.geometry("620x520")
        win.minsize(480, 400)
        win.attributes("-topmost", True)

        ctk.CTkLabel(win, text="偵測關鍵字", font=FONT_LABEL_BOLD, text_color=ACCENT).pack(anchor="w", padx=16, pady=(16, 4))
        kw_box = scrolledtext.ScrolledText(win, height=6, font=FONT_MONO_SMALL, bg="#0a0f0f", fg=LOG_WHITE, borderwidth=0)
        kw_box.pack(fill="x", padx=16)
        kw_box.insert("1.0", "、".join(rule.trigger_keywords))
        kw_box.config(state="disabled")
        style_scrollbar(kw_box)

        ctk.CTkLabel(win, text="反擊內容", font=FONT_LABEL_BOLD, text_color=ACCENT).pack(anchor="w", padx=16, pady=(14, 4))
        rp_box = scrolledtext.ScrolledText(win, height=10, font=FONT_MONO_SMALL, bg="#0a0f0f", fg=LOG_WHITE, borderwidth=0)
        rp_box.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        rp_box.insert("1.0", "\n".join(rule.reply_pool))
        rp_box.config(state="disabled")
        style_scrollbar(rp_box)

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


def _remove_quarantine_on_macos():
    """macOS 第一次打開自簽應用時會被隔離，自動移除隔離屬性"""
    if sys.platform != "darwin":
        return
    try:
        app_path = os.path.dirname(sys.executable)
        # 如果是 .app bundle，app_path 會包含 .app 路徑
        if ".app" in app_path:
            app_bundle = app_path.split(".app")[0] + ".app"
            subprocess.run(
                ["xattr", "-d", "com.apple.quarantine", app_bundle],
                capture_output=True, timeout=2
            )
    except Exception:
        pass


if __name__ == "__main__":
    import socket

    _remove_quarantine_on_macos()

    # 防止重複啟動：試著佔用一個特定的 port（9876），若失敗表示已有實例運行
    try:
        lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lock_socket.bind(("127.0.0.1", 9876))
        lock_socket.listen(1)
    except OSError:
        messagebox.showerror(
            "應用已在執行",
            "直播小幫手已在運行中，請勿重複啟動多個實例。"
        )
        sys.exit(1)

    try:
        app = App()
        app.mainloop()
    finally:
        lock_socket.close()
