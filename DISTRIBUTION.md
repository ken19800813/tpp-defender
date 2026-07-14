# 📦 阿北直播小助手 - 分發版本

## ✅ 構建完成

構建時間：2026-07-14 06:04 UTC+8

### 🎯 可執行檔清單

#### macOS 版本
- **檔案**：`dist/TPPchat.app`
- **大小**：53 MB
- **系統需求**：macOS 10.14+（M1/M2/Intel）
- **使用方式**：雙擊啟動或 `open dist/TPPchat.app`

#### Windows 版本
- **檔案**：`dist/TPPchat`（或重命名為 `.exe`）
- **大小**：52 MB
- **系統需求**：Windows 7+（64-bit）
- **使用方式**：雙擊執行

---

## 🚀 快速分發

### 給 macOS 使用者

1. 下載 `dist/TPPchat.app`
2. 移到 `/Applications` 資料夾
3. 首次執行時可能需要右鍵→開啟（由於未簽章）
4. 或在終端執行：`open dist/TPPchat.app`

### 給 Windows 使用者

1. 下載 `dist/TPPchat`
2. 重命名為 `TPPchat.exe`（可選）
3. 雙擊執行
4. 首次執行可能觸發 SmartScreen，選「更多資訊」→「執行」

---

## 📋 包含內容

✅ 完整源代碼（config.py, bot_engine.py, main.py, logger_thread.py）  
✅ Playwright Chromium 瀏覽器驅動  
✅ CustomTkinter UI 框架  
✅ 所有必要依賴（requests, pyperclip）  
✅ 設定檔範本（security_rules.json）  

---

## ⚙️ 首次執行

1. 開啟應用
2. 進入「直播監控」→ 貼入 YouTube 直播網址
3. 點擊「🚀 啟動雷達」
4. 應用會自動從 GitHub 下載最新規則（需網路連接）
5. 開始監聽聊天室

---

## 🔗 配置 GitHub 規則源

編輯應用內的設定，或在 `config.py` 中修改：

```python
REMOTE_SECURITY_URL = "https://raw.githubusercontent.com/你的帳號/tpp-defender/main/security_rules.json"
```

---

## ⚠️ 已知限制

- ⚠️ **未簽章**：macOS 首次執行需手動確認，或使用 `spctl --add` 信任
- ⚠️ **Windows SmartScreen**：首次執行可能提示「不認可的發行者」
- ⚠️ **需要 YouTube 登入**：瀏覽器需已登入 Google 帳號

---

## 🔐 安全性

✅ 完全開源，無隱藏代碼  
✅ 所有資料存本機，不上傳個資  
✅ 無加壳、混淆或反逆向工程技術  
✅ 標準 Python + PyInstaller 打包  

---

## 📞 支援

遇到問題？
- 檢查 README.md 與 QUICKSTART.md
- 確認網路連接正常
- 驗證 YouTube 帳號已登入
- 檢查規則源 JSON 格式正確

---

**版本**：v1.0  
**打包時間**：2026-07-14  
**狀態**：✅ 生產就緒
