# 部署與維運指南

本文件說明 Discord 審核所需的營運工作：靜態加密、Developer Portal 設定，以及可實際受理的資料刪除流程。本文只涵蓋需要人工在主機或入口網站完成的操作。

> 範例以 Windows 主機與 PowerShell 為主。實際路徑、服務與工具請依你的環境調整。

## 1. 主機／日誌／備份的靜態加密

SQLCipher 只保護 `data/` 下的資料庫檔案。以下項目必須另外處理。

### 1.1 加密金鑰注入（不落地）

金鑰是 64 個十六進位字元（32 bytes），必須與資料庫分開保存。

```powershell
# 產生金鑰（只顯示一次，請立即存入祕密管理器）
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

正式環境的注入方式（擇一）：

- **Windows 服務／排程**：用服務帳號的環境變數，或 `nssm set <service> AppEnvironmentExtra DATABASE_ENCRYPTION_KEY=...`。
- **祕密管理器**：例如 Windows Credential Manager、Azure Key Vault、1Password CLI、HashiCorp Vault；啟動腳本在程式啟動前將值載入 `DATABASE_ENCRYPTION_KEY`。
- **容器／雲端**：以 secret 掛載環境變數，不要寫進映像或 repo。

**不要**把金鑰放進 `.env`、`config.json`、`data/`、日誌或 Git。`.env` 只在開發機使用；`.gitignore` 已排除 `.env`、`data/`、`log/`。

### 1.2 首次遷移與切換

```powershell
# 1. 停止 Bot
# 2. 備份既有資料庫
Copy-Item data\database.db data\database.backup-<date>.db
# 3. 載入金鑰
$env:DATABASE_ENCRYPTION_KEY = "<從祕密管理器載入>"
# 4. 建立並驗證加密副本（不改來源檔）
.\.venv\Scripts\python.exe -m database.encryption_migration data\database.db data\database.encrypted.db
# 5. 將 DATABASE_PATH 指向 data\database.encrypted.db（正式環境用環境變數，不要改 .env.example）
# 6. 啟動 Bot 驗證功能正常
```

核對資料筆數與備份可還原後，再安全退役舊明文資料庫及其 `-wal`/`-shm`、`data/config-backups/` 內的舊備份。

### 1.3 主機磁碟加密

- 啟用 **BitLocker**（Windows Pro/Server）加密整個系統與資料磁碟，並妥善保管復原金鑰。
- 確認執行 Bot 的服務帳號只能存取專案目錄、`data/` 與 `log/`。
- 若主機是雲端 VM，使用雲端磁碟加密（例如 Azure SSE、AWS EBS 加密）。

### 1.4 日誌與備份

- 本機日誌：程式已每日輪替並刪除超過 30 天的檔案；`log/` 目錄權限限制為服務帳號。
- `data/config-backups/`：程式已清除超過 30 天的備份。
- 外部備份：若使用雲端備份或 NAS，確認**備份本身有加密**，並設定最多 30 天生命週期後銷毀。不要把加密資料庫與金鑰備份放在同一個位置。

## 2. Developer Portal 設定

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications)，選取本 Bot 應用程式。
2. **General Information**
   - 填寫清楚的名稱、描述、圖示。
   - **Privacy Policy URL**：填入公開的政策網址（見第 3 節）。
   - **Terms of Service URL**：可填你的服務條款網址（若沒有可留空）。
3. **Installation / OAuth2**：確認需要的 scope 與權限，移除未使用的權限。
4. **Bot**
   - **Privileged Gateway Intents**：
     - `Presence Intent`：**保持關閉**（本 Bot 不使用）。
     - `Server Members Intent`：僅在需要 Welcome Cleaner 的 profile 開啟。
     - `Message Content Intent`：僅在需要 relay／前綴指令／Showcase 的 profile 開啟。
   - 關閉不需要的 Public Bot 選項視情況調整。
5. 在 **App Directory / Verification** 或審核表單中，依 `PRIVACY_POLICY.md` 回覆各 intent 的用途。
6. 設定 `PRIVACY_POLICY_URL` 環境變數，讓 `/privacy` 指令回覆相同網址，並在 Bot 的說明/關於中提供支援與回報管道。

## 3. 公開隱私權政策

1. 以 `PRIVACY_POLICY.md` 為底稿，確認已填入營運者、聯絡方式與服務商資訊。
2. 發布到**穩定、公開、免登入**的網址，例如：
   - GitHub Pages（`<user>.github.io/<repo>/privacy`）
   - 你自己的網站或 Cloudflare Pages / Netlify。
3. 確認手機與桌機皆可開啟，且不需要登入。
4. 將同一網址填入 Developer Portal 與 `PRIVACY_POLICY_URL`。
5. 政策更新時同步更新頁面與入口網站。

## 4. 可實際受理的資料刪除流程

### 4.1 受理管道

- 使用政策第 4 節所列的聯絡方式（目前為 `longhi.tw` 的 Discord）。
- 回覆時要求對方提供：要處理的伺服器 ID／頻道 ID／訊息 ID 或使用者 ID，以利定位。
- 提醒對方**不要**提供密碼或 Bot Token。

### 4.2 核實

- 確認請求者就是資料當事人（例如以該 Discord 帳號本人聯絡）。
- 若無法核實，先說明需要哪些資訊再處理。

### 4.3 執行刪除

先停止 Bot，載入與正式環境相同的 `DATABASE_ENCRYPTION_KEY` 與 `DATABASE_PATH`，再使用營運工具：

```powershell
# 查詢某使用者儲存了哪些資料
.\.venv\Scripts\python.exe -m tools.privacy_admin lookup --user-id <使用者ID>

# 刪除該使用者的退出偏好、違規與封鎖紀錄
.\.venv\Scripts\python.exe -m tools.privacy_admin delete --user-id <使用者ID> --confirm

# 刪除特定訊息的 relay/討論串映射
.\.venv\Scripts\python.exe -m tools.privacy_admin delete-messages --message-id <訊息ID> --confirm
```

工具的處理範圍：

- `delete --user-id`：`relay_opt_out`、`user_warnings`、`group_blacklist`。
- `delete-messages --message-id`：`relayed_messages`、`relay_threads`、`relay_thread_notices`。
- 訊息／討論串映射不含使用者 ID，無法依使用者查詢；它們由固定 30 天保留政策自動清除。

### 4.4 其他位置的資料

- **本機日誌**：`log/` 每日輪替、30 天清除；必要時可手動刪除相關檔案。
- **config 備份**：`data/config-backups/` 由程式清除；必要時手動刪除。
- **外部備份**：依你的備份服務刪除或等待生命週期到期。若要求「立即」刪除，需在備份服務端操作。
- **Discord 上的訊息**：Bot 只能透過 API 刪除自己送出的副本；其他使用者複製、截圖的內容無法處理。

### 4.5 回覆與記錄

- 於 30 個日曆日內完成並回覆處理結果。
- 記錄請求日期、處理項目與完成日期，但**不要**在記錄中保存不必要的個資。
- 若依法必須保留部分資料，向請求者說明依據與期限。

## 5. 驗收檢查清單

- [ ] 正式環境以祕密管理器注入 `DATABASE_ENCRYPTION_KEY`，且缺金鑰時 Bot 拒絕啟動。
- [ ] 正式 `DATABASE_PATH` 指向加密資料庫，舊明文 DB/WAL/SHM 與未加密備份已退役。
- [ ] 主機磁碟、`log/` 與外部備份皆有加密與存取控制。
- [ ] Developer Portal 的 Privacy Policy URL 與 `PRIVACY_POLICY_URL` 一致且可匿名開啟。
- [ ] Privileged intents 與實際功能一致（Presence 關閉）。
- [ ] 以測試帳號演練一次 `lookup`／`delete`／`delete-messages`。
- [ ] 資料請求可在 30 天內完成並留下處理紀錄。
