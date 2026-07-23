# MCTW Bot

多功能 Discord 機器人，支援跨伺服器訊息中繼、關鍵字回應、定時排程任務等功能。

## 功能模組

| 模組 | 說明 | 預設開關 |
|------|------|---------|
| **Relay** | 跨伺服器訊息橋接 — 支援文字頻道、討論串、論壇貼文的雙向同步 | ✅ 單一 bot profile 可啟用 |
| **Keywords** | 被動關鍵字回應 — 「你好/hello」「生日/birthday/hbd」 | ✅ 可關閉 |
| **Scheduler** | 定時任務 — 週五日落 gif、週日 21:00 圖片 | ✅ 可關閉 |
| **Moderation** | 頻道與成員管理，目前包含 Welcome Cleaner | ✅ 可關閉 |
| **Commands** | 基本指令模組，目前包含 `!ping` | ✅ 可關閉 |
| **Admin** | 管理員功能，目前包含 JSON 訊息控制 | ✅ 可關閉 |

## 快速開始

```bash
# 1. 安裝依賴
pip install -r requirements.txt

# 2. 設定環境變數
cp .env.example .env
# 編輯 .env，填入 config.json 中各 profile 指定的 bot token

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
| `BOT_TOKEN_ALLIANCE` | `alliance` bot profile 的 Token（範例，可自行改名） |
| `BOT_TOKEN_OPS` | `ops` bot profile 的 Token（範例，可自行改名） |
| `RELAY_QUEUE_DELAY_MS` | Webhook 發送間隔毫秒（預設 600） |
| `CONFIG_PATH` | 設定檔路徑（預設 `config.json`） |
| `DATABASE_PATH` | SQLite 資料庫路徑（預設 `data/database.db`） |

### `config.json` — 功能設定

#### `notifications`

接收管理員通知的 Discord 使用者 ID 陣列。通知會以私訊發送。

```json
"notifications": {
  "admin_user_ids": ["123456789012345678"]
}
```

#### `admin`

允許使用 Admin 類指令的 Discord 使用者 ID 陣列。具備伺服器 `Administrator` 權限的成員也可以使用。

```json
"admin": {
  "user_ids": ["123456789012345678"]
}
```

#### `bots`

可選的 bot profile 列表。每個 profile 對應一組 bot token，並用 `features` 決定要載入哪些 Cog。若未設定 `bots`，程式會使用舊版單 bot 模式：`DISCORD_TOKEN`，但建議新設定都使用 `bots`。

```json
"bots": [
  {
    "id": "alliance",
    "token_env": "BOT_TOKEN_ALLIANCE",
    "command_prefix": "!",
    "features": {
      "relay": true,
      "commands": true,
      "admin": true
    }
  },
  {
    "id": "ops",
    "token_env": "BOT_TOKEN_OPS",
    "command_prefix": "!",
    "features": {
      "relay": false,
      "keywords": true,
      "scheduler": true,
      "moderation": true,
      "commands": true,
      "admin": true
    }
  }
]
```

`relay` 同一時間只能在一個 profile 啟用，避免多個 bot 同時處理同一批中繼事件。

`features` 使用大分類控制 Cog 載入，所有功能預設都是 `false`，需要的模組必須在各 profile 中明確啟用。細項設定放在各分類自己的區塊，例如 `moderation.welcome_cleaner`。

> `commands` 目前只載入 `ping`；`admin` 是獨立 feature，專門處理管理員功能。

#### `commands`

Commands 類功能目前提供基本指令：

```text
!ping
```

#### `admin`

Admin 類功能目前提供 JSON 訊息控制，只有 `admin.user_ids` 內的使用者或伺服器 Administrator 可以使用：

```text
!msg send #channel {"content":"文字內容"}
!msg edit message_id {"content":"新文字內容"}
!msg delete message_id
!msg source message_id
!announce group_name {"content":"公告內容"}
```

所有訊息都使用同一種 JSON 格式：

```json
{
  "content": "今天 21:00 開會",
  "embeds": [
    {
      "title": "公告",
      "description": "請準時到語音頻道",
      "color": "#5865F2",
      "fields": [
        {
          "name": "地點",
          "value": "語音頻道",
          "inline": true
        }
      ]
    }
  ]
}
```

`source` 會輸出指定訊息的 JSON，方便複製後微調再用 `edit`。`announce` 會把同一份 JSON 發送到指定 relay group 的所有一般文字頻道，論壇頻道會略過。`edit` / `delete` 只會操作同一隻 bot 自己發出的訊息。

#### `keywords`

Keywords 類功能的細項設定（需在 profile 開啟 `keywords`）：

```json
"keywords": {
  "hello": {
    "enabled": true
  },
  "birthday": {
    "enabled": true
  }
}
```

#### `moderation`

Moderation 類功能的細項設定（需在 profile 開啟 `moderation`）：

```json
"moderation": {
  "welcome_cleaner": {
    "enabled": true,
    "channels": ["1015827632731996251"]
  }
}
```

#### `scheduler`

Scheduler 類功能的細項設定（需在 profile 開啟 `scheduler`）：

```json
"scheduler": {
  "friday_night": {
    "enabled": true,
    "channels": ["1349540882369478688"]
  },
  "sunday_night": {
    "enabled": true,
    "channels": ["1349540882369478688"]
  }
}
```

#### `relay` — 跨伺服器中繼設定

| 欄位 | 說明 |
|------|------|
| `prune_days` | relayed_messages 資料表清理天數（預設 7） |
| `groups` | 中繼群組列表，每個群組包含多個頻道 |

##### 頻道設定

| 欄位 | 必要 | 說明 |
|------|------|------|
| `channel_id` | ✅ 必填 | 頻道 ID（文字頻道或論壇頻道的母頻道 ID） |
| `direction` | ✅ 必填 | 同步方向：`BOTH` / `SEND_ONLY` / `RECEIVE_ONLY` |
| `brand_name` | ❌ 選填 | 顯示的名稱標籤，留空則自動帶入伺服器名稱 |
| `process_bot_messages` | ❌ 選填 | 是否轉發其他 bot 的訊息（預設 `false`） |
| `allow_forward_delete` | ❌ 選填 | 原始訊息刪除時是否同步刪除中繼副本（預設 `true`） |
| `allow_reverse_delete` | ❌ 選填 | 中繼副本被刪除時是否反刪原始訊息（預設 `false`） |

##### 角色映射

跨伺服器 @提及 角色對應。例如所有伺服器都有一個 `@K30`：

```json
"role_mappings": [
  {
    "group_name": "main",
    "guild_id": "333333333333333333",
    "role_id": "444444444444444444",
    "common_name": "K30"
  },
  {
    "group_name": "main",
    "guild_id": "555555555555555555",
    "role_id": "666666666666666666",
    "common_name": "K30"
  }
]
```

`role_mappings` 放在 `relay` 下，與 `groups` 同層。若只有一個 relay group，`group_name` 可省略；多 group 時請明確指定。

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
├── main.py              ← 啟動入口，建立 bot profiles 並註冊各模組
├── run.py               ← python run.py 啟動腳本
├── app/
│   ├── bot_profiles.py      ← 多 bot token profile 載入與驗證
│   ├── config.py            ← 讀取 .env
│   ├── config_validator.py  ← 啟動早期驗證 config.json
│   └── config_sync.py       ← 讀取 config.json → SQLite
├── data/                ← SQLite runtime 檔案（不進 git）
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
    ├── admin/           ← 管理員指令
    └── commands/        ← 基本指令
```

## 注意事項

- 討論串和論壇貼文的中繼需要 bot 有「管理討論串」權限
- Relay 功能只能在一個 bot profile 啟用
- 啟動時會先驗證 `config.json`，設定格式錯誤會直接中止啟動
- 設定檔修改後需重啟 bot 才會生效
