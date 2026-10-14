# RunPod

> 適合：gputw 沒卡、要特定 GPU（5090、多卡）、要全自動 API。
> 缺點：資料會出境（機房在國外）。
> 我們 2026-08～09 在上面跑過多次 31B QLoRA 訓練、4×5090 FP8 推論壓測。

## 1. 帳號與計費

- 網頁註冊、儲值（預付）。
- 兩種機房：
  - **SECURE**（正規機房，貴一點、穩）
  - **COMMUNITY**（社群主機，便宜、比較不穩）
- ⚠️ 餘額用完一樣是直接砍機，不預警。

## 2. API 基本

- REST API：`https://rest.runpod.io/v1`
- 認證：`Authorization: Bearer <RUNPOD_API_KEY>`
- 查餘額用 GraphQL：`https://api.runpod.io/graphql`，查 `myself { clientBalance }`
  （2026-10 仍可用。pod 的查詢、開關機我們一律走 REST。）
- 範本：`scripts/runpod_api.sh`（金鑰讀環境變數 `RUNPOD_API_KEY`）
- 也有官方 CLI `runpodctl`，我們沒用，全走 REST。

| 做什麼 | 方法與路徑 |
|---|---|
| 建機器（pod） | `POST /pods` |
| 查狀態、IP、SSH port | `GET /pods/<id>?includeMachine=true` |
| 停機（保留硬碟） | `POST /pods/<id>/stop` |
| 重開已停的機器 | `POST /pods/<id>/start` |
| **刪除**（硬碟一起刪） | `DELETE /pods/<id>` |

## 3. 開機

`POST /pods` 的 body（我們實際用的）：

```json
{
  "name": "my-train",
  "imageName": "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04",
  "cloudType": "SECURE",
  "gpuTypeIds": ["NVIDIA GeForce RTX 5090"],
  "gpuTypePriority": "custom",
  "gpuCount": 1,
  "containerDiskInGb": 60,
  "volumeInGb": 150,
  "volumeMountPath": "/workspace",
  "ports": ["22/tcp"],
  "supportPublicIp": true,
  "interruptible": false,
  "minVCPUPerGPU": 4,
  "minRAMPerGPU": 32,
  "env": { "PUBLIC_KEY": "<你的 ssh 公鑰內容>" }
}
```

- `gpuTypeIds` 可以放多個，當候補。
- `PUBLIC_KEY` 放你的 ssh **公鑰**（`~/.ssh/id_ed25519.pub` 的內容），之後才連得進去。
- 要跑 vLLM：映像改用 **Python 3.12** 的版本（py3.11 跟 vLLM 0.27.1 不相容）。

**搶機策略**（熱門卡常滿）：每 60 秒試一次 `POST /pods`；先試 SECURE，不行再試 COMMUNITY；等太久再把備用卡型加進候選。

## 4. 連進機器

```bash
# 從 GET /pods/<id>?includeMachine=true 拿 publicIp 和 portMappings["22"]
ssh root@<publicIp> -p <port>
```

⚠️ 有些 SECURE 機器沒有 publicIp，只能走 RunPod 的 proxy SSH，而且只給互動終端、不能跑自動化指令。自動化一定要 `supportPublicIp: true` 並確認拿到 IP。

## 5. 傳檔

- `scp -P <port>` 或 `rsync`。
- 有些映像沒裝 rsync → 用 tar 串流：

```bash
tar -cf - data/ | ssh -p <port> root@<ip> "tar -xf - -C /workspace"
```

## 6. 資料會留在哪

| 動作 | `/workspace`（volume） | 計費 |
|---|---|---|
| `stop` 停機 | **保留** | GPU 不收錢，但硬碟空間照收 |
| `start` 重開 | 還在，省下重建環境時間（我們省過約 55 分鐘） | — |
| `DELETE` 刪除 | **全部消失** | 不再收錢 |

⚠️ 重開已停的機器常失敗（原本那台主機的 GPU 被別人租走了）。要背景每 5 分鐘重試。
所以：**重要結果先拉回本機、核對 sha256（檔案指紋），再 DELETE。**

## 7. 環境建置

- 上機第一件事：`python gpu_preflight.py --min-vram-gb <這次需要的 GB>`
- 停機保險：本機一條＋機上一條（機器自己呼叫 `POST /pods/<id>/stop`），見 07。
- `pip install` 可能 2～34 分鐘不等，長連線 ssh 常在中途斷掉 → 把安裝做成背景腳本，跑完寫一個 `pip_install.done` 標記檔，本機輪詢這個檔。
- 模型下載到 `/workspace/hfcache`（停機後還在）。

## 8. 實際花費（我們實付，2026-08～09）

| GPU | 時價 | 跑過什麼 |
|---|---|---|
| RTX 5090（SECURE／COMMUNITY） | 約 US$0.3～1.0/hr，隨時段、機房變動 | 31B QLoRA 多組訓練，單次數小時到 12 小時 |
| RTX PRO 6000 Blackwell | US$0.83/hr | 5090 缺貨時備用 |
| 4×RTX 5090（COMMUNITY） | US$2.76/hr | FP8 推論壓測（2 份模型 × 每份 2 卡），15.63 QPS |
| A100 80GB | US$0.79/hr（早期） | 最初的 QLoRA 訓練方案 |

價格浮動大，開機前看 API 回的 `costPerHr`。

## 9. 踩坑表

| 症狀 | 原因 | 解法 |
|---|---|---|
| 停了還在扣錢 | volume 照收 | 收割完 `DELETE` |
| POST stop 成功但其實沒停 | 回應不等於結果 | 停機後 `GET` 回查，直到 404 或 EXITED |
| 重開 EXITED 機器失敗 | 主機 GPU 被佔 | 背景重試；重要東西不要只留在機器上 |
| SSH 只給互動終端 | 沒 publicIp、走 proxy | `supportPublicIp: true` |
| 沒 rsync | 映像沒裝 | tar 串流 |
| 訓練跑到一半被砍，0 個 checkpoint | 存檔間隔參數沒傳進去，落回預設 200 步 | 啟動時印出「實際生效」的參數值再確認 |
| pip 裝到一半 ssh 斷 | 長連線不穩 | 背景安裝＋完成標記檔 |
| 26B MoE 模型 4-bit 載入炸掉 | 4-bit 不支援這架構 | 自動退回 bf16 |
| vLLM FP8 在 5090 上 OOM（記憶體不足） | 記憶體用量設太高＋CUDA graph（預先錄好的運算流程，會多吃記憶體） | `--enforce-eager` 並把 `--gpu-memory-utilization` 降到 0.85 |
