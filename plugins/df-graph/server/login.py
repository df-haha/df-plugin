"""一次性互動式登入：產生並存下 token 快取（含 refresh token）。

執行（從你的 df-graph 目錄）：
  uv run --with msal python login.py

之後 server 會靠 refresh token 自動續，不需再跑——除非密碼變更/太久沒用/公司政策強制。
"""
import auth

if __name__ == "__main__":
    ok = auth.login_interactive()
    raise SystemExit(0 if ok else 1)
