"""對真實 Graph 的煙霧測試（登入後跑；唯讀、不打擾他人）：
  uv run --with msal python selftest.py

每項檢查獨立 try，最後印 PASS/FAIL 統計並以 exit code 反映（CI 友善）。
不做破壞性動作（不寄信/不 RSVP/不刪除）。
"""
import sys
import json
import tools

_passed, _failed = [], []


def check(name, fn):
    try:
        out = fn()
        _passed.append(name)
        print(f"[PASS] {name}")
        return out
    except Exception as e:  # noqa: BLE001 — 煙霧測試要把每項隔離
        _failed.append((name, repr(e)))
        print(f"[FAIL] {name}: {e!r}")
        return None


def _first_id(listing_json_after_summary):
    arr = json.loads(listing_json_after_summary.split("\n", 1)[1])
    return arr[0]["id"] if arr else None


def t_folder_top():
    out = tools.folder_list()
    arr = json.loads(out)
    assert isinstance(arr, list) and arr, "頂層資料夾應非空"
    assert "child_count" in arr[0], "應含 child_count 欄位"


def t_folder_children():
    out = tools.folder_list(parent_id="inbox")
    json.loads(out)  # 可能為空，能解析即可


def t_mail_list_and_get():
    listing = tools.mail_list_recent(7)
    mid = _first_id(listing)
    if not mid:
        return
    body = tools.mail_get(mid)            # default concise
    assert "Body:" in body, "mail_get 應含 Body 段"


def t_calendar_list_tz():
    out = tools.calendar_list(14, 7)
    assert tools.DEFAULT_TZ in out, "calendar_list 摘要應標明時區"


def t_calendar_get():
    cal = tools.calendar_list(14, 7)
    eid = _first_id(cal)
    if not eid:
        return
    detail = json.loads(tools.calendar_get(eid))
    assert "attendees" in detail and "body" in detail


def t_find_times_self():
    out = tools.calendar_find_times("", duration_minutes=30, max_suggestions=2)
    assert ("suggestion" in out) or ("No common free slot" in out)


def t_resolve_person():
    # People.Read：解析自己的名字片段（容忍查無）
    out = tools.resolve_person("a")
    assert ("Resolved" in out) or ("ERROR" in out)


if __name__ == "__main__":
    check("folder_list (top level)", t_folder_top)
    check("folder_list (child folders)", t_folder_children)
    check("mail_list_recent + mail_get(concise)", t_mail_list_and_get)
    check("calendar_list (tz label)", t_calendar_list_tz)
    check("calendar_get (full attendees/body)", t_calendar_get)
    check("calendar_find_times (self, Shared scope)", t_find_times_self)
    check("resolve_person (People.Read)", t_resolve_person)

    print(f"\n== SELFTEST: {len(_passed)} passed, {len(_failed)} failed ==")
    if _failed:
        for n, e in _failed:
            print(f"   FAIL {n}: {e}")
        sys.exit(1)
