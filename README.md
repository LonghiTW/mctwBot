# MCTW Bot

多功能 Discord 機器人，支援跨伺服器訊息中繼、關鍵字回應、定時排程任務等功能。

## 功能模組

| 模組 | 說明 | 預設開關 |
|------|------|---------|
| **Relay** | 跨伺服器訊息橋接 — 支援文字頻道、討論串、論壇貼文的雙向同步；並提供 relay 管理指令（`!reload`、`!announce`、`!relaylist`） | ✅ 單一 bot profile 可啟用 |
| **Keywords** | 被動關鍵字回應 — 「你好/hello」「生日/birthday/hbd」 | ✅ 可關閉 |
| **Scheduler** | 定時任務 — 週五日落 gif、週日 21:00 圖片 | ✅ 可關閉 |
| **Moderation** | 頻道與成員管理，目前包含 Welcome Cleaner | ✅ 可關閉 |
| **Commands** | 基本指令模組，目前包含 `!ping` | ✅ 可關閉 |
| **Admin** | 管理員功能，目前包含 JSON 訊息控制（`!msg` 系列） | ✅ 可關閉 |

## 快速開始

```bash
# 1. 安裝依賴
pip install -r requirements.txt

# 2. 設定環境變數
cp .env.example .env
# 編輯 .env，填入 config.json 中各 profile 指定的 bot token

# 3. 建立設定檔
cp config.json.example config.json
# 編輯 config.json，填入 bot profiles 與 relay 等全域設定

# 4. 啟動
python run.py

# 5. 單服功能設定
# 首次啟動後會自動在 config.guilds/ 產生各伺服器 ID 的設定檔
# 也可以參考 config.guild.json.example 手動建立
```

## 設定檔說明

### `.env` — 環境變數

| 變數 | 說明 |
|------|------|
| `BOT_TOKEN_ALLIANCE` | `alliance` bot profile 的 Token（範例，可自行改名） |
| `BOT_TOKEN_OPS` | `ops` bot profile 的 Token（範例，可自行改名） |
| `RELAY_QUEUE_DELAY_MS` | Webhook 發送間隔毫秒（預設 600） |
| `CONFIG_PATH` | 設定檔路徑（預設 `config.json`） |
| `GUILD_CONFIG_DIR` | 單服設定檔目錄（預設 `config.guilds`） |
| `DATABASE_PATH` | SQLite 資料庫路徑（預設 `data/database.db`） |

### `config.json` — 全域功能設定

`config.json` 控制 bot profile、全域管理員、通知與跨服 relay。單一伺服器功能（keywords、scheduler、moderation）的細項設定不放在這裡，改由 `config.guilds/{guild_id}.json` 控制。

#### `bot_admins` — Bot 管理員與功能節點

管理員權限分為兩個獨立軸線：

1. **`bot_admins[]`** — 在 `config.json` 宣告的 bot 管理員，每個成員用 `features` 物件逐項開關功能節點。
2. **Discord 伺服器權限** — 由 Discord 本身決定（`管理伺服器` / `Administrator`），bot 無法設定，直接影響 `!msg`、`!relaylist` 等指令（後者僅在啟用 relay 的 profile 上載入）。

```json
"bot_admins": [
  {
    "id": "123456789012345678",
    "name": "Example Admin",
    "features": {
      "exclusive_command": true,
      "notifications": true,
      "relay_reverse_delete": false
    }
  }
]
```

| 功能節點 | 說明 |
|---------|------|
| `exclusive_command` | 允許使用 `!reload`、`!announce` 與 `/relay` 系列 slash 指令（重新載入設定、廣播訊息、管理 relay 群組與頻道） |
| `notifications` | 接收管理操作通知（DM）與錯誤通知 |
| `relay_reverse_delete` | 刪除中繼副本時，即使頻道未開啟 `allow_reverse_delete` 也允許反刪原始訊息 |

- `id` 必填；`name` 僅供人類閱讀，不參與任何邏輯判斷。
- 未列在 `bot_admins` 的成員，即使擁有伺服器 `Administrator` 權限，也**不能**使用 `!reload` / `!announce` 等 bot 級指令。
- 舊版扁平欄位仍會自動相容：`admin.user_ids` → `exclusive_command`，`notifications.admin_user_ids` → `notifications`。

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

`features` 使用大分類控制 Cog 載入，所有功能預設都是 `false`，需要的模組必須在各 profile 中明確啟用。`relay`、`commands`、`admin` 是全域功能；`keywords`、`scheduler`、`moderation` 啟用後，仍會再依每個伺服器的 `config.guilds/{guild_id}.json` 決定是否執行。

> `commands` 目前只載入 `ping`。`admin` 提供與 relay 無關的 JSON 訊息控制（`!msg` 系列）；relay 管理指令（`!reload`、`!announce`、`!relaylist`）會隨 `relay` feature 一起載入，因此只有在啟用 relay 的 profile 上可用。

#### `commands`

Commands 類功能目前提供基本指令：

```text
!ping
```

#### 管理指令

管理指令分為兩類，權限來源不同：

**Relay 管理指令**（僅在啟用 `relay` 的 profile 上載入；需在 `bot_admins` 且啟用對應功能節點）：

```text
!reload                       # exclusive_command — 重新載入設定
!announce group_name {JSON}   # exclusive_command — 廣播到 relay group 所有頻道
!relaylist                    # 需「管理伺服器」權限 — 列出所有中繼群組與所屬頻道
```

**Discord 管理員指令**（`admin` feature，不需 relay；僅需伺服器 `管理伺服器` / `Administrator` 權限，與 bot_admins 無關）：

```text
!msg send #channel {"content":"文字內容"}   # 只能傳送到指令所在伺服器內的頻道
!msg edit message_id {"content":"新文字內容"}
!msg delete message_id
!msg source message_id
```

**Relay Slash 指令**（僅在啟用 `relay` 的 profile 上載入；需在 `bot_admins` 且啟用 `exclusive_command`；與 `!reload` 相同權限門檻）：

```text
/relay group add name [hidden]                      # 新增 relay group（可先為空）
/relay group edit group [new_name] [hidden]         # 編輯 group 名稱／隱藏狀態
/relay group remove group                           # 移除 group（連同頻道與角色映射）
/relay channel add group channel [direction] [brand_name] [process_bot_messages] [allow_forward_delete] [allow_reverse_delete]
/relay channel edit channel [group] [direction] [brand_name] [clear_brand_name] [process_bot_messages] [allow_forward_delete] [allow_reverse_delete]
/relay channel remove channel                       # 從 group 移除頻道（保留空 group）
```

Slash 指令的參數詳細說明：

| 參數 | 類型 | 說明 |
|------|------|------|
| `name` | 文字 | 新 group 名稱，需唯一 |
| `hidden` | 布林 | 是否在 `!relaylist` 隱藏（預設 `false`） |
| `group` | 文字（自動補全） | 目標 group 名稱 |
| `new_name` | 文字 | 改名（不填則保留） |
| `channel` | 文字（頻道 ID 或 `#頻道`） | 輸入頻道 ID 或 `#頻道` mention；只接受一般文字頻道與論壇頻道。刻意不使用 Discord 的頻道選擇器型別，因為 Discord 會在參數驗證階段就拒絕它無法解析的 ID（顯示「指定的頻道 ID 無效」），bot 根本收不到指令 — 改用文字輸入後由 bot 自己驗證並給出明確錯誤，也支援在 DM 中指定其他伺服器的頻道（bot 需已加入該伺服器） |
| `direction` | 選單 | `BOTH` / `SEND_ONLY` / `RECEIVE_ONLY`（預設 `BOTH`） |
| `brand_name` | 文字 | 自訂顯示名稱（不填則自動產生） |
| `clear_brand_name` | 布林 | 清除自訂名稱、恢復自動產生 |
| `process_bot_messages` | 布林 | 是否轉發其他 bot 的訊息（預設 `false`） |
| `allow_forward_delete` | 布林 | 順向刪除同步（預設 `true`） |
| `allow_reverse_delete` | 布林 | 反向刪除同步（預設 `false`） |

每次執行都會：自動備份 `config.json` → 原子寫入（通過 `validate_config` 驗證）→ 同步資料庫 → 觸發 `bot_reload` 事件 → 記錄審計日誌並 DM 通知啟用 `notifications` 的 bot 管理員。指令回覆一律為 ephemeral（只有操作者看得到）。

> Slash 指令與 `!reload` 共用同一份 `config.json`，兩者修改會即時互相反映。

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

`source` 會輸出指定訊息的 JSON，方便複製後微調再用 `edit`。`announce` 會把同一份 JSON 發送到指定 relay group 的所有一般文字頻道，論壇頻道會略過。`edit` / `delete` 只會操作同一隻 bot 自己發出的訊息。`!msg` 與 `!announce` 的每次使用都會記錄審計日誌，並 DM 通知啟用 `notifications` 的 bot 管理員。

#### `slash_commands`

Slash 指令同步設定。可選，預設為全域同步：

```json
"slash_commands": {
  "guild_ids": []
}
```

| 欄位 | 說明 |
|------|------|
| `guild_ids` | 要同步指令到特定伺服器的 ID 列表（開發期用，指令只會出現在這些伺服器，更新即時生效） |

- 留空或省略 `guild_ids` 時，指令會同步到所有伺服器（全域同步，更新可能需要最多 1 小時才生效）。
- 填入 guild ID 可讓 `/relay` 指令只在指定測試伺服器出現，避免開發期間干擾正式環境。

### `config.guilds/{guild_id}.json` — 單服功能設定

首次啟動時，bot 會依目前加入的伺服器自動產生對應檔案，例如：

```text
config.guilds/
  123456789012345678.json
  987654321098765432.json
```

這些檔案是 runtime 設定，預設不進 git。格式可參考 [config.guild.json.example](config.guild.json.example)。

#### `features`

每個伺服器可以獨立開關單服功能。即使 profile 已載入 Cog，這裡關閉後該伺服器也不會執行該功能。

```json
"features": {
  "keywords": true,
  "scheduler": false,
  "moderation": false
}
```

#### `keywords`

Keywords 類功能的細項設定（需在 profile 與此伺服器都開啟 `keywords`）：

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

Moderation 類功能的細項設定（需在 profile 與此伺服器都開啟 `moderation`）：

```json
"moderation": {
  "welcome_cleaner": {
    "enabled": true,
    "channels": ["1015827632731996251"]
  }
}
```

#### `scheduler`

Scheduler 類功能的細項設定（需在 profile 與此伺服器都開啟 `scheduler`）：

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
| `allow_reverse_delete` | ❌ 選填 | 中繼副本被刪除時是否反刪原始訊息（預設 `false`）。關閉時，啟用 `relay_reverse_delete` 功能的 bot 管理員仍可反刪，但需要 bot 擁有「檢視審計日誌」權限以確認刪除者身分 |

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

權限分為兩類：**機器人權限**（bot 在伺服器／頻道中的權限）與**使用者權限**（操作者本身需要的權限）。以下依功能列出對應關係。

### Relay — 跨伺服器中繼

| 權限 | 用途 | 必要性 |
|------|------|--------|
| 管理 Webhook | 建立與自動修復中繼 webhook | ⭐ 核心 |
| 檢視頻道 | 讀取中繼頻道的訊息 | ⭐ 核心 |
| 發送訊息 | webhook 失效時的警示訊息等 | ⭐ 核心 |
| 讀取訊息歷史 | 回覆重建（`fetch_message`）、編輯／刪除同步 | ⭐ 核心 |
| 嵌入連結 | 發送嵌入（回覆原文嵌入、圖片預覽、投票） | 視需求 |
| 新增表情符號 | 表情同步（在轉發副本上添加／移除反應） | 視需求 |
| 使用外部表情符號 | 跨伺服器表情同步（在目標頻道使用其他伺服器的自訂表情） | 視需求 |
| 管理表情符號 | 表情快取伺服器建立快取表情 | 視需求（表情快取） |
| 建立公開討論串 | 討論串／論壇貼文同步 | 視需求（討論串） |
| 在討論串中發送訊息 | 在討論串中轉發 | 視需求（討論串） |
| 管理討論串 | 討論串鎖定／封存／刪除同步 | 視需求（討論串） |
| 管理角色 | 角色映射自動建立對應角色 | 視需求（角色映射） |
| 檢視審計日誌 | bot 管理員的 `relay_reverse_delete` 反刪繞過（需確認刪除者身分） | 視需求 |

> 基本轉發（純文字＋附件）其實只需要「**管理 Webhook** ＋ 檢視頻道 ＋ 發送訊息 ＋ 讀取訊息歷史」四項——webhook 會負責轉發的實際發送；其餘權限是啟用表情同步、討論串、角色映射、embed 等進階功能時才需要。

### Admin — 管理指令

| 指令 | 使用者權限 | 需要 bot 權限 |
|------|-----------|--------------|
| `!msg` 系列 | 管理伺服器 或 管理員 | 發送訊息、讀取訊息歷史、嵌入連結（payload 含 embed 時）、附加檔案（`!msg source` 輸出檔） |
| `!relaylist` | 管理伺服器 或 管理員 | 無（只讀 relay 設定） |
| `!reload` | bot_admins 且啟用 `exclusive_command` | 管理 Webhook（同步時建立 webhook） |
| `!announce` | bot_admins 且啟用 `exclusive_command` | 發送訊息、嵌入連結（payload 含 embed 時） |
| `/relay` 系列 | bot_admins 且啟用 `exclusive_command` | 管理 Webhook（`channel add` 同步時建立 webhook）、檢視頻道（頻道選擇器需要） |

### 單服功能

| 模組 | 需要 bot 權限 |
|------|--------------|
| Keywords | 檢視頻道、發送訊息 |
| Scheduler | 檢視頻道、發送訊息（圖片）、附加檔案 |
| Moderation（Welcome Cleaner） | 管理訊息（成員離開時刪除歡迎訊息） |
| Commands（`!ping`） | 檢視頻道、發送訊息 |

## 專案結構

```
Bot/
├── main.py              ← 啟動入口，建立 bot profiles 並註冊各模組
├── run.py               ← python run.py 啟動腳本
├── app/
│   ├── bot_admins.py        ← bot_admins 功能節點判斷（新 schema + 舊欄位相容）
│   ├── bot_profiles.py      ← 多 bot token profile 載入與驗證
│   ├── config.py            ← 讀取 .env
│   ├── config_validator.py  ← 啟動早期驗證 config.json
│   ├── config_sync.py       ← 讀取 config.json → SQLite
│   ├── guild_config.py      ← 單服功能設定載入、驗證與自動生成
│   └── relay_config_editor.py ← relay 設定編輯資料層（slash 指令使用）
├── config.guilds/       ← 單服 runtime 設定檔（不進 git）
├── data/                ← SQLite runtime 檔案（不進 git）
├── database/
│   └── database.py      ← SQLite + migration
├── utils/
│   ├── admin_audit.py       ← 管理操作審計日誌 + DM 通知
│   ├── admin_notifier.py    ← DM 通知 bot 管理員
│   ├── log_manager.py
│   ├── message_payload.py   ← JSON 訊息 payload 解析/輸出
│   └── time_utils.py
└── cogs/
    ├── relay/           ← 跨伺服器中繼（整包為一個 Cog）
    ├── bot_admin/       ← bot 管理員指令（bot_admins 功能節點控管）
    │   └── relay_admin_commands.py ← /relay slash 指令（group / channel 管理）
    ├── guild_admin/     ← Discord 管理員指令（manage_guild / administrator）
    ├── keywords/        ← 關鍵字被動回應
    ├── scheduler/       ← 定時任務
    ├── moderation/      ← 頻道管理
    └── commands/        ← 基本指令
```

## 注意事項

- 討論串和論壇貼文的中繼需要 bot 有「管理討論串」權限
- Relay 功能只能在一個 bot profile 啟用
- 同步副本會保留使用者、身分組、頻道標註，但不會觸發 ping；只有原始訊息所在頻道會通知
- 圖片附件會以圖片預覽 embed 同步；非圖片附件仍以連結同步
- 啟動時會先驗證 `config.json`，並在 bot ready 後補齊 `config.guilds/{guild_id}.json`
- `config.json` 只放全域設定；`keywords`、`scheduler`、`moderation` 細項請放在單服設定檔
- 修改設定後可用 `!reload` 重新載入 `config.json`、同步 relay，並清除/重讀單服設定快取
- `/relay` slash 指令同樣直接改 `config.json`（自動備份 + 原子寫入 + 同步），修改後不需要再手動 `!reload`
- Slash 指令預設全域同步；開發期可在 `config.json` 的 `slash_commands.guild_ids` 指定測試伺服器，讓指令更新即時生效
