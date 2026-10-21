"""Inline script for the digest HTML page and its CSP hash.

The script is a Python constant so the sha256 hash can be computed once at
import time and embedded in the Content-Security-Policy meta tag.  The
template receives it wrapped in ``markupsafe.Markup`` so Jinja autoescape
does not mangle its content.

Security invariants checked at import:
  - The source must not contain ``</script`` (prevents early tag close).
  - The source must not contain ``<!--`` (prevents HTML comment injection).
"""

from __future__ import annotations

import base64
import hashlib

# ---------------------------------------------------------------------------
# Script source — the ONLY inline script allowed on the digest page.
# ---------------------------------------------------------------------------
# It reads the report code from a ``data-report`` attribute on the element
# ``#app`` (validated server-side to match ``R-[A-Z0-9]{3}`` or omitted).
# Todo number and priority come from ``data-no`` / ``data-pri`` attributes
# whose values are emitted only from validated integers and a P0|P1|P2
# whitelist.
# ---------------------------------------------------------------------------

DIGEST_SCRIPT: str = (
    "(function(){"
    'var el=document.getElementById("app");'
    'var REPORT=el?el.dataset.report||"":"";'
    'var rows=Array.prototype.slice.call(document.querySelectorAll(".todo-row"));'
    'var opts=[["keep","\\u4fdd\\u7559"],["done","\\u5b8c\\u6210"],["drop","\\u522a\\u9664"]];'
    "rows.forEach(function(row){"
    "var no=row.dataset.no,pri=row.dataset.pri,"
    'box=row.querySelector(".controls");'
    "if(!box)return;"
    'var seg=document.createElement("div");seg.className="seg";'
    "opts.forEach(function(o,i){"
    'var l=document.createElement("label"),'
    'inp=document.createElement("input"),'
    's=document.createElement("span");'
    'inp.type="radio";inp.name="act-"+no;inp.value=o[0];inp.checked=i===0;'
    "s.textContent=o[1];l.appendChild(inp);l.appendChild(s);seg.appendChild(l);"
    "});"
    'var sel=document.createElement("select");'
    'sel.setAttribute("aria-label","#"+no+" \\u7684\\u512a\\u5148\\u9806\\u5e8f");'
    '["P0","P1","P2"].forEach(function(p){'
    'var op=document.createElement("option");'
    "op.value=p;op.textContent=p;op.selected=p===pri;sel.appendChild(op);});"
    "box.appendChild(seg);box.appendChild(sel);"
    "box.addEventListener(\"change\",refresh);"
    "});"
    "function state(){"
    "return rows.map(function(row){"
    'return{row:row,no:row.dataset.no,act:row.querySelector("input:checked").value,'
    'pri:row.querySelector("select").value,origPri:row.dataset.pri};'
    "}).sort(function(a,b){return a.no-b.no;});}"
    "function refresh(){"
    "var n=0;state().forEach(function(s){"
    'var changed=s.act!=="keep"||s.pri!==s.origPri;'
    's.row.classList.toggle("is-done",s.act==="done");'
    's.row.classList.toggle("is-drop",s.act==="drop");'
    's.row.classList.toggle("changed",changed);'
    'var badge=s.row.querySelector(".pri");'
    'if(badge){badge.className="pri "+s.pri;badge.textContent=s.pri;}'
    "if(changed)n++;});"
    'var cb=document.querySelector("#count b");if(cb)cb.textContent=n;}'
    'document.getElementById("gen").addEventListener("click",function(){'
    "var done=[],drop=[],pri=[];state().forEach(function(s){"
    'if(s.act==="done")done.push(s.no);'
    'else if(s.act==="drop")drop.push(s.no);'
    'if(s.act==="keep"&&s.pri!==s.origPri)pri.push(s.no+"\\u2192"+s.pri);'
    "});"
    'var lines=["\\u78ba\\u8a8d\\u5f85\\u8fa6"+(REPORT?" "+REPORT:"")];'
    'if(done.length)lines.push("\\u5b8c\\u6210\\uff1a"+done.join("\\u3001"));'
    'if(drop.length)lines.push("\\u522a\\u9664\\uff1a"+drop.join("\\u3001"));'
    'if(pri.length)lines.push("\\u6539\\u512a\\u5148\\uff1a"+pri.join("\\u3001"));'
    "lines.push(done.length||drop.length||pri.length?"
    '"\\u5176\\u9918\\u4fdd\\u7559":"\\u5168\\u90e8\\u4fdd\\u7559");'
    'var t=document.getElementById("txt");t.value=lines.join("\\n");'
    'document.getElementById("out").classList.add("show");t.focus();t.select();});'
    'document.getElementById("fold").addEventListener("click",function(){'
    'document.getElementById("out").classList.remove("show");'
    'document.getElementById("gen").focus();});'
    'document.getElementById("copy").addEventListener("click",function(){'
    'var t=document.getElementById("txt"),toast=document.getElementById("toast");'
    'function ok(){toast.textContent="\\u5df2\\u8907\\u88fd\\uff0c\\u8cbc\\u5230 /agenda \\u5c0d\\u8a71\\u5373\\u53ef";}'
    "if(navigator.clipboard&&navigator.clipboard.writeText){"
    'navigator.clipboard.writeText(t.value).then(ok,function(){t.select();document.execCommand("copy");ok();});'
    '}else{t.select();document.execCommand("copy");ok();}});'
    "})();"
)

# ---------------------------------------------------------------------------
# Safety checks — fail at import if the source is unsafe to embed inline.
# ---------------------------------------------------------------------------

assert "</script" not in DIGEST_SCRIPT.lower(), \
    "DIGEST_SCRIPT must not contain </script (tag close injection)"
assert "<!--" not in DIGEST_SCRIPT, \
    "DIGEST_SCRIPT must not contain <!-- (HTML comment injection)"

# ---------------------------------------------------------------------------
# CSP hash — sha256 of the raw UTF-8 bytes, base64-encoded.
# ---------------------------------------------------------------------------

DIGEST_SCRIPT_HASH: str = base64.b64encode(
    hashlib.sha256(DIGEST_SCRIPT.encode("utf-8")).digest()
).decode("ascii")
"""Base64-encoded SHA-256 hash for ``script-src 'sha256-…'``."""
