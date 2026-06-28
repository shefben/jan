#!/usr/bin/env python3
# Fix Jan local-AI stack import crash: 'IsReranking' is not defined.
from pathlib import Path
import sys

ROOT = Path.cwd()

SEARCH_DIRS = [
    "extensions/llamacpp-extension/src",
    "web-app/src",
    "src-tauri/plugins/tauri-plugin-llamacpp/guest-js",
]

EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}

def fail(msg):
    print("[fail] " + msg, file=sys.stderr)
    sys.exit(1)

def read(path):
    return path.read_text(encoding="utf-8")

def write(path, text):
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)

def main():
    if not (ROOT / "package.json").exists() or not (ROOT / "src-tauri").exists():
        fail("Run this from the Jan repository root.")

    changed = []
    found = []

    for base in SEARCH_DIRS:
        folder = ROOT / base
        if not folder.exists():
            continue

        for path in folder.rglob("*"):
            if not path.is_file() or path.suffix not in EXTS:
                continue

            text = read(path)
            if "IsReranking" not in text:
                continue

            found.append(str(path.relative_to(ROOT)))
            fixed = text.replace("IsReranking", "isReranking")

            if fixed != text:
                write(path, fixed)
                changed.append(str(path.relative_to(ROOT)))

    if not found:
        print("[warn] No IsReranking identifier found. The crash may be coming from a built/cached bundle.")
    else:
        print("[ok] Replaced IsReranking -> isReranking in:")
        for item in changed:
            print("  " + item)

    print()
    print("Now rebuild the extension/front-end and restart Jan:")
    print("  yarn build:extensions")
    print("  yarn build:web")
    print("  yarn dev:tauri")
    print()
    print("If you installed a packaged build, rebuild/install again:")
    print("  yarn tauri build")

if __name__ == "__main__":
    main()
