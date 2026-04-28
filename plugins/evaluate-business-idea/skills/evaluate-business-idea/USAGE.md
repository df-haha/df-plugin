# 使用方法

安裝這個 plugin 後（透過 `claude plugin install evaluate-business-idea`），skill 會在以下情境自動觸發。

## 觸發詞

- 「我有個 idea 想評估」
- 「這個值不值得做？」
- 「幫我打分」
- 「五維評估」
- 「該不該做這個 SaaS」
- 「市場調查 X」「法規調查 X」
- 「evaluate this idea」
- 「should I build X」

## 標準流程

### 1. 建立 idea 資料夾

在你想存放 ideas 的地方先建好 `ideas/` 目錄，第一次評估時 skill 會引導你複製 `idea-template/`：

```
你：我有個 idea 叫 my-saas-idea，想評估看看
Claude：（自動載入 evaluate-business-idea skill）
        好，我先幫你建 ideas/my-saas-idea/，請告訴我：
        - 問題定義：想解決什麼？
        - 初步構想：怎麼做？
        - 觸發點：為什麼想到這個？
```

### 2. 跑調查

```
你：幫我做 my-saas-idea 的市場調查
你：幫我搜尋 my-saas-idea 相關的 GitHub 開源專案
你：my-saas-idea 涉及廢棄物，幫我做法規調查
```

每一步 skill 會：
1. 用對應的 SOP（research-process.md 內定義）
2. 寫入 `ideas/my-saas-idea/{market-research,github-scan,regulation}.md`
3. 標註所有來源連結

### 3. 五維評估

```
你：我要做 my-saas-idea 的五維評估
```

Claude 會：
1. 讀取所有調查結果
2. 對 5 個維度逐一打分（0/1/2，每分附 2-3 句理由）
3. 跑紅線檢驗（維度 1 ≥ 1 且維度 2-5 ≥ 4）
4. 對照校準表（最像哪個已評估產品）
5. 寫入 `ideas/my-saas-idea/five-dim-eval.md`

### 4. 決策

```
你：幫我整理 my-saas-idea 的 decision.md
   總分 7 分，傾向做但要規劃深化路徑
```

## 進階：自訂校準範例

`references/evaluation-framework.md` 內建的校準表是某家環保科技公司的內部產品。你可以：

1. 評估 3-5 個自家已知產品（哪些有效、哪些是免洗）
2. fork plugin 後在 `references/evaluation-framework.md` 加進你公司的校準產品
3. 之後評估新 idea 時自動以你公司產品為基準

## 進階：自訂法規來源

`references/research-process.md` 預設台灣法規資料庫（`law.moj.gov.tw`）。其他地區替換：

- 美國：`law.cornell.edu`、`federalregister.gov`
- 英國：`legislation.gov.uk`、`gov.uk`
- 歐盟：`eur-lex.europa.eu`
- 日本：`elaws.e-gov.go.jp`

## 檔案地圖

```
plugins/evaluate-business-idea/
├── .claude-plugin/plugin.json
└── skills/evaluate-business-idea/
    ├── SKILL.md                 # 主入口
    ├── USAGE.md                 # 你正在看的檔案
    ├── references/
    │   ├── evaluation-framework.md  # 五維完整定義 + 校準 + 商業模式
    │   └── research-process.md      # 市場 / GitHub / 法規 SOP
    └── idea-template/           # 6 檔 idea 資料夾模板
        ├── README.md
        ├── market-research.md
        ├── github-scan.md
        ├── regulation.md
        ├── five-dim-eval.md
        └── decision.md
```

## FAQ

**Q：一定要做完所有調查才能打分嗎？**
A：市場 + GitHub 是必要的，沒做就打分等於憑感覺。法規視 idea 性質決定（受管制產業必做）。

**Q：總分 5-7 分怎麼決定要不要做？**
A：看「深化路徑」是否可行。有清楚加深方法且成本可控 → 做。只能停在 5-7 → 變成工具買斷模式，不要當主業。

**Q：適合純 ToC App 嗎？**
A：框架原本是設計給 ToB / ToB2C 工具型產品。純 ToC 娛樂型 App（遊戲、社交）不太適用。

**Q：能用在客戶提案前的 pre-screening 嗎？**
A：可以，這是它最有價值的用法。客戶問「這能不能做」之前，你先用五維打分，太低分的直接勸退或改賣斷。

**Q：分數是主觀的，怎麼確保一致性？**
A：(1) 每分必須附理由 (2) 跟校準表對照 (3) 紅線檢驗。不同人打分可能差 1-2 分，但「深度 vs 免洗」的判定通常不會錯。
