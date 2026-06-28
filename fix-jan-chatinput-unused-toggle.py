#!/usr/bin/env python3
from pathlib import Path
import sys

path = Path("web-app/src/containers/ChatInput.tsx")
if not path.exists():
    print("[fail] Run from the Jan repository root. Missing web-app/src/containers/ChatInput.tsx", file=sys.stderr)
    sys.exit(1)

text = path.read_text(encoding="utf-8")
before = text

patterns = [
    "  const toggleCapability = useCapabilityToggles((state) => state.toggle)\n",
    "  const toggleCapability = useCapabilityToggles((state) => state.toggle)\r\n",
]

for pattern in patterns:
    text = text.replace(pattern, "")

if text == before:
    print("[warn] toggleCapability selector was not found; file may already be fixed.")
else:
    path.write_text(text, encoding="utf-8")
    print("[ok] Removed unused toggleCapability selector from ChatInput.tsx")

print("Run: yarn build:web")
