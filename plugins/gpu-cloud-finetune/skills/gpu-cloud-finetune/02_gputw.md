# gputw.ai（台灣 GPU 雲）

> 適合：正式 fine-tune、要跑好幾小時、資料不想出國。
> 我們 2026-08～10 在上面跑過 Gemma 4 31B QLoRA 訓練（訓練本身最長約 5 小時，含評測整趟近 7 小時）、推論壓測。
> 優點：台灣機房、按秒計費、中文客服、可開三聯式發票。缺點：常常缺卡。

## 1. 帳號與計費

- 網頁註冊、儲值。餘額是**預付制**。
- ⚠️ **餘額不夠時，平台會直接把機器砍掉，不預警**，`/workspace` 全消失。開機前先確認餘額夠跑完整段。
- ⚠️ 餘額不夠一小時，API 會回「Insufficient credits to cover one hour」，看起來很像「沒空機器」，要看 log 才分得出來。

## 2. API 基本

- Base URL：`https://gputw.ai/api`
- 認證：`Authorization: Bearer <你的 API key>`
- 範本：`scripts/gputw_api.sh`（金鑰讀環境變數 `GPUTW_TOKEN`）

| 做什麼 | 方法與路徑 | body |
|---|---|---|
| 列出我的機器（看狀態、時價） | `GET /instances` | — |
| 查哪些節點有空卡 | `GET /nodes/available` | — |
| 查餘額 | `GET /auth/me` → `data.creditBalance` | — |
| 開機（同節點） | `POST /instances/<id>/restart` | `{"mode":"same"}` |
| 開機（換節點） | `POST /instances/<id>/restart` | `{"mode":"alternative","nodeId":"<節點id>"}` |
| **停機** | `POST /instances/stop` | `{"instanceId":"<id>"}` |
| 列 `/vault` 資料夾內容（不用開機） | `GET /vault/list?path=<資料夾>` | — |
| 下載 `/vault` 單檔（不用開機） | `GET /vault/download?filename=<路徑>` | — |
| 下載 `/vault` 整個資料夾成 zip（不用開機） | `GET /vault/download-zip?folder=<資料夾>` | — |

⚠️ 停機路徑**不是** `/instances/<id>/stop`（那個回 404）。
⚠️ 在機器裡打 `poweroff` **沒用**，一定要用 API 停。
⚠️ `GET /instances` 偶爾回傳截斷的 JSON，要重試。
⚠️ `/nodes/available` 欄位改過版（舊的 `status`、`machinePriceHr` 不見了，現在看 `availableGpus`、`hourlyRate`）。舊腳本會因此永遠搶不到機器。

⚠️ `/vault` 三支 API 要金鑰有 **`vault:read`** 權限。沒有的話回 `missing required scope: vault:read`，到網頁的 API 金鑰設定加上權限即可。
（`download-zip` 我們沒實際用過；逐檔下載有實測，見下面第 5 節。）

**第一台機器**：我們的腳本裡沒有「建立新機器」的 API 呼叫，一律 `restart` 既有機器。第一台請從網頁建立，之後用 API 重開（或換節點）。

## 3. 連進機器

```bash
ssh -p 2222 pod-<instance-id>@ssh.gputw.ai
```

- port 固定 2222，使用者名稱固定是 `pod-<instance-id>`。
- 自動化腳本裡的查詢指令一律加 `-n -o BatchMode=yes`，不然 ssh 會卡住呼叫它的程式。
- ⚠️ 用 ssh 直接發射長任務會把 session 吊住 → 長任務用 `nohup ... &` 或 `setsid` 丟背景。

## 4. 傳檔

- 機器上沒有 rsync。用 tar 串流：

```bash
tar -cf - data/ | ssh -p 2222 pod-<id>@ssh.gputw.ai "tar -xf - -C /workspace"
```

- 或 `scp -P 2222`。

## 5. 資料會留在哪（最重要）

| 路徑 | 是什麼 | 停機後 |
|---|---|---|
| `/workspace` | 本機 NVMe 硬碟，快 | **清空**（連 `mode=same` 重開也會清） |
| HF 快取（若放 `/workspace`） | 下載的模型 | **清空** |
| `/vault` | 帳號專屬 NFS（網路硬碟），跨機器共用 | **保留**（餘額歸零後保留 30 天） |

做法：

1. 模型、訓練資料、腳本第一次上傳後，**備份一份到 `/vault`**。
2. 之後開機從 `/vault` 用內網複製到 `/workspace` 再用（不要直接從 NFS 讀模型，慢）。
3. 結果跑完**先存 `/vault`，再拉回本機**。
4. ⚠️ 不要把 checkpoint（訓練中途存檔）整包塞進 `/vault`，我們誤存過 17GB。

### 機器停了，還要拿 `/vault` 裡的東西

不用為了拿檔案再開一次機器。直接用 API 下載：

```bash
export GPUTW_TOKEN=...    # 要有 vault:read 權限
./scripts/gputw_vault_fetch.sh my_run/adapter ./adapter_from_vault
```

腳本會一檔一檔下載、比對大小；資料夾裡有 `required_artifacts.sha256`（檔案指紋清單）的話，也會一併驗證。

建議：訓練腳本跑完時，就在結果資料夾寫一份 `required_artifacts.sha256`，之後不管從哪裡拿回來都能驗。

2026-10-07 實例：訓練跑完後，停機保險走 ssh 把 adapter（LoRA 權重檔）拉回本機，中途斷線（Broken pipe）失敗；機器已經停了。改用這支腳本從 `/vault` 免開機抓回，指紋驗證通過。

## 6. 環境建置

- 基本映像：`gputw/unsloth-notebook:latest`（已裝 Unsloth）。API 不提供映像的 digest（唯一指紋），所以要自己記版本。
- 上機第一件事：`python gpu_preflight.py --min-vram-gb <這次需要的 GB>`（見 `scripts/`）。
- 套件用 lock 檔（鎖死版本的清單）安裝，不要 `pip install unsloth` 就上。原因見 05。
- 自動化建議做成 bootstrap 腳本：開機、拿到 instance id 就掛停機保險 → 等 RUNNING → 從 `/vault` 拉資料 → 裝套件 → preflight → 發射訓練。（範本會等機器進入 RUNNING 才開始看 ssh；開機寬限期內都沒進 RUNNING 就停機。）
- 參考時間：上傳 12,613 張訓練圖＋整套 bootstrap 約 33 分鐘（2026-10-06）。

### 搶機

- 熱門卡常被搶光，要寫一支「搶機迴圈」：每隔一段時間查 `/nodes/available`，有空卡就 `restart` 搶下來。
- 搶機迴圈只能有一份在跑。用鎖檔（`flock`）擋住第二份；腳本自己重啟時要沿用同一把鎖，不要重新開鎖檔，不然鎖會掉、變成兩份同時搶。
- 搶到的同一個動作就掛停機保險（三條，見 07）。

## 7. 實際花費（2026-08～10，含頻寬費）

| GPU | 實付時價 | 跑過什麼 |
|---|---|---|
| RTX PRO 6000 WS 96GB（Blackwell） | US$1.0988/hr | 31B QLoRA 3 epochs 1,224 步，5 小時 ≈ US$5.5；100 步測試 18 分鐘 |
| RTX 5090 32GB | US$0.5488/hr（備援節點 0.77～0.79） | 31B 4-bit 訓練，VRAM 用約 21GB |
| RTX 5090 32GB（2026-10） | US$0.6356/hr | 31B QLoRA 2,618 步（每步約 6.5 秒）＋評測，開機到停機約 6.8 小時，實花 NT$141 |

⚠️ 網頁掛牌價 ≠ 實付。5090 掛牌看起來 US$0.32，實付含頻寬是 0.55。
H100 我們去問時常缺貨。

## 8. 踩坑表

| 症狀 | 原因 | 解法 |
|---|---|---|
| 停機 API 回 404 | 路徑錯 | 用 `POST /instances/stop` + body |
| 機器裡 poweroff 了還在扣錢 | 機內關機無效 | 一定用 API 停，再 `GET /instances` 回查 STOPPED |
| 重開後模型不見了 | `restart mode=same` 也清 `/workspace` | 模型放 `/vault`，每次開機重拉 |
| 一直搶不到機器 | `/nodes/available` 欄位改版 | 讀 `availableGpus` |
| 說沒機器，其實是錢不夠 | 餘額不足一小時 | 看 log 內容，先儲值 |
| 機器突然整台消失 | 餘額用完被砍 | 開機前算好預算；結果每輪拉回本機 |
| 腳本跑完了機器還開著空轉 7 小時 | 沒掛停機保險 | 見 07 |
| 本機 WSL 重開機，停機保險跟著死 | 保險只掛在本機 | 機器上也掛一份（見 07） |
| 機器停了，結果還沒拉回來 | 走 ssh 拉大檔中途斷線（Broken pipe） | 用 `scripts/gputw_vault_fetch.sh` 從 `/vault` 免開機下載 |
| `/vault` API 回 `missing required scope: vault:read` | 金鑰沒開 vault 權限 | 到網頁替 API 金鑰加 `vault:read` |
| 停機保險的硬上限沒照預期生效 | 沿用了別支腳本的預設值（曾經是 55 小時） | 每次發射都明確寫上硬上限，並在 log 印出實際生效值 |
| 發射時 ssh 回 rc=124 | 發射用的 ssh 設了逾時 | 實測無害；以機上 log 和標記檔為準 |
| ssh 被 hostkey 警告擋住、或出現 bad signature | 本機記得的舊主機指紋跟新機器對不上 | 自動化 ssh 加 `-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no` |
| Blackwell 卡上 `torch.cuda.is_available()` 是 True，一算就炸 "no kernel image" | torch 不是 CUDA 12.8+ 編的 | 先跑 `gpu_preflight.py` |
| `pip install` 後 torch／transformers 版本被偷改 | 沒鎖版本、`-q` 讓它無聲 | 用 lock 檔 |
