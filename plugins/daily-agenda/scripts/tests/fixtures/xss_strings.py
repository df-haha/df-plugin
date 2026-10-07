"""XSS and injection test strings for all untrusted fields."""

from __future__ import annotations

# Each string should be placed in EVERY rendered field (todo title, source label,
# event subject/location, new chat name, notice, etc.).
XSS_PAYLOADS: list[str] = [
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    'javascript:alert(1)',
    'JaVaScRiPt:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    '![x](https://evil.example/x.png)',
    '[Click me](javascript:alert(1))',
    '<iframe src="https://evil.example"></iframe>',
    '<object data="https://evil.example"></object>',
    '<embed src="https://evil.example">',
    '<link rel="stylesheet" href="https://evil.example/x.css">',
    'java\tscript:alert(1)',  # tab inside "javascript:"
    '  javascript:alert(1)  ',  # leading/trailing whitespace
    '<svg onload=alert(1)>',
    '"><script>alert(1)</script><a href="',
]

# A malicious href that should be sanitised to None.
MALICIOUS_HREFS: list[str] = [
    'javascript:alert(1)',
    'JaVaScRiPt:alert(1)',
    'java\tscript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    '  javascript:alert(1)  ',
    'vbscript:MsgBox("XSS")',
    'ftp://evil.example/malware',
    '',
]

SAFE_HREFS: list[str] = [
    'https://outlook.office.com/mail/read/123',
    'mailto:someone@example.com',
]
