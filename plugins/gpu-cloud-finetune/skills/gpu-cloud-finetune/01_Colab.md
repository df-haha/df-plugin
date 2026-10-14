# Colab（Google Colab）

> 適合：先試一下、1 小時內結束的測試；推論評測（跑考卷）也很順。
> 我們 2026-09～10 用 Colab CLI（命令列工具）開 A100 80GB 跑過多次 Gemma 4 31B 推論評測。
> QLoRA 訓練的發射腳本也寫好了（2026-10-06），但那次 smoke（小量試跑）後就讓位給 gputw，**還沒在 Colab 上完整跑完一次訓練**。

## 1. 帳號與計費

- 計費單位：CU（compute unit，運算點數）。Google AI 訂閱會送點數。
- A100 80GB（high-mem）實測耗 **6.77 CU／小時**（2026-09）。
- 查餘額：`colab usage`
- ⚠️ 第一次登入的帳號可能是 0 點，要確認登入的是「有訂閱的那個帳號」。

## 2. 安裝與登入

```bash
uv tool install google-colab-cli     # 或 pip install google-colab-cli
colab usage                          # 第一次跑會跳 Google 登入
```

- 登入憑證存在 `~/.config/colab-cli/token.json`。
- 換帳號：刪掉這個檔，再跑一次 `colab usage` 重新登入。

## 3. 開機

```bash
colab new -s mybox --gpu A100 --high-mem
```

- `--gpu` 可選：`T4`、`L4`、`G4`、`H100`、`A100`（看你的訂閱等級有哪些）。
- `--high-mem`：要 Pro／Pro+。我們實測加了它拿到的是 **A100 80GB**。沒加是不是只有 40GB，我們沒驗證。
- 31B BF16 要 62GB，所以一定要加 `--high-mem`。

**最省事的用法**：一次性跑完自動關機

```bash
colab run --gpu T4 my_test.py arg1 arg2
```

開新機器 → 跑腳本 → 自動釋放。短測試用這個，不會忘記關機。

## 4. 跑程式

```bash
colab exec -s mybox -f script.py --timeout 3600
echo 'import torch; print(torch.cuda.get_device_name())' | colab exec -s mybox
colab install -s mybox -r requirements.txt
```

- `--timeout` 預設只有 30 秒，長任務要加大。
- `--env KEY=VALUE` 可以傳環境變數（可重複）。
- ⚠️ `colab exec` 是 **Python kernel，不是 shell**。要跑背景程式（例如 vLLM 服務）要這樣：

```python
import subprocess
subprocess.Popen(
    ["vllm", "serve", "<模型ID或本機路徑>", "--port", "8000"],
    stdout=open("/content/vllm.log", "w"), stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL, start_new_session=True,
)
```

`start_new_session=True` 實測程式能活下來。

其他指令：`colab repl`、`colab console`、`colab ssh`、`colab status -s mybox`、`colab log -s mybox`、`colab drivemount`（掛 Google Drive）。

## 5. 傳檔

```bash
colab upload -s mybox ./local.tar /content/local.tar
colab download -s mybox /content/results.tar ./results.tar
```

⚠️ **單檔超過約 100MB 會回 HTTP 500**。解法：切成 40MB 小塊上傳，機上再合併：

```bash
split -b 40m big.tar part_
for f in part_*; do colab upload -s mybox "$f" "/content/parts/$f"; done
# 機上：cat /content/parts/part_* > /content/big.tar，再比對 md5
```

實測 921MB 約 6 分鐘。

## 6. 資料會留在哪

- 機器上的東西，關機就沒了。
- 要留：下載回本機，或 `colab drivemount` 存到 Google Drive。

## 7. 停機＋回查

```bash
colab stop -s mybox
colab sessions          # 一定要看到 "No active sessions" 才算停了
colab usage             # 看花了多少
```

停機保險（開機後立刻掛上，見 07）：每 2 分鐘檢查一次，看到「完成」或「失敗」標記檔、或超過硬上限時數，就自動下載結果＋停機＋回查。

## 8. 實際花費（2026-09）

| 任務 | 時間 | 花費 |
|---|---|---|
| Gemma 4 31B BF16＋vLLM，開機到模型可用 | 約 20 分鐘 | — |
| 712 張圖、每張 5～7k token 提示，沒開 prefix cache（重複提示快取） | 43 分鐘 | — |
| 上面整趟（含開機、上傳、收尾） | 約 1 小時多 | 8.07 CU |
| 文字題端到端評測（2026-09-28 三趟） | 每趟約 30～60 分鐘 | 2.99～4.79 CU |
| 31B＋LoRA 重排評測，約 500 題（2026-10-07） | 開機到停機 27 分鐘 | 2.76 CU |
| QLoRA 訓練發射後 15 分鐘讓位停機（2026-10-06） | 15 分鐘 | 1.6 CU |

省點數：同樣的長提示重複用，就開 prefix cache。

## 9. 用 Colab 跑訓練

做法跟 gputw／RunPod 一樣，用同一支訓練 wrapper（包裝腳本）、同一組設定，只是換平台：

1. 本機把程式、資料、lock 檔打包，分塊上傳（第 5 節）。
2. 機上建乾淨 venv、照 lock 檔裝套件，先跑 import 檢查（Colab 上第一次跑 Unsloth，這關最容易出事，排最前面）。
3. 資料逐檔比對指紋、模型釘死 revision 下載、跑 `gpu_preflight.py`。
4. 發射訓練。跑完把小檔放 `results/`，adapter 切成 40MB 小塊＋指紋清單，方便下載。
5. **Colab 機器裡沒辦法自己關機**，停機一律靠本機的停機保險＋絕對時限。

### 兩個平台同時搶機時

常見做法是 Colab 和 gputw 同時準備，誰先拿到機器就用誰。規則：**同一時間只准一邊訓練**。
- gputw 已經搶到機器（或它的停機保險在跑）→ Colab 不開機。
- Colab smoke 先通過 → 收掉 gputw 的搶機迴圈。
- 兩邊都已開機 → 保留一邊，另一邊立刻停機並回查。

## 10. 網頁版 notebook（替代做法）

不想用 CLI 的話：

1. 本機把資料和腳本打包，上傳到 Google Drive。
2. Colab 網頁「Secrets」設 `HF_TOKEN`。
3. 「執行階段」選 A100（要 80GB 就選 high-RAM）。
4. 一格一格執行。
5. 斷線後用同一個輸出資料夾接續。
6. **跑完手動「中斷連線並刪除執行階段」**。

缺點：要手動點、手動關，沒有自動停機保險。

## 11. 踩坑表

| 症狀 | 原因 | 解法 |
|---|---|---|
| 上傳回 HTTP 500 | 單檔 >約 100MB | 切 40MB 分塊 |
| 背景程式跑一下就死 | `colab exec` 是 Python kernel | `subprocess.Popen(..., start_new_session=True)` |
| 腳本說「VRAM 不夠」擋掉 80GB 卡 | 門檻寫死 90000MB（給 96GB 卡用的） | 門檻改成可調，A100 設 80000 |
| 偵測不到 vLLM 程序 | 用 `python -m vllm...` 啟動，`pgrep "vllm serve"` 對不上 | 統一用 `vllm serve` 啟動 |
| 登入後點數 0 | 登錯帳號 | 刪 token.json 換帳號 |
| 開機後幾分鐘就被自己的發射腳本 ABORT（中止） | 發射腳本的「背景程式還活著嗎」檢查誤判（同一檢查兩天前正常，原因未查明） | 看 log 確認是誤判就重發；2026-10-07 那次被誤擋，只花 0.08 CU |
| 監看腳本誤報失敗 | 監看的關鍵字太寬：說明文字裡出現「FATAL」、環境變數名稱裡有 `HARD_TIMEOUT_H` | 關鍵字要帶上下文（例如行首、冒號）；或直接看標記檔，不 grep log |
| 環境 | driver 580、系統 Python 3.13（另有 3.12） | vLLM 要 3.12 就建 venv 指定 3.12 |
