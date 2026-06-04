"""df-graph 信箱工具：讀（清單/搜尋/單封/附件）、寫寄（送信/草稿/回覆/轉寄/附件上傳）、
整理（標已讀/移動/刪除/子資料夾）。

設計：無狀態、id-based；附件下載寫檔不灌 base64 進對話；大附件自動分段上傳。

⚠️ 安全提醒：mail_send / mail_draft 會讀取呼叫端指定的本機檔案當附件。為降低 prompt-injection
造成的外洩，_collect_files 內建附件護欄：一律擋隱藏檔/dotfile 與敏感目錄（.ssh/.aws/.gnupg/
.config…），並支援以環境變數 DF_GRAPH_ALLOWED_ATTACH_DIRS 設定可寄目錄白名單（未設＝一般檔案放行）。
讀取類（mail_get 及清單 preview）回傳的外部內容會用 _wrap_untrusted / 不可信註記標示，提示模型
不要把內文當指令。
"""
import os
import re
import json
import base64
from html.parser import HTMLParser
import html as _htmlmod

from graphcore import (GRAPH, GraphHTTPError, _req, _q, _enc, _is_bad_id,
                        _utc_iso_days_ago, _fmt_addr, _recips, _fetch_paged, _PAGE_CEILING,
                        _wrap_untrusted, _UNTRUSTED_FIELDS_NOTE)

_SEL = "id,subject,from,receivedDateTime,bodyPreview,isRead"


class _TextExtractor(HTMLParser):
    """極簡 HTML→純文字（純 stdlib，不依賴 bs4）；丟掉 script/style，區塊標籤換行。"""
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        if tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _strip_html(html_str):
    """把 HTML 轉成乾淨純文字（concise 模式的後備；主要仍靠伺服器端轉換）。"""
    p = _TextExtractor()
    p.feed(html_str or "")
    txt = _htmlmod.unescape("".join(p.parts))
    txt = re.sub(r"[ \t]+", " ", txt)
    return re.sub(r"\n{3,}", "\n\n", txt).strip()


def _render_messages(summary, msgs, truncated=False):
    items = [{
        "id": m.get("id"),
        "subject": m.get("subject"),
        "from": _fmt_addr(m.get("from")),
        "date": m.get("receivedDateTime"),
        "is_read": m.get("isRead"),
        "preview": (m.get("bodyPreview") or "")[:200],
    } for m in msgs]
    if truncated:
        summary += f" ⚠️ WARNING: 超過 {_PAGE_CEILING} 封安全上限，清單可能不完整。"
    # 清單裡的 subject/from/preview 均為寄件者可控 → 標記為不可信（單行，維持 summary\n+JSON 結構）
    summary += " " + _UNTRUSTED_FIELDS_NOTE
    return summary + "\n" + json.dumps(items, ensure_ascii=False, indent=2)


# ========== 信箱（讀）==========
def mail_list_recent(days: int = 7, folder: str = "inbox") -> str:
    since = _utc_iso_days_ago(days)
    params = {
        "$filter": f"receivedDateTime ge {since}",
        "$orderby": "receivedDateTime desc",
        "$top": "50",
        "$select": _SEL,
    }
    url = f"{GRAPH}/me/mailFolders/{_enc(folder)}/messages?" + _q(params)
    msgs, trunc = _fetch_paged(url)
    summary = (f"Found {len(msgs)} emails in {folder} from last {days} days "
               f"(newest first; use mail_get(message_id) to read a message by its id).")
    return _render_messages(summary, msgs, trunc)


def mail_search(subject_prefix: str = "", body_query: str = "",
                days: int = 7, folder: str = "inbox") -> str:
    """以主旨前綴（startswith）或內文關鍵字（$search）搜信。兩者擇一或都給。"""
    subject_prefix = (subject_prefix or "").strip()
    body_query = (body_query or "").strip()
    if not subject_prefix and not body_query:
        return "ERROR: 請給 subject_prefix 或 body_query 其中之一。"
    if body_query:
        # KQL $search 不可與 $orderby/$filter 並用 → folder 以 URL 限定、days 於 client 端過濾。
        # 去掉雙引號避免 KQL 語法破壞/運算子注入（值維持單一引號片語）。
        safe_q = body_query.replace('"', "")
        url = f"{GRAPH}/me/mailFolders/{_enc(folder)}/messages?" + _q({
            "$search": f'"{safe_q}"', "$top": "50", "$select": _SEL})
        msgs, trunc = _fetch_paged(url)
        # days 視窗：$search 不能配 $filter，故抓回後依 receivedDateTime 於 client 端過濾。
        since = _utc_iso_days_ago(days)
        msgs = [m for m in msgs if (m.get("receivedDateTime") or "") >= since]
        # 同時也給了 subject_prefix → client 端再以主旨前綴過濾
        if subject_prefix:
            pfx = subject_prefix.lower()
            msgs = [m for m in msgs if (m.get("subject") or "").lower().startswith(pfx)]
    else:
        esc = subject_prefix.replace("'", "''")
        since = _utc_iso_days_ago(days)
        url = f"{GRAPH}/me/mailFolders/{_enc(folder)}/messages?" + _q({
            "$filter": f"startswith(subject,'{esc}') and receivedDateTime ge {since}",
            "$top": "50", "$select": _SEL})
        msgs, trunc = _fetch_paged(url)
    # 先抓到底再排序（避免截斷後才排，砍掉最新）
    msgs.sort(key=lambda m: m.get("receivedDateTime") or "", reverse=True)
    label = body_query or f"subject^={subject_prefix}"
    summary = f'Found {len(msgs)} emails matching {label} (newest first; use mail_get(message_id)).'
    return _render_messages(summary, msgs, trunc)


def _attachment_meta(message_id):
    """回傳該信附件的高訊號 metadata（不含 contentBytes，省 token）。"""
    url = (f"{GRAPH}/me/messages/{_enc(message_id)}/attachments"
           "?$select=id,name,contentType,size,isInline&$top=50")
    items, _ = _fetch_paged(url)
    return [{
        "id": a.get("id"),
        "name": a.get("name"),
        "type": a.get("contentType"),
        "size": a.get("size"),
        "is_inline": a.get("isInline"),
    } for a in items]


def mail_get(message_id: str, mode: str = "concise") -> str:
    """以 id 讀整封信。
    mode="concise"（預設）：回純文字（Exchange 伺服器端去 HTML），省 token、適合閱讀。
    mode="full"：回原始 HTML，零淨化（保留 <!-- 註解 -->），適合需要原文/程式化解析。
    有附件時附上附件清單（id/name/type/size）；用 mail_download_attachment 下載內容。"""
    m = (mode or "concise").strip().lower()
    if m not in ("concise", "full"):
        return "ERROR: mode 須為 'concise'（純文字）或 'full'（原始 HTML）。"
    content_type = "text" if m == "concise" else "html"
    url = (f"{GRAPH}/me/messages/{_enc(message_id)}"
           "?$select=id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,hasAttachments")
    try:
        d = _req("GET", url, extra_headers={"Prefer": f'outlook.body-content-type="{content_type}"'})
    except GraphHTTPError as ex:
        if _is_bad_id(ex):
            return "ERROR: 找不到該郵件（id 不存在或已被移動/刪除）。"
        raise
    subject = d.get("subject", "")
    frm = _fmt_addr(d.get("from"))
    to = ", ".join(_fmt_addr(r) for r in d.get("toRecipients", []))
    cc = ", ".join(_fmt_addr(r) for r in d.get("ccRecipients", []))
    date = d.get("receivedDateTime", "")
    body_obj = d.get("body") or {}
    body = body_obj.get("content", "")
    # concise 後備：若伺服器仍回 HTML（理論上不會），client 端再去標籤一次
    if m == "concise" and body_obj.get("contentType") == "html":
        body = _strip_html(body)
    head = f"Subject: {subject}\nFrom: {frm}\nTo: {to}\n"
    if cc:
        head += f"Cc: {cc}\n"
    # body 由寄件者控制 → 包進不可信界線，避免被當成指令（prompt-injection 緩解）
    out = head + f"Date: {date}\nBody:\n{_wrap_untrusted(body, kind='EMAIL BODY')}\n"
    if d.get("hasAttachments"):
        atts = _attachment_meta(message_id)
        if atts:
            out += ("\nAttachments (use mail_download_attachment(message_id, attachment_id)):\n"
                    + json.dumps(atts, ensure_ascii=False, indent=2) + "\n")
    return out


# 預設下載目錄（dest_dir 留空時）：固定安全位置，而非不可預期的行程 cwd。
_DEFAULT_DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "df-graph")


def _safe_attachment_name(name):
    """把伺服器/寄件者給的附件名收斂成安全的單一檔名（防穿越、去控制字元/NUL、不會落到目錄上）。"""
    base = os.path.basename((name or "").rstrip("/\\"))
    base = "".join(ch for ch in base if ch >= " ").strip()  # 去 NUL/控制字元（否則 open() 會拋 ValueError）
    if base in ("", ".", ".."):
        return "attachment.bin"
    return base


def _unique_path(path):
    """若 path 已存在，於副檔名前插入 ' (1)'、' (2)'… 直到不衝突；否則原樣回傳。"""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 1
    while True:
        cand = f"{root} ({i}){ext}"
        if not os.path.exists(cand):
            return cand
        i += 1


def mail_download_attachment(message_id: str, attachment_id: str, dest_dir: str = "",
                             overwrite: bool = False) -> str:
    """下載某封信的單一附件到本機磁碟，回傳存檔路徑（不把 base64 灌進對話，省 token）。
    dest_dir 留空則存到 ~/Downloads/df-graph。預設不覆蓋同名檔（自動加 (1)/(2)…）；
    overwrite=True 才覆寫。僅支援 fileAttachment（一般檔案）。"""
    url = f"{GRAPH}/me/messages/{_enc(message_id)}/attachments/{_enc(attachment_id)}"
    try:
        a = _req("GET", url)
    except GraphHTTPError as ex:
        if _is_bad_id(ex):
            return "ERROR: 找不到該附件（message_id 或 attachment_id 不存在）。"
        raise
    odata_type = a.get("@odata.type", "")
    if "fileAttachment" not in odata_type:
        return (f"ERROR: 此附件型別為 {odata_type or '未知'}，非一般檔案"
                "（itemAttachment/referenceAttachment 尚不支援下載）。")
    content_b64 = a.get("contentBytes")
    if not content_b64:
        return "ERROR: 附件無 contentBytes，無法下載。"
    safe_name = _safe_attachment_name(a.get("name"))  # 防路徑穿越 + 病態檔名收斂
    target_dir = os.path.abspath(dest_dir) if dest_dir else _DEFAULT_DOWNLOAD_DIR
    # 對稱於寄信護欄：別把攻擊者控制的附件內容寫進 ~/.ssh 等敏感/隱藏目錄
    reason = _sensitive_reason(os.path.realpath(target_dir))
    if reason:
        return f"ERROR: 拒絕下載到此目錄（{reason}）：{target_dir}"
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, safe_name)
    if not overwrite:
        path = _unique_path(path)          # 不覆蓋既有同名檔
    with open(path, "wb") as f:
        f.write(base64.b64decode(content_b64))
    size = os.path.getsize(path)
    return json.dumps({"path": path, "name": os.path.basename(path), "size": size},
                      ensure_ascii=False, indent=2)


def folder_list(parent_id: str = "", include_hidden: bool = False) -> str:
    """列出郵件資料夾。parent_id 留空 → 頂層；給資料夾 id 或 well-known 名稱（如 inbox）
    → 列其直接子資料夾（一層）。child_count>0 表示其下還有子夾，可再以該 id 遞迴。
    include_hidden=True 也列出隱藏夾（Clutter/搜尋夾等）。"""
    base = (f"{GRAPH}/me/mailFolders/{_enc(parent_id)}/childFolders"
            if parent_id else f"{GRAPH}/me/mailFolders")
    params = {"$top": "100",
              "$select": "id,displayName,unreadItemCount,totalItemCount,childFolderCount,isHidden"}
    if include_hidden:
        params["includeHiddenFolders"] = "true"
    items, _ = _fetch_paged(base + "?" + _q(params))
    out = [{"id": f.get("id"), "name": f.get("displayName"),
            "unread": f.get("unreadItemCount"), "total": f.get("totalItemCount"),
            "child_count": f.get("childFolderCount"), "hidden": f.get("isHidden")}
           for f in items]
    return json.dumps(out, ensure_ascii=False, indent=2)


# ========== 信箱（寫/寄）==========
_INLINE_ATTACH_LIMIT = 3 * 1024 * 1024      # 單檔/單請求內嵌 base64 的 Graph 上限（< 3MB）
_MAX_ATTACH_SIZE = 150 * 1024 * 1024        # Graph 對 Outlook 附件的硬上限
_UPLOAD_CHUNK = 10 * 320 * 1024             # 3,276,800：320KiB 倍數且 < 4MB，符合分段上傳要求


# ---- 附件護欄（防 prompt-injection 把本機敏感檔外送）----
# 一律拒絕的敏感目錄名（路徑任一段命中即擋）與隱藏檔（basename 以 '.' 起）。
_SENSITIVE_DIR_NAMES = {".ssh", ".aws", ".gnupg", ".config", ".kube", ".docker", ".gcloud"}
# 可選白名單：設了才強制（os.pathsep 分隔的目錄）；沒設＝一般非敏感檔照常可寄（本機自用）。
_ALLOWLIST_ENV = "DF_GRAPH_ALLOWED_ATTACH_DIRS"


def _within(child, parent):
    """child 是否落在 parent 目錄內（realpath 比較，跨磁碟回 False）。"""
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False


def _sensitive_reason(real):
    """realpath 是否落在敏感/隱藏位置；是 → 回原因字串，否 → None。
    擋「路徑任一段」以 '.' 起的隱藏目錄/檔（涵蓋 .ssh/.aws/.azure/.mozilla/.thunderbird…
    即使裡面的檔名本身不是 dot），外加一份明確的敏感目錄名單。共用於寄附件與下載目的地。"""
    segs = [s for s in real.split(os.sep) if s]
    if any(s.startswith(".") for s in segs):
        return "隱藏檔/dot 目錄（可能含機密）"
    if {s.lower() for s in segs} & _SENSITIVE_DIR_NAMES:
        return "敏感目錄"
    return None


def _attach_guard(path):
    """檢查單一附件路徑是否可寄；不可寄 → ValueError。回傳解析後的 realpath。
    規則（本機自用軟性）：(1) 永遠擋隱藏檔/dot 目錄與敏感目錄；
    (2) 若設了 DF_GRAPH_ALLOWED_ATTACH_DIRS 則另要求路徑落在允許目錄內。
    註：非 dot、又不在敏感名單的任意機密檔（如 ~/Documents/id_rsa_backup）在軟性預設下
    不會被擋，需要硬性控管時請設白名單。"""
    real = os.path.realpath(path)
    reason = _sensitive_reason(real)
    if reason:
        raise ValueError(f"拒絕寄送（{reason}）：{path}")
    allow = os.environ.get(_ALLOWLIST_ENV, "").strip()
    if allow:
        allowed = [os.path.realpath(d) for d in allow.split(os.pathsep) if d.strip()]
        if not any(_within(real, d) for d in allowed):
            raise ValueError(f"檔案不在 {_ALLOWLIST_ENV} 允許目錄內：{path}")
    return real


def _collect_files(paths_csv):
    """把逗號分隔路徑解析成 [(realpath, name, size)]；檔案不存在/超過 150MB/未過護欄 → ValueError。"""
    files = []
    for p in (paths_csv or "").split(","):
        p = p.strip()
        if not p:
            continue
        if not os.path.isfile(p):
            raise ValueError(f"找不到檔案：{p}")
        real = _attach_guard(p)            # 護欄：擋敏感/隱藏檔，套用可選白名單
        size = os.path.getsize(real)
        if size > _MAX_ATTACH_SIZE:
            raise ValueError(f"檔案超過 150MB 上限（Graph 不支援更大附件）：{p}")
        files.append((real, os.path.basename(real), size))
    return files


def _needs_upload_session(files):
    """任一檔 >=3MB，或總和 >=3MB（避免超過單一請求上限）→ 走 upload session 路徑。"""
    total = sum(s for _, _, s in files)
    return any(s >= _INLINE_ATTACH_LIMIT for _, _, s in files) or total >= _INLINE_ATTACH_LIMIT


def _inline_attachment(path, name):
    with open(path, "rb") as f:
        return {"@odata.type": "#microsoft.graph.fileAttachment",
                "name": name,
                "contentBytes": base64.b64encode(f.read()).decode("ascii")}


_UPLOAD_PUT_RETRIES = 3  # 單段 PUT 失敗重試次數（PUT 冪等，可安全重送同一段）


def _put_no_auth(url, data, headers):
    """對預授權的 uploadUrl 做 PUT；刻意不帶 Authorization（url 本身已授權）。
    回傳 (status, json_or_none)；json 內含 nextExpectedRanges（中段）或無（最後一段 201）。"""
    import urllib.request
    req = urllib.request.Request(url, data=data, method="PUT", headers=headers)
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
        try:
            parsed = json.loads(raw) if raw else None
        except (ValueError, TypeError):
            parsed = None
        return r.status, parsed


def _next_start_from(parsed, fallback):
    """從 PUT 回應的 nextExpectedRanges（如 ["2097152-"]）取下一個起始 byte；無則用 fallback。"""
    try:
        ranges = (parsed or {}).get("nextExpectedRanges") or []
        if ranges:
            return int(str(ranges[0]).split("-", 1)[0])
    except (ValueError, TypeError, IndexError):
        pass
    return fallback


def _upload_attachment_session(message_id, path, name, size):
    """大檔（>=3MB）：開 upload session 後分段 PUT 上傳到草稿。
    依伺服器回報的 nextExpectedRanges 續傳；單段失敗重試（PUT 冪等，可安全重送）。
    防護：要求每輪 start 嚴格前進、設絕對迭代上限、最後驗證狀態碼，避免伺服器回報停滯範圍時死迴圈。"""
    import urllib.error
    sess = _req("POST",
                f"{GRAPH}/me/messages/{_enc(message_id)}/attachments/createUploadSession",
                body={"AttachmentItem": {"attachmentType": "file", "name": name, "size": size}})
    upload_url = sess["uploadUrl"]
    max_iters = (size // _UPLOAD_CHUNK) + 3  # 切片數 + slack；死迴圈最後防線
    iters = 0
    last_status = None
    with open(path, "rb") as f:
        start = 0
        while start < size:
            iters += 1
            if iters > max_iters:
                raise GraphHTTPError(0, f"upload exceeded {max_iters} chunks (server range stuck?)",
                                     "PUT", upload_url)
            f.seek(start)
            chunk = f.read(_UPLOAD_CHUNK)
            if not chunk:
                break
            end = start + len(chunk) - 1
            attempt = 0
            while True:
                try:
                    last_status, parsed = _put_no_auth(upload_url, chunk, {
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start}-{end}/{size}",
                    })
                    break
                except (urllib.error.HTTPError, urllib.error.URLError) as e:
                    attempt += 1
                    if attempt > _UPLOAD_PUT_RETRIES:
                        # 耗盡重試 → 正規化成 GraphHTTPError，與其餘工具錯誤型別一致
                        code = getattr(e, "code", 0) or 0
                        detail = getattr(e, "reason", None) or str(e)
                        raise GraphHTTPError(code, f"attachment upload PUT failed: {detail}",
                                             "PUT", upload_url)
            # 依伺服器回報的下一段起點續傳；要求嚴格前進，否則視為停滯並中止（不再死迴圈）
            nxt = _next_start_from(parsed, end + 1)
            if nxt <= start:
                raise GraphHTTPError(0, f"upload stalled: server still expects byte {nxt} "
                                     f"after sending {start}-{end}", "PUT", upload_url)
            start = nxt
    if last_status not in (200, 201):  # 最後一段應為 201 Created
        raise GraphHTTPError(last_status or 0,
                             f"upload finished with unexpected status {last_status}", "PUT", upload_url)


def _attach_files_to_message(message_id, files):
    """把檔案逐一加到既有訊息：<3MB 走簡單 POST，>=3MB 走 upload session。"""
    for path, name, size in files:
        if size < _INLINE_ATTACH_LIMIT:
            _req("POST", f"{GRAPH}/me/messages/{_enc(message_id)}/attachments",
                 body=_inline_attachment(path, name))
        else:
            _upload_attachment_session(message_id, path, name, size)


def _build_message(to, subject, body, cc, bcc, html):
    """組訊息主體（收件人/內文），不含附件——附件另以 _attach_files_to_message 處理。"""
    msg = {
        "subject": subject,
        "body": {"contentType": "HTML" if html else "Text", "content": body},
        "toRecipients": _recips(to),
    }
    if cc:
        msg["ccRecipients"] = _recips(cc)
    if bcc:
        msg["bccRecipients"] = _recips(bcc)
    return msg


def mail_send(to: str, subject: str, body: str, cc: str = "", bcc: str = "",
              html: bool = True, attachments: str = "") -> str:
    """寄信。to/cc/bcc 逗號分隔地址；attachments 逗號分隔本機檔案路徑（單檔/總計各上限 150MB）。
    小附件單發；含 >=3MB 的大檔時自動改走「草稿→分段上傳→送出」。"""
    if not _recips(to):
        return "ERROR: 請至少提供一個收件人 (to)。"
    try:
        files = _collect_files(attachments)
    except ValueError as e:
        return f"ERROR: {e}"
    message = _build_message(to, subject, body, cc, bcc, html)
    if not files or not _needs_upload_session(files):
        # 快速路徑：單一 sendMail，小附件直接內嵌
        if files:
            message["attachments"] = [_inline_attachment(p, n) for p, n, _ in files]
        _req("POST", f"{GRAPH}/me/sendMail", body={"message": message, "saveToSentItems": True})
    else:
        # 大附件路徑：建草稿 → 逐一加附件（大檔走 upload session）→ 送出
        draft = _req("POST", f"{GRAPH}/me/messages", body=message)
        did = draft["id"]
        try:
            _attach_files_to_message(did, files)
            _req("POST", f"{GRAPH}/me/messages/{_enc(did)}/send")
        except Exception:
            # 失敗時清掉剛建的草稿，避免草稿匣留下半成品（best-effort，不掩蓋原錯誤）
            try:
                _req("DELETE", f"{GRAPH}/me/messages/{_enc(did)}")
            except Exception:
                pass
            raise
    n = len(files)
    return f"Sent to {to}" + (f" cc {cc}" if cc else "") + (f" with {n} attachment(s)" if n else "")


def mail_draft(to: str, subject: str, body: str, cc: str = "", bcc: str = "",
               html: bool = True, attachments: str = "") -> str:
    """建立草稿到「草稿匣」供你在 Outlook 過目後再手動寄出（不會自動送出）。
    參數同 mail_send（含大附件，最大 150MB）。回傳 draft_id 與 web_link。"""
    try:
        files = _collect_files(attachments)
    except ValueError as e:
        return f"ERROR: {e}"
    message = _build_message(to, subject, body, cc, bcc, html)
    d = _req("POST", f"{GRAPH}/me/messages", body=message)
    did = d.get("id")
    if files:
        _attach_files_to_message(did, files)
    return json.dumps({"draft_id": did, "web_link": d.get("webLink"),
                       "subject": d.get("subject")}, ensure_ascii=False, indent=2)


def mail_reply(message_id: str, body: str, reply_all: bool = False) -> str:
    action = "replyAll" if reply_all else "reply"
    try:
        _req("POST", f"{GRAPH}/me/messages/{_enc(message_id)}/{action}", body={"comment": body})
    except GraphHTTPError as ex:
        if _is_bad_id(ex):
            return "ERROR: 找不到該郵件（id 不存在或已被移動/刪除）。"
        raise
    return "Replied" + (" (all)" if reply_all else "")


def mail_reply_draft(message_id: str, body: str, reply_all: bool = False) -> str:
    """建立『回覆草稿』到草稿匣供你在 Outlook 過目後再寄出（不自動送出）。
    用 Graph createReply/createReplyAll 建 threaded 草稿（保留原信 thread 與引用），
    再把 body（如卡片 HTML）prepend 到草稿內文上方。
    與 mail_reply 不同：mail_reply 會【直接寄出】；本工具只建草稿、保留主管審稿。
    回傳 draft_id / web_link / subject。"""
    action = "createReplyAll" if reply_all else "createReply"
    try:
        d = _req("POST", f"{GRAPH}/me/messages/{_enc(message_id)}/{action}")
    except GraphHTTPError as ex:
        if _is_bad_id(ex):
            return "ERROR: 找不到該郵件（id 不存在或已被移動/刪除）。"
        raise
    did = d.get("id")
    if not did:
        return "ERROR: 建立回覆草稿失敗（Graph 未回傳草稿 id）。"
    # createReply 預填的內文含原信引用；把卡片 prepend 在上方，下方保留 thread 引用。
    existing = (d.get("body") or {}).get("content", "")
    new_content = body + existing
    try:
        d2 = _req("PATCH", f"{GRAPH}/me/messages/{_enc(did)}",
                  body={"body": {"contentType": "HTML", "content": new_content}})
    except GraphHTTPError:
        # PATCH 失敗 → 清掉剛建的半成品草稿（best-effort，不掩蓋原錯誤）
        try:
            _req("DELETE", f"{GRAPH}/me/messages/{_enc(did)}")
        except Exception:
            pass
        raise
    return json.dumps({"draft_id": did,
                       "web_link": d2.get("webLink") or d.get("webLink"),
                       "subject": d2.get("subject") or d.get("subject"),
                       "reply_all": reply_all,
                       "note": "已存回覆草稿（threaded），請在 Outlook 過目後送出"},
                      ensure_ascii=False, indent=2)


def mail_forward(message_id: str, to: str, comment: str = "") -> str:
    if not _recips(to):
        return "ERROR: 請至少提供一個收件人 (to)。"
    try:
        _req("POST", f"{GRAPH}/me/messages/{_enc(message_id)}/forward",
             body={"comment": comment, "toRecipients": _recips(to)})
    except GraphHTTPError as ex:
        if _is_bad_id(ex):
            return "ERROR: 找不到該郵件（id 不存在或已被移動/刪除）。"
        raise
    return f"Forwarded to {to}"


# ========== 信箱（整理：標已讀/移動/刪除）==========
# 可當 destinationId 的 well-known 資料夾名稱（locale 無關）
WELL_KNOWN_FOLDERS = {
    "inbox", "archive", "deleteditems", "junkemail", "drafts", "sentitems",
    "outbox", "clutter", "conversationhistory", "scheduled", "conflicts",
    "localfailures", "msgfolderroot", "recoverableitemsdeletions",
    "searchfolders", "serverfailures", "syncissues",
}


def mail_mark_read(message_id: str, read: bool = True) -> str:
    """標記郵件已讀(read=True)/未讀(read=False)。PATCH 只送 isRead 一個欄位；id 不變。"""
    try:
        d = _req("PATCH", f"{GRAPH}/me/messages/{_enc(message_id)}", body={"isRead": bool(read)})
    except GraphHTTPError as ex:
        if _is_bad_id(ex):
            return "ERROR: 找不到該郵件（id 不存在或已被移動/刪除）。"
        raise
    state = "read" if read else "unread"
    return f"Marked message {d.get('id', message_id)} as {state} (isRead={d.get('isRead', read)})."


def mail_move(message_id: str, destination: str) -> str:
    """把郵件移到指定資料夾。destination 可用 well-known 名稱（inbox/archive/deleteditems/
    junkemail/drafts/sentitems…）或資料夾 id。
    ⚠️ 移動後郵件會取得【新的 id】，舊 id 失效——回傳的 new_message_id 才是後續可用的 id。"""
    dest = (destination or "").strip()
    if not dest:
        return "ERROR: 請提供 destination（well-known 名稱或資料夾 id）。"
    if dest.lower() in WELL_KNOWN_FOLDERS:
        dest = dest.lower()  # well-known 名稱標準化為小寫；任意資料夾 id 原樣放行
    try:
        d = _req("POST", f"{GRAPH}/me/messages/{_enc(message_id)}/move",
                 body={"destinationId": dest})
    except GraphHTTPError as ex:
        if _is_bad_id(ex):
            return "ERROR: 找不到該郵件或目的資料夾（id 不存在或已被移動/刪除）。"
        raise
    return json.dumps({"moved_to": dest, "new_message_id": d.get("id"),
                       "note": "舊 id 已失效，請改用 new_message_id"}, ensure_ascii=False, indent=2)


def mail_delete(message_id: str, permanent: bool = False) -> str:
    """刪除郵件。預設【軟刪除】：移到「刪除的郵件」，可在 Outlook 還原，並回傳刪除匣中的新 id
    （可用 mail_move(new_id,'inbox') 還原）。permanent=True 走 HTTP DELETE（Graph 對 message 的
    DELETE 本身即軟刪到刪除匣、非永久銷毀），不回傳新 id。"""
    if not permanent:
        try:
            d = _req("POST", f"{GRAPH}/me/messages/{_enc(message_id)}/move",
                     body={"destinationId": "deleteditems"})
        except GraphHTTPError as ex:
            if _is_bad_id(ex):
                return "ERROR: 找不到該郵件（id 不存在或已被移動/刪除）。"
            raise
        return json.dumps({"soft_deleted": True, "new_message_id_in_deleted_items": d.get("id"),
                           "undo": "mail_move(new_id, 'inbox')"}, ensure_ascii=False, indent=2)
    try:
        _req("DELETE", f"{GRAPH}/me/messages/{_enc(message_id)}")
    except GraphHTTPError as ex:
        if _is_bad_id(ex):
            return "ERROR: 找不到該郵件（id 不存在或已被移動/刪除）。"
        raise
    return f"Deleted message {message_id} (sent to Deleted Items; recoverable in Outlook)."
