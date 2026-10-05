"""Main CLI entrypoint and argument parser for AI Build Coach."""

import sys
import argparse
from typing import Optional, List

from backend.cli.runner import (
    get_cli_version,
    resolve_workspace,
    run_status,
    run_scan,
    run_understand_preview,
    run_understand_explain,
    run_understand_submit,
    run_viva_start,
    run_viva_submit,
    run_viva_report,
    run_conversation_import,
    run_conversation_normalize,
    run_conversation_analyze,
    run_conversation_verify,
)
from backend.cli.json_output import emit_json_response, emit_json_error, log_diagnostic
from backend.cli.interactive import main_menu


def build_parser() -> argparse.ArgumentParser:
    """Constructs the CLI argument parser with parent inheritance for global flags."""
    # Shared parent parser supporting --json and --project-root in any position
    json_parent = argparse.ArgumentParser(add_help=False)
    json_parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Output machine-readable JSON on stdout",
    )
    json_parent.add_argument(
        "--project-root",
        type=str,
        default=argparse.SUPPRESS,
        help="Explicit project root directory override",
    )

    main_parser = argparse.ArgumentParser(
        prog="ai-build-coach",
        description="AI Build Coach: The human-understanding layer around AI-built software.",
        parents=[json_parent],
    )
    main_parser.set_defaults(json=False, project_root=None)
    main_parser.add_argument(
        "--version",
        action="version",
        version=f"ai-build-coach {get_cli_version()}",
    )

    subparsers = main_parser.add_subparsers(dest="command", help="Available commands")

    # 1. status
    subparsers.add_parser("status", parents=[json_parent], help="Show repository, database, and graph status")

    # 2. scan
    subparsers.add_parser("scan", parents=[json_parent], help="Perform passive codebase scan and sync graph")

    # 3. understand
    understand_parser = subparsers.add_parser("understand", parents=[json_parent], help="Workflow 1: Understand What Changed")
    understand_subparsers = understand_parser.add_subparsers(dest="understand_action", help="Understand actions")

    # understand preview
    understand_subparsers.add_parser("preview", parents=[json_parent], help="Generate change explanation preview")

    # understand explain
    explain_parser = understand_subparsers.add_parser("explain", parents=[json_parent], help="Execute change explanation")
    explain_parser.add_argument(
        "--consent-token",
        type=str,
        default="",
        help="Explicit consent token for AI provider transmission",
    )

    # understand submit
    submit_parser = understand_subparsers.add_parser("submit", parents=[json_parent], help="Submit answer to comprehension loop")
    submit_parser.add_argument("--prompt-id", type=str, required=True, help="Prompt ID from explanation")
    submit_parser.add_argument("--packet-id", type=str, required=True, help="Context packet ID")
    submit_parser.add_argument(
        "--answer-stdin",
        action="store_true",
        default=False,
        help="Read student answer from standard input",
    )

    # 4. viva
    viva_parser = subparsers.add_parser("viva", parents=[json_parent], help="Workflow 2: Viva Defence Engine")
    viva_subparsers = viva_parser.add_subparsers(dest="viva_action", help="Viva actions")

    # viva start
    start_parser = viva_subparsers.add_parser("start", parents=[json_parent], help="Start a new viva session")
    start_parser.add_argument("--mode", type=str, default="project-wide", help="project-wide or category-focus")
    start_parser.add_argument("--category", type=str, default=None, help="Target category for focus mode")
    start_parser.add_argument("--difficulty", type=str, default="easy", help="easy, medium, hard, or deep")

    # viva submit
    viva_submit_parser = viva_subparsers.add_parser("submit", parents=[json_parent], help="Submit answer to viva question")
    viva_submit_parser.add_argument("--session-id", type=str, required=True, help="Active viva session ID")
    viva_submit_parser.add_argument(
        "--answer-stdin",
        action="store_true",
        default=False,
        help="Read student answer from standard input",
    )

    # viva report
    report_parser = viva_subparsers.add_parser("report", parents=[json_parent], help="Compile and display final viva report")
    report_parser.add_argument("--session-id", type=str, required=True, help="Viva session ID to report on")

    # 5. conversation
    conv_parser = subparsers.add_parser("conversation", parents=[json_parent], help="Workflow 3: Conversation Bridge Foundation")
    conv_subparsers = conv_parser.add_subparsers(dest="conversation_action", help="Conversation actions")

    # conversation import
    import_parser = conv_subparsers.add_parser("import", parents=[json_parent], help="Import and persist conversation")
    import_parser.add_argument("--provider", type=str, required=True, help="Provider name: chatgpt, claude, gemini, other")
    import_parser.add_argument("--file", type=str, default=None, help="Path to conversation file")
    import_parser.add_argument("--stdin", action="store_true", default=False, help="Read payload from stdin")
    import_parser.add_argument("--consent", action="store_true", default=False, help="Explicit user consent for ingestion")
    import_parser.add_argument("--title", type=str, default=None, help="Optional conversation title")

    # conversation normalize
    norm_parser = conv_subparsers.add_parser("normalize", parents=[json_parent], help="Normalize conversation without persistence")
    norm_parser.add_argument("--provider", type=str, required=True, help="Provider name: chatgpt, claude, gemini, other")
    norm_parser.add_argument("--file", type=str, default=None, help="Path to conversation file")
    norm_parser.add_argument("--stdin", action="store_true", default=False, help="Read payload from stdin")
    norm_parser.add_argument("--consent", action="store_true", default=False, help="Explicit user consent for ingestion")
    norm_parser.add_argument("--title", type=str, default=None, help="Optional conversation title")

    # conversation analyze
    analyze_parser = conv_subparsers.add_parser("analyze", parents=[json_parent], help="Analyze conversation against project evidence")
    analyze_parser.add_argument("--conversation-id", type=str, required=True, help="Conversation ID to analyze")
    analyze_parser.add_argument("--project-id", type=str, default=None, help="Project ID to analyze (defaults to current project)")

    # conversation verify
    verify_parser = conv_subparsers.add_parser("verify", parents=[json_parent], help="Verify a conversation claim using AI Gateway")
    verify_parser.add_argument("--conversation-id", type=str, required=True, help="Conversation ID")
    verify_parser.add_argument("--claim-id", type=str, required=True, help="Claim ID to verify")
    verify_parser.add_argument("--project-id", type=str, default=None, help="Project ID (defaults to current project)")
    verify_parser.add_argument("--consent", action="store_true", default=False, help="Explicit consent for AI Gateway processing")

    return main_parser


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entrypoint."""
    if argv is None:
        argv = sys.argv[1:]

    parser = build_parser()
    args = parser.parse_args(argv)

    # Resolve explicit flags from argv if needed
    project_root_arg = getattr(args, "project_root", None)
    if "--project-root" in argv:
        idx = argv.index("--project-root")
        if idx + 1 < len(argv):
            project_root_arg = argv[idx + 1]

    if "--json" in argv:
        args.json = True

    # Resolve workspace root and database
    try:
        root, db, project = resolve_workspace(project_root_arg)
    except Exception as exc:
        if getattr(args, "json", False):
            emit_json_error("workspace", "resolve", "WORKSPACE_ERROR", str(exc))
        else:
            sys.stderr.write(f"Workspace error: {exc}\n")
        return 1

    # Default command: interactive main menu (unless --json is specified)
    if not args.command:
        if args.json:
            emit_json_error("root", "none", "INVALID_ARGUMENTS", "Subcommand required in JSON mode.")
            return 1
        main_menu(root, db, project.id)
        return 0

    # 1. status
    if args.command == "status":
        try:
            status_data = run_status(root, db, project.id)
            if args.json:
                emit_json_response("status", "view", status_data)
            else:
                from backend.cli.interactive import interactive_status
                interactive_status(root, db, project.id)
            return 0
        except Exception as exc:
            if args.json:
                emit_json_error("status", "view", "STATUS_ERROR", str(exc))
            else:
                sys.stderr.write(f"Status error: {exc}\n")
            return 2

    # 2. scan
    elif args.command == "scan":
        try:
            scan_data = run_scan(root, db, project.id)
            if args.json:
                emit_json_response("scan", "execute", scan_data)
            else:
                print(f"Scan complete. Indexed {scan_data['scanned_files_count']} files, {scan_data['graph_nodes_count']} graph nodes.")
            return 0
        except Exception as exc:
            if args.json:
                emit_json_error("scan", "execute", "SCAN_ERROR", str(exc))
            else:
                sys.stderr.write(f"Scan error: {exc}\n")
            return 2

    # 3. understand
    elif args.command == "understand":
        action = getattr(args, "understand_action", None)
        if not action:
            if args.json:
                emit_json_error("understand", "none", "INVALID_ARGUMENTS", "Action required: preview, explain, or submit.")
            else:
                from backend.cli.interactive import interactive_understand
                interactive_understand(root, db, project.id)
            return 0 if not args.json else 1

        if action == "preview":
            try:
                preview_data = run_understand_preview(root, db, project.id)
                if args.json:
                    emit_json_response("understand", "preview", preview_data)
                else:
                    print(f"Changed files: {preview_data['total_files_changed']}, token estimate: {preview_data['token_estimate']}")
                return 0
            except Exception as exc:
                if args.json:
                    emit_json_error("understand", "preview", "PREVIEW_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Preview error: {exc}\n")
                return 2

        elif action == "explain":
            if not args.consent_token:
                if args.json:
                    emit_json_error("understand", "explain", "CONSENT_REQUIRED", "Explicit --consent-token is required to run explain.")
                else:
                    sys.stderr.write("Error: --consent-token is required to run explain in non-interactive mode.\n")
                return 1
            try:
                explain_data = run_understand_explain(root, db, project.id, args.consent_token)
                if args.json:
                    emit_json_response("understand", "explain", explain_data)
                else:
                    print("Explanation completed.")
                return 0
            except Exception as exc:
                if args.json:
                    emit_json_error("understand", "explain", "EXPLAIN_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Explain error: {exc}\n")
                return 2

        elif action == "submit":
            if not args.answer_stdin:
                if args.json:
                    emit_json_error("understand", "submit", "INVALID_ARGUMENTS", "--answer-stdin is required for headless answer submission.")
                else:
                    sys.stderr.write("Error: --answer-stdin is required to submit answer in non-interactive mode.\n")
                return 1

            answer_text = sys.stdin.read().strip()
            try:
                submit_data = run_understand_submit(
                    root=root,
                    db=db,
                    project_id=project.id,
                    prompt_id=args.prompt_id,
                    packet_id=args.packet_id,
                    answer_text=answer_text,
                )
                if args.json:
                    emit_json_response("understand", "submit", submit_data)
                else:
                    print(f"Overall comprehension: {submit_data.get('overall_state')}")
                return 0
            except Exception as exc:
                if args.json:
                    emit_json_error("understand", "submit", "SUBMIT_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Submit error: {exc}\n")
                return 2

    # 4. viva
    elif args.command == "viva":
        action = getattr(args, "viva_action", None)
        if not action:
            if args.json:
                emit_json_error("viva", "none", "INVALID_ARGUMENTS", "Action required: start, submit, or report.")
            else:
                from backend.cli.interactive import interactive_viva
                interactive_viva(root, db, project.id)
            return 0 if not args.json else 1

        if action == "start":
            try:
                start_data = run_viva_start(
                    root=root,
                    db=db,
                    project_id=project.id,
                    mode_str=args.mode,
                    category_str=args.category,
                    difficulty_str=args.difficulty,
                )
                if args.json:
                    emit_json_response("viva", "start", start_data)
                else:
                    print(f"Viva session {start_data['session']['session_id']} started.")
                return 0
            except Exception as exc:
                if args.json:
                    emit_json_error("viva", "start", "VIVA_START_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Viva start error: {exc}\n")
                return 2

        elif action == "submit":
            if not args.answer_stdin:
                if args.json:
                    emit_json_error("viva", "submit", "INVALID_ARGUMENTS", "--answer-stdin is required for headless answer submission.")
                else:
                    sys.stderr.write("Error: --answer-stdin is required to submit viva answer in non-interactive mode.\n")
                return 1

            answer_text = sys.stdin.read().strip()
            try:
                submit_data = run_viva_submit(
                    root=root,
                    db=db,
                    project_id=project.id,
                    session_id=args.session_id,
                    answer_text=answer_text,
                )
                if args.json:
                    emit_json_response("viva", "submit", submit_data)
                else:
                    print(f"Turn rating: {submit_data['turn_evaluation'].get('rating')}")
                return 0
            except Exception as exc:
                if args.json:
                    emit_json_error("viva", "submit", "VIVA_SUBMIT_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Viva submit error: {exc}\n")
                return 2

        elif action == "report":
            try:
                report_data = run_viva_report(
                    root=root,
                    db=db,
                    project_id=project.id,
                    session_id=args.session_id,
                )
                if args.json:
                    emit_json_response("viva", "report", report_data)
                else:
                    print(f"Readiness: {report_data.get('readiness')}")
                return 0
            except Exception as exc:
                if args.json:
                    emit_json_error("viva", "report", "VIVA_REPORT_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Viva report error: {exc}\n")
                return 2

    elif args.command == "conversation":
        action = getattr(args, "conversation_action", None)
        if not action:
            if getattr(args, "json", False):
                emit_json_error("conversation", "none", "MISSING_ACTION", "No conversation action specified (import, normalize, or analyze).")
            else:
                parser.parse_args(["conversation", "--help"])
            return 2

        if action == "analyze":
            conversation_id = getattr(args, "conversation_id", None)
            project_id = getattr(args, "project_id", None) or (project.id if project else None)
            if not conversation_id:
                if getattr(args, "json", False):
                    emit_json_error("conversation", "analyze", "MISSING_ARGUMENT", "--conversation-id is required.")
                else:
                    sys.stderr.write("Error: --conversation-id is required.\n")
                return 2

            try:
                res = run_conversation_analyze(
                    db=db,
                    conversation_id=conversation_id,
                    project_id=project_id,
                )
                if getattr(args, "json", False):
                    emit_json_response("conversation", "analyze", res)
                else:
                    print(f"Analyzed conversation {conversation_id}: {res['summary']['total_claims']} claims, {res['summary']['total_links']} links.")
                return 0
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("conversation", "analyze", "CONVERSATION_ANALYZE_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Conversation analyze error: {exc}\n")
                return 2

        if action == "verify":
            conversation_id = getattr(args, "conversation_id", None)
            claim_id = getattr(args, "claim_id", None)
            project_id = getattr(args, "project_id", None) or (project.id if project else None)
            has_consent = getattr(args, "consent", False)

            if not conversation_id or not claim_id:
                if getattr(args, "json", False):
                    emit_json_error("conversation", "verify", "MISSING_ARGUMENT", "--conversation-id and --claim-id are required.")
                else:
                    sys.stderr.write("Error: --conversation-id and --claim-id are required.\n")
                return 2

            try:
                res = run_conversation_verify(
                    db=db,
                    conversation_id=conversation_id,
                    claim_id=claim_id,
                    project_id=project_id,
                    has_consent=has_consent,
                )
                if getattr(args, "json", False):
                    emit_json_response("conversation", "verify", res)
                else:
                    verdict = res["verification"]["verdict"]
                    expl = res["verification"]["explanation"]
                    print(f"Verified claim {claim_id}: {verdict} - {expl}")
                return 0
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("conversation", "verify", "CONVERSATION_VERIFY_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Conversation verify error: {exc}\n")
                return 2

        # Read payload from file or stdin
        raw_payload = ""
        if getattr(args, "file", None):
            try:
                with open(args.file, "r", encoding="utf-8", errors="replace") as f:
                    raw_payload = f.read()
            except OSError as err:
                if getattr(args, "json", False):
                    emit_json_error("conversation", action, "FILE_READ_ERROR", f"Cannot read input file: {err}")
                else:
                    sys.stderr.write(f"File error: {err}\n")
                return 2
        elif getattr(args, "stdin", False):
            raw_payload = sys.stdin.read()
        else:
            if getattr(args, "json", False):
                emit_json_error("conversation", action, "MISSING_INPUT", "Must provide either --file or --stdin.")
            else:
                sys.stderr.write("Error: Must provide either --file or --stdin.\n")
            return 2

        has_consent = getattr(args, "consent", False)

        if action == "import":
            root, db, project = resolve_workspace(project_root_arg)
            try:
                res = run_conversation_import(
                    root=root,
                    db=db,
                    provider=args.provider,
                    raw_payload=raw_payload,
                    has_consent=has_consent,
                    project_id=project.id,
                    title=getattr(args, "title", None),
                )
                if getattr(args, "json", False):
                    emit_json_response("conversation", "import", res)
                else:
                    print(f"Imported conversation {res['conversation']['conversation_id']} with {res['total_messages']} messages.")
                return 0
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("conversation", "import", "CONVERSATION_IMPORT_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Conversation import error: {exc}\n")
                return 2

        elif action == "normalize":
            try:
                res = run_conversation_normalize(
                    provider=args.provider,
                    raw_payload=raw_payload,
                    has_consent=has_consent,
                    title=getattr(args, "title", None),
                )
                if getattr(args, "json", False):
                    emit_json_response("conversation", "normalize", res)
                else:
                    print(f"Normalized conversation {res['conversation']['conversation_id']} with {res['total_messages']} messages.")
                return 0
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("conversation", "normalize", "CONVERSATION_NORMALIZE_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Conversation normalize error: {exc}\n")
                return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
