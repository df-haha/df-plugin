# gpu-cloud-finetune

租雲端 GPU 測模型能力、做 QLoRA 微調、量化的實戰手冊，涵蓋 Colab CLI、gputw.ai、RunPod 三個平台。

內容來自實際租機跑過的紀錄（Gemma 4 31B 視覺語言模型的推論評測、QLoRA 微調、FP8 量化、vLLM 壓測），包含實付價格和出過的事故。價格會變動，開機前請看官網。

## 安裝

```bash
claude plugin marketplace add df-haha/df-plugin
claude plugin install gpu-cloud-finetune@df-haha-plugins
```

安裝後跟 Claude 說「開 GPU」「幫我 fine-tune」「量化成 4 bit」之類的話就會觸發。

## 從哪裡開始看

`skills/gpu-cloud-finetune/README.md` 是總覽：該選哪個平台、固定 8 個步驟、哪張卡放得下多大的模型。各章：

| 檔案 | 內容 |
|---|---|
| `01_Colab.md` | Colab CLI 開 A100、傳檔、在 Colab 跑訓練、停機 |
| `02_gputw.md` | gputw API、`/vault` 免開機取檔、搶機 |
| `03_RunPod.md` | RunPod REST API、停機與刪除的差別 |
| `04_下載模型與測能力.md` | Hugging Face 釘版下載、vLLM、公平測法 |
| `05_Finetune_QLoRA.md` | Unsloth QLoRA、鎖版本、合併、GGUF |
| `06_量化與邊緣部署.md` | FP8／INT8／4-bit／GGUF 對照與實測掉分 |
| `07_停機保險與踩坑總表.md` | 三條停機保險、事故紀錄、跨平台踩坑表 |
| `scripts/` | 上機檢查、gputw／RunPod API 包裝、停機保險範本 |

## 注意

- 金鑰一律放環境變數（`GPUTW_TOKEN`、`RUNPOD_API_KEY`、`HF_TOKEN`），不要寫進檔案。
- `scripts/` 裡除了 `gpu_preflight.py`，都是從實戰腳本簡化或改寫而來，只用假 API／假 ssh 測過，沒在真機器上跑過。用之前請先小量試跑。
