"""df-graph 驗證模組：MSAL public client + 持久化 token 快取。

- login.py 跑一次互動式 device-code 登入，把 token（含 refresh token）存進快取檔。
- server.py / tools.py 之後只做 acquire_token_silent，靠 refresh token 自動續，無人值守。
- token 快取只存本機，原子寫入 + 權限 600。client_id / tenant 非機密。
"""
import os
import json
import atexit
import msal

_DEFAULT_CLIENT_ID = "2d456ab4-e636-4ba1-bd09-b0055727ab5e"
_DEFAULT_TENANT_ID = "619dfe56-7cb7-4ff8-b015-1bf1e7632258"
_DEFAULT_SCOPES = ["Mail.ReadWrite", "Mail.Send",
                   "Calendars.ReadWrite", "Calendars.ReadWrite.Shared"]


def _env_or_default(name, default):
    val = os.environ.get(name, "").strip()
    return val or default


def _scope_list(value):
    # env 是「完整取代」非「附加」；空字串/純空白/純逗號都視為未設，回預設。
    scopes = [s for s in (value or "").replace(",", " ").split() if s]
    return scopes or list(_DEFAULT_SCOPES)


# 公司預設值可直接用；若要給其他 tenant 或 IT 接手的 Azure app，可用環境變數覆寫。
CLIENT_ID = _env_or_default("DF_GRAPH_CLIENT_ID", _DEFAULT_CLIENT_ID)
TENANT_ID = _env_or_default("DF_GRAPH_TENANT_ID", _DEFAULT_TENANT_ID)
AUTHORITY = _env_or_default("DF_GRAPH_AUTHORITY",
                            f"https://login.microsoftonline.com/{TENANT_ID}")
# 以下皆「員工可自批」的 scope（本租戶實測可自行同意）。
# Calendars.ReadWrite.Shared：findMeetingTimes / getSchedule 讀同事 free/busy 所需。
SCOPES = _scope_list(os.environ.get("DF_GRAPH_SCOPES", ""))

# 需 IT admin consent，故不放進預設 SCOPES（放了會讓整個登入要管理員核准）：
#   - People.Read       → resolve_person 姓名→email（本租戶實測需管理員核准）
#   - User.ReadBasic.All → resolve_person 的目錄後備查詢
#   - Files.ReadWrite    → OneDrive
# 待 IT 核准後，把需要的項目加回 SCOPES 並重新 login.py 即可。
_ADMIN_CONSENT_SCOPES = ["People.Read", "User.ReadBasic.All", "Files.ReadWrite"]

# token 快取放在 agent-neutral 的 df-graph 目錄；舊 Claude/skill 路徑存在則一次性遷移。
_STATE_DIR = os.path.expanduser(_env_or_default("DF_GRAPH_STATE_DIR", "~/.df-graph"))
CACHE_FILE = os.path.join(_STATE_DIR, "graph_token_cache.json")
_OLD_CACHE_FILES = [
    os.path.expanduser("~/.claude/df-graph/graph_token_cache.json"),
    os.path.expanduser("~/.claude/om-daily-work-log/graph_token_cache.json"),
]

# 路徑自動推算（不寫死家目錄，方便同事在不同帳號下使用）
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_LOGIN_CMD = f"uv run --with msal python {os.path.join(_THIS_DIR, 'login.py')}"
_LOGIN_HINT = "尚未登入或 token 失效。請在終端機執行一次：\n  " + _LOGIN_CMD

# 需要「重新登入」（而非暫時性網路/AAD 問題）的錯誤碼
_REAUTH_ERRORS = {"invalid_grant", "interaction_required", "login_required",
                  "consent_required", "invalid_client"}

# 快取有多個帳號時，用此環境變數（username/email）指定要用哪個；單帳號可忽略。
_ACCOUNT_ENV = "DF_GRAPH_ACCOUNT"


def _select_account(accounts, want=""):
    """從快取帳號挑一個。want（DF_GRAPH_ACCOUNT）有給就比對 username（不分大小寫）；
    沒給且有多個帳號 → 取 username 排序後第一個（決定性，避免靠 MSAL 不保證的順序而選錯身分）。
    指定了卻找不到 → 回 None，交由呼叫端報清楚的錯。"""
    if not accounts:
        return None
    want = (want or "").strip().lower()
    if want:
        for a in accounts:
            if (a.get("username") or "").lower() == want:
                return a
        return None
    return sorted(accounts, key=lambda a: (a.get("username") or ""))[0]


class AuthError(RuntimeError):
    """需要使用者重新登入（login.py）。"""


class TransientAuthError(RuntimeError):
    """暫時性（網路/服務）問題，稍後重試即可，不需重新登入。"""


def _ensure_dir():
    os.makedirs(_STATE_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(_STATE_DIR, 0o700)  # makedirs(exist_ok) 不會收緊既有目錄
    except OSError:
        pass


def _atomic_write_600(path, text):
    """原子寫入並從建立起就是 0600（避免 write→chmod 之間的外洩窗口）。"""
    _ensure_dir()
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    os.chmod(path, 0o600)  # 明確再保險；失敗就讓它噴（機密檔不靜默吞）


# ---- 單例：app + cache 只建一次，atexit 只註冊一次 ----
_app = None
_cache = None


def _migrate_old_cache():
    """從舊路徑一次性複製 token 到新路徑。
    用複製（非搬移）以免任何沿用舊路徑的舊工具失效；新路徑優先。"""
    if os.path.exists(CACHE_FILE):
        return
    for old_cache_file in _OLD_CACHE_FILES:
        if not os.path.exists(old_cache_file):
            continue
        try:
            with open(old_cache_file, "r", encoding="utf-8") as f:
                _atomic_write_600(CACHE_FILE, f.read())
            return
        except OSError:
            pass  # 遷移失敗不致命：登入流程會在新路徑重建


def _get_app():
    global _app, _cache
    if _app is not None:
        return _app, _cache
    _ensure_dir()
    _migrate_old_cache()
    _cache = msal.SerializableTokenCache()
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:  # with：不漏 fd
                _cache.deserialize(f.read())
        except Exception:
            pass
    _app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=_cache)
    atexit.register(_persist)
    return _app, _cache


def _persist():
    if _cache is not None and _cache.has_state_changed:
        _atomic_write_600(CACHE_FILE, _cache.serialize())


def get_token():
    """回傳有效 access token；靜默續期。

    失效分兩類：AuthError（要重登）vs TransientAuthError（暫時性，稍後重試）。
    """
    app, _ = _get_app()
    accounts = app.get_accounts()
    if not accounts:
        raise AuthError(_LOGIN_HINT)
    account = _select_account(accounts, os.environ.get(_ACCOUNT_ENV, ""))
    if account is None:  # 指定了 DF_GRAPH_ACCOUNT 卻不在快取帳號中
        names = ", ".join(a.get("username") or "?" for a in accounts)
        raise AuthError(f"{_ACCOUNT_ENV}={os.environ.get(_ACCOUNT_ENV)!r} "
                        f"不在已登入帳號中：[{names}]")
    try:
        result = app.acquire_token_silent_with_error(SCOPES, account=account)
    except Exception as e:  # 網路層級例外 → 暫時性
        raise TransientAuthError(f"連線取得 token 失敗，請稍後重試：{e}")
    _persist()
    if result and "access_token" in result:
        return result["access_token"]
    err = (result or {}).get("error")
    if not result or err in _REAUTH_ERRORS:
        raise AuthError(_LOGIN_HINT + (f"\n(error={err})" if err else ""))
    raise TransientAuthError(
        f"token 取得失敗（error={err}），請稍後重試；持續失敗再執行：\n  {_LOGIN_CMD}")


def login_interactive():
    """互動式 device-code 登入，產生並存下 token 快取（含 refresh token）。"""
    app, _ = _get_app()
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print("INITIATE_FAILED " + json.dumps(flow, ensure_ascii=False))
        return False
    print(flow["message"], flush=True)
    result = app.acquire_token_by_device_flow(flow)
    _persist()
    if "access_token" in result:
        print("LOGIN_OK scopes=" + (result.get("scope") or ""))
        return True
    print("LOGIN_FAILED " + json.dumps(
        {k: result.get(k) for k in ("error", "error_description")}, ensure_ascii=False))
    return False
