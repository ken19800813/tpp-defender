# 🚀 快速開始指南

## 📥 第一步：環境準備

```bash
cd /Users/huangjianming/Documents/0_宏康智慧/OTDR/TPPchat

# 安裝 Python 依賴
pip install -r requirements.txt

# 安裝 Playwright 瀏覽器驅動
python -m playwright install chromium
```

## ▶️ 第二步：執行應用

```bash
python main.py
```

應該會看到一個 CustomTkinter UI 視窗彈出。

## 🏗️ 第三步：打包成執行檔（可選）

### macOS (.app)

```bash
pip install pyinstaller
pyinstaller build.spec
```

輸出位置：`dist/TPPchat.app`

### Windows (.exe)

```bash
pip install pyinstaller
pyinstaller --windowed --onefile --name TPPchat main.py
```

輸出位置：`dist/TPPchat.exe`

## 🎬 第四步：測試應用

1. 開啟 TPPchat
2. 進入「直播監控」頁籤
3. 貼入一個 YouTube 直播網址（例如：`https://www.youtube.com/watch?v=...`）
4. 點擊「🚀 啟動雷達」
5. 會開啟一個新的 Chromium 瀏覽器視窗，顯示直播聊天室
6. 當有人在聊天室發送符合「防禦規則」的關鍵字時，右下角會彈出提醒小窗
7. 點擊「📋 複製」或按空白鍵，建議回覆會複製到剪貼簿
8. 手動粘貼到YouTube聊天框、自己按Enter發送

## ⚙️ 自訂規則

1. 進入「防禦規則設定」頁籤
2. 點擊「➕ 新增規則」
3. 輸入要監聽的關鍵字（用逗號分隔）
4. 輸入對應的回覆草稿（每行一則）
5. 系統會檢查是否包含禁用詞
6. 點擊「💾 儲存規則」

## 🔗 設定 GitHub 規則源

編輯 `config.py` 第 3 行：

```python
REMOTE_SECURITY_URL = "https://raw.githubusercontent.com/您的帳號/tpp-defender/main/security_rules.json"
```

然後在 GitHub 建立該 JSON 檔案，應用會自動同步最新規則。

## 📝 常見問題

**Q: 無法開啟聊天室怎麼辦？**
A: 確保瀏覽器已登入 Google 帳號，且直播連結正確。

**Q: 為什麼沒有彈出提醒？**
A: 檢查規則是否已啟用，以及聊天室留言是否包含觸發關鍵字。

**Q: 如何停止監聽？**
A: 點擊「⏹️ 停止」按鈕，或關閉 Chromium 瀏覽器視窗。

**Q: 能否自動發言？**
A: 不行，設計上完全不自動操作YouTube。所有發言由使用者手動粘貼和發送。

## 🔒 隱私安全

- ✅ 所有資料存在本機
- ✅ 不上傳任何個人資訊
- ✅ 完全開源，無隱藏代碼
- ✅ 不使用加壳、混淆或反逆向工程技術

---

**需要幫助？**
- 閱讀 README.md 取得完整文檔
- 檢查 security_rules.json 瞭解規則格式
- 在 config.py 中調整 GitHub 規則源
