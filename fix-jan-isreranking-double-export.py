#!/usr/bin/env python3
"""
Fix duplicate export typo in web-app/src/lib/modelCapabilities.ts:
  export export const isReranking -> export const isReranking

Run from the Jan repository root.
"""

from pathlib import Path
import re
import sys

path = Path("web-app/src/lib/modelCapabilities.ts")

if not path.exists():
    print("[fail] Run this from the Jan repository root. Missing web-app/src/lib/modelCapabilities.ts", file=sys.stderr)
    sys.exit(1)

text = path.read_text(encoding="utf-8")
original = text

# Direct malformed output from the previous fixer.
text = text.replace("export export const isReranking", "export const isReranking")
text = text.replace("export export function isReranking", "export function isReranking")

# Collapse any repeated export tokens around this helper, because text patchers apparently
# need adult supervision.
text = re.sub(
    r"\b(?:export\s+){2,}(const\s+isReranking\b)",
    r"export \1",
    text,
)
text = re.sub(
    r"\b(?:export\s+){2,}(function\s+isReranking\b)",
    r"export \1",
    text,
)

# If a non-exported helper exists, export it so noUnusedLocals does not complain.
text = re.sub(
    r"(?m)^(?!export\s+)const\s+isReranking\s*=",
    "export const isReranking =",
    text,
    count=1,
)

if text == original:
    print("[warn] No duplicate export or non-exported isReranking helper was changed.")
else:
    path.write_text(text, encoding="utf-8")
    print("[ok] Normalized isReranking export in web-app/src/lib/modelCapabilities.ts")

print("Run:")
print("  yarn build:web")
