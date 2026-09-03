from __future__ import annotations

import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
patterns = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "openai_key": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}"),
    "bearer_token": re.compile(rb"Bearer [A-Za-z0-9._-]{20,}"),
    "credential_assignment": re.compile(rb"(?i)(?:api_key|password|secret)\s*[=:]\s*['\"][^'\"]{8,}['\"]"),
}
findings = []
for path in root.rglob("*"):
    if not path.is_file() or any(p in {".git", ".state", "__pycache__"} for p in path.parts): continue
    try: data = path.read_bytes()
    except OSError: continue
    for name, pattern in patterns.items():
        if pattern.search(data): findings.append({"file":str(path.relative_to(root)),"pattern":name})
print(f"secret_scan files_scanned=tracked_worktree findings={len(findings)}")
for finding in findings: print(finding)
raise SystemExit(bool(findings))
