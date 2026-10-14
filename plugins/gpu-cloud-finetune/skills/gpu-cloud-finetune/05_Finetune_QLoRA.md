# Fine-tune（QLoRA，用 Unsloth）

> QLoRA：模型本體用 4-bit 壓著不動，只訓練旁邊加上去的一小塊（LoRA adapter，外掛小模組）。很省記憶體，31B 模型一張 32GB 卡就能訓。
> Unsloth：把 QLoRA 做得又快又省的開源套件。官方文件：https://unsloth.ai/docs

## 1. 最小流程（文字模型）

```python
from unsloth import FastLanguageModel
from unsloth.chat_templates import train_on_responses_only
from datasets import load_dataset
from trl import SFTTrainer, SFTConfig

max_seq_length = 4096

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/<模型>-bnb-4bit",
    max_seq_length=max_seq_length,
    load_in_4bit=True,
)
model = FastLanguageModel.get_peft_model(
    model,
    r=16, lora_alpha=16, lora_dropout=0,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj"],
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)

dataset = load_dataset("json", data_files="train.jsonl", split="train")  # 每筆要有 "text"

trainer = SFTTrainer(
    model=model, tokenizer=tokenizer, train_dataset=dataset,
    args=SFTConfig(
        dataset_text_field="text",
        max_seq_length=max_seq_length,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        num_train_epochs=3,
        learning_rate=2e-4,
        logging_steps=10,
        save_steps=100,          # 定期存檔，被砍還能接著跑
        output_dir="/workspace/outputs",
        optim="adamw_8bit",
        seed=3407,
        report_to="none",
    ),
)
# 只對「模型的回答」算 loss，見下面第 3 點（標記字串依模型的對話模板而定）
trainer = train_on_responses_only(
    trainer,
    instruction_part="<使用者段開頭標記>",
    response_part="<模型段開頭標記>",
)
trainer.train()
```

**視覺模型**（看圖的）：改用 `FastVisionModel` 和 `UnslothVisionDataCollator`，照 Unsloth 官方對應模型的 notebook 寫。

## 2. 我們實際用的設定（Gemma 4 31B 看圖分類）

| 項目 | 值 |
|---|---|
| base | `unsloth/gemma-4-31B-it-unsloth-bnb-4bit`（釘 revision） |
| LoRA | r=64、alpha=128、dropout=0.05 |
| 學習率 | 2e-4，3 epochs |
| batch | 1 × 梯度累積 8 |
| 卡 | RTX PRO 6000 96GB，峰值用 25GB |
| 速度 | 7.4 秒／步；1,224 步約 5 小時，約 US$5.5 |

## 3. 一定要注意的事

### (1) 只對回答算 loss（response-only masking）

loss（訓練誤差，越低代表越像標準答案）預設會連「系統提示＋題目」一起算。每筆提示都一樣時，模型只是在背提示，loss 很低但沒學到東西。
我們實測：打開後 loss 從 0.02 變 0.21（高一個量級）——這才是真的在學答案。
打開後要**抽查幾筆**，確認被算 loss 的只剩答案那段。

### (2) 版本一定要鎖

不鎖版本直接 `pip install unsloth trl datasets`，我們遇過兩次：
- torch 被默默從 2.11 換成 2.10，連帶 torchaudio 壞掉。
- transformers 被換版本。
- 加了 `-q` 所以完全沒提示。

做法：

1. 第一次在機上裝好、跑通後 `pip freeze` 存下來。
2. ⚠️ `pip freeze` **不能直接當 lock**：裡面有映像自帶的系統套件（例如 `python-apt`），照裝會失敗，要剔掉；`+cu128` 這種版號也會不見，要另外指定 PyTorch 的 index。
3. 之後每次開機都用這份 lock 裝，裝完跑 `gpu_preflight.py`。
4. ⚠️ **訓練環境和推論環境可能要不同版本**：Unsloth 綁比較舊的 transformers，vLLM 要比較新的。兩邊各自鎖死、分開建環境；**評測一律在推論環境跑**。我們混用過，輸出差到完全不能比。

### (3) 訓練長度和驗證長度要一樣

`max_seq_length` 只改一邊，答案那段會被截掉，要學的部分全變成「不算分」——**模型什麼都沒學到，也不會報錯**。

### (4) 確認「接續訓練」真的接上了

中斷後從 checkpoint 接著跑，**看進度條**：應該從 `0/100` 直接跳到 `76/100`。
別看 `train_runtime` 之類的數字，那個分不出來是接續還是重跑。

### (5) 資料格式坑（視覺模型）

- 同一筆訊息裡有的 content 是字串、有的是 list（圖＋文），直接 `Dataset.from_list` 會被 PyArrow 擋。→ 直接餵 Python list，不轉 HF Dataset。
- collator（把資料打包成一批的元件）要的是 PIL 圖片物件，不吃檔案路徑。→ 寫一個逐筆開圖的 Dataset（一次全讀進記憶體要十幾 GB）。

### (6) dry-run 不能假成功

我們的腳本曾經把「模型載入失敗」吞掉，還印「Dry Run 完成」。錯誤一律往上拋，不要只記 log。

## 4. 合併與匯出

訓練完得到的是 adapter（小檔，幾百 MB）。三種用法：

| 用法 | 怎麼做 | 給誰用 |
|---|---|---|
| 不合併，直接掛 | vLLM `--enable-lora --lora-modules name=adapter路徑` | 快速測試 |
| 合併成 16-bit 完整模型 | `model.save_pretrained_merged("out", tokenizer, save_method="merged_16bit")` | vLLM 正式上線 |
| 轉 GGUF | `model.save_pretrained_gguf("out", tokenizer, quantization_method="q4_k_m")` | llama.cpp／Ollama、筆電、邊緣裝置 |

GGUF 常用等級：`q4_k_m`（4 bit，最常用）、`q5_k_m`、`q8_0`（8 bit，幾乎不掉分）、`f16`。

⚠️ 我們遇過：Gemma 4 轉 GGUF 失敗（`KeyError: image_mean`），因為模型 repo 只有 `processor_config.json`、沒有 `preprocessor_config.json`。16-bit 合併版本身是好的，可以先上 vLLM，GGUF 之後離線再轉。

## 5. 訓練前自問

- 這個 fine-tune 要贏過誰？先用同一份題目測 base 模型（見 04）。
- 有沒有留一份「沒訓練過的類別」來測？我們發現 fine-tune 後，沒訓過的東西會 100% 被硬塞進最像的已知類別——模型不會說「我不知道」，要靠外部機制擋。
