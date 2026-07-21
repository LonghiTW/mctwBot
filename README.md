# MCTW Bot

多功能 Discord 機器人，支援跨伺服器訊息中繼、關鍵字回應、定時排程任務等功能。

## 功能模組

| 模組 | 說明 | 預設開關 |
|------|------|---------|
| **Relay** (永遠啟用) | 跨伺服器訊息橋接 — 支援文字頻道、討論串、論壇貼文的雙向同步 | ✅ 強制 |
| **Keyword Responder** | 被動關鍵字回應 — 「你好/hello」「生日/birthday/hbd」 | ✅ 可關閉 |
| **Scheduler** | 定時任務 — 週五日落 gif、週日 21:00 圖片 | ✅ 可關閉 |
| **Welcome Cleaner** | 成員離開時自動刪除歡迎訊息 | ✅ 可關閉 |
| **Ping** | `!ping` 基本存活確認指令 | ✅ 可關閉 |

## 快速開始

```bash
# 1. 安裝依賴
pip install -r requirements.txt

# 2. 設定環境變數
cp .env.example .env
# 編輯 .env，填入 DISCORD_TOKEN 和 CLIENT_ID

# 3. 建立設定檔
cp config.json.example config.json
# 編輯 config.json，填入頻道 ID 與功能設定

# 4. 啟動
python run.py
```

## 設定檔說明

### `.env` — 環境變數

| 變數 | 說明 |
|------|------|
| `DISCORD_TOKEN` | Discord Bot Token（必填） |
| `CLIENT_ID` | Discord Bot Client ID（必填） |
| `RELAY_QUEUE_DELAY_MS` | Webhook 發送間隔毫秒（預設 600） |
| `CONFIG_PATH` | 設定檔路徑（預設 `config.json`） |

### `config.json` — 功能設定

#### `admin_user_ids`

接收管理員通知的 Discord 使用者 ID 陣列。通知會以私訊發送。

```json
"admin_user_ids": ["123456789012345678"]
```

#### `features`

各功能的開關：

```json
"features": {
  "keyword_responder": true,
  "scheduler": true,
  "welcome_cleaner": true,
  "ping_command": true
}
```

#### `welcome_channels`

Welcome Cleaner 監聽的歡迎頻道 ID 陣列（需開啟 `welcome_cleaner`）：

```json
"welcome_channels": ["1015827632731996251"]
```

#### `scheduler_channels`

定時任務的發送頻道：

```json
"scheduler_channels": {
  "friday_night": ["1349540882369478688"],
  "sunday_night": ["1349540882369478688"]
}
```

#### `relay` — 跨伺服器中繼設定

| 欄位 | 說明 |
|------|------|
| `prune_days` | relayed_messages 資料表清理天數（預設 7） |
| `groups` | 中繼群組列表，每個群組包含多個頻道 |

##### 頻道設定

| 欄位 | 說明 |
|------|------|
| `channel_id` | 頻道 ID（文字頻道或論壇頻道的母頻道 ID） |
| `direction` | 同步方向：`BOTH` / `SEND_ONLY` / `RECEIVE_ONLY` |
| `brand_name` | 顯示的名稱標籤，例如「Server A」 |
| `process_bot_messages` | 是否轉發其他 bot 的訊息 |
| `allow_forward_delete` | 原始訊息刪除時是否同步刪除中繼副本 |
| `allow_reverse_delete` | 中繼副本被刪除時是否反刪原始訊息 |

##### 角色映射

跨伺服器 @提及 角色對應。例如所有伺服器都有一個 `@K30`：

```json
"role_mappings": [
  {
    "guild_id": "333333333333333333",
    "role_id": "444444444444444444",
    "common_name": "K30"
  },
  {
    "guild_id": "555555555555555555",
    "role_id": "666666666666666666",
    "common_name": "K30"
  }
]
```

## 權限需求

機器人需要在每一個設定的頻道具備以下權限：

- 檢視頻道
- 發送訊息
- 讀取訊息歷史
- 管理 Webhook
- 建立公開討論串
- 在討論串中發送訊息
- 管理討論串（用於鎖定/封存/刪除同步）

## 專案結構

```
Bot/
├── main.py              ← 啟動入口，註冊各模組
├── run.py               ← python run.py 啟動腳本
├── config.py            ← 讀取 .env
├── config_sync.py       ← 讀取 config.json → SQLite
├── database/
│   └── database.py      ← SQLite + migration
├── utils/
│   ├── log_manager.py
│   ├── time_utils.py
│   └── admin_notifier.py
└── cogs/
    ├── relay/           ← 跨伺服器中繼（整包為一個 Cog）
    ├── keywords/        ← 關鍵字被動回應
    ├── scheduler/       ← 定時任務
    ├── moderation/      ← 頻道管理
    └── commands/        ← 基本指令
```

## 注意事項

- 討論串和論壇貼文的中繼需要 bot 有「管理討論串」權限
- 設定檔修改後需重啟 bot 才會生效
