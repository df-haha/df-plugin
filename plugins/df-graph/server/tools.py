"""df-graph 公開介面（facade）：彙整 graphcore / mailtools / caltools / peopletools。

工具實作已拆分到各領域模組；server.py 與 selftest.py 透過 `import tools; tools.X`
使用，介面保持穩定。低階符號（GRAPH/_req/_q/_enc…）也在此重新匯出，供測試與內部使用。

設計原則：無狀態、id-based；讀取零淨化（mode="full"）；錯誤分型；限流自動重試。
"""
# --- 低階（測試/內部會用到的符號一併重新匯出）---
from graphcore import (  # noqa: F401
    GRAPH, GraphHTTPError, _req, _q, _enc, _is_bad_id,
    _utc_iso, _utc_iso_days_ago, _strip_z, _fmt_addr, _recips, _fetch_paged,
    _wrap_untrusted, _PAGE_CEILING,
)

# --- 信箱 ---
from mailtools import (  # noqa: F401
    mail_list_recent, mail_search, mail_get, mail_download_attachment,
    folder_list, mail_send, mail_draft, mail_reply, mail_reply_draft, mail_forward,
    mail_mark_read, mail_move, mail_delete, _attachment_meta, _strip_html,
)

# --- 行事曆 ---
from caltools import (  # noqa: F401
    DEFAULT_TZ, calendar_list, calendar_get, calendar_create, calendar_find_times,
    calendar_get_schedule, calendar_rsvp, calendar_forward,
    calendar_update, calendar_delete,
)

# --- 人員 ---
from peopletools import resolve_person  # noqa: F401
