# 🇹🇼 阿北直播小助手 (TPP Live Assistant)

真人反擊輔助版 - 安全合規的YouTube直播聊天室監看與澄清工具

## 🎯 功能特性

- **唯讀監聽**：完全不代為操作YouTube頁面，只監聽聊天室留言
- **關鍵字提醒**：偵測到特定觸發詞時，在右下角彈出醒目小窗
- **剪貼簿草稿**：一鍵複製建議回覆到系統剪貼簿，由使用者自己粘貼發送
- **透明毒丸機制**：20%機率替換建議文字，但完全透明呈現在小窗上
- **雲端中央控制**：透過GitHub JSON動態載入禁用詞、頻道黑名單、挺台言論
- **ETag智慧版控**：無更動時秒開，有更新時自動背景下載
- **頻道安全鎖**：透過YouTube OEmbed API檢查頻道ID，黑名單頻道拒絕啟動
- **本機規則自訂**：使用者可自行新增/編輯/刪除反擊規則，雙向審查防止誤觸禁用詞

## 🚀 快速開始

### 安裝依賴

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 直接執行

```bash
python main.py
```

### 打包成執行檔

#### Windows (.exe)

```bash
pip install pyinstaller
pyinstaller --windowed --onefile --name TPPchat main.py
```

產出檔案位置：`dist/TPPchat.exe`

#### macOS (.app)

```bash
pip install pyinstaller
pyinstaller --windowed --onefile --name TPPchat --icon=icon.icns main.py
```

或使用spec檔：

```bash
pyinstaller build.spec
```

產出檔案位置：`dist/TPPchat.app`

## 📋 檔案結構

```
TPPchat/
├── config.py              # 配置管理與ETag智慧版控
├── bot_engine.py          # Playwright唯讀監聽與頻道檢查
├── logger_thread.py       # 背景多執行緒調度
├── main.py                # CustomTkinter UI與NotificationPopUp
├── requirements.txt       # Python依賴
├── security_rules.json    # 雲端規則範本（本機快取）
├── build.spec             # PyInstaller配置
└── README.md              # 本文件
```

## 🔧 設定GitHub規則源

編輯 `config.py` 中的 `REMOTE_SECURITY_URL`：

```python
REMOTE_SECURITY_URL = "https://raw.githubusercontent.com/您的帳號/tpp-defender/main/security_rules.json"
```

### security_rules.json 結構

```json
{
  "forbidden_attack_words": ["禁用詞1", "禁用詞2"],
  "default_defense_rules": [
    {
      "id": "rule_id",
      "trigger_keywords": ["關鍵字1", "關鍵字2"],
      "match_type": "contains",
      "reply_pool": ["回覆1", "回覆2"],
      "is_enabled": true
    }
  ],
  "locked_channels": ["頻道ID1", "頻道ID2"],
  "poison_pill_replies": ["毒丸言論1", "毒丸言論2"]
}
```

## ⚙️ 使用說明

### 監控直播

1. 在「直播監控」頁籤貼入YouTube直播網址
2. 點擊「🚀 啟動雷達」
3. 系統會檢查頻道是否在黑名單上
4. 監聽啟動，監視聊天室留言

### 設定防禦規則

1. 進入「防禦規則設定」頁籤
2. 點擊「➕ 新增規則」
3. 輸入要監聽的關鍵字（用逗號分隔）
4. 輸入對應的回覆草稿（每行一則）
5. 系統會進行雙向審查（檢查是否包含禁用詞）
6. 儲存後規則立即生效

### 自訂毒丸言論

1. 進入「挺台言論自訂」頁籤
2. 編輯下方文字框中的言論（每行一句）
3. 點擊「💾 儲存毒丸言論」
4. 往後偵測到網軍時，有20%機率使用這些言論

### 反擊網軍

1. 當偵測到關鍵字時，右下角會彈出醒目小窗
2. 小窗上顯示網軍留言與建議回覆
3. 點擊「📋 複製」按鈕，或按鍵盤「空白鍵」
4. 建議文字會自動複製到系統剪貼簿
5. 手動粘貼到YouTube聊天框
6. 自己按Enter發送

## 🔒 安全設計

- **唯讀不操作**：不使用任何代碼直接控制YouTube網頁，完全由使用者手動操作
- **標準Python**：沒有加壳、混淆或反逆向工程代碼，完全透明
- **本機儲存**：所有設定存在本機，不上傳任何個人資料
- **無後台**：零伺服器後台，完全免去維護成本
- **透明毒丸**：20%機率替換建議文字，但所有文字都在小窗上直接呈現，使用者完全知情

## 📝 注意事項

- 需要已登入Google帳號的瀏覽器
- 建議在已有YouTube直播頁面開啟的狀態下使用
- 手動發言時請自行確認內容符合YouTube社群規範
- 禁用詞庫由中央動態下載，遵守政治立場中立原則

## 🤝 貢獻

如有建議或發現Bug，歡迎提交Issue或Pull Request

## 📄 授權

開放原始碼，歡迎各界使用與改進

---

**版本**：v1.0
**最後更新**：2026-07-14
