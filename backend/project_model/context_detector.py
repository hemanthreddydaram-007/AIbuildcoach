"""Deterministic, read-only Development Context and Change Evidence detector."""

import subprocess
import shutil
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from backend.domain.models import (
    GitState,
    ChangeType,
    DiffHunk,
    FileChange,
    EvidenceRecord,
    ChangeSet,
    utc_now_iso,
)
from backend.project_model.diff_parser import parse_unified_diff, ParsedFileDiff, _unquote_git_path
from backend.project_model.git_detector import detect_git_state

MAX_FILE_SIZE_FOR_CONTEXT = 1024 * 1024  # 1 MB threshold for reading untracked files


def _run_git_cmd(git_bin: str, repo_root: Path, args: List[str]) -> Optional[str]:
    """Runs a read-only git command safely with timeout and error handling.
    
    Never executes repository code or mutates git state.
    """
    try:
        res = subprocess.run(
            [git_bin] + args,
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
        if res.returncode == 0:
            return res.stdout
        return None
    except (subprocess.SubprocessError, OSError):
        return None


class ContextDetector:
    """Reads working tree and Git state to construct deterministic ChangeSets and Evidence."""

    def __init__(self, project_root: Path, project_id: str):
        self.project_root = project_root.resolve()
        self.project_id = project_id
        self.git_bin = shutil.which("git")

    def collect(self, git_state: Optional[GitState] = None) -> ChangeSet:
        """Collects development context following the 5-layer priority:
        1. Working-tree state
        2. git status
        3. unstaged diff
        4. staged diff
        5. recent commit information
        """
        created_at = utc_now_iso()

        # Check if Git is available
        if not self.git_bin:
            return self._build_non_git_changeset(created_at, "Git binary not found on system")

        if git_state is None:
            git_state = detect_git_state(self.project_root, git_bin=self.git_bin)

        if not git_state.is_git_repo:
            return self._build_non_git_changeset(created_at, "Directory is not a Git repository")

        # Layer 2: git status --porcelain=v1 -uall
        # If git_state reports a clean tree, skip status and diff queries safely
        if not git_state.is_dirty:
            status_output = ""
            unstaged_diff_raw = ""
            staged_diff_raw = ""
        else:
            status_output = _run_git_cmd(self.git_bin, self.project_root, ["status", "--porcelain=v1", "-uall"])
            if status_output is None:
                return self._build_non_git_changeset(created_at, "Failed to execute git status")

            # Layer 3: unstaged diff (only if there are modified/deleted working-tree files)
            if git_state.modified_count > 0:
                unstaged_diff_raw = _run_git_cmd(self.git_bin, self.project_root, ["diff", "--no-color", "-p", "-U3"]) or ""
            else:
                unstaged_diff_raw = ""

            # Layer 4: staged diff (only if there are staged files)
            if git_state.staged_count > 0:
                staged_diff_raw = _run_git_cmd(self.git_bin, self.project_root, ["diff", "--cached", "--no-color", "-p", "-U3"]) or ""
            else:
                staged_diff_raw = ""

        unstaged_diffs = parse_unified_diff(unstaged_diff_raw)
        unstaged_map: Dict[str, ParsedFileDiff] = {d.new_path: d for d in unstaged_diffs}
        for d in unstaged_diffs:
            if d.old_path:
                unstaged_map[d.old_path] = d

        staged_diffs = parse_unified_diff(staged_diff_raw)
        staged_map: Dict[str, ParsedFileDiff] = {d.new_path: d for d in staged_diffs}
        for d in staged_diffs:
            if d.old_path:
                staged_map[d.old_path] = d

        # Layer 5: recent commit information
        commit_evidence: Optional[EvidenceRecord] = None
        log_raw = _run_git_cmd(self.git_bin, self.project_root, ["log", "-1", "--format=%H%x00%an%x00%ad%x00%s"])
        if log_raw and log_raw.strip():
            parts = log_raw.strip().split("\x00")
            if len(parts) >= 4:
                c_hash, c_author, c_date, c_subject = parts[0], parts[1], parts[2], parts[3]
                obs = f"HEAD commit is {c_hash[:8]} by {c_author}: {c_subject}"
                ev_id = self._compute_evidence_id("RECENT_COMMIT", "repo", f"git log -1:{obs}")
                commit_evidence = EvidenceRecord(
                    id=ev_id,
                    project_id=self.project_id,
                    evidence_type="RECENT_COMMIT",
                    source="git log -1",
                    file_path=None,
                    observation=obs,
                    raw_data=log_raw.strip(),
                    confidence="HIGH",
                    created_at=created_at,
                )

        # Parse porcelain status lines
        status_lines = [l for l in status_output.splitlines() if l.strip()]
        file_entries: List[Tuple[str, str, str, Optional[str]]] = []  # (status_code, path, change_type, old_path)

        for line in status_lines:
            if len(line) < 3:
                continue
            code = line[:2]
            raw_path_part = line[3:].strip()

            old_path = None
            new_path = raw_path_part

            if " -> " in raw_path_part:
                # Rename e.g. R  old.txt -> new.txt
                old_raw, new_raw = raw_path_part.split(" -> ", 1)
                old_path = _unquote_git_path(old_raw)
                new_path = _unquote_git_path(new_raw)
            else:
                new_path = _unquote_git_path(raw_path_part)

            # Exclude internal Build Coach state directory
            if new_path == ".buildcoach" or new_path.startswith(".buildcoach/") or new_path.startswith(".buildcoach\\"):
                continue
            if old_path and (old_path == ".buildcoach" or old_path.startswith(".buildcoach/") or old_path.startswith(".buildcoach\\")):
                continue

            # Classify change type
            index_status = code[0]
            worktree_status = code[1]

            if index_status == "?" and worktree_status == "?":
                ctype = ChangeType.ADDED
            elif index_status == "R" or worktree_status == "R":
                ctype = ChangeType.RENAMED
            elif index_status == "D" or worktree_status == "D":
                ctype = ChangeType.DELETED
            elif index_status == "A" or worktree_status == "A":
                ctype = ChangeType.ADDED
            else:
                ctype = ChangeType.MODIFIED

            file_entries.append((code, new_path, ctype, old_path))

        # Sort file entries deterministically by new_path
        file_entries.sort(key=lambda e: e[1])

        # Generate canonical fingerprint for ChangeSet ID
        canon_parts = [self.project_id, git_state.head_commit or "none"]
        for code, path, ctype, old_p in file_entries:
            canon_parts.append(f"{code}:{path}:{ctype}:{old_p or ''}")
        if not file_entries:
            canon_parts.append("clean")

        canon_str = "|".join(canon_parts)
        changeset_hash = hashlib.sha256(canon_str.encode("utf-8")).hexdigest()[:16]
        changeset_id = f"cs:{self.project_id}:{changeset_hash}"

        file_changes: List[FileChange] = []
        evidence_list: List[EvidenceRecord] = []

        if commit_evidence:
            commit_evidence.change_set_id = changeset_id
            evidence_list.append(commit_evidence)

        # Working tree clean observation if 0 changed files
        if not file_entries:
            ev_clean_id = self._compute_evidence_id("GIT_STATUS", "repo", "git status:clean")
            evidence_list.append(
                EvidenceRecord(
                    id=ev_clean_id,
                    project_id=self.project_id,
                    change_set_id=changeset_id,
                    evidence_type="GIT_STATUS",
                    source="git status --porcelain",
                    file_path=None,
                    observation="Working tree is clean; no uncommitted changes detected.",
                    raw_data="",
                    confidence="HIGH",
                    created_at=created_at,
                )
            )

        for code, path, ctype, old_p in file_entries:
            index_status = code[0]
            worktree_status = code[1]
            is_untracked = (index_status == "?" and worktree_status == "?")
            is_staged = index_status in ("M", "A", "D", "R", "C")

            fc_id = f"fc:{changeset_id}:{path}:{ctype}:{1 if is_staged else 0}_{1 if is_untracked else 0}"

            # Collect diff hunks for this file
            hunks: List[DiffHunk] = []
            parsed_staged = staged_map.get(path) or (staged_map.get(old_p) if old_p else None)
            parsed_unstaged = unstaged_map.get(path) or (unstaged_map.get(old_p) if old_p else None)

            is_binary = False
            if (parsed_staged and parsed_staged.is_binary) or (parsed_unstaged and parsed_unstaged.is_binary):
                is_binary = True

            # Add staged hunks
            if parsed_staged:
                for idx, ph in enumerate(parsed_staged.hunks):
                    h_hash = hashlib.sha256(ph.content.encode("utf-8")).hexdigest()[:8]
                    hunk_id = f"hunk:{fc_id}:staged:{ph.old_start},{ph.old_lines}->{ph.new_start},{ph.new_lines}:{h_hash}"
                    hunks.append(
                        DiffHunk(
                            id=hunk_id,
                            file_change_id=fc_id,
                            old_start=ph.old_start,
                            old_lines=ph.old_lines,
                            new_start=ph.new_start,
                            new_lines=ph.new_lines,
                            header=ph.header,
                            content=ph.content,
                        )
                    )
                    # Evidence record for staged hunk
                    obs_hunk = f"Staged hunk @@ -{ph.old_start},{ph.old_lines} +{ph.new_start},{ph.new_lines} @@ in {path}"
                    ev_hunk_id = self._compute_evidence_id("GIT_DIFF", path, f"staged:{ph.content}")
                    evidence_list.append(
                        EvidenceRecord(
                            id=ev_hunk_id,
                            project_id=self.project_id,
                            change_set_id=changeset_id,
                            evidence_type="GIT_DIFF",
                            source="git diff --cached",
                            file_path=path,
                            observation=obs_hunk,
                            raw_data=ph.content,
                            confidence="HIGH",
                            created_at=created_at,
                        )
                    )

            # Add unstaged hunks
            if parsed_unstaged:
                for idx, ph in enumerate(parsed_unstaged.hunks):
                    h_hash = hashlib.sha256(ph.content.encode("utf-8")).hexdigest()[:8]
                    hunk_id = f"hunk:{fc_id}:unstaged:{ph.old_start},{ph.old_lines}->{ph.new_start},{ph.new_lines}:{h_hash}"
                    hunks.append(
                        DiffHunk(
                            id=hunk_id,
                            file_change_id=fc_id,
                            old_start=ph.old_start,
                            old_lines=ph.old_lines,
                            new_start=ph.new_start,
                            new_lines=ph.new_lines,
                            header=ph.header,
                            content=ph.content,
                        )
                    )
                    # Evidence record for unstaged hunk
                    obs_hunk = f"Unstaged hunk @@ -{ph.old_start},{ph.old_lines} +{ph.new_start},{ph.new_lines} @@ in {path}"
                    ev_hunk_id = self._compute_evidence_id("GIT_DIFF", path, f"unstaged:{ph.content}")
                    evidence_list.append(
                        EvidenceRecord(
                            id=ev_hunk_id,
                            project_id=self.project_id,
                            change_set_id=changeset_id,
                            evidence_type="GIT_DIFF",
                            source="git diff",
                            file_path=path,
                            observation=obs_hunk,
                            raw_data=ph.content,
                            confidence="HIGH",
                            created_at=created_at,
                        )
                    )

            # Line counts and ranges
            old_line_count: Optional[int] = None
            new_line_count: Optional[int] = None
            line_ranges: List[Tuple[int, int]] = []

            for h in hunks:
                if ctype == ChangeType.DELETED or h.new_lines == 0:
                    start = h.old_start
                    end = h.old_start + max(h.old_lines - 1, 0)
                else:
                    start = h.new_start
                    end = h.new_start + max(h.new_lines - 1, 0)
                line_ranges.append((start, end))

            # If untracked file, inspect working tree directly (Priority 1)
            if is_untracked:
                abs_fpath = self.project_root / path
                if abs_fpath.is_file():
                    try:
                        sz = abs_fpath.stat().st_size
                        if sz <= MAX_FILE_SIZE_FOR_CONTEXT:
                            text = abs_fpath.read_text(encoding="utf-8", errors="replace")
                            lines_cnt = len(text.splitlines())
                            new_line_count = lines_cnt
                            if lines_cnt > 0:
                                line_ranges.append((1, lines_cnt))
                        else:
                            is_binary = True
                    except OSError:
                        pass

                obs_wt = f"Untracked file in working tree: {path} ({new_line_count or 0} lines)"
                ev_wt_id = self._compute_evidence_id("WORKING_TREE", path, obs_wt)
                evidence_list.append(
                    EvidenceRecord(
                        id=ev_wt_id,
                        project_id=self.project_id,
                        change_set_id=changeset_id,
                        evidence_type="WORKING_TREE",
                        source="working-tree",
                        file_path=path,
                        observation=obs_wt,
                        raw_data=None,
                        confidence="HIGH",
                        created_at=created_at,
                    )
                )

            # Layer 2 GIT_STATUS evidence for the file
            status_desc = f"Status code [{code}]: {path} is {ctype}"
            if old_p:
                status_desc += f" (renamed from {old_p})"
            if is_staged:
                status_desc += " [staged]"
            if worktree_status in ("M", "D"):
                status_desc += " [unstaged]"

            ev_status_id = self._compute_evidence_id("GIT_STATUS", path, f"{code}:{status_desc}")
            evidence_list.append(
                EvidenceRecord(
                    id=ev_status_id,
                    project_id=self.project_id,
                    change_set_id=changeset_id,
                    evidence_type="GIT_STATUS",
                    source="git status --porcelain",
                    file_path=path,
                    observation=status_desc,
                    raw_data=code,
                    confidence="HIGH",
                    created_at=created_at,
                )
            )

            # Deduplicate and sort line ranges
            unique_ranges = sorted(list(set(line_ranges)))

            file_changes.append(
                FileChange(
                    id=fc_id,
                    change_set_id=changeset_id,
                    old_path=old_p,
                    new_path=path,
                    change_type=ctype,
                    is_staged=is_staged,
                    is_untracked=is_untracked,
                    old_line_count=old_line_count,
                    new_line_count=new_line_count,
                    line_ranges=unique_ranges,
                    hunks=hunks,
                    is_binary=is_binary,
                )
            )

        summary = {
            "total_changed_files": len(file_changes),
            "added": sum(1 for f in file_changes if f.change_type == ChangeType.ADDED),
            "modified": sum(1 for f in file_changes if f.change_type == ChangeType.MODIFIED),
            "deleted": sum(1 for f in file_changes if f.change_type == ChangeType.DELETED),
            "renamed": sum(1 for f in file_changes if f.change_type == ChangeType.RENAMED),
            "staged_count": sum(1 for f in file_changes if f.is_staged),
            "untracked_count": sum(1 for f in file_changes if f.is_untracked),
            "total_hunks": sum(len(f.hunks) for f in file_changes),
        }

        # Deterministic sorting
        file_changes.sort(key=lambda f: f.new_path)
        evidence_list.sort(key=lambda e: (e.file_path or "", e.evidence_type, e.id))

        return ChangeSet(
            id=changeset_id,
            project_id=self.project_id,
            git_state=git_state,
            file_changes=file_changes,
            evidence=evidence_list,
            summary=summary,
            created_at=created_at,
        )

    def _compute_evidence_id(self, ev_type: str, file_path: str, content: str) -> str:
        """Constructs a deterministic SHA-256 evidence record ID."""
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        return f"ev:{self.project_id}:{ev_type}:{file_path}:{h}"

    def _build_non_git_changeset(self, created_at: str, reason: str) -> ChangeSet:
        """Constructs a deterministic empty ChangeSet when Git is unavailable."""
        cs_id = f"cs:{self.project_id}:no_git"
        ev_id = self._compute_evidence_id("WORKING_TREE", "repo", reason)
        evidence = [
            EvidenceRecord(
                id=ev_id,
                project_id=self.project_id,
                change_set_id=cs_id,
                evidence_type="WORKING_TREE",
                source="filesystem",
                file_path=None,
                observation=reason,
                raw_data=None,
                confidence="HIGH",
                created_at=created_at,
            )
        ]
        return ChangeSet(
            id=cs_id,
            project_id=self.project_id,
            git_state=GitState(is_git_repo=False),
            file_changes=[],
            evidence=evidence,
            summary={"total_changed_files": 0, "is_git_repo": False},
            created_at=created_at,
        )
