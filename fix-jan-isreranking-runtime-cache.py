#!/usr/bin/env python3
"""
Fix Jan runtime error: "isReranking is not defined".

This patches both source files and already-extracted Jan extension cache files.
Run it from the Jan repository root with Jan closed.
"""

from pathlib import Path
import os
import re
import sys

ROOT = Path.cwd()

SOURCE_DIRS = [
    ROOT / "extensions",
    ROOT / "web-app" / "src",
    ROOT / "core" / "src",
    ROOT / "src-tauri" / "plugins" / "tauri-plugin-llamacpp" / "guest-js",
]

CACHE_DIRS = []
userprofile = os.environ.get("USERPROFILE")
appdata = os.environ.get("APPDATA")
localappdata = os.environ.get("LOCALAPPDATA")
if userprofile:
    CACHE_DIRS += [
        Path(userprofile) / "jan" / "extensions",
        Path(userprofile) / ".jan" / "extensions",
    ]
if appdata:
    CACHE_DIRS += [
        Path(appdata) / "Jan" / "extensions",
        Path(appdata) / "jan" / "extensions",
    ]
if localappdata:
    CACHE_DIRS += [
        Path(localappdata) / "Jan" / "extensions",
        Path(localappdata) / "jan" / "extensions",
    ]

EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
BAD_REPLACEMENTS = {
    "IsReranking": "isReranking",
    "isRreranking": "isReranking",
    "IsRreranking": "isReranking",
    "is_rreranking": "is_reranking",
}

HELPER_MARKER = "local-ai-stack isReranking runtime helper"

TS_HELPER = """// local-ai-stack isReranking runtime helper
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
      lower(value.path).includes('rerank') ||
      hasCap(value.capabilities) ||
      hasCap(metadata.capabilities)
  )
}

"""

JS_HELPER = """// local-ai-stack isReranking runtime helper
const isReranking = (model) => {
  const value = model ?? {}
  const metadata = value.metadata ?? value.meta ?? {}
  const lower = (input) => String(input ?? '').toLowerCase()
  const hasCap = (capabilities) => {
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
      lower(value.path).includes('rerank') ||
      hasCap(value.capabilities) ||
      hasCap(metadata.capabilities)
  )
}

"""

def die(msg):
    print("[fail] " + msg, file=sys.stderr)
    sys.exit(1)

def read(path):
    return path.read_text(encoding="utf-8", errors="ignore")

def write(path, text):
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)

def is_text_source(path):
    return path.is_file() and path.suffix in EXTS

def has_import_or_definition(text):
    if HELPER_MARKER in text:
        return True
    if re.search(r"\b(?:const|let|var|function)\s+isReranking\b", text):
        return True
    if re.search(r"\bexport\s+const\s+isReranking\b", text):
        return True
    if re.search(r"\bexport\s+function\s+isReranking\b", text):
        return True
    if re.search(r"\bimport\s+[^;\n]*\bisReranking\b", text):
        return True
    if re.search(r"import\s*\{[\s\S]*?\bisReranking\b[\s\S]*?\}\s*from", text):
        return True
    return False

def insert_index(text):
    # Skip shebang if present.
    offset = 0
    if text.startswith("#!"):
        nl = text.find("\n")
        if nl != -1:
            offset = nl + 1

    # Insert after all top-level import statements.
    imports = list(re.finditer(r"^import[\s\S]*?from\s+['\"][^'\"]+['\"]\s*;?\s*$", text[offset:], re.M))
    if imports:
        return offset + imports[-1].end() + 1

    # Insert before first declaration.
    m = re.search(r"^(?:export\s+)?(?:const|let|var|function|class|interface|type|enum)\b", text[offset:], re.M)
    if m:
        return offset + m.start()

    return offset

def fix_model_capabilities():
    path = ROOT / "web-app" / "src" / "lib" / "modelCapabilities.ts"
    if not path.exists():
        return None
    text = read(path)
    original = text

    # Make any standalone helper exported so noUnusedLocals doesn't reject it.
    text = text.replace(
        "const isReranking = (model: any): boolean => {",
        "export const isReranking = (model: any): boolean => {",
    )
    text = text.replace(
        "const isReranking = (model: unknown): boolean => {",
        "export const isReranking = (model: unknown): boolean => {",
    )

    # If no helper exists, add an exported wrapper.
    if "isReranking" not in text:
        text += "\n" + TS_HELPER.replace("const isReranking", "export const isReranking")

    if text != original:
        write(path, text)
        return str(path.relative_to(ROOT))
    return None

def patch_file(path):
    text = read(path)
    original = text

    for bad, good in BAD_REPLACEMENTS.items():
        text = text.replace(bad, good)

    # Only add helper if file references isReranking but has no local/imported definition.
    if re.search(r"\bisReranking\s*\(", text) and not has_import_or_definition(text):
        helper = TS_HELPER if path.suffix in {".ts", ".tsx"} else JS_HELPER
        idx = insert_index(text)
        text = text[:idx] + helper + text[idx:]

    if text != original:
        write(path, text)
        return True
    return False

def walk_dirs(dirs):
    seen = set()
    for base in dirs:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            if is_text_source(path):
                yield path

def main():
    if not (ROOT / "package.json").exists() or not (ROOT / "src-tauri").exists():
        die("Run this from the Jan repository root.")

    changed = []

    cap = fix_model_capabilities()
    if cap:
        changed.append(cap)

    all_dirs = SOURCE_DIRS + CACHE_DIRS
    for path in walk_dirs(all_dirs):
        try:
            if patch_file(path):
                try:
                    changed.append(str(path.relative_to(ROOT)))
                except ValueError:
                    changed.append(str(path))
        except UnicodeDecodeError:
            continue

    print("[info] scanned source dirs:")
    for d in SOURCE_DIRS:
        print("  " + str(d))
    print("[info] scanned cache dirs:")
    for d in CACHE_DIRS:
        print("  " + str(d))

    if changed:
        print("\n[ok] patched files:")
        for item in changed:
            print("  " + item)
    else:
        print("\n[warn] no files changed. The error may be in a stale installed app bundle.")

    print("\nRun this exact rebuild/refresh sequence:")
    print("  yarn build:extensions")
    print("  yarn copy:assets:tauri")
    print("  yarn build:web")
    print("  Remove-Item -Recurse -Force \"$env:USERPROFILE\\jan\\extensions\\janhq-llamacpp-extension\" -ErrorAction SilentlyContinue")
    print("  Remove-Item -Recurse -Force \"$env:USERPROFILE\\jan\\extensions\\janhq-rag-extension\" -ErrorAction SilentlyContinue")
    print("  yarn dev:tauri")
    print("\nIf you are using an installed build, rebuild the installer and reinstall it:")
    print("  yarn tauri build")

if __name__ == "__main__":
    main()
