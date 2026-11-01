"""Messages envoyés aux agents.

Les instructions "système" vivent sur la plateforme Cominty (page Agents). Ici on construit le message
utilisateur de chaque appel : il rappelle le format de sortie attendu, ce qui fiabilise le parsing.
"""
from __future__ import annotations

import json
from typing import Any

JSON_RULE = ("FORMAT RULE: your reply must be ONLY the JSON below, in the message body. "
             "No prose, no markdown fences, do not write the JSON to a file.")

PROFILE_SCHEMA = """{
  "rows": int, "columns": int,
  "schema": [{"name": str, "inferred_type": str, "missing_pct": float, "unique": int, "sample": [str]}],
  "numeric_stats": {"<column>": {"min": num, "max": num, "mean": num, "std": num}},
  "detected_formats": {"<column>": [str]},
  "exact_duplicate_rows": int,
  "notes": [str]
}"""

ISSUES_SCHEMA = """[{"id": "DQ-001", "severity": "high|medium|low", "columns": [str], "rows_affected": int,
  "description": str, "proposed_fix": str, "auto_safe": bool, "contract_violation": bool}]"""

REMEDIATION_SUMMARY = '{"rows_before": int, "rows_after": int, "fixes_applied": [str], "fixes_skipped": [str]}'


def profiler_message() -> str:
    return f"Profile the attached file in ONE sandbox execution.\n{JSON_RULE}\nSchema:\n{PROFILE_SCHEMA}"


def auditor_message(namespace: str, profile: Any, has_contract: bool) -> str:
    contract = ("A memory file data_contract.md exists for this dataset family: read it FIRST and mark every "
                "violated rule as an issue with contract_violation=true and severity high."
                if has_contract else
                "No data contract exists yet for this family; do not look for one.")
    return (f"Dataset family: {namespace}. {contract}\n"
            "Audit the attached file. Use the sandbox at most twice to confirm counts. "
            "Every issue must have ONE concrete fix and an honest auto_safe flag "
            "(true only if the fix is deterministic and reversible).\n"
            f"{JSON_RULE}\nSchema:\n{ISSUES_SCHEMA}\n\n"
            f"Profiler JSON profile for context:\n{json.dumps(profile)[:12000]}")


def remediator_message(approved: list[dict[str, Any]], filename: str) -> str:
    compact = [{k: i.get(k) for k in ("id", "columns", "description", "proposed_fix")} for i in approved]
    stem = filename.rsplit(".", 1)[0]
    return (f"Apply ONLY these human-approved fixes to the attached file, in ONE sandbox execution:\n"
            f"{json.dumps(compact, ensure_ascii=False)}\n\n"
            f"Produce and attach two files: {stem}_clean.csv and change_log.md. "
            "If a fix is ambiguous, ask a question with 2-4 options instead of guessing. "
            f"End your reply with this JSON summary: {REMEDIATION_SUMMARY}")


def reporter_message(namespace: str, filename: str, profile: Any, issues: Any,
                     approved_ids: list[str], remediation_text: str, change_log: str,
                     slack_channel: str | None = None) -> str:
    slack_step = (
        f"4) Post a message in the Slack channel {slack_channel} using the Slack integration: "
        "title 'Data Clinic report', the file name, the health score, the number of issues found and fixed, "
        "and the two most important remaining risks in one line each. Plain text, no file attachment.\n"
        if slack_channel else "")
    return (f"Dataset family: {namespace}. Original file: {filename}.\n"
            "1) Write and attach quality_report.md for a non-technical reader, including a line "
            "'Data health score: NN/100'.\n"
            f"2) Create or update the memory file data_contract.md (namespace {namespace}) with the rules "
            "validated by the human-approved fixes, so the Auditor can check the next file of this family.\n"
            "3) Reply with a 3-sentence summary.\n"
            f"{slack_step}\n"
            f"PROFILER PROFILE:\n{json.dumps(profile)[:6000]}\n\n"
            f"AUDITOR ISSUES:\n{json.dumps(issues, ensure_ascii=False)[:6000]}\n\n"
            f"HUMAN-APPROVED FIX IDS: {approved_ids}\n\n"
            f"REMEDIATOR SUMMARY:\n{remediation_text[-3000:]}\n\n"
            f"CHANGE LOG:\n{change_log[:5000]}")


def slack_alert_message(namespace: str, filename: str, violations: list[dict[str, Any]], channel: str) -> str:
    """Alerte envoyée dès que l'Auditor détecte des violations du contrat de données."""
    lines = "\n".join(
        f"- {v.get('id')} [{v.get('severity')}] {', '.join(v.get('columns') or [])}: {v.get('description')}"
        for v in violations)
    return (f"Use the Slack integration to post ONE alert message in the channel {channel}. Do not write memory, "
            "do not create files, do nothing else.\n"
            f"Content: ':rotating_light: Data Clinic alert: {filename} (family {namespace}) violates "
            f"{len(violations)} rule(s) of its data contract.' Then list each violation on its own line, "
            "and end with 'Open Data Clinic to review the proposed fixes.'\n"
            f"Violations:\n{lines}\n\n"
            "Reply with exactly 'POSTED' if the message was sent, otherwise 'FAILED: <reason>'.")


def json_retry_message(schema: str) -> str:
    return (f"Your previous reply did not contain the required JSON. {JSON_RULE}\n"
            f"Reply now with ONLY the JSON, based on the analysis you already did:\n{schema}")
