---
name: gpu-cloud-finetune
description: 租雲端 GPU（Colab CLI／gputw.ai／RunPod）測模型能力、做 QLoRA fine-tune、量化（FP8／4-bit／GGUF）的標準流程與停機保險。使用者說「開 GPU」「開機」「租機器」「搶機」「測這個模型」「fine-tune」「微調」「量化」「8 bit」「4 bit」「Colab A100」「gputw」「vault」「RunPod」「停機保險」時觸發。
---

# 雲端 GPU：測模型／fine-tune／量化

完整說明在本 skill 資料夾裡：`README.md` 是總覽，`01_Colab.md`～`07_停機保險與踩坑總表.md` 是各章，`scripts/` 是腳本範本。下面提到的路徑都以本 skill 資料夾為準。這個 skill 是給 AI 助理照做的硬規則；細節查各章。

## 鐵則（違反就停手）

1. **開機的同一個動作就掛停機保險**（`scripts/deadman_template.sh`，`setsid nohup` 丟背景）。小測試也一樣。正式訓練：gputw 掛三條（本機保險＋機上看門狗＋純時限 API 保險 `scripts/gputw_api_backstop.sh`），RunPod 掛前兩條。機上看門狗沒附範本，照 `deadman_template.sh` 的判斷邏輯改成在機上跑。發射後用 `pgrep` 確認保險真的在跑。
2. **每次發射都明確寫上硬上限時數（整數）**，不沿用別支腳本的預設值，並在 log 印出實際生效值。
3. **付費機器開著時，不可以停下來等人回答問題**。要人決定的事：先停機再問；或寫「若無指示，X 分鐘後照預設 Y 做」然後繼續。
4. **停機後一定回查狀態**，看到「已停」才算數。API 回應成功不算。
5. **收工時先送停機**，快照、拉檔、寫 log 放後面，而且都要有逾時；任何一步卡住都不能擋住停機。
6. **開機前確認餘額夠跑完整段**。gputw／RunPod 餘額用完會直接砍機、資料全失。
7. **兩個平台同時搶機時，同一時間只准一邊訓練**。一邊搶到或 smoke 通過，另一邊立刻停機並回查。
8. **每輪把 log／結果小檔拉回本機**。結果只放在機器暫存區 = 沒有結果。
9. **金鑰只讀環境變數**（`GPUTW_TOKEN`、`RUNPOD_API_KEY`、`HF_TOKEN`），絕不印出、不寫進指令參數、不 commit。
10. `pgrep -f`／`pkill -f` 的樣式一律用中括號寫法（例 `"[t]rain.py"`），避免匹配到自己。

## 選平台

- 1 小時內的試跑、推論評測 → Colab：`colab new -s NAME --gpu A100 --high-mem`，或 `colab run --gpu T4 script.py`（跑完自動釋放）。Colab 機內無法自停，只能靠本機保險。Colab 上的完整訓練還沒跑完過，先當備案。
- 長時間訓練、資料不出境 → gputw：API `POST /instances/<id>/restart`；停機 `POST /instances/stop` body `{"instanceId":"<id>"}`
- 缺卡或要特定卡 → RunPod：`POST https://rest.runpod.io/v1/pods`；停 `POST /pods/<id>/stop`，收割後 `DELETE /pods/<id>`

## 固定流程

1. 確認餘額 → 2. 開機 → 3. 掛停機保險 → 4. `python gpu_preflight.py --min-vram-gb <這次需要的 GB>`（非 0 就停；31B 4-bit 訓練約 30，BF16 推論約 70）→
5. 用 lock 檔裝套件 → 6. `snapshot_download(repo, revision=<40碼hash>)`，`HF_HUB_CACHE` 指到會保留的硬碟 →
7. 測／訓 → 8. 收割（結果資料夾附 `required_artifacts.sha256`）→ 9. 停機＋回查

## 收割失敗時

- gputw：機器停了也能拿 `/vault` 的檔：`scripts/gputw_vault_fetch.sh <vault 路徑> <本機資料夾>`。金鑰要有 `vault:read` 權限（錯誤訊息 `missing required scope: vault:read`）。不要為了拿檔再開機。
- ssh 拉大檔容易中途斷線（Broken pipe）。大檔優先走 `/vault` 或分塊＋指紋。

## 測模型

- 原始模型和新模型：同機、同題、同程式，成對比。
- 判準先寫下，跑完照判準打 PASS／FAIL，不准事後改門檻。
- 推論引擎和 transformers 版本都鎖死，開跑前檢查。
- 訓練／測試／上線的提示逐字相同。

## fine-tune

- Unsloth QLoRA；打開 response-only masking，並抽查確認只剩答案被算 loss。
- `max_seq_length` 訓練與驗證一致。
- 設 `save_steps`，啟動時印出實際生效值。
- 接續訓練看進度條是否從中間開始。

## 量化

- FP8：`vllm serve <model> --quantization fp8`（Hopper／Blackwell；A100 不支援）。
- GGUF：`save_pretrained_gguf(..., quantization_method="q8_0"|"q4_k_m")`。
- 一律跟 16-bit 成對比「一致率」和「正確率」。
