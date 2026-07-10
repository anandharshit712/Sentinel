"""js_imports (shared by dependency_graph_tool, test_mapper_tool) — JS/TS import resolution.

Regex-based on purpose (LLD §5.3 specs regex here, not tree-sitter, for import extraction): finds
`import ... from '...'`/`export ... from '...'`/`require('...')`/dynamic `import('...')`/bare
`import '...'` string literals and resolves relative ones (`./x`, `../y`) against a known set of
in-repo file-path module keys. Bare specifiers (npm packages) are out of scope, same as the Python
path only resolving imports internal to the repo.
"""
from __future__ import annotations

import posixpath
import re
from typing import Optional, Set

JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")

_IMPORT_RE = re.compile(
    r"""(?:\bfrom\s+|\brequire\(\s*|\bimport\(\s*|^\s*import\s+)['"]([^'"]+)['"]""", re.MULTILINE)


def module_key(path: str) -> str:
    """Repo-relative path -> module key (extension stripped, forward-slashed)."""
    dot = path.rfind(".")
    return path[:dot] if dot != -1 else path


def resolve_relative(importer_key: str, literal: str) -> Optional[str]:
    """Relative literal ('./x', '../y') -> normalized module key, unresolved against any file set."""
    if not literal.startswith("."):
        return None  # bare specifier (npm package) — out of scope
    return posixpath.normpath(posixpath.join(posixpath.dirname(importer_key), literal))


def resolve_relative_import(importer_key: str, literal: str, known: Set[str]) -> Optional[str]:
    """Like resolve_relative, but only returns a key that's an actual file in `known` (trying the
    bare candidate and its `/index` form — the two ways a JS/TS import can resolve to a file)."""
    cand = resolve_relative(importer_key, literal)
    if cand is None:
        return None
    if cand in known:
        return cand
    idx = f"{cand}/index"
    return idx if idx in known else None


def extract_import_targets(src: str, importer_key: str, known: Set[str]) -> Set[str]:
    out: Set[str] = set()
    for m in _IMPORT_RE.finditer(src):
        dep = resolve_relative_import(importer_key, m.group(1), known)
        if dep:
            out.add(dep)
    return out


def extract_relative_targets(src: str, importer_key: str) -> Set[str]:
    """All relative-import targets, unfiltered by any known-file set (both the bare and `/index`
    candidate forms) — for callers that intersect against a target set afterward, mirroring how
    test_mapper_tool's Python path extracts all imports first and intersects second."""
    out: Set[str] = set()
    for m in _IMPORT_RE.finditer(src):
        cand = resolve_relative(importer_key, m.group(1))
        if cand:
            out.add(cand)
            out.add(f"{cand}/index")
    return out


def demo() -> None:
    src = ("import { a } from './a';\nexport { b } from '../b';\n"
           "const c = require('./c');\nimport('./d');\nimport 'react';\n")
    known = {"src/a", "b", "src/c"}
    assert extract_import_targets(src, "src/x", known) == {"src/a", "b", "src/c"}
    assert extract_relative_targets(src, "src/x") == {
        "src/a", "src/a/index", "b", "b/index", "src/c", "src/c/index", "src/d", "src/d/index"}
    assert module_key("src/auth/login.js") == "src/auth/login"
    print("js_imports: OK")


if __name__ == "__main__":
    demo()
