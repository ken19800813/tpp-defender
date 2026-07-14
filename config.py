import json
import os
import base64
import requests
from dataclasses import dataclass, asdict
from typing import List

# GitHub URL Base64編碼（防止易被解讀）- ken19800813/tpp-defender
REMOTE_SECURITY_URL_B64 = "aHR0cHM6Ly9yYXcuZ2l0aHVidXNlcmNvbnRlbnQuY29tL2tlbjE5ODAwODEzL3RwcC1kZWZlbmRlci9tYWluL3NlY3VyaXR5X3J1bGVzLmpzb24="
REMOTE_SECURITY_URL = base64.b64decode(REMOTE_SECURITY_URL_B64).decode()
CACHE_FILE = "security_cache.json"
VERSION_INFO_FILE = "security_version.json"

# 黑名單頻道、防禦關鍵字規則、跑馬燈文字改由 LINEBOT 後台的
# 「直播小幫手」獨立模組統一編輯與更新(/ken_admin/livestream/*)，
# GitHub security_rules.json 只再保留 forbidden_attack_words 跟
# poison_pill_replies(反擊建議)兩項。
LIVESTREAM_CONFIG_API_URL = "https://line-news-0p7m.onrender.com/api/livestream/config"
LIVESTREAM_CACHE_FILE = "livestream_cache.json"


@dataclass
class Rule:
    id: str
    trigger_keywords: List[str]
    match_type: str
    reply_pool: List[str]
    is_enabled: bool = True


@dataclass
class SafetySettings:
    user_data_dir: str = "./user_data"


class ConfigManager:
    def __init__(self, filepath="config.json"):
        self.filepath = filepath
        self.settings = SafetySettings()
        self.rules: List[Rule] = []
        self.user_rules: List[Rule] = []
        self.forbidden_words: List[str] = []
        self.default_rules_data: List[dict] = []
        self.locked_channels: List[str] = []
        self.poison_pill_base: List[str] = []
        self.marquee_messages: List[str] = []

        self.fetch_remote_rules()
        self.fetch_livestream_config()
        self.load()
        self._rebuild_rules()

    def fetch_remote_rules(self):
        """智慧 ETag 版控：有更動才下載，否則使用本機快取秒開
        關閉 gzip 協商是為了避開 raw.githubusercontent.com 的 CDN 對
        gzip 壓縮版本的快取變體，該變體在剛 push 後常有數分鐘的延遲，
        會導致抓到比 curl 看到的還舊的內容。檔案本身很小，不壓縮也無影響。"""
        headers = {"Accept-Encoding": "identity"}
        if os.path.exists(VERSION_INFO_FILE):
            try:
                with open(VERSION_INFO_FILE, "r", encoding="utf-8") as f:
                    version_data = json.load(f)
                    if "ETag" in version_data:
                        headers["If-None-Match"] = version_data["ETag"]
            except Exception:
                pass

        try:
            res = requests.get(REMOTE_SECURITY_URL, headers=headers, timeout=4)
            if res.status_code == 304:
                self._load_from_local_cache()
                return
            elif res.status_code == 200:
                data = res.json()
                self.forbidden_words = data.get("forbidden_attack_words", [])
                self.poison_pill_base = data.get("poison_pill_replies", [])

                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)

                with open(VERSION_INFO_FILE, "w", encoding="utf-8") as f:
                    version_info = {}
                    if "ETag" in res.headers:
                        version_info["ETag"] = res.headers["ETag"]
                    if "Last-Modified" in res.headers:
                        version_info["Last-Modified"] = res.headers["Last-Modified"]
                    json.dump(version_info, f, indent=4)
                return
        except Exception:
            pass

        self._load_from_local_cache()

    def fetch_livestream_config(self):
        """黑名單頻道、防禦關鍵字規則、跑馬燈文字統一從 LINEBOT 後台的
        「直播小幫手」模組同步。連線失敗時改用本機快取，避免整個空白。"""
        try:
            res = requests.get(LIVESTREAM_CONFIG_API_URL, timeout=4)
            if res.status_code == 200:
                data = res.json()
                if data.get("success"):
                    self.locked_channels = data.get("locked_channels", [])
                    self.default_rules_data = data.get("default_defense_rules", [])
                    self.marquee_messages = data.get("marquee_messages", [])
                    with open(LIVESTREAM_CACHE_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=4)
                    return
        except Exception:
            pass

        self._load_livestream_from_local_cache()

    def _load_livestream_from_local_cache(self):
        if os.path.exists(LIVESTREAM_CACHE_FILE):
            try:
                with open(LIVESTREAM_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.locked_channels = data.get("locked_channels", [])
                    self.default_rules_data = data.get("default_defense_rules", [])
                    self.marquee_messages = data.get("marquee_messages", [])
            except Exception:
                pass

    def _load_from_local_cache(self):
        """從本機快取載入 GitHub 端規則（forbidden_attack_words / poison_pill_replies）"""
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.forbidden_words = data.get("forbidden_attack_words", [])
                    self.poison_pill_base = data.get("poison_pill_replies", [])
            except Exception:
                pass

    def load(self):
        """載入本機自訂規則（僅使用者自行新增的規則，不含雲端預設規則）"""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    self.user_rules = [Rule(**r) for r in d.get("rules", [])]
            except Exception:
                self.user_rules = []
        else:
            self.user_rules = []

    def _rebuild_rules(self):
        """合併雲端預設規則與使用者自訂規則，雲端規則更新時會自動反映在這裡"""
        cloud_rules = [Rule(**r) for r in self.default_rules_data]
        self.rules = cloud_rules + self.user_rules

    def validate_custom_rule(self, user_keywords: List[str], user_replies: List[str]) -> bool:
        """雙向審查：若輸入內容包含中央禁用詞，則攔截拒絕儲存"""
        for word in self.forbidden_words:
            for kw in user_keywords:
                if word in kw:
                    return False
            for rp in user_replies:
                if word in rp:
                    return False
        return True

    def save(self):
        """存檔本機自訂規則（僅使用者新增的部分，雲端預設規則不寫入本機檔案）"""
        os.makedirs(os.path.dirname(self.filepath) or ".", exist_ok=True)
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(
                {"rules": [asdict(r) for r in self.user_rules]},
                f,
                indent=4,
                ensure_ascii=False
            )

    def add_rule(self, rule: Rule):
        """新增使用者自訂規則"""
        self.user_rules.append(rule)
        self._rebuild_rules()
        self.save()

    def delete_rule(self, rule_id: str):
        """刪除使用者自訂規則（雲端預設規則無法在此刪除）"""
        self.user_rules = [r for r in self.user_rules if r.id != rule_id]
        self._rebuild_rules()
        self.save()

    def update_rule(self, rule_id: str, updated_rule: Rule):
        """更新使用者自訂規則"""
        self.user_rules = [updated_rule if r.id == rule_id else r for r in self.user_rules]
        self._rebuild_rules()
        self.save()
