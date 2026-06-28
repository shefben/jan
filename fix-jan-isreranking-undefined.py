#!/usr/bin/env python3
"""
Fix runtime import error: "isReranking is not defined".

The local-AI stack patcher can leave `isReranking(...)` references in source files
without declaring/importing that helper. This script adds a local helper only to
files that reference `isReranking` and do not already define or import it.

Run from the Jan repository root.
"""

from pathlib import Path
import re
import sys

ROOT = Path.cwd()

SEARCH_DIRS = [
    "extensions/llamacpp-extension/src",
    "extensions/rag-extension/src",
    "web-app/src",
    "src-tauri/plugins/tauri-plugin-llamacpp/guest-js",
]

EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}

HELPER_MARKER = "local-ai-stack isReranking helper"

HELPER = """// local-ai-stack isReranking helper
const isReranking = (model: any): boolean => {
  const value = model ?? {}
  const metadata = value.metadata ?? value.meta ?? {}
  const lower = (input: unknown): string => String(input ?? '').toLowerCase()
  const hasCap = (capabilities: unknown): boolean => {
    if (!Array.isArray(capabilities)) return false
    return capabilities.some((cap) => {
      const name = lower(cap)
      return name === 'rerank' || name === 'reranking' || name === 'rank'
    })
  }

  return Boolean(
    value.reranking === true ||
      value.isReranking === true ||
      value.is_reranking === true ||
      metadata.reranking === true ||
      metadata.isReranking === true ||
      metadata.is_reranking === true ||
      lower(value.type) === 'reranker' ||
      lower(value.model_type) === 'reranker' ||
      lower(metadata.type) === 'reranker' ||
      lower(metadata.model_type) === 'reranker' ||
      lower(value.id).includes('rerank') ||
      lower(value.name).includes('rerank') ||
      lower(value.model).includes('rerank') ||
      hasCap(value.capabilities) ||
      hasCap(metadata.capabilities)
  )
}

"""

def fail(msg):
    print("[fail] " + msg, file=sys.stderr)
    sys.exit(1)

def read(path):
    return path.read_text(encoding="utf-8")

def write(path, text):
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)

def has_local_definition_or_import(text):
    if HELPER_MARKER in text:
        return True
    if re.search(r"\b(?:const|let|var|function)\s+isReranking\b", text):
        return True
    if re.search(r"\bimport\s+[^\\n]*\bisReranking\b", text):
        return True
    # Multiline import block support.
    if re.search(r"import\s*\{[\s\S]*?\bisReranking\b[\s\S]*?\}\s*from", text):
        return True
    return False

def insertion_index(text):
    # Insert before first real source declaration after import/type preamble.
    m = re.search(r"^(?:const|function|class|export\s+(?:class|function|const|interface|type)|interface|type|enum)\b", text, re.M)
    if m:
        return m.start()

    # Fallback: after the final import-like statement.
    matches = list(re.finditer(r"^import[\s\S]*?['\"][^'\"]+['\"]\s*\n", text, re.M))
    if matches:
        return matches[-1].end()

    return 0

def main():
    if not (ROOT / "package.json").exists() or not (ROOT / "src-tauri").exists():
        fail("Run this from the Jan repository root.")

    touched = []
    refs = []

    for base in SEARCH_DIRS:
        folder = ROOT / base
        if not folder.exists():
            continue

        for path in folder.rglob("*"):
            if not path.is_file() or path.suffix not in EXTS:
                continue

            text = read(path)
            if "isReranking" not in text:
                continue

            rel = str(path.relative_to(ROOT))
            refs.append(rel)

            if has_local_definition_or_import(text):
                continue

            idx = insertion_index(text)
            fixed = text[:idx] + HELPER + text[idx:]
            write(path, fixed)
            touched.append(rel)

    if refs:
      print("[info] Files referencing isReranking:")
      for item in refs:
          print("  " + item)
    else:
      print("[warn] No source references to isReranking were found.")
      print("       If the GUI still errors, it is probably running stale packaged extension code.")

    if touched:
        print()
        print("[ok] Added local isReranking helper to:")
        for item in touched:
            print("  " + item)
    else:
        print()
        print("[info] No helper insertion was needed; definitions/imports already exist in source.")

    print()
    print("Now rebuild and refresh packaged extensions:")
    print("  yarn build:extensions")
    print("  yarn copy:assets:tauri")
    print("  yarn build:web")
    print()
    print("If the installed/dev GUI still errors, clear Jan's user extension cache:")
    print("  Remove-Item -Recurse -Force \"$env:USERPROFILE\\jan\\extensions\\janhq-llamacpp-extension\" -ErrorAction SilentlyContinue")
    print("  Remove-Item -Recurse -Force \"$env:USERPROFILE\\jan\\extensions\\janhq-rag-extension\" -ErrorAction SilentlyContinue")
    print()
    print("Then run:")
    print("  yarn dev:tauri")
    print()
    print("To inspect manually:")
    print("  Get-ChildItem -Recurse -Include *.ts,*.tsx,*.js,*.mjs | Select-String -Pattern '\\bisReranking\\b' -Context 2,2")

if __name__ == "__main__":
    main()
