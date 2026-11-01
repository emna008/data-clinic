"""Pipeline complet sans interface, pour tester les quatre agents de bout en bout.
Approuve automatiquement les corrections marquées auto_safe.
Usage : python scripts/run_pipeline.py [chemin_du_csv]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clinic import engine, prompts  # noqa: E402
from clinic.config import SAMPLE_FILE, load_agents, load_slack, namespace_for  # noqa: E402

SLACK = load_slack()


def log(ev: dict) -> None:
    print(f"  [{ev['agent']}] {ev['text']}")


def main(path: Path) -> None:
    agents = load_agents()
    raw, name, ns = path.read_bytes(), path.name, namespace_for(path.name)
    print(f"Fichier {name} · famille mémoire {ns}")
    before = engine.run_sync(engine.read_memory(ns))
    has_contract = any(f["path"].endswith("data_contract.md") for f in before)
    print(f"Mémoire existante : {[f['path'] for f in before]}")

    print("\n== Profiler")
    prof, profile = engine.run_sync(engine.run_agent_json(
        agents["profiler"], prompts.profiler_message(), prompts.PROFILE_SCHEMA,
        file_bytes=raw, filename=name, on_event=log))
    print(f"rows={profile.get('rows')} dup={profile.get('exact_duplicate_rows')} coût={prof.cost:.4f}")

    print("\n== Auditor")
    aud, issues = engine.run_sync(engine.run_agent_json(
        agents["auditor"], prompts.auditor_message(ns, profile, has_contract), prompts.ISSUES_SCHEMA,
        file_bytes=raw, filename=name, namespace=ns, on_event=log))
    for i in issues:
        print(f"  {i['id']} {i['severity']:<6} safe={i.get('auto_safe')} contract={i.get('contract_violation')} {i['description'][:90]}")
    print(f"coût={aud.cost:.4f}")

    violated = [i for i in issues if i.get("contract_violation")]
    if violated and SLACK.enabled:
        print(f"\n== Alerte Slack ({len(violated)} violations) dans {SLACK.channel}")
        alert = engine.run_sync(engine.run_agent(
            agents["reporter"], prompts.slack_alert_message(ns, name, violated, SLACK.channel),
            connections=[SLACK.connection], max_steps=4, on_event=log))
        print(f"réponse={alert.text.strip()[:200]} coût={alert.cost:.4f}")

    approved = [i for i in issues if i.get("auto_safe")]
    print(f"\n== Remediator ({len(approved)} corrections auto-approuvées)")
    rem = engine.run_sync(engine.run_agent(
        agents["remediator"], prompts.remediator_message(approved, name), file_bytes=raw, filename=name, on_event=log))
    while rem.questions:
        q = rem.questions[0]
        print(f"  QUESTION: {q['prompt']} {q['options']}")
        answer = q["options"][0] if q["options"] else "Use your best judgement and continue."
        print(f"  -> réponse automatique : {answer}")
        rem = engine.run_sync(engine.answer_agent(agents["remediator"], rem.thread_id, str(answer), on_event=log))
    paths = engine.save_outputs(rem.files)
    print(f"fichiers={[p.name for p in paths]} coût={rem.cost:.4f}")
    print(rem.text[-700:])

    print("\n== Reporter")
    change_log = next((f["bytes"].decode("utf-8", "replace") for f in rem.files if f["name"].endswith(".md")), "")
    rep = engine.run_sync(engine.run_agent(
        agents["reporter"],
        prompts.reporter_message(ns, name, profile, issues, [i["id"] for i in approved], rem.text, change_log,
                                 slack_channel=SLACK.channel if SLACK.enabled else None),
        extra_files=rem.files, namespace=ns,
        connections=[SLACK.connection] if SLACK.enabled else None, on_event=log))
    engine.save_outputs(rep.files)
    print(f"fichiers={[f['name'] for f in rep.files]} coût={rep.cost:.4f}")
    print(rep.text[:800])

    mem = engine.run_sync(engine.read_memory(ns))
    print(f"\n== Mémoire après : {[f['path'] for f in mem]}")
    for f in mem:
        print(f"--- {f['path']} ({f['purpose']})\n{f['content'][:1500]}")
    print(f"\nCoût total : {prof.cost + aud.cost + rem.cost + rep.cost:.4f} €")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else SAMPLE_FILE)
