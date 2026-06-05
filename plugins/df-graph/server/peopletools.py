"""df-graph 人員解析：把姓名（或片段/email）解析成 email 地址。

主路徑：/me/people 相關性搜尋（需 People.Read；本租戶需 IT admin 核准，預設未啟用）。
後備：目錄 /users 搜尋（User.ReadBasic.All，需 IT admin consent）—— 未授予時明確回報。
"""
import json

from graphcore import GRAPH, GraphHTTPError, _req, _q


def _person_email(p):
    """從 People 結果取最佳 email：scoredEmailAddresses 相關性最高者，否則 UPN/mail。"""
    sea = p.get("scoredEmailAddresses") or []
    if sea:
        best = max(sea, key=lambda e: e.get("relevanceScore") if e.get("relevanceScore") is not None
                   else float("-inf"))
        if best.get("address"):
            return best["address"]
    return p.get("userPrincipalName") or p.get("mail") or ""


def resolve_person(query: str, limit: int = 10) -> str:
    """把姓名/片段/email 解析成 [{name, email}]。
    1) /me/people 模糊搜尋（含曾互動的同事；People.Read）。
    2) 查無 → 後備直查目錄 /users（需 User.ReadBasic.All；未授予則回報需 IT 開通）。"""
    q = (query or "").strip().replace('"', "")  # 去雙引號，避免 KQL 語法破壞/運算子注入
    if not q:
        return "ERROR: query 不可為空。"
    results = []
    people_denied = False
    # --- 1) People API：相關性排序、模糊。$search 不可與 $skip/$filter 並用 → 單頁 $top ---
    url = f"{GRAPH}/me/people?" + _q({
        "$search": f'"{q}"',
        "$select": "displayName,scoredEmailAddresses,userPrincipalName,personType",
        "$top": str(limit),
    })
    try:
        d = _req("GET", url)  # People API 不需特別 header
    except GraphHTTPError as ex:
        if ex.code in (401, 403):       # 尚未授予 People.Read（本租戶需 IT admin 核准）
            people_denied, d = True, {"value": []}
        else:
            raise
    for p in d.get("value", []):
        email = _person_email(p)
        if email:
            results.append({"name": p.get("displayName") or email, "email": email})
    # --- 2) 後備：直查目錄 /users（沒互動過的同事 People 查不到）---
    if not results:
        url = f"{GRAPH}/users?" + _q({
            "$search": f'"displayName:{q}"',
            "$count": "true",                       # $search 時必填
            "$select": "displayName,mail,userPrincipalName",
            "$top": str(limit),
        })
        try:
            d = _req("GET", url, extra_headers={"ConsistencyLevel": "eventual"})  # 必填 header
            for u in d.get("value", []):
                email = u.get("mail") or u.get("userPrincipalName") or ""
                if email:
                    results.append({"name": u.get("displayName") or email, "email": email})
        except GraphHTTPError as ex:
            if ex.code in (401, 403):
                if people_denied:
                    return ("ERROR: resolve_person 需要 People.Read（本租戶需 IT admin 核准）；"
                            "目錄後備查詢另需 User.ReadBasic.All。兩者皆尚未開通——其餘工具不受影響。")
                return ("ERROR: People 查無此人，且目錄查詢需 User.ReadBasic.All 權限"
                        "（尚未授予/需 IT admin consent）。")
            raise
    # 去重（同一人可能同時出現在 People 與目錄）
    seen, uniq = set(), []
    for r in results:
        k = r["email"].lower()
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    summary = f'Resolved "{q}" to {len(uniq)} person(s):'
    return summary + "\n" + json.dumps(uniq, ensure_ascii=False, indent=2)
