"""df-graph 行事曆工具：列表/單筆/建立/修改/刪除 + 找共同空檔 + free/busy + RSVP/轉寄。

時區：讀取類（list/get/find_times/get_schedule）預設回 Asia/Taipei 當地時間（可用 tz 覆寫、
或環境變數 DF_GRAPH_TZ）。寫入類（create/update）的 start_iso/end_iso 仍以 UTC ISO-8601 輸入。
"""
import os
import json
import datetime

from graphcore import (GRAPH, GraphHTTPError, _req, _q, _enc, _is_bad_id,
                        _utc_iso, _strip_z, _fmt_addr, _recips, _fetch_paged, _PAGE_CEILING,
                        _wrap_untrusted, _UNTRUSTED_WARNING, _UNTRUSTED_FIELDS_NOTE)


def _iso_dt(s):
    """寬鬆解析 ISO 時間字串（先去 Z）；無法解析回 None（不硬擋，交給 Graph）。"""
    try:
        return datetime.datetime.fromisoformat(_strip_z(s))
    except (ValueError, TypeError):
        return None

# 使用者面向時區用 IANA（Graph 直接接受），預設台北；可用環境變數覆寫。
DEFAULT_TZ = os.environ.get("DF_GRAPH_TZ", "Asia/Taipei")


def _prefer_tz(tz=None):
    """組 Prefer: outlook.timezone 值；tz 為 IANA（如 Asia/Taipei）或 Windows 名稱。"""
    return f'outlook.timezone="{tz or DEFAULT_TZ}"'


def _fmt_attendees(attendees):
    """把 event.attendees 攤平成 name/email/type/response 的乾淨清單。"""
    out = []
    for a in attendees or []:
        ea = (a or {}).get("emailAddress") or {}
        out.append({
            "name": ea.get("name") or ea.get("address") or "",
            "email": ea.get("address") or "",
            "type": a.get("type"),                       # required / optional / resource
            "response": ((a.get("status") or {}).get("response")),  # none/accepted/declined/tentativelyAccepted
        })
    return out


# ========== 行事曆（Calendars.ReadWrite[.Shared]）==========
def calendar_list(days_ahead: int = 7, days_back: int = 0, tz: str = "") -> str:
    """列出 -days_back ~ +days_ahead 的事件。時間預設以 tz（預設 Asia/Taipei）當地時間回傳。"""
    tz = tz or DEFAULT_TZ
    now = datetime.datetime.now(datetime.timezone.utc)
    params = {
        "startDateTime": _utc_iso(now - datetime.timedelta(days=days_back)),
        "endDateTime": _utc_iso(now + datetime.timedelta(days=days_ahead)),
        "$select": ("id,subject,start,end,location,organizer,isAllDay,"
                    "attendees,bodyPreview,isOnlineMeeting,onlineMeeting,"
                    "responseStatus,reminderMinutesBeforeStart,isReminderOn,webLink"),
        "$orderby": "start/dateTime",
        "$top": "50",
    }
    url = f"{GRAPH}/me/calendarView?" + _q(params)
    # 分頁到底（Prefer tz 套用到每一頁）；視窗內 >50 筆才會有第二頁
    raw, trunc = _fetch_paged(url, extra_headers={"Prefer": _prefer_tz(tz)})
    events = [{
        "id": e.get("id"),
        "subject": e.get("subject"),
        "start": (e.get("start") or {}).get("dateTime"),
        "end": (e.get("end") or {}).get("dateTime"),
        "location": (e.get("location") or {}).get("displayName"),
        "organizer": _fmt_addr(e.get("organizer")),
        "all_day": e.get("isAllDay"),
        "attendees": _fmt_attendees(e.get("attendees")),
        "is_online_meeting": e.get("isOnlineMeeting"),
        "join_url": (e.get("onlineMeeting") or {}).get("joinUrl"),
        "my_response": (e.get("responseStatus") or {}).get("response"),
        "reminder_minutes": e.get("reminderMinutesBeforeStart"),
        "is_reminder_on": e.get("isReminderOn"),
        "web_link": e.get("webLink"),
        # 列表用 preview（避免全文爆量）；完整議程用 calendar_get(event_id)
        "preview": (e.get("bodyPreview") or "")[:300],
    } for e in raw]
    summary = f"Found {len(events)} events from -{days_back}d to +{days_ahead}d (times in {tz})."
    if trunc:
        summary += f" ⚠️ WARNING: 超過 {_PAGE_CEILING} 筆安全上限，清單可能不完整。"
    summary += " Use calendar_get(event_id) for the full agenda/body."
    # subject/organizer/location/preview 由他人可控 → 標記不可信（單行，維持 summary\n+JSON）
    summary += " " + _UNTRUSTED_FIELDS_NOTE
    return summary + "\n" + json.dumps(events, ensure_ascii=False, indent=2)


def calendar_get(event_id: str, tz: str = "") -> str:
    """讀單一事件（完整與會者 + 原始 body）。時間以 tz（預設 Asia/Taipei）當地時間回傳。"""
    tz = tz or DEFAULT_TZ
    url = (f"{GRAPH}/me/events/{_enc(event_id)}?$select="
           "id,subject,start,end,location,organizer,attendees,body,isAllDay,"
           "isOnlineMeeting,onlineMeeting,responseStatus,"
           "reminderMinutesBeforeStart,isReminderOn,webLink,categories")
    try:
        d = _req("GET", url, extra_headers={
            "Prefer": f'{_prefer_tz(tz)}, outlook.body-content-type="html"'})
    except GraphHTTPError as ex:
        if _is_bad_id(ex):
            return "ERROR: 找不到該活動（id 不存在或已被移動/刪除）。"
        raise
    detail = {
        "id": d.get("id"),
        "subject": d.get("subject"),
        "start": (d.get("start") or {}).get("dateTime"),
        "end": (d.get("end") or {}).get("dateTime"),
        "timezone": tz,
        "location": (d.get("location") or {}).get("displayName"),
        "organizer": _fmt_addr(d.get("organizer")),
        "attendees": _fmt_attendees(d.get("attendees")),
        "all_day": d.get("isAllDay"),
        "is_online_meeting": d.get("isOnlineMeeting"),
        "join_url": (d.get("onlineMeeting") or {}).get("joinUrl"),
        "my_response": (d.get("responseStatus") or {}).get("response"),
        "reminder_minutes": d.get("reminderMinutesBeforeStart"),
        "is_reminder_on": d.get("isReminderOn"),
        "web_link": d.get("webLink"),
        "categories": d.get("categories"),
        # body 由召集人控制 → 包進不可信界線（與 mail_get 一致）；另保留旗標供程式化判斷
        "body": _wrap_untrusted((d.get("body") or {}).get("content", ""), kind="EVENT BODY"),
        "body_is_untrusted": True,
        "_warning": _UNTRUSTED_WARNING,
    }
    return json.dumps(detail, ensure_ascii=False, indent=2)


def calendar_create(subject: str, start_iso: str, end_iso: str,
                    body: str = "", location: str = "", attendees: str = "",
                    online_meeting: bool = False) -> str:
    """建立行事曆事件/會議。start_iso/end_iso 為 UTC ISO-8601（如 2026-06-04T09:00:00）。
    attendees 逗號分隔地址。online_meeting=True 會自動產生 Teams 連結並於回傳附 join_url。"""
    # 去尾 Z：dateTimeTimeZone.dateTime 不可含時區標記（與 find_times/get_schedule 一致）
    s_iso, e_iso = _strip_z(start_iso), _strip_z(end_iso)
    sdt, edt = _iso_dt(s_iso), _iso_dt(e_iso)
    if sdt and edt and edt <= sdt:
        return "ERROR: 結束時間需晚於開始時間。"
    ev = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body},
        "start": {"dateTime": s_iso, "timeZone": "UTC"},
        "end": {"dateTime": e_iso, "timeZone": "UTC"},
    }
    if location:
        ev["location"] = {"displayName": location}
    if attendees:
        ev["attendees"] = [{"emailAddress": {"address": a.strip()}, "type": "required"}
                           for a in attendees.split(",") if a.strip()]
    if online_meeting:
        ev["isOnlineMeeting"] = True
        ev["onlineMeetingProvider"] = "teamsForBusiness"
    d = _req("POST", f"{GRAPH}/me/events", body=ev)
    result = {"id": d.get("id"), "subject": d.get("subject")}
    if online_meeting:
        result["join_url"] = (d.get("onlineMeeting") or {}).get("joinUrl")
    return "Created event\n" + json.dumps(result, ensure_ascii=False, indent=2)


_RSVP_ACTION = {"accept": "accept", "decline": "decline", "tentative": "tentativelyAccept"}


def calendar_find_times(attendees: str, duration_minutes: int = 30,
                        window_start_iso: str = "", window_end_iso: str = "",
                        max_suggestions: int = 5, tz: str = "") -> str:
    """找出與會者共同有空的時段（Microsoft findMeetingTimes）。
    attendees 逗號分隔地址；window 以 UTC ISO-8601；窗預設「現在～未來 5 天」。
    建議時段以 tz（預設 Asia/Taipei）當地時間回傳，依信心度排序。"""
    tz = tz or DEFAULT_TZ
    now = datetime.datetime.now(datetime.timezone.utc)
    # dateTime 不帶 Z（timeZone 欄位另帶 UTC），與 getSchedule 一致
    start = _strip_z(window_start_iso or _utc_iso(now))
    end = _strip_z(window_end_iso or _utc_iso(now + datetime.timedelta(days=5)))
    # clamp 到合理區間，避免 PT0M/PT-30M 或荒謬 maxCandidates 讓 Graph 回 400（比照 get_schedule）
    dur = max(1, min(1440, int(duration_minutes)))
    cand = max(1, min(100, int(max_suggestions)))
    payload = {
        "attendees": [{"type": "required", "emailAddress": {"address": a.strip()}}
                      for a in (attendees or "").split(",") if a.strip()],
        "timeConstraint": {"activityDomain": "work", "timeSlots": [
            {"start": {"dateTime": start, "timeZone": "UTC"},
             "end": {"dateTime": end, "timeZone": "UTC"}}]},
        "meetingDuration": f"PT{dur}M",
        "maxCandidates": cand,
        "returnSuggestionReasons": False,
    }
    d = _req("POST", f"{GRAPH}/me/findMeetingTimes", body=payload,
             extra_headers={"Prefer": _prefer_tz(tz)})
    reason = d.get("emptySuggestionsReason")
    suggestions = [{
        "start": ((s.get("meetingTimeSlot") or {}).get("start") or {}).get("dateTime"),
        "end": ((s.get("meetingTimeSlot") or {}).get("end") or {}).get("dateTime"),
        "confidence": s.get("confidence"),
        "organizer_availability": s.get("organizerAvailability"),
    } for s in d.get("meetingTimeSuggestions", [])]
    if not suggestions:
        return f"No common free slot found. reason={reason or 'unknown'} (tz={tz})."
    return (f"Found {len(suggestions)} suggestion(s) (times in {tz}, best first):\n"
            + json.dumps(suggestions, ensure_ascii=False, indent=2))


def calendar_get_schedule(attendees: str, start_iso: str = "", end_iso: str = "",
                          interval_minutes: int = 30, tz: str = "") -> str:
    """查多人 free/busy（Microsoft getSchedule）。attendees 逗號分隔 SMTP；
    window 以 UTC ISO-8601；窗預設「現在～未來 1 天」；interval 5..1440（預設 30）。
    回傳每人 busy/tentative/oof 區塊，時間以 tz（預設 Asia/Taipei）當地時間。"""
    tz = tz or DEFAULT_TZ
    schedules = [a.strip() for a in (attendees or "").split(",") if a.strip()]
    if not schedules:
        return "ERROR: 請至少給一個 attendees（逗號分隔 SMTP 地址）。"
    interval = max(5, min(1440, int(interval_minutes)))   # clamp 到 Graph 合法區間
    now = datetime.datetime.now(datetime.timezone.utc)
    # dateTime 不可帶 Z（timeZone 欄位另帶）；窗以 UTC 輸入，輸出由 Prefer 轉成 tz
    start = (start_iso or _utc_iso(now)).rstrip("Z")
    end = (end_iso or _utc_iso(now + datetime.timedelta(days=1))).rstrip("Z")
    batch_size = 20  # Graph 每次 getSchedule 最多 20 個 mailbox
    people = []
    for i in range(0, len(schedules), batch_size):
        chunk = schedules[i:i + batch_size]
        payload = {
            "schedules": chunk,
            "startTime": {"dateTime": start, "timeZone": "UTC"},
            "endTime": {"dateTime": end, "timeZone": "UTC"},
            "availabilityViewInterval": interval,
        }
        d = _req("POST", f"{GRAPH}/me/calendar/getSchedule", body=payload,
                 extra_headers={"Prefer": _prefer_tz(tz)})
        for s in d.get("value", []):
            err = (s.get("error") or {}).get("message")
            busy = [{
                "status": it.get("status"),
                "start": (it.get("start") or {}).get("dateTime"),
                "end": (it.get("end") or {}).get("dateTime"),
                "subject": it.get("subject"),       # 常為 None（隱私）
                "location": it.get("location"),
            } for it in (s.get("scheduleItems") or [])
              if it.get("status") in ("busy", "tentative", "oof")]
            wh = s.get("workingHours") or {}
            people.append({
                "person": s.get("scheduleId"),
                "availability_view": s.get("availabilityView"),  # 0free 1tent 2busy 3oof 4elsewhere
                "busy_blocks": busy,
                "working_hours": {"days": wh.get("daysOfWeek"),
                                  "start": wh.get("startTime"), "end": wh.get("endTime")},
                "error": err,
            })
    summary = (f"free/busy for {len(people)} person(s), interval={interval}m, times in {tz} "
               f"(availability_view digit per slot: 0=free 1=tentative 2=busy 3=oof 4=elsewhere).")
    return summary + "\n" + json.dumps(people, ensure_ascii=False, indent=2)


def calendar_rsvp(event_id: str, response: str, comment: str = "",
                  send_response: bool = True) -> str:
    """回覆會議邀請。response 須為 accept / decline / tentative。
    send_response=True 會把回覆通知寄給召集人。"""
    action = _RSVP_ACTION.get((response or "").strip().lower())
    if not action:
        return "ERROR: response 須為 accept / decline / tentative 其中之一。"
    body = {"sendResponse": send_response}
    if comment:
        body["comment"] = comment
    try:
        _req("POST", f"{GRAPH}/me/events/{_enc(event_id)}/{action}", body=body)
    except GraphHTTPError as ex:
        if _is_bad_id(ex):
            return "ERROR: 找不到該活動（event_id 不存在或已被移動/刪除）。"
        raise
    return f"RSVP '{response}' sent for event {event_id}"


def calendar_forward(event_id: str, to: str, comment: str = "") -> str:
    """把會議邀請轉寄給其他人。to 逗號分隔地址。"""
    if not _recips(to):
        return "ERROR: 請至少提供一個收件人 (to)。"
    body = {"ToRecipients": _recips(to)}
    if comment:
        body["Comment"] = comment
    try:
        _req("POST", f"{GRAPH}/me/events/{_enc(event_id)}/forward", body=body)
    except GraphHTTPError as ex:
        if _is_bad_id(ex):
            return "ERROR: 找不到該活動（event_id 不存在或已被移動/刪除）。"
        raise
    return f"Forwarded event {event_id} to {to}"


def calendar_update(event_id: str, subject: str = "", start_iso: str = "",
                    end_iso: str = "", body: str = "", location: str = "") -> str:
    """修改既有事件欄位（id 來自 calendar_list）。start/end 以 UTC ISO-8601。空參數略過。"""
    # 兩者都給才檢查順序（單給一邊無從比對既有事件，交給 Graph）
    if start_iso and end_iso:
        sdt, edt = _iso_dt(start_iso), _iso_dt(end_iso)
        if sdt and edt and edt <= sdt:
            return "ERROR: 結束時間需晚於開始時間。"
    patch = {}
    if subject:
        patch["subject"] = subject
    if start_iso:
        patch["start"] = {"dateTime": _strip_z(start_iso), "timeZone": "UTC"}
    if end_iso:
        patch["end"] = {"dateTime": _strip_z(end_iso), "timeZone": "UTC"}
    if body:
        patch["body"] = {"contentType": "HTML", "content": body}
    if location:
        patch["location"] = {"displayName": location}
    if not patch:
        return "Nothing to update."
    try:
        _req("PATCH", f"{GRAPH}/me/events/{_enc(event_id)}", body=patch)
    except GraphHTTPError as ex:
        if _is_bad_id(ex):
            return "ERROR: 找不到該活動（event_id 不存在或已被移動/刪除）。"
        raise
    return f"Updated event {event_id}"


def calendar_delete(event_id: str) -> str:
    """刪除事件（軟刪除到刪除的項目，可在 Outlook 還原）。"""
    try:
        _req("DELETE", f"{GRAPH}/me/events/{_enc(event_id)}")
    except GraphHTTPError as ex:
        if _is_bad_id(ex):
            return "ERROR: 找不到該活動（event_id 不存在或已被移動/刪除）。"
        raise
    return f"Deleted event {event_id}"
