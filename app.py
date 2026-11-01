"""Data Clinic : une boîte de réception pour datasets, soignée par des agents Cominty.

Flux : dépôt du fichier -> Profiler -> Auditor -> approbation humaine -> Remediator -> Reporter
Les agents tournent sur la plateforme Cominty ; ce fichier est l'interface et l'orchestrateur.
"""
from __future__ import annotations

import io
import re
from datetime import datetime

import pandas as pd
import streamlit as st

from clinic import engine, prompts
from clinic.config import SAMPLE_FILE, load_agents, load_slack, namespace_for

st.set_page_config(page_title="Data Clinic", page_icon="🩺", layout="wide")

# ----------------------------------------------------------------------------- style
st.markdown(
    """
    <style>
      .block-container {padding-top: 1.6rem; max-width: 1180px;}
      .dc-hero h1 {font-size: 2.1rem; margin-bottom: .1rem; letter-spacing: -.02em;}
      .dc-hero p {color: #9FB0C8; margin-top: 0; font-size: 1.05rem;}
      .dc-step {display:flex; gap:.6rem; align-items:center; margin:.4rem 0 .8rem 0;}
      .dc-pill {padding:.18rem .6rem; border-radius:999px; font-size:.78rem; font-weight:600;
                border:1px solid #27354D; color:#9FB0C8; background:#121C2E;}
      .dc-pill.done {border-color:#2DD4BF; color:#2DD4BF;}
      .dc-pill.active {border-color:#E6EDF7; color:#E6EDF7;}
      .dc-feed {font-family: ui-monospace, Menlo, Consolas, monospace; font-size:.8rem; line-height:1.5;
                background:#0E1727; border:1px solid #1E2A40; border-radius:10px; padding:.7rem .9rem;
                max-height: 260px; overflow-y:auto;}
      .dc-feed .ag {color:#2DD4BF; font-weight:700;}
      .dc-feed .tool {color:#F5B14C;}
      .dc-feed .llm {color:#9FB0C8;}
      .dc-feed .ok {color:#7BE495;}
      .dc-score {font-size:3.4rem; font-weight:800; letter-spacing:-.04em; line-height:1;}
      .dc-score small {font-size:1rem; color:#9FB0C8; font-weight:500; margin-left:.3rem;}
      .dc-card {background:#121C2E; border:1px solid #1E2A40; border-radius:12px; padding:1rem 1.1rem;}
      .dc-muted {color:#9FB0C8;}
    </style>
    """,
    unsafe_allow_html=True,
)

SEV_LABEL = {"high": "Élevée", "medium": "Moyenne", "low": "Faible"}

# ----------------------------------------------------------------------------- état
DEFAULTS: dict = {
    "stage": 0,                 # 0 dépôt, 1 diagnostic prêt, 2 corrections appliquées, 3 rapport prêt
    "file_name": None, "file_bytes": None, "namespace": "",
    "profile": None, "issues": None, "approved": None,
    "remediation": None, "pending_questions": None, "remediation_thread": None,
    "report_text": None, "report_file": None, "memory_files": [], "memory_before": [],
    "log": [], "costs": {}, "threads": {}, "slack_alert": None,
}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)

try:
    AGENTS = load_agents()
except RuntimeError as exc:
    st.error(f"Configuration incomplète : {exc}. Copie .env.example en .env et remplis-le.")
    st.stop()
SLACK = load_slack()


def reset_case() -> None:
    for k, v in DEFAULTS.items():
        st.session_state[k] = v if not isinstance(v, (list, dict)) else type(v)()


def record(result: engine.AgentResult, key: str) -> None:
    st.session_state.costs[key] = result.cost
    st.session_state.threads[key] = result.thread_id


def feed_callback(placeholder):
    """Construit le callback qui pousse chaque événement agent dans le flux d'activité en direct."""
    def _cb(ev: dict) -> None:
        st.session_state.log.append({**ev, "at": datetime.now().strftime("%H:%M:%S")})
        placeholder.markdown(render_feed(st.session_state.log[-14:]), unsafe_allow_html=True)
    return _cb


def render_feed(items: list[dict]) -> str:
    rows = []
    for e in items:
        cls = {"tool": "tool", "llm": "llm", "result": "ok"}.get(e["kind"], "llm")
        rows.append(f'<div><span class="dc-muted">{e["at"]}</span> <span class="ag">{e["agent"]}</span> '
                    f'<span class="{cls}">{e["text"]}</span></div>')
    return '<div class="dc-feed">' + ("".join(rows) or '<span class="dc-muted">En attente…</span>') + "</div>"


# ----------------------------------------------------------------------------- barre latérale
with st.sidebar:
    st.markdown("### 🩺 Data Clinic")
    st.caption("Agents Cominty au travail sur vos données")
    st.markdown("**Équipe d'agents**")
    for spec in AGENTS.values():
        mem = " · mémoire" if spec.uses_memory else ""
        st.markdown(f"- **{spec.label}**  \n  <span class='dc-muted'>{spec.role}{mem}</span>", unsafe_allow_html=True)
    st.divider()
    max_steps = st.slider("Budget d'étapes par agent", 3, 15, 8,
                          help="Nombre maximal de tours d'outils (max_steps Cominty). Protège les crédits.")
    total_cost = sum(st.session_state.costs.values())
    st.metric("Coût de ce dossier", f"{total_cost:.4f} €")
    if st.session_state.costs:
        st.caption(" · ".join(f"{k} {v:.4f}" for k, v in st.session_state.costs.items()))
    st.divider()
    st.markdown("**Intégration Slack (MCP)**")
    if SLACK.enabled:
        slack_on = st.toggle(f"Alertes dans {SLACK.channel}", value=True,
                             help="Le Reporter poste une alerte dès qu'un contrat de données est violé, "
                                  "et le rapport final à la fin du dossier.")
    else:
        slack_on = False
        st.caption("Non configurée. Ajoute SLACK_CHANNEL dans .env après avoir connecté Slack sur "
                   "platform.cominty.ai → Intégrations.")
    st.divider()
    st.markdown("**Mémoire des datasets**")
    if st.button("Rafraîchir la liste", width="stretch"):
        st.session_state["namespaces"] = engine.run_sync(engine.list_namespaces())
    for ns in st.session_state.get("namespaces", []):
        st.markdown(f"- `{ns}`")
    if st.session_state.namespace and st.button("Oublier ce dataset", width="stretch",
                                                 help="Supprime le contrat de données appris (pour rejouer la démo)."):
        n = engine.run_sync(engine.forget_namespace(st.session_state.namespace))
        st.toast(f"{n} fichier(s) mémoire supprimé(s)")
    st.divider()
    if st.button("Nouveau dossier", type="secondary", width="stretch"):
        reset_case()
        st.rerun()

# ----------------------------------------------------------------------------- en-tête
st.markdown(
    '<div class="dc-hero"><h1>🩺 Data Clinic</h1>'
    "<p>Déposez un dataset. Les agents l'auscultent, vous approuvez le traitement, ils l'exécutent, "
    "et ils s'en souviennent pour la prochaine fois.</p></div>",
    unsafe_allow_html=True,
)
stage = st.session_state.stage
steps = ["1 · Dépôt", "2 · Diagnostic", "3 · Traitement", "4 · Rapport & mémoire"]
pills = "".join(
    f'<span class="dc-pill {"done" if i < stage else "active" if i == stage else ""}">{s}</span>'
    for i, s in enumerate(steps)
)
st.markdown(f'<div class="dc-step">{pills}</div>', unsafe_allow_html=True)

feed_box = st.empty()
feed_box.markdown(render_feed(st.session_state.log[-14:]), unsafe_allow_html=True)

# ============================================================================= ÉTAPE 1 : dépôt
st.subheader("1 · Déposer un dataset")
c1, c2 = st.columns([3, 1])
with c1:
    up = st.file_uploader("Fichier CSV ou Excel", type=["csv", "xlsx"], label_visibility="collapsed")
with c2:
    if st.button("Utiliser l'exemple", width="stretch", help=str(SAMPLE_FILE)):
        reset_case()
        st.session_state.file_name = SAMPLE_FILE.name
        st.session_state.file_bytes = SAMPLE_FILE.read_bytes()
        st.session_state.namespace = namespace_for(SAMPLE_FILE.name)
if up is not None and up.name != st.session_state.file_name:
    reset_case()
    st.session_state.file_name = up.name
    st.session_state.file_bytes = up.getvalue()
    st.session_state.namespace = namespace_for(up.name)

if st.session_state.file_bytes:
    name, raw = st.session_state.file_name, st.session_state.file_bytes
    try:
        df_in = pd.read_excel(io.BytesIO(raw)) if name.endswith(".xlsx") else pd.read_csv(io.BytesIO(raw))
    except Exception as exc:  # fichier illisible : on laisse quand même les agents essayer
        df_in, _ = None, st.warning(f"Aperçu impossible ({exc}). Les agents tenteront quand même.")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Fichier", name)
    if df_in is not None:
        m2.metric("Lignes", f"{len(df_in):,}".replace(",", " "))
        m3.metric("Colonnes", df_in.shape[1])
    ns = m4.text_input("Famille de dataset (mémoire)", st.session_state.namespace,
                       help="Namespace mémoire Cominty. Les fichiers d'une même famille partagent leurs règles.")
    st.session_state.namespace = ns.strip() or st.session_state.namespace
    if df_in is not None:
        with st.expander("Aperçu des données brutes"):
            st.dataframe(df_in.head(15), width="stretch", hide_index=True)

    if stage == 0 and st.button("🔍 Lancer le diagnostic", type="primary"):
        cb = feed_callback(feed_box)
        st.session_state.memory_before = engine.run_sync(engine.read_memory(st.session_state.namespace))
        with st.status("Profiler : auscultation du fichier…", expanded=False) as s:
            try:
                prof, profile = engine.run_sync(engine.run_agent_json(
                    AGENTS["profiler"], prompts.profiler_message(), prompts.PROFILE_SCHEMA,
                    file_bytes=raw, filename=name, max_steps=max_steps, on_event=cb))
            except Exception as exc:
                st.error(f"Le Profiler n'a pas pu analyser le fichier : {exc}")
                st.stop()
            record(prof, "Profiler")
            st.session_state.profile = profile
            s.update(label=f"Profiler terminé ({prof.cost:.4f} €)", state="complete")
        with st.status("Auditor : diagnostic des problèmes…", expanded=False) as s:
            has_contract = any(f["path"].endswith("data_contract.md") for f in st.session_state.memory_before)
            try:
                aud, issues = engine.run_sync(engine.run_agent_json(
                    AGENTS["auditor"], prompts.auditor_message(st.session_state.namespace, profile, has_contract),
                    prompts.ISSUES_SCHEMA, file_bytes=raw, filename=name,
                    namespace=st.session_state.namespace, max_steps=max_steps, on_event=cb))
                st.session_state.issues = issues if isinstance(issues, list) else issues.get("issues", [])
            except Exception as exc:
                st.session_state.issues = []
                st.error(f"L'Auditor n'a pas renvoyé de diagnostic exploitable : {exc}")
                aud = engine.AgentResult("Auditor", "", "", cost=0.0)
            record(aud, "Auditor")
            s.update(label=f"Auditor terminé ({aud.cost:.4f} €)", state="complete")
        # Les agents viennent prévenir l'équipe : alerte Slack si le contrat de données est violé.
        violated = [i for i in st.session_state.issues if i.get("contract_violation")]
        if violated and slack_on:
            with st.status(f"Reporter : alerte Slack dans {SLACK.channel}…", expanded=False) as s:
                alert = engine.run_sync(engine.run_agent(
                    AGENTS["reporter"],
                    prompts.slack_alert_message(st.session_state.namespace, name, violated, SLACK.channel),
                    connections=[SLACK.connection], max_steps=4, on_event=cb))
                st.session_state.costs["Alerte Slack"] = alert.cost
                st.session_state.slack_alert = alert.text.strip()
                ok = st.session_state.slack_alert.upper().startswith("POSTED")
                s.update(label="Alerte Slack envoyée" if ok else "Alerte Slack non confirmée",
                         state="complete" if ok else "error")
        st.session_state.stage = 1
        st.rerun()

# ============================================================================= ÉTAPE 2 : diagnostic
if st.session_state.stage >= 1 and st.session_state.issues is not None:
    st.subheader("2 · Diagnostic")
    prof, issues = st.session_state.profile or {}, st.session_state.issues
    counts = {k: sum(1 for i in issues if str(i.get("severity", "")).lower() == k) for k in SEV_LABEL}
    violations = sum(1 for i in issues if i.get("contract_violation"))
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Problèmes détectés", len(issues))
    k2.metric("Sévérité élevée", counts["high"])
    k3.metric("Lignes concernées", sum(int(i.get("rows_affected") or 0) for i in issues))
    k4.metric("Doublons exacts", prof.get("exact_duplicate_rows", "–"))
    k5.metric("Violations du contrat", violations,
              help="Règles apprises lors d'un passage précédent et non respectées par ce fichier.")
    if st.session_state.memory_before:
        st.info("🧠 Un contrat de données existait déjà pour cette famille : l'Auditor l'a utilisé comme référence.")
    if violations:
        st.error(f"{violations} règle(s) du contrat de données sont violées par ce fichier.")
        alert_txt = st.session_state.slack_alert or ""
        if alert_txt.upper().startswith("POSTED"):
            st.success(f"📣 L'équipe a été alertée dans Slack ({SLACK.channel}) par l'agent Reporter.")
        elif alert_txt:
            st.warning(f"Alerte Slack non confirmée par l'agent : {alert_txt[:300]}")

    if issues:
        st.markdown("**Corrections proposées.** Cochez celles que vous approuvez. "
                    "Les corrections jugées sûres par l'Auditor sont pré-cochées.")
        table = pd.DataFrame([{
            "Approuver": bool(i.get("auto_safe", False)),
            "Id": i.get("id"), "Sévérité": SEV_LABEL.get(str(i.get("severity", "")).lower(), i.get("severity")),
            "Colonnes": ", ".join(i.get("columns", []) or []), "Lignes": i.get("rows_affected"),
            "Problème": i.get("description"), "Correction proposée": i.get("proposed_fix"),
            "Contrat": "⛔" if i.get("contract_violation") else "",
        } for i in issues])
        edited = st.data_editor(
            table, hide_index=True, width="stretch", key="approval_table",
            column_config={"Approuver": st.column_config.CheckboxColumn(required=True),
                           "Problème": st.column_config.TextColumn(width="large"),
                           "Correction proposée": st.column_config.TextColumn(width="large")},
            disabled=[c for c in table.columns if c != "Approuver"],
        )
        approved_ids = set(edited.loc[edited["Approuver"], "Id"])
        approved = [i for i in issues if i.get("id") in approved_ids]
        with st.expander("Profil technique du fichier (sortie brute du Profiler)"):
            st.json(prof, expanded=False)

        if st.session_state.stage == 1:
            cta = f"🛠️ Appliquer {len(approved)} correction(s) approuvée(s)"
            if st.button(cta, type="primary", disabled=not approved):
                st.session_state.approved = approved
                cb = feed_callback(feed_box)
                with st.status("Remediator : application des corrections…", expanded=False) as s:
                    msg = prompts.remediator_message(approved, st.session_state.file_name)
                    rem = engine.run_sync(engine.run_agent(
                        AGENTS["remediator"], msg, file_bytes=st.session_state.file_bytes,
                        filename=st.session_state.file_name, max_steps=max_steps, on_event=cb))
                    record(rem, "Remediator")
                    st.session_state.remediation = rem
                    st.session_state.remediation_thread = rem.thread_id
                    st.session_state.pending_questions = rem.questions or None
                    s.update(label=f"Remediator terminé ({rem.cost:.4f} €)", state="complete")
                st.session_state.stage = 2
                st.rerun()
    else:
        st.success("Aucun problème détecté. Ce fichier est sain.")

# ============================================================================= ÉTAPE 3 : traitement
if st.session_state.stage >= 2 and st.session_state.remediation is not None:
    st.subheader("3 · Traitement")
    rem: engine.AgentResult = st.session_state.remediation

    if st.session_state.pending_questions:
        st.warning("Le Remediator a besoin d'une décision humaine avant de continuer.")
        for qi, q in enumerate(st.session_state.pending_questions):
            st.markdown(f"**{q['prompt']}**")
            cols = st.columns(max(1, len(q["options"]) or 1))
            for oi, opt in enumerate(q["options"] or []):
                if cols[oi % len(cols)].button(str(opt), key=f"q{qi}o{oi}"):
                    cb = feed_callback(feed_box)
                    with st.status("Remediator : reprise avec votre réponse…"):
                        rem2 = engine.run_sync(engine.answer_agent(
                            AGENTS["remediator"], st.session_state.remediation_thread, str(opt),
                            max_steps=max_steps, on_event=cb))
                    st.session_state.costs["Remediator"] += rem2.cost
                    st.session_state.remediation = rem2
                    st.session_state.pending_questions = rem2.questions or None
                    st.rerun()
            free = st.text_input("Ou répondez librement", key=f"free{qi}")
            if free:
                cb = feed_callback(feed_box)
                with st.status("Remediator : reprise avec votre réponse…"):
                    rem2 = engine.run_sync(engine.answer_agent(
                        AGENTS["remediator"], st.session_state.remediation_thread, free,
                        max_steps=max_steps, on_event=cb))
                st.session_state.costs["Remediator"] += rem2.cost
                st.session_state.remediation = rem2
                st.session_state.pending_questions = rem2.questions or None
                st.rerun()
    else:
        clean = next((f for f in rem.files if f["name"].lower().endswith((".csv", ".xlsx"))), None)
        log_file = next((f for f in rem.files if f["name"].lower().endswith(".md")), None)
        try:
            summary = engine.parse_json(rem.text[rem.text.rfind("{"):]) if "{" in rem.text else {}
        except Exception:
            summary = {}
        a, b, c = st.columns(3)
        a.metric("Lignes avant", summary.get("rows_before", "–"))
        b.metric("Lignes après", summary.get("rows_after", "–"))
        c.metric("Corrections appliquées", len(summary.get("fixes_applied", [])) or len(st.session_state.approved or []))
        if clean:
            engine.save_outputs(rem.files)
            d1, d2 = st.columns(2)
            d1.download_button("⬇️ Télécharger le fichier nettoyé", clean["bytes"], clean["name"],
                               width="stretch", type="primary")
            if log_file:
                d2.download_button("⬇️ Télécharger le journal des modifications", log_file["bytes"],
                                   log_file["name"], width="stretch")
            try:
                df_clean = pd.read_csv(io.BytesIO(clean["bytes"]))
                with st.expander("Aperçu du fichier nettoyé", expanded=True):
                    st.dataframe(df_clean.head(15), width="stretch", hide_index=True)
            except Exception:
                pass
            if log_file:
                with st.expander("Journal des modifications"):
                    st.markdown(log_file["bytes"].decode("utf-8", "replace"))
        else:
            st.warning("Le Remediator n'a pas joint de fichier. Sa réponse :")
            st.markdown(rem.text)

        if st.session_state.stage == 2 and st.button("📝 Générer le rapport et mémoriser les règles", type="primary"):
            cb = feed_callback(feed_box)
            with st.status("Reporter : rédaction du rapport et mise à jour du contrat de données…") as s:
                change_log = log_file["bytes"].decode("utf-8", "replace") if log_file else ""
                msg = prompts.reporter_message(
                    st.session_state.namespace, st.session_state.file_name, st.session_state.profile,
                    st.session_state.issues, [i.get("id") for i in st.session_state.approved or []],
                    rem.text, change_log, slack_channel=SLACK.channel if slack_on else None)
                rep = engine.run_sync(engine.run_agent(
                    AGENTS["reporter"], msg, extra_files=rem.files, namespace=st.session_state.namespace,
                    connections=[SLACK.connection] if slack_on else None,
                    max_steps=max_steps, on_event=cb))
                record(rep, "Reporter")
                st.session_state.report_text = rep.text
                st.session_state.report_file = next((f for f in rep.files if f["name"].endswith(".md")), None)
                if rep.files:
                    engine.save_outputs(rep.files)
                st.session_state.memory_files = engine.run_sync(engine.read_memory(st.session_state.namespace))
                s.update(label=f"Reporter terminé ({rep.cost:.4f} €)", state="complete")
            st.session_state.stage = 3
            st.rerun()

# ============================================================================= ÉTAPE 4 : rapport & mémoire
if st.session_state.stage >= 3:
    st.subheader("4 · Rapport & mémoire")
    report_md = (st.session_state.report_file["bytes"].decode("utf-8", "replace")
                 if st.session_state.report_file else st.session_state.report_text or "")
    m = re.search(r"(\d{1,3})\s*(?:/|sur|out of)\s*100", report_md)
    left, right = st.columns([1, 2])
    with left:
        st.markdown('<div class="dc-card">', unsafe_allow_html=True)
        st.markdown("**Score de santé des données**")
        st.markdown(f'<div class="dc-score">{m.group(1) if m else "–"}<small>/ 100</small></div>',
                    unsafe_allow_html=True)
        st.caption("Calculé par le Reporter à partir du diagnostic et des corrections appliquées.")
        st.markdown("</div>", unsafe_allow_html=True)
        if st.session_state.report_file:
            st.download_button("⬇️ Télécharger le rapport", st.session_state.report_file["bytes"],
                               st.session_state.report_file["name"], width="stretch")
        total = sum(st.session_state.costs.values())
        st.metric("Coût total du dossier", f"{total:.4f} €")
    with right:
        if slack_on:
            st.success(f"📣 Le Reporter a aussi publié le résumé du rapport dans Slack ({SLACK.channel}).")
        with st.expander("Rapport qualité (lecture non technique)", expanded=True):
            st.markdown(report_md or "_Aucun rapport_")

    st.markdown("#### 🧠 Ce que les agents ont retenu")
    files = st.session_state.memory_files
    if files:
        st.success(f"{len(files)} fichier(s) mémoire dans la famille `{st.session_state.namespace}`. "
                   "Au prochain dépôt d'un fichier de cette famille, l'Auditor vérifiera ces règles automatiquement.")
        for f in files:
            with st.expander(f"{f['path']}  —  {f['purpose']}"):
                st.markdown(f["content"])
    else:
        st.warning("Aucun fichier mémoire trouvé. Vérifie que la mémoire est activée sur l'agent Reporter.")
    st.info("💡 Démo : déposez maintenant un second fichier de la même famille (par exemple `sales_2026-11.csv`) "
            "pour voir le contrôle automatique par rapport au contrat de données.")
