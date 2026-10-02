"""Deterministic, passive relationship extractor for Python, JavaScript, and TypeScript."""

import ast
import re
from pathlib import Path
from typing import List, Optional


class ExtractedImport:
    def __init__(
        self,
        imported_name: str,
        source_module: Optional[str] = None,
        level: int = 0,
        line_number: Optional[int] = None,
        raw_statement: Optional[str] = None,
        source_type: str = "ast",
    ):
        self.imported_name = imported_name
        self.source_module = source_module
        self.level = level
        self.line_number = line_number
        self.raw_statement = raw_statement
        self.source_type = source_type


class ExtractionResult:
    def __init__(
        self,
        file_path: str,
        language: str,
        is_supported: bool = True,
        imports: Optional[List[ExtractedImport]] = None,
        error: Optional[str] = None,
    ):
        self.file_path = file_path
        self.language = language
        self.is_supported = is_supported
        self.imports = imports or []
        self.error = error


# Regex patterns for JavaScript & TypeScript import extraction
JS_TS_IMPORT_PATTERNS = [
    re.compile(r"""(?:import\s+(?:type\s+)?.*?from\s+|export\s+(?:type\s+)?.*?from\s+)['"]([^'"]+)['"]"""),
    re.compile(r"""(?:^|\s+)import\s+['"]([^'"]+)['"]"""),
    re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
]

FROM_MODULE_PATTERN = re.compile(r"""\bfrom\s+['"]([^'"]+)['"]""")
MULTILINE_CALL_PATTERN = re.compile(r"""\b(?:require|import)\s*\(\s*['"]([^'"]+)['"]\s*\)""")


def _strip_comments_for_matching(text: str) -> str:
    """Strips comments for regex matching without mutating original source."""
    t = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    t = re.sub(r"//.*$", " ", t, flags=re.MULTILINE)
    return t

LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}


def extract_python_imports(file_path: Path, rel_path: str) -> ExtractionResult:
    """Extracts imports passively from a Python file using the built-in ast module."""
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as e:
        return ExtractionResult(rel_path, language="python", is_supported=True, error=str(e))

    lines = source.splitlines()

    try:
        tree = ast.parse(source, filename=str(file_path))
    except (SyntaxError, ValueError, MemoryError, RecursionError) as e:
        return ExtractionResult(rel_path, language="python", is_supported=True, error=f"ParseError: {e}")

    imports: List[ExtractedImport] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            line_no = getattr(node, "lineno", None)
            raw = lines[line_no - 1].strip() if line_no and 0 < line_no <= len(lines) else None
            for alias in node.names:
                imports.append(
                    ExtractedImport(
                        imported_name=alias.name,
                        source_module=None,
                        level=0,
                        line_number=line_no,
                        raw_statement=raw,
                        source_type="ast",
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            line_no = getattr(node, "lineno", None)
            raw = lines[line_no - 1].strip() if line_no and 0 < line_no <= len(lines) else None
            module = node.module
            level = getattr(node, "level", 0)
            for alias in node.names:
                imports.append(
                    ExtractedImport(
                        imported_name=alias.name,
                        source_module=module,
                        level=level,
                        line_number=line_no,
                        raw_statement=raw,
                        source_type="ast",
                    )
                )

    # Sort deterministically by line number, then name
    imports.sort(key=lambda x: (x.line_number or 0, x.source_module or "", x.imported_name))
    return ExtractionResult(rel_path, language="python", is_supported=True, imports=imports)


def extract_js_ts_imports(file_path: Path, rel_path: str, language: str) -> ExtractionResult:
    """Extracts imports deterministically from JS/TS source files with safe statement accumulation."""
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as e:
        return ExtractionResult(rel_path, language=language, is_supported=True, error=str(e))

    lines = source.splitlines()
    imports: List[ExtractedImport] = []
    idx = 0
    n = len(lines)

    while idx < n:
        line = lines[idx]
        clean_line = line.strip()

        # Skip empty lines or pure comment lines
        if not clean_line or clean_line.startswith("//") or clean_line.startswith("/*") or clean_line.startswith("*"):
            idx += 1
            continue

        # 1. Check for single-line imports first
        matched_single = False
        for pattern in JS_TS_IMPORT_PATTERNS:
            match = pattern.search(clean_line)
            if match:
                module_name = match.group(1).strip()
                if module_name:
                    imports.append(
                        ExtractedImport(
                            imported_name=module_name,
                            source_module=None,
                            level=1 if module_name.startswith(".") else 0,
                            line_number=idx + 1,
                            raw_statement=clean_line,
                            source_type="regex",
                        )
                    )
                    matched_single = True
                    break

        if matched_single:
            idx += 1
            continue

        # 2. Check for multiline ES module import / re-export statement
        is_import_start = clean_line == "import" or clean_line.startswith(
            ("import ", "import\t", "import{", "import type")
        )
        is_export_start = bool(re.match(r"^export\s+(?:type\s+)?(?:\{|\*)", clean_line))

        if is_import_start or is_export_start:
            accumulated = [line]
            start_line = idx + 1
            consumed_to = idx
            found = False

            for j in range(idx + 1, min(idx + 50, n)):
                sub_line = lines[j]
                accumulated.append(sub_line)
                consumed_to = j

                combined = "\n".join(accumulated)
                combined_clean = _strip_comments_for_matching(combined)

                # Check if we found 'from ...'
                from_match = FROM_MODULE_PATTERN.search(combined_clean)
                if from_match:
                    mod = from_match.group(1).strip()
                    if mod:
                        imports.append(
                            ExtractedImport(
                                imported_name=mod,
                                source_module=None,
                                level=1 if mod.startswith(".") else 0,
                                line_number=start_line,
                                raw_statement="\n".join(accumulated).strip(),
                                source_type="regex",
                            )
                        )
                    found = True
                    idx = consumed_to
                    break

                # Support multiline bare import: import\n  "style.css";
                bare_match = re.search(r"""(?:^|\s+)import\s+['"]([^'"]+)['"]""", combined_clean)
                if bare_match and is_import_start:
                    mod = bare_match.group(1).strip()
                    if mod:
                        imports.append(
                            ExtractedImport(
                                imported_name=mod,
                                source_module=None,
                                level=1 if mod.startswith(".") else 0,
                                line_number=start_line,
                                raw_statement="\n".join(accumulated).strip(),
                                source_type="regex",
                            )
                        )
                    found = True
                    idx = consumed_to
                    break

                # Abort boundaries:
                # 1. Semicolon reached without 'from' in combined text (e.g. export { a, b };)
                if ";" in combined_clean and not ("from" in combined_clean):
                    break

                # 2. Another top-level declaration keyword encountered before finding 'from'
                sub_uncommented = _strip_comments_for_matching(sub_line).strip()
                if sub_uncommented.startswith((
                    "import ", "import\t", "import{", "export ", "export\t",
                    "class ", "function ", "const ", "let ", "var "
                )):
                    break

            if found:
                idx += 1
                continue

        # 3. Multiline require(...) or dynamic import(...)
        if re.search(r"\b(?:require|import)\s*\(", clean_line):
            accumulated = [line]
            start_line = idx + 1
            consumed_to = idx
            found = False

            for j in range(idx + 1, min(idx + 20, n)):
                sub_line = lines[j]
                accumulated.append(sub_line)
                consumed_to = j

                combined = "\n".join(accumulated)
                combined_clean = _strip_comments_for_matching(combined)

                call_match = MULTILINE_CALL_PATTERN.search(combined_clean)
                if call_match:
                    mod = call_match.group(1).strip()
                    if mod:
                        imports.append(
                            ExtractedImport(
                                imported_name=mod,
                                source_module=None,
                                level=1 if mod.startswith(".") else 0,
                                line_number=start_line,
                                raw_statement="\n".join(accumulated).strip(),
                                source_type="regex",
                            )
                        )
                    found = True
                    idx = consumed_to
                    break

                # Abort if statement closed or terminated
                if ";" in combined_clean or ")" in combined_clean:
                    break

            if found:
                idx += 1
                continue

        idx += 1

    imports.sort(key=lambda x: (x.line_number or 0, x.imported_name))
    return ExtractionResult(rel_path, language=language, is_supported=True, imports=imports)


def extract_file_relationships(
    file_path: Path,
    rel_path: str,
    is_binary: bool = False,
    is_large: bool = False,
) -> ExtractionResult:
    """Extracts deterministic imports and relationships for a project file.
    
    Treats file as untrusted; never executes or evaluates code.
    """
    if is_binary:
        return ExtractionResult(rel_path, language="binary", is_supported=False)
    if is_large:
        return ExtractionResult(rel_path, language="large_file", is_supported=False)

    ext = file_path.suffix.lower()
    language = LANGUAGE_EXTENSIONS.get(ext)

    if not language:
        # Unsupported language
        unsupported_lang = ext.lstrip(".") if ext else "no_ext"
        return ExtractionResult(rel_path, language=unsupported_lang, is_supported=False)

    if language == "python":
        return extract_python_imports(file_path, rel_path)
    elif language in ("javascript", "typescript"):
        return extract_js_ts_imports(file_path, rel_path, language)

    return ExtractionResult(rel_path, language=language, is_supported=False)
