# cli-install.md — Coolify CLI 安裝 + context 管理

> **何時讀**：新機器第一次接 Coolify、CLI 要升級、要加第二個 instance（staging / 多公司多 Coolify host）時。

> 依 Coolify CLI **v1.6.2** 驗證。新版可能多 / 少子指令，跑 `coolify --help` / `coolify <cmd> --help` 確認，**不要憑記憶猜 flag**。

---

## 是否已裝

```bash
which coolify          # 預期 /home/<user>/.local/bin/coolify（或 /usr/local/bin/coolify）
coolify version        # 印出 server 版本（透過當前 context 連） —— 注意是 subcommand 不是 --version flag
```

沒裝 → 走「安裝」段。已裝 → 走「升級」段。

---

## 安裝

CLI 是 single static binary（Go），無 runtime 依賴。三種主流安裝法：

### 方法 A：官方 install script（最簡單）

```bash
curl -sSL https://cdn.coollabs.io/coolify-cli/install.sh | bash
```

預設裝到 `~/.local/bin/coolify`。如果 `~/.local/bin` 不在 PATH，CLI 會印 warning；補到 `~/.bashrc`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### 方法 B：手動下載 binary（不執行 remote script）

```bash
# 看 https://github.com/coollabsio/coolify-cli/releases 找最新版本號
VERSION="1.6.2"
ARCH="$(uname -m)"   # x86_64 / aarch64
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"  # linux / darwin

mkdir -p ~/.local/bin
curl -L "https://github.com/coollabsio/coolify-cli/releases/download/v${VERSION}/coolify_${OS}_${ARCH}" \
  -o ~/.local/bin/coolify
chmod +x ~/.local/bin/coolify
~/.local/bin/coolify version
```

### 方法 C：package manager（如有）

部分 distro 在 community repo 有 `coolify-cli` package；版本通常落後 1–2 版。建議走 A / B。

---

## 升級

CLI 自帶 update 指令：

```bash
coolify update          # 印目前版本 + 最新版本，互動確認後升級
coolify update --force  # 不互動
```

`update` 走的是與 install script 同個 CDN。如果手動裝 binary 也可以重跑方法 B 蓋掉舊檔。

---

## 第一個 context

CLI 用「context」概念多 Coolify instance 共存（類似 kubectl context）。第一次設：

```bash
coolify context add zerozero \
  --url https://coolify.example.com \
  --token "$COOLIFY_TOKEN"
coolify context use zerozero        # 設為 default
coolify context verify              # 應印「Connection successful」之類訊息
```

`--token "$COOLIFY_TOKEN"` 從環境變數讀，**禁** 直接把 token 字面值寫進 shell history。建議：

```bash
read -s -p "Coolify token: " COOLIFY_TOKEN; echo
export COOLIFY_TOKEN
coolify context add zerozero --url https://coolify.example.com --token "$COOLIFY_TOKEN"
unset COOLIFY_TOKEN
```

`read -s` 不 echo 輸入；`unset` 後 shell history 也找不到。

---

## 多 instance / 切 context

```bash
coolify context list                 # 列所有，顯示哪個是 default
coolify context add staging --url https://coolify-staging.example.com --token "$STAGING_TOKEN"
coolify context use staging          # 改 default
coolify --context zerozero app list  # 一次性指定 context，不改 default
```

production 與 staging **必拆 context**（每個對應獨立 token + 獨立 team），這樣 token 爆炸半徑更小。

---

## `~/.config/coolify/config.json` 安全

CLI 把所有 context（含 token 明文）存在這個檔。**任何讀得到此檔的進程都拿得到所有 context 的 token**。

驗證：

```bash
stat -c '%a %n' ~/.config/coolify/config.json
# 預期：600 /home/<user>/.config/coolify/config.json
# 目錄 ~/.config/coolify 應是 700
stat -c '%a %n' ~/.config/coolify
# 預期：700 或 750
```

CLI 在新建 config 時會自動 chmod 600，但有時 mv / cp / restore 後權限會跑掉。發現非 600 → 立即 `chmod 600`，並評估這段時間有沒有別的 process 用同 user 讀過此檔。

config.json 結構：

```json
{
  "instances": [
    { "name": "zerozero", "fqdn": "https://coolify.example.com", "token": "<redacted>" },
    { "name": "staging",  "fqdn": "https://coolify-staging.example.com", "token": "<redacted>" }
  ],
  "lastupdatechecktime": "..."
}
```

⚠️ **禁** `cat ~/.config/coolify/config.json` 進 AI 對話 / paste 到 chat —— token 會直接外洩。檢查 schema 用 `jq 'keys'` 或 `jq '.instances[0] | keys'`（只看 key 名，不出 value）。

---

## context 換 token（rotation）

```bash
coolify context set-token zerozero <new-token>
coolify context verify                       # 確認新 token 通
# 確認後到 Coolify Web UI Security → API Tokens 刪舊 token
```

換 token 時順手檢查 `coolify context list` 沒有殘留 staging-old / test 之類的 context。

---

## config 位置

```bash
coolify config        # 印出 config 檔路徑（CLI 不會印 token 內容）
```

預設 `~/.config/coolify/config.json`（XDG Base Directory）。如果要改路徑（多帳號隔離），目前 CLI 沒提供 `--config` flag —— 改用 `HOME` 變數隔離：

```bash
HOME=/path/to/alt-home coolify context list
```

但 alt-home 仍要自己保證 `chmod 700`。一般情況用 context 切換就夠，不必動 config 路徑。

---

## 移除 / 重置

```bash
coolify context delete <name>     # 刪一個 context
rm -rf ~/.config/coolify          # 整個重來（會清光所有 context）
```

刪完到 Coolify Web UI 把對應的 API token 也撤銷（CLI 端刪不會 revoke server 端 token，token 還能被別處用）。
