# devin 模型速查（2026-11-21 實跑；可用模型依帳號方案而定）

來源：`devin models` 實跑輸出，原文在 `models-full.md`。價格單位＝美元／每 100 萬 token（輸入 · 快取輸入 · 輸出）。
重抓：`devin models`。清單會變，下指令前拿不準就重抓。

## 數量

- 55 個模型家族、1302 個模型代號；其中 995 個是 Fusion 組合。
- 指令（對 `devin models > models.txt` 的完整輸出跑；`models-full.md` 已刪掉 Fusion 列，對它跑不出這兩個數字）：`grep -cE '^\s+[a-z0-9].* \[' models.txt` → 1302；`grep -cE '^\s+fusion-' models.txt` → 995。

## 代號規則

- `--model` 接受三種寫法：家族代號（`swe-2`）、別名（`opus`）、完整代號（`swe-2-high`）。
- 多數完整代號＝`<家族>-<思考強度>[-fast|-priority|-lightning]`。思考強度：`none|minimal|low|medium|high|xhigh|max`（各家族支援的不同）。
- 有例外：`adaptive`、`claude-5-fable-low`（家族是 `claude-fable-5`）、`glm-5-2-max-1m`、`swe-1-7`。**代號從 `devin models` 輸出複製，不自己拼。**
- `-fast`（Claude）、`-priority`（GPT）、`-lightning`（SWE）＝加速版，價格較高。倍數依模型而異：多數 2 倍，GPT-5.5 Priority 是 2.5 倍，SWE-2 Lightning 是「免費版的付費加速版」。
- **派工一律寫完整代號。** 多數別名指向舊版，見下表。

## 別名（多數指到舊版，不要用）

| 別名 | 實際指向 | 問題 |
|---|---|---|
| `opus` | Claude Opus 5 | 不是 Opus 5.5 |
| `fable` | Claude Fable 5.1 | 不是 Fable 5.2 |
| `claude`、`sonnet` | Claude Sonnet 5 | 不是 Sonnet 5.5 |
| `gpt` | GPT-5.5 | 不是 GPT-6 系列 |
| `gemini` | Gemini 3.8 Flash | — |
| `swe` | SWE-2 | 可用 |

## 免費模型（標示 Free）

| 代號 | 上下文 |
|---|---|
| `swe-2-medium`、`swe-2-high`、`swe-2-max` | 262K |
| `swe-2-flash-medium`、`swe-2-flash-high`、`swe-2-flash-max` | 262K |

Fusion 組合的副手（Sidekick）是 SWE-2 系列時免費，主模型照價收費。

## 常用付費模型

| 家族 | 代號範例 | 上下文 | 價格（輸入 · 快取 · 輸出） |
|---|---|---|---|
| GPT-6 Luna | `gpt-6-luna-medium` | 1M | 0.1 · 0.01 · 0.5 |
| Claude Haiku 5.5 | `claude-haiku-5-5-medium` | 1M | 0.1 · 0.01 · 0.5 |
| Adaptive | `adaptive` | — | 0.5 · 0.1 · 2 |
| GPT-6.2 Sol | `gpt-6-2-sol-xhigh` | 1.05M | 2 · 0.1 · 10 |
| GPT-6 Sol | `gpt-6-sol-high` | 1M | 2 · 0.2 · 10 |
| Claude Sonnet 5.5 | `claude-sonnet-5-5-high` | 1M | 2 · 0.1 · 10 |
| Gemini 4 Carbon | `gemini-4-carbon-high` | 1.05M | 2 · 0.1 · 10 |
| Kimi K3.1 Pro | `kimi-k3-1-pro-high` | 1.05M | 2 · 0.2 · 10 |
| GLM-5.3 | `glm-5-3-high` | 1.05M | 1.4 · 0.26 · 4.4 |
| Claude Opus 5.5 | `claude-opus-5-5-high` | 1M | 4 · 0.2 · 20 |
| Claude Fable 5.2 | `claude-fable-5-2-medium` | 1M | 8 · 0.2 · 40 |
| GPT-6.2 Astra | `gpt-6-2-astra-high` | 1.05M | 8 · 0.4 · 40 |
| GPT-6 Astra | `gpt-6-astra-high` | 1M | 10 · 1 · 50 |

其餘家族（Gemini 3.x Flash、Gemini 4 Argon／Hydrogen／Neon、GPT-5.x、GPT-6.1、Grok、Muse Wonder、Nemotron、DeepSeek、Kimi K2／K3、SWE-1.x、舊版 Claude）見 `models-full.md`。

## Fusion（主模型＋免費副手）

- 代號：`fusion-<主模型代號>-sidekick-<副手代號>`。副手可選 SWE-2（`swe-2-medium`、`swe-2-high`、`swe-2-flash-max`，免費），也可選 GPT、GLM、Claude Sonnet 5.5、Haiku 5.5 等付費模型（2026-11-22 `devin models` 實跑）。
- 例：`fusion-claude-fable-5-2-high-sidekick-swe-2-high`（主模型 8 · 0.2 · 40，副手免費）。
- 分工（官方文件 https://docs.devin.ai/cli/fusion 原文）：「The lead model owns your task — it plans, reasons through the hard parts, and reviews the work — while the sidekick handles the mechanical implementation.」兩個模型各照自己的價格計費。官方推薦組合是 Fable＋SWE-2。
- 適合寫 code（`code`、`edit`）；審查幾乎都在讀和想，副手能分擔的不多。
- `-p` 非互動模式下的行為：未驗證。

## 本 skill 的預設

| 用途 | 預設模型 | 理由 |
|---|---|---|
| 問答、偵察 | `swe-2-medium` | 免費 |
| 寫 code | `swe-2-high` | 免費；失敗兩次再升級 |
| code review | `gpt-6-2-sol-xhigh` | 與 Claude 不同家族，對齊現行 Codex 審查模型 |

未查：付費模型在你的方案下是否另外計費、額度多少。看用量請到 https://app.devin.ai 。
