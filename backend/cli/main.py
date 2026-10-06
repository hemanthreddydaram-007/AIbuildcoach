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
    run_bridge_start,
    run_bridge_status,
    run_project_list,
    run_project_register,
    run_project_status,
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
    analyze_parser.add_argument("--project-id", type=str, default=None, help="Project ID to analyze (defaults to bound project)")

    # conversation evidence (M12.4: conversation evidence <conversation_id> or --conversation-id)
    evidence_parser = conv_subparsers.add_parser("evidence", parents=[json_parent], help="Analyze project-grounded evidence for a bound conversation")
    evidence_parser.add_argument("pos_conversation_id", nargs="?", default=None, help="Conversation ID to analyze")
    evidence_parser.add_argument("--conversation-id", type=str, default=None, help="Conversation ID to analyze")
    evidence_parser.add_argument("--project-id", type=str, default=None, help="Project ID to analyze (defaults to bound project)")

    # conversation verify
    verify_parser = conv_subparsers.add_parser("verify", parents=[json_parent], help="Verify a conversation claim using AI Gateway")
    verify_parser.add_argument("--conversation-id", type=str, required=True, help="Conversation ID")
    verify_parser.add_argument("--claim-id", type=str, required=True, help="Claim ID to verify")
    verify_parser.add_argument("--project-id", type=str, default=None, help="Project ID (defaults to current project)")
    verify_parser.add_argument("--consent", action="store_true", default=False, help="Explicit consent for AI Gateway processing")

    # 6. bridge
    bridge_parser = subparsers.add_parser("bridge", parents=[json_parent], help="M12.2: Local HTTP bridge for browser extension")
    bridge_subparsers = bridge_parser.add_subparsers(dest="bridge_action", help="Bridge actions: start, status")

    # bridge start
    bridge_start_parser = bridge_subparsers.add_parser("start", parents=[json_parent], help="Start the local bridge server")
    bridge_start_parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (must be 127.0.0.1)")
    bridge_start_parser.add_argument("--port", type=int, default=8765, help="Port to listen on (default 8765)")

    # bridge status
    bridge_status_parser = bridge_subparsers.add_parser("status", parents=[json_parent], help="Check if local bridge is running")
    bridge_status_parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default 127.0.0.1)")
    bridge_status_parser.add_argument("--port", type=int, default=8765, help="Port to check (default 8765)")

    # 7. project
    project_parser = subparsers.add_parser("project", parents=[json_parent], help="M12.3: Project registry and binding commands")
    project_subparsers = project_parser.add_subparsers(dest="project_action", help="Project actions: list, register, status")

    # project list
    project_subparsers.add_parser("list", parents=[json_parent], help="List registered local projects")

    # project register <path>
    register_parser = project_subparsers.add_parser("register", parents=[json_parent], help="Register a local project root")
    register_parser.add_argument("path", type=str, help="Local directory path of the project")

    # project status <project_id>
    proj_status_parser = project_subparsers.add_parser("status", parents=[json_parent], help="Check status of a registered project")
    proj_status_parser.add_argument("target_project_id", type=str, help="Stable project ID")

    # 8. observation (M12.5)
    obs_parser = subparsers.add_parser("observation", parents=[json_parent], help="M12.5: Runtime Failure & Change Observation")
    obs_subparsers = obs_parser.add_subparsers(dest="observation_action", help="Observation actions: timeline, record, explanation")

    # observation timeline <project_id>
    obs_tl_parser = obs_subparsers.add_parser("timeline", parents=[json_parent], help="Retrieve chronological observation timeline")
    obs_tl_parser.add_argument("target_project_id", nargs="?", default=None, help="Target project ID")
    obs_tl_parser.add_argument("--project-id", type=str, default=None, help="Target project ID")

    # observation record <project_id>
    obs_rec_parser = obs_subparsers.add_parser("record", parents=[json_parent], help="Record an observation event")
    obs_rec_parser.add_argument("target_project_id", nargs="?", default=None, help="Target project ID")
    obs_rec_parser.add_argument("--project-id", type=str, default=None, help="Target project ID")
    obs_rec_parser.add_argument("--event-type", type=str, required=True, help="Observation event type")
    obs_rec_parser.add_argument("--source", type=str, default="TERMINAL", help="Observation source (TERMINAL, GIT, PYTEST, HTTP)")
    obs_rec_parser.add_argument("--payload", type=str, default="{}", help="JSON string of event payload")

    # observation explanation <project_id>
    obs_exp_parser = obs_subparsers.add_parser("explanation", parents=[json_parent], help="Construct deterministic explanation packet")
    obs_exp_parser.add_argument("target_project_id", nargs="?", default=None, help="Target project ID")
    obs_exp_parser.add_argument("--project-id", type=str, default=None, help="Target project ID")

    # observation explain [incident_id] [--project-id ID] [--consent] [--api-key KEY] (M12.6)
    obs_expl_parser = obs_subparsers.add_parser("explain", parents=[json_parent], help="M12.6: Explain timeline incident")
    obs_expl_parser.add_argument("incident_id", nargs="?", default=None, help="Incident event ID or error signature to explain")
    obs_expl_parser.add_argument("--incident-id", dest="opt_incident_id", type=str, default=None, help="Incident ID")
    obs_expl_parser.add_argument("target_project_id", nargs="?", default=None, help="Target project ID")
    obs_expl_parser.add_argument("--project-id", type=str, default=None, help="Target project ID")
    obs_expl_parser.add_argument("--consent", action="store_true", default=False, help="Grant explicit consent for AI Gateway explanation")
    obs_expl_parser.add_argument("--api-key", type=str, default=None, help="Optional explicit API key for provider")

    # 9. guidance (M12.7)
    guidance_parser = subparsers.add_parser("guidance", parents=[json_parent], help="M12.7: Knowledge Gap & Next Action Engine")
    guidance_subparsers = guidance_parser.add_subparsers(dest="guidance_action", help="Guidance actions: show, incident")

    # guidance show <project_id> [--incident-id ID]
    guide_show_parser = guidance_subparsers.add_parser("show", parents=[json_parent], help="Show prioritized next actions and gaps for a project")
    guide_show_parser.add_argument("target_project_id", nargs="?", default=None, help="Target project ID")
    guide_show_parser.add_argument("--project-id", type=str, default=None, help="Target project ID")
    guide_show_parser.add_argument("--incident-id", type=str, default=None, help="Optional incident ID focus")

    # guidance incident <incident_id> [--project-id ID]
    guide_inc_parser = guidance_subparsers.add_parser("incident", parents=[json_parent], help="Show guidance focused on a specific incident")
    guide_inc_parser.add_argument("incident_id", type=str, help="Incident ID")
    guide_inc_parser.add_argument("target_project_id", nargs="?", default=None, help="Target project ID")
    guide_inc_parser.add_argument("--project-id", type=str, default=None, help="Target project ID")

    # 10. session (M12.8)
    session_parser = subparsers.add_parser("session", parents=[json_parent], help="M12.8: Unified Build Coach Session")
    session_subparsers = session_parser.add_subparsers(dest="session_action", help="Session actions: show")

    # session show <project_id>
    sess_show_parser = session_subparsers.add_parser("show", parents=[json_parent], help="Show unified project Build Coach session")
    sess_show_parser.add_argument("target_project_id", nargs="?", default=None, help="Target project ID")
    sess_show_parser.add_argument("--project-id", type=str, default=None, help="Target project ID")

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

        if action in ("analyze", "evidence"):
            pos_id = getattr(args, "pos_conversation_id", None)
            flag_id = getattr(args, "conversation_id", None)
            conversation_id = pos_id or flag_id
            project_id = getattr(args, "project_id", None)
            if not conversation_id:
                if getattr(args, "json", False):
                    emit_json_error("conversation", action, "MISSING_ARGUMENT", "conversation_id is required.")
                else:
                    sys.stderr.write("Error: conversation_id is required.\n")
                return 2

            try:
                res = run_conversation_analyze(
                    db=db,
                    conversation_id=conversation_id,
                    project_id=project_id,
                )
                if getattr(args, "json", False):
                    emit_json_response("conversation", action, res)
                else:
                    print(f"Analyzed conversation {conversation_id}: {res['summary']['total_claims']} claims, {res['summary']['total_links']} links.")
                return 0
            except Exception as exc:
                err_msg = str(exc)
                code = "PROJECT_BINDING_REQUIRED" if "PROJECT_BINDING_REQUIRED" in err_msg else "CONVERSATION_ANALYZE_ERROR"
                if getattr(args, "json", False):
                    emit_json_error("conversation", action, code, err_msg)
                else:
                    sys.stderr.write(f"Conversation {action} error: {exc}\n")
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

    # 6. bridge
    elif args.command == "bridge":
        action = getattr(args, "bridge_action", None)
        host = getattr(args, "host", "127.0.0.1")
        port = getattr(args, "port", 8765)

        if action == "start":
            if getattr(args, "json", False):
                emit_json_response("bridge", "start", {"running": True, "host": host, "port": port, "protocol": "buildcoach-bridge-v1"})
            try:
                run_bridge_start(
                    db=db,
                    project_id=project.id,
                    host=host,
                    port=port,
                    blocking=not getattr(args, "json", False),
                )
                return 0
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("bridge", "start", "BRIDGE_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Bridge start error: {exc}\n")
                return 2

        elif action == "status":
            try:
                status_data = run_bridge_status(host=host, port=port)
                if getattr(args, "json", False):
                    emit_json_response("bridge", "status", status_data)
                else:
                    running_str = "RUNNING" if status_data.get("running") else "STOPPED"
                    print(f"Bridge {status_data['host']}:{status_data['port']} is {running_str} (protocol: {status_data.get('protocol')})")
                return 0 if status_data.get("running") else 1
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("bridge", "status", "STATUS_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Bridge status error: {exc}\n")
                return 2
        else:
            if getattr(args, "json", False):
                emit_json_error("bridge", "none", "INVALID_ARGUMENTS", "Action required: start or status.")
            else:
                sys.stderr.write("Error: Subcommand required for 'bridge': start, status.\n")
            return 1

    # 7. project
    elif args.command == "project":
        action = getattr(args, "project_action", None)

        if action == "list":
            try:
                res = run_project_list(db=db)
                if getattr(args, "json", False):
                    emit_json_response("project", "list", res)
                else:
                    if not res["projects"]:
                        print("No projects registered.")
                    else:
                        print(f"Registered Projects ({res['total_projects']}):")
                        for p in res["projects"]:
                            print(f"  [{p['project_id']}] {p['display_name']} -> {p['root_path']}")
                return 0
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("project", "list", "PROJECT_LIST_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Project list error: {exc}\n")
                return 2

        elif action == "register":
            target_path = getattr(args, "path", None)
            if not target_path:
                if getattr(args, "json", False):
                    emit_json_error("project", "register", "INVALID_ARGUMENTS", "Path required for project register.")
                else:
                    sys.stderr.write("Error: Path required for 'project register'.\n")
                return 1

            try:
                res = run_project_register(db=db, target_path_str=target_path)
                if getattr(args, "json", False):
                    emit_json_response("project", "register", res)
                else:
                    print(f"Project '{res['display_name']}' [{res['project_id']}] successfully registered ({res['status']}).")
                return 0
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("project", "register", "PROJECT_REGISTER_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Project register error: {exc}\n")
                return 2

        elif action == "status":
            target_proj_id = getattr(args, "target_project_id", None)
            if not target_proj_id:
                if getattr(args, "json", False):
                    emit_json_error("project", "status", "INVALID_ARGUMENTS", "project_id required for project status.")
                else:
                    sys.stderr.write("Error: project_id required for 'project status'.\n")
                return 1

            try:
                res = run_project_status(db=db, project_id=target_proj_id)
                if getattr(args, "json", False):
                    emit_json_response("project", "status", res)
                else:
                    print(f"Project [{res['project_id']}]: {res['display_name']}")
                    print(f"  Root: {res['root_path']}")
                    print(f"  Bound conversations: {res['bound_conversations_count']}")
                return 0
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("project", "status", "PROJECT_STATUS_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Project status error: {exc}\n")
                return 2

        else:
            if getattr(args, "json", False):
                emit_json_error("project", "none", "INVALID_ARGUMENTS", "Action required: list, register, or status.")
            else:
                sys.stderr.write("Error: Subcommand required for 'project': list, register, status.\n")
            return 1

    # 8. observation (M12.5)
    elif args.command == "observation":
        action = getattr(args, "observation_action", None)
        target_proj_id = getattr(args, "target_project_id", None) or getattr(args, "project_id", None) or (project.id if project else None)

        if not action:
            if getattr(args, "json", False):
                emit_json_error("observation", "none", "INVALID_ARGUMENTS", "Action required: timeline, record, or explanation.")
            else:
                sys.stderr.write("Error: Subcommand required for 'observation': timeline, record, explanation.\n")
            return 1

        if not target_proj_id:
            if getattr(args, "json", False):
                emit_json_error("observation", action, "INVALID_ARGUMENTS", "project_id is required.")
            else:
                sys.stderr.write("Error: project_id is required for observation commands.\n")
            return 1

        if action == "timeline":
            try:
                from backend.cli.runner import run_observation_timeline
                res = run_observation_timeline(db=db, project_id=target_proj_id)
                if getattr(args, "json", False):
                    emit_json_response("observation", "timeline", res)
                else:
                    print(f"Observation Timeline for [{target_proj_id}] ({res['total_events']} events):")
                    for e in res["events"]:
                        print(f"  [{e['timestamp']}] {e['event_type']} ({e['source']})")
                return 0
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("observation", "timeline", "OBSERVATION_TIMELINE_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Observation timeline error: {exc}\n")
                return 2

        elif action == "record":
            event_type = getattr(args, "event_type", None)
            source = getattr(args, "source", "TERMINAL")
            payload_str = getattr(args, "payload", "{}")
            try:
                from backend.cli.runner import run_observation_record
                res = run_observation_record(
                    db=db,
                    project_id=target_proj_id,
                    event_type=event_type,
                    source=source,
                    payload_str=payload_str,
                )
                if getattr(args, "json", False):
                    emit_json_response("observation", "record", res)
                else:
                    print(f"Recorded {res['event']['event_type']} event [{res['event']['event_id']}].")
                return 0
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("observation", "record", "OBSERVATION_RECORD_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Observation record error: {exc}\n")
                return 2

        elif action == "explanation":
            try:
                from backend.cli.runner import run_observation_explanation
                res = run_observation_explanation(db=db, project_id=target_proj_id)
                if getattr(args, "json", False):
                    emit_json_response("observation", "explanation", res)
                else:
                    expl = res["explanation"]
                    print(f"Explanation for [{target_proj_id}]:")
                    print(f"  Problem: {expl.get('problem', {}).get('error_kind', 'None')}")
                    print(f"  Recovery: {expl.get('recovery', {}).get('status', 'None')}")
                return 0
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("observation", "explanation", "OBSERVATION_EXPLANATION_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Observation explanation error: {exc}\n")
                return 2

        elif action == "explain":
            inc_id = getattr(args, "opt_incident_id", None) or getattr(args, "incident_id", None)
            has_consent = getattr(args, "consent", False)
            api_key = getattr(args, "api_key", None)
            try:
                from backend.cli.runner import run_observation_explain
                res = run_observation_explain(
                    db=db,
                    project_id=target_proj_id,
                    incident_id=inc_id,
                    has_consent=has_consent,
                    explicit_api_key=api_key,
                )
                if getattr(args, "json", False):
                    emit_json_response("observation", "explain", res)
                else:
                    expl = res["explanation"]
                    print(f"\nBUILD TIMELINE EXPLANATION [{expl['fix_status']}] (Confidence: {expl['confidence']})")
                    print(f"Summary: {expl['summary']}\n")
                    if expl.get("problem"):
                        print("WHAT HAPPENED (PROBLEM):")
                        for p in expl["problem"]:
                            refs = f" [{', '.join(p['evidence_refs'])}]" if p.get("evidence_refs") else ""
                            print(f"  - {p['statement']}{refs}")
                    if expl.get("changes"):
                        print("\nWHAT CHANGED:")
                        for c in expl["changes"]:
                            refs = f" [{', '.join(c['evidence_refs'])}]" if c.get("evidence_refs") else ""
                            print(f"  - {c['statement']}{refs}")
                    if expl.get("verification"):
                        print("\nHOW WE KNOW / VERIFICATION:")
                        for v in expl["verification"]:
                            refs = f" [{', '.join(v['evidence_refs'])}]" if v.get("evidence_refs") else ""
                            print(f"  - {v['statement']}{refs}")
                    if expl.get("what_to_understand"):
                        print("\nWHAT YOU SHOULD UNDERSTAND:")
                        for u in expl["what_to_understand"]:
                            print(f"  - {u}")
                    if expl.get("unknowns"):
                        print("\nWHAT REMAINS UNKNOWN:")
                        for unk in expl["unknowns"]:
                            print(f"  - {unk}")
                return 0
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("observation", "explain", "OBSERVATION_EXPLAIN_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Observation explain error: {exc}\n")
                return 2

    # 9. guidance commands (M12.7)
    elif command == "guidance":
        action = getattr(args, "guidance_action", None)
        target_proj_id = getattr(args, "target_project_id", None) or getattr(args, "project_id", None)

        if not target_proj_id:
            # Fall back to active project from working tree
            try:
                active_proj = db.get_active_project()
                if active_proj:
                    target_proj_id = active_proj.id
            except Exception:
                pass

        if not target_proj_id:
            if getattr(args, "json", False):
                emit_json_error("guidance", action or "unknown", "MISSING_PROJECT_ID", "Target project ID is required.")
            else:
                sys.stderr.write("Error: Target project ID is required. Pass <project_id> or register project.\n")
            return 2

        if action in ("show", "incident"):
            inc_id = getattr(args, "incident_id", None)
            try:
                from backend.cli.runner import run_guidance_show
                res = run_guidance_show(db, project_id=target_proj_id, incident_id=inc_id)

                if getattr(args, "json", False):
                    emit_json_response("guidance", action, res)
                else:
                    print(f"\n========================================================")
                    print(f"BUILD COACH GUIDANCE — PROJECT: {res['project_id']}")
                    print(f"Status: {res['status']} | Generated: {res['generated_at']}")
                    print(f"========================================================\n")

                    top = res.get("top_next_action")
                    if top:
                        print("TOP NEXT ACTION:")
                        print(f"  [{top['priority']}] {top['title']} ({top['action_type']})")
                        print(f"  Description: {top['description']}")
                        print(f"  Completion Condition: {top['completion_condition']}")
                        if top.get("evidence_ids"):
                            print(f"  Grounded Evidence: {', '.join(top['evidence_ids'])}")
                    else:
                        print("TOP NEXT ACTION: None (All active verification and knowledge gaps satisfied).")

                    sec = res.get("secondary_actions", [])
                    if sec:
                        print("\nSECONDARY ACTIONS:")
                        for s in sec:
                            print(f"  - [{s['priority']}] {s['title']} ({s['action_type']})")

                    gaps = res.get("gaps", [])
                    if gaps:
                        print("\nIDENTIFIED KNOWLEDGE & VERIFICATION GAPS:")
                        for g in gaps:
                            refs = f" [{', '.join(g['evidence_ids'])}]" if g.get("evidence_ids") else ""
                            print(f"  - [{g['priority']}] {g['category']}: {g['description']}{refs}")
                            print(f"    Why: {g['reason']}")

                    print()
                return 0
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("guidance", action, "GUIDANCE_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Guidance error: {exc}\n")
                return 2

    # 10. session (M12.8)
    if command == "session":
        action = getattr(args, "session_action", None)
        target_proj_id = getattr(args, "target_project_id", None) or getattr(args, "project_id", None)
        if not target_proj_id:
            root = resolve_project_root(args)
            if root:
                proj = db.get_project_by_root(str(root))
                if proj:
                    target_proj_id = proj.id

        if not target_proj_id:
            if getattr(args, "json", False):
                emit_json_error("session", action or "unknown", "MISSING_PROJECT_ID", "Target project ID is required.")
            else:
                sys.stderr.write("Error: Target project ID is required. Pass <project_id> or register project.\n")
            return 2

        if action == "show":
            try:
                from backend.cli.runner import run_session_show
                res = run_session_show(db, project_id=target_proj_id)

                if getattr(args, "json", False):
                    emit_json_response("session", action, res["session"])
                else:
                    print(res["human_text"])
                return 0
            except Exception as exc:
                if getattr(args, "json", False):
                    emit_json_error("session", action, "SESSION_ERROR", str(exc))
                else:
                    sys.stderr.write(f"Session error: {exc}\n")
                return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
