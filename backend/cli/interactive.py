"""Human-facing interactive terminal interface for AI Build Coach."""

import sys
from pathlib import Path
from typing import Optional, List, Dict, Any

from backend.project_model.db import Database
from backend.cli.runner import (
    run_status,
    run_scan,
    run_understand_preview,
    run_understand_explain,
    run_understand_submit,
    run_viva_start,
    run_viva_submit,
    run_viva_report,
)


# ANSI Color Codes
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def colorize(text: str, color_code: str) -> str:
    """Wraps text in ANSI color codes if stdout is an interactive terminal."""
    if sys.stdout.isatty():
        return f"{color_code}{text}{RESET}"
    return text


def prompt_multiline_answer(prompt_text: str = "Enter your answer (press Enter on an empty line to submit):") -> str:
    """Safely prompts for multiline student input without exposing it via argv or history."""
    print(f"\n{colorize(prompt_text, BOLD)}")
    lines: List[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            if lines:
                break
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def display_banner(project_name: str, branch: Optional[str], dirty_count: int) -> None:
    """Renders the standard V1 first-use banner."""
    branch_str = f", branch: {branch}" if branch else ""
    info = f"{dirty_count} files modified{branch_str}"
    print(f"\n{colorize('+--------------------------------------------------------------+', CYAN)}")
    print(f"{colorize('|', CYAN)} {colorize('AI BUILD COACH', BOLD):<60} {colorize('|', CYAN)}")
    print(f"{colorize('|', CYAN)} Project: {project_name} [{info}]"[:62] + f" {colorize('|', CYAN)}")
    print(f"{colorize('+--------------------------------------------------------------+', CYAN)}")


def interactive_status(root: Path, db: Database, project_id: str) -> None:
    """Displays project status overview."""
    status = run_status(root, db, project_id)
    print(f"\n{colorize('=== PROJECT STATUS ===', BOLD)}")
    print(f"Project ID:       {status['project_id']}")
    print(f"Root:             {status['project_root']}")
    print(f"Git Repo:         {'Yes' if status['is_git_repo'] else 'No'}")
    print(f"Branch:           {status['current_branch'] or 'N/A'}")
    print(f"Dirty Tree:       {'Yes' if status['is_dirty'] else 'No'}")
    print(f"Files Modified:   {status['modified_files_count']}")
    print(f"Untracked Files:  {status['untracked_files_count']}")
    print(f"Staged Files:     {status['staged_files_count']}")
    print(f"Schema Version:   {status['schema_version']}")
    print(f"Graph Nodes:      {status['graph_nodes_count']}")
    print(f"Graph Edges:      {status['graph_edges_count']}")

    if status.get("recent_understand_runs"):
        print(f"\n{colorize('Recent Understand Runs:', BOLD)}")
        for r in status["recent_understand_runs"]:
            print(f"  - [{r['created_at'][:19]}] {r['primary_category']} ({r['files_changed_count']} files, {r['grounding_ratio']*100:.0f}% grounded)")

    if status.get("recent_viva_sessions"):
        print(f"\n{colorize('Recent Viva Sessions:', BOLD)}")
        for s in status["recent_viva_sessions"]:
            print(f"  - [{s['started_at'][:19]}] {s['session_id']} ({s['status']}, {s['mode']}, {s['current_difficulty']})")


def interactive_understand(root: Path, db: Database, project_id: str) -> None:
    """Runs interactive Workflow 1: Understand What Changed."""
    print(f"\n{colorize('=== STEP 1: INSPECTING REPOSITORY CHANGES ===', BOLD)}")
    preview = run_understand_preview(root, db, project_id)

    if preview["clean_working_tree"]:
        print(f"{colorize('Working tree is clean.', GREEN)} No modified files detected.")
        return

    print(f"Total files changed: {colorize(str(preview['total_files_changed']), BOLD)}")
    for f in preview["changed_files"]:
        print(f"  - {f}")
    print(f"Token estimate: {preview['token_estimate']}")

    # Consent Gate
    redactions = preview.get("redaction_summary", {})
    if redactions.get("redacted_count", 0) > 0:
        print(f"{colorize('Redacted secrets:', YELLOW)} {redactions['redacted_count']} sensitive patterns removed.")

    proceed = input(f"\n{colorize('Proceed with AI explanation? [y/N]: ', BOLD)}").strip().lower()
    if proceed not in ("y", "yes"):
        print("Explanation aborted by user.")
        return

    print(f"\n{colorize('=== STEP 2: GENERATING EXPLANATION ===', BOLD)}")
    result = run_understand_explain(
        root=root,
        db=db,
        project_id=project_id,
        consent_token_str="{}",
    )

    # Display What Changed
    wc = result.get("what_changed", {})
    print(f"\n{colorize('1. WHAT CHANGED:', BOLD)} {wc.get('primary_category', 'UNKNOWN')}")
    print(wc.get("summary", "No summary available."))
    for fc in wc.get("files_changed", []):
        print(f"  - {fc.get('file_path')} [{fc.get('change_type')}] (+{fc.get('additions', 0)} / -{fc.get('deletions', 0)})")

    # Display Why
    why = result.get("why", {})
    pi = why.get("primary_intent", {})
    print(f"\n{colorize('2. WHY:', BOLD)} [{pi.get('status', 'UNKNOWN')}]")
    print(pi.get("summary", "No rationale documented in codebase."))

    # Display What Should I Understand
    concepts = result.get("what_should_i_understand", [])
    if concepts:
        print(f"\n{colorize('3. CONCEPTS TO UNDERSTAND:', BOLD)}")
        for c in concepts:
            print(f"  * {colorize(c.get('concept_name', 'Concept'), BOLD)}: {c.get('why_it_matters')}")

    # Display Can I Explain This? Prompt
    can_explain = result.get("can_i_explain_this", {})
    question = can_explain.get("question")
    prompt_id = can_explain.get("prompt_id")
    packet_id = result.get("packet_id")

    if not question or not prompt_id:
        print("\nComprehension prompt is unavailable.")
        return

    print(f"\n{colorize('=== STEP 3: CAN I EXPLAIN THIS? ===', BOLD)}")
    print(colorize(question, CYAN))
    print(f"{DIM}Expected aspects: Purpose, Mechanism, Failure Modes, Downstream Impact{RESET}")

    answer = prompt_multiline_answer("Enter your explanation in your own words (or press Enter on empty line to skip):")
    if not answer:
        print("Skipped comprehension evaluation.")
        return

    print(f"\n{colorize('=== STEP 4: EVALUATING COMPREHENSION ===', BOLD)}")
    eval_res = run_understand_submit(
        root=root,
        db=db,
        project_id=project_id,
        prompt_id=prompt_id,
        packet_id=packet_id,
        answer_text=answer,
    )

    overall = eval_res.get("overall_state", "UNKNOWN")
    color = GREEN if overall == "UNDERSTOOD" else (YELLOW if overall == "PARTIALLY_UNDERSTOOD" else RED)
    print(f"\nOverall Comprehension: {colorize(overall, color)}")

    dims = eval_res.get("dimensions", {})
    for dim_name, d in dims.items():
        r = d.get("rating", "UNKNOWN")
        d_color = GREEN if r == "UNDERSTOOD" else (YELLOW if r == "PARTIALLY_UNDERSTOOD" else RED)
        print(f"  * {dim_name:<20}: {colorize(r, d_color)}")
        if d.get("feedback"):
            print(f"    {d['feedback']}")

    # Display teaching takeaways if present
    teaching = eval_res.get("targeted_teaching", {})
    takeaways = teaching.get("key_takeaways", [])
    if takeaways:
        print(f"\n{colorize('Targeted Teaching Takeaways:', BOLD)}")
        for t in takeaways:
            print(f"  - {t}")


def interactive_viva(root: Path, db: Database, project_id: str) -> None:
    """Runs interactive Workflow 2: Viva Defence."""
    print(f"\n{colorize('=== VIVA DEFENCE ENGINE ===', BOLD)}")
    print("Choose Viva Mode:")
    print("  [1] Project-wide Defence (all categories)")
    print("  [2] Category Focus Mode")
    mode_choice = input(colorize("Selection [1/2]: ", BOLD)).strip()
    mode_str = "category-focus" if mode_choice == "2" else "project-wide"

    cat_str = None
    if mode_str == "category-focus":
        cat_input = input("Enter target category (e.g. AUTHENTICATION_SESSION, ARCHITECTURE_OVERVIEW): ").strip()
        cat_str = cat_input if cat_input else None

    print("\nStarting Difficulty:")
    print("  [1] EASY\n  [2] MEDIUM\n  [3] HARD\n  [4] DEEP")
    diff_choice = input(colorize("Selection [1-4] (default 1): ", BOLD)).strip()
    diff_map = {"1": "easy", "2": "medium", "3": "hard", "4": "deep"}
    difficulty_str = diff_map.get(diff_choice, "easy")

    print(f"\n{colorize('Initializing session...', DIM)}")
    start_res = run_viva_start(
        root=root,
        db=db,
        project_id=project_id,
        mode_str=mode_str,
        category_str=cat_str,
        difficulty_str=difficulty_str,
    )

    session = start_res["session"]
    session_id = session["session_id"]
    current_q = start_res.get("first_question")

    while current_q is not None:
        turn_idx = current_q.get("turn_index", 0)
        category = current_q.get("category", "GENERAL")
        diff = current_q.get("difficulty", "EASY")
        q_text = current_q.get("question_text", "")
        is_follow_up = current_q.get("is_follow_up", False)

        tag = "[FOLLOW-UP QUESTION]" if is_follow_up else f"[QUESTION {turn_idx + 1}]"
        print(f"\n{colorize(f'=== {tag} ({category} | {diff}) ===', CYAN)}")
        print(f"{colorize(q_text, BOLD)}")

        target_modules = current_q.get("target_modules", [])
        if target_modules:
            print(f"{DIM}Target modules: {', '.join(target_modules)}{RESET}")

        answer = prompt_multiline_answer("Enter your defence answer (or empty line to conclude session):")
        if not answer:
            print("Session concluded by student.")
            break

        print(f"\n{colorize('Evaluating response against project evidence...', DIM)}")
        step_res = run_viva_submit(
            root=root,
            db=db,
            project_id=project_id,
            session_id=session_id,
            answer_text=answer,
        )

        turn_eval = step_res.get("turn_evaluation", {})
        rating = turn_eval.get("rating", "UNKNOWN")
        r_color = GREEN if rating in ("STRONG", "ADEQUATE") else (YELLOW if rating == "PARTIAL" else RED)
        print(f"\nTurn Rating: {colorize(rating, r_color)}")

        feedback = turn_eval.get("feedback")
        if feedback:
            print(f"Feedback: {feedback}")

        gaps = turn_eval.get("identified_gaps", [])
        if gaps:
            print(f"{colorize('Knowledge Gaps Identified:', YELLOW)}")
            for g in gaps:
                print(f"  - [{g.get('severity')}] {g.get('expected_understanding')}")

        session = step_res.get("session", session)
        current_q = step_res.get("next_question")

    # Final Report
    print(f"\n{colorize('=== GENERATING FINAL VIVA DEFENCE REPORT ===', BOLD)}")
    report = run_viva_report(root, db, project_id, session_id)
    readiness = report.get("readiness", "INCOMPLETE")
    read_color = GREEN if readiness == "DEFENCE_READY" else (YELLOW if readiness == "NEEDS_PREPARATION" else RED)
    print(f"\nOverall Readiness: {colorize(readiness, read_color)}")
    print(report.get("readiness_rationale", ""))

    cat_mastery = report.get("category_mastery", {})
    if cat_mastery:
        print(f"\n{colorize('Category Mastery Breakdown:', BOLD)}")
        for cat, mastery in cat_mastery.items():
            lvl = mastery.get("level", "NOT_EVALUATED")
            m_color = GREEN if lvl in ("STRONG", "ADEQUATE") else (YELLOW if lvl == "PARTIAL" else RED)
            print(f"  * {cat:<28}: {colorize(lvl, m_color)}")


def main_menu(root: Path, db: Database, project_id: str) -> None:
    """Top-level interactive menu loop."""
    git_state = run_status(root, db, project_id)
    while True:
        display_banner(
            project_name=root.name,
            branch=git_state["current_branch"],
            dirty_count=git_state["modified_files_count"] + git_state["untracked_files_count"],
        )
        print("What do you want to do?")
        print(f"  {colorize('[1]', BOLD)} Understand what changed")
        print(f"  {colorize('[2]', BOLD)} Prepare for viva")
        print(f"  {colorize('[3]', BOLD)} Project graph & status overview")
        print(f"  {colorize('[4]', BOLD)} Trigger project scan")
        print(f"  {colorize('[q]', BOLD)} Quit")

        choice = input(f"\n{colorize('Selection [1/2/3/4/q]: ', BOLD)}").strip().lower()
        if choice == "1":
            interactive_understand(root, db, project_id)
        elif choice == "2":
            interactive_viva(root, db, project_id)
        elif choice == "3":
            interactive_status(root, db, project_id)
        elif choice == "4":
            print(f"\n{colorize('Scanning project...', DIM)}")
            scan_res = run_scan(root, db, project_id)
            print(f"{colorize('Scan completed.', GREEN)} Indexed {scan_res['scanned_files_count']} files, {scan_res['graph_nodes_count']} graph nodes.")
        elif choice in ("q", "quit", "exit"):
            print("Goodbye.")
            break
        else:
            print("Invalid selection.")
