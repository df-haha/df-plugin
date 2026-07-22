# Deep Research CHANGELOG

> Repo 發佈版 **v2.0.0** 對應 upstream v.260625 + 收錄修正。
> 本檔記錄各版本的功能異動歷程，從 SKILL.md 移出以精簡主文件。

---

## v.260625（依 Workflow 自動切換編排模式 + 跨平台移植）

**動機**：此前 Synthesis → Citation Verify → QG → 補查重跑這段閉環全靠主對話讀 SKILL.md 的 prose 自律執行，在長對話 / 高 context 壓力下會漂移（漏跑 verify、補查只跑 1 輪就停、重跑 QG 被略過）。

**機制**：技能啟動時偵測當前對話 system context 是否含 `Ultracode is on` 字串：

- **偵測到 Ultracode**：Synthesis 階段改呼叫 `Workflow({ scriptPath: "${SKILL_DIR}/references/synthesis-pipeline.workflow.js" })` 跑閉環。流程順序、subagent 數量、補查輪數全由 JS 程式碼硬性保證，漂移率歸零
- **未偵測到**：完全沿用 Task-based prose 編排，行為一致（保留 streaming 過程可見的 UX）

**Phase 1/2 / Gap Analysis / Devil's Advocate 維持 Task 編排**（這段並行多工穩定且 streaming 利於 debug），只有「合成 → 驗證 → 品質 → 重跑」這段確定性最關鍵的閉環交給 Workflow。

**Windows 跨平台移植**：移除 openssl / POSIX grep / `ls | sort -V` / `python3` 依賴，改用 Claude Code 內建工具（Grep/Glob/Read/WebFetch）與 `python`（Windows 標準命令名），PowerShell 亦可跑。

---

## v.260621-3（業界 SOTA 對標）

**(1) 多輪辯論 + voting**（MARCH/Free-MAD pattern，業界論文驗證減幻覺最高 96%）
- Devil's Advocate 原 1 輪反論改為 3 輪 reflexion
- Round 1 反論 → Round 2 結論方反駁 → Round 3 強化反論
- 獨立 Judge Agent（agent-config §15）仲裁，不由 Devil's Advocate 自評
- 詳見 agent-config.md §11 §15

**(2) 資料點級評級強制**（取代章節級星級評分）
- 每個資料點強制標 🟢/🟡/⚠️/⬜/❗ + L1-L6 + URL + 日期
- QG 統計分母改用資料點級（實測發現章節級分母過小導致閘門誤差）
- 詳見 frameworks.md §8 + synthesis-spec.md + output-template.md

**(3) LLM-as-judge 5 項 0-1 分 Rubric**（FactScore/RAGAS/ALCE 對標）
- factual_accuracy / citation_accuracy / completeness / source_quality / tool_efficiency
- 由 §14 Citation Verifier 輸出
- quality-gate.md 擴展為三閘門（A 分佈 + B 加權聚合 + C LLM-judge）
- 跨版本品質可量化比較

---

## v.260621-2

- **Citation Verification Subagent**：Synthesis 後、QG 前強制執行
- **投資專項 Subagent**：公司研究 + 投資決策維度時觸發
- **GTM 行銷專項 Subagent**：產品研究 + GTM 維度時觸發
- **獨立來源 5 規則操作化**：同一原始研究的 N 篇轉述 = 1 個獨立來源；3 個來源至少 2 個追溯到不同原始研究；官方公司來源不與公關稿轉述媒體混算；AI 摘要不算獨立來源；同作者 N 篇 = 1 個
- **30+ 常見網域 L 級對照表**：L1-L6 各級附具體網域 mapping
- **同級來源衝突仲裁 5 步規則**：日期較新 → 資料原始日 → 鏈條較短 → 歷史準確率 → 並列
- **QG 雙閘門**（A 分佈 + B 加權聚合）+ 補查閉環（最多 2 輪 QG，禁止靜默通過）
- **Subagent Model 分層配置**：~80% 走 Sonnet，省 50-65% token 成本
- **共用安全前言**：反 prompt-injection 鐵律 + 工具白名單 + 不確定就標 ⬜ + run_id 落地核對

---

## v.260621

- **Exa rate limit 修正**：實際為 /search 10 QPS（舊版誤寫 1 QPS，10 倍偏差），解鎖並行
- **多視窗並發隔離**：NONCE 目錄後綴 + `.run-meta` 身分證 + 絕對路徑釘住（禁止「取最新目錄」策略）
- **斷點恢復身分核對**：多視窗候選時列出所有 `.run-meta` 摘要讓用戶選

---

## v.260315（效能提升 + 維度擴展版）

**研究維度擴展**：
- **投資決策維度**（P3，公司研究）：第三方估值整理（DCF/可比倍數/情境表）、市場預期 vs 錯誤定價分析、資本配置效率（股息/回購/再投資歷史回報）、投資論點壓力測試（與 Devil's Advocate 聯動）。限定為「整理既有第三方分析」，不自行從零建模。
- **GTM 行銷維度**（P3，產品研究）：目標客群痛點優先級、獲客渠道效率對比、定位切角與差異化敘事、競品行銷策略分析。

**效能提升**：
- **Phase 1 每批並行上限 4→6**：深度分析從「2 批 x 4 個」縮減為「1-2 批 x 6 個」，預估總研究時間縮短 20-25%。
- **Phase 2 每批 4→5**：深度查更吃 context，相對保守。

---

## v.260313（批判性思維強化 + 多維度升級）

**批判性思維強化**：
- **Devil's Advocate Subagent**：獨立 subagent 主動搜尋否定結論的證據，深度分析時強制啟動
- **Steel-man 反論框架**：對 3 個核心結論構建最強反面論述，搜索反論證據，評估結論韌性（強/中/弱）
- **假設審計**：逆向推導每個結論的隱含前提假設，標記高風險假設（穩固度分級）

**驗證強化**：
- **三角驗證強制規則**：關鍵數據點（財務、市佔率、用戶數）必須 ≥3 獨立來源交叉驗證
- **來源可信度 6 級分級**：L1 一手來源 → L6 AI/聚合，衝突裁定按等級優先
- **反事實推理量化**：伴隨事實矩陣 + 驗證通過率判定標準（≥80% 強力支持 → <40% 顯著削弱）
- **衝突影響度評估**：按 ROI 決定是否啟動 Resolution Search（高/中/低影響）
- **信心聚合規則**：加權信心分數公式，按維度重要性加權計算整體信心指數
- **時效性驗證**：4 級時效標記（即時/近期/需注意/過時風險），>20% 過時觸發 QG 警告

**搜索升級**：
- **反向查詢**：每維度至少 20% 查詢為反向查詢，避免確認偏誤
- **時間切片搜索**：4 段（即時/近期/歷史/起源），用於趨勢分析
- **多語言配比矩陣**：依研究類型自動配置語言比例（如技術研究英文 80%、社會議題當地語言 60%）
- **語意擴展控制**：每維度最多 5 組查詢變體，擴展深度最多 2 層

**品質與架構升級**：
- **動態 QG 門檻**：依研究類型調整信心門檻（投資決策 ≥80%、標準 ≥70%、學習型 ≥60%）
- **Quality Gate 6 層**：新增時效性檢查 + 邏輯一致性檢查（Steel-man 與決策建議一致性）
- **研究深度配置矩陣**：快速/標準/深度 3 級，自動配置 subagent 數、查詢數、時間切片等 12 項參數
- **維度優先級 P1/P2/P3**：8 種研究類型的所有維度標記優先級，用於快速掃描時的維度篩選
- **決策 5 級評分**：替代簡單的正面/中性/負面
- **二階效應分析**：情境展望中推演利害關係人反應和連鎖效應
- **框架選擇矩陣**：依研究類型自動選擇必用/可選框架
- **研究可重現性**：報告附錄記錄完整研究參數
- **工具失敗分類重試**：依 HTTP 狀態碼（429/403/503/timeout）決定不同重試策略
- **研究成果 README**：QG 通過後自動生成使用指南，包含檔案導航、符號圖例、閱讀建議
