"""Configuration : identifiants d'agents, namespace mémoire, chemins."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # le SDK Cominty ne lit pas .env tout seul


def _load_streamlit_secrets() -> None:
    """En déploiement Streamlit Cloud, les secrets sont dans st.secrets : on les expose en variables d'environnement."""
    try:
        import streamlit as st

        for key, value in st.secrets.items():
            if isinstance(value, str) and key not in os.environ:
                os.environ[key] = value
    except Exception:  # pas de secrets.toml en local, ou hors contexte Streamlit
        pass


_load_streamlit_secrets()

SAMPLE_FILE = ROOT / "data" / "sales_dirty.csv"
OUTPUT_DIR = ROOT / "data" / "output"


@dataclass(frozen=True)
class AgentSpec:
    key: str
    label: str
    role: str          # phrase courte affichée dans l'interface
    agent_id: str
    uses_memory: bool


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Variable {name} manquante dans .env")
    return value


def load_agents() -> dict[str, AgentSpec]:
    return {
        "profiler": AgentSpec("profiler", "Profiler", "Ausculte le fichier", _env("PROFILER_AGENT_ID"), False),
        "auditor": AgentSpec("auditor", "Auditor", "Diagnostique les problèmes", _env("AUDITOR_AGENT_ID"), True),
        "remediator": AgentSpec("remediator", "Remediator", "Applique les corrections approuvées", _env("REMEDIATOR_AGENT_ID"), False),
        "reporter": AgentSpec("reporter", "Reporter", "Rédige le rapport et mémorise les règles", _env("REPORTER_AGENT_ID"), True),
    }


@dataclass(frozen=True)
class SlackSettings:
    """Intégration MCP Slack, optionnelle : activée dès que SLACK_CHANNEL est défini dans .env."""
    channel: str
    connection: str   # nom de la connexion tel qu'affiché sur la page Intégrations de Cominty

    @property
    def enabled(self) -> bool:
        return bool(self.channel)


def load_slack() -> SlackSettings:
    return SlackSettings(
        channel=os.environ.get("SLACK_CHANNEL", "").strip(),
        connection=os.environ.get("SLACK_CONNECTION", "slack").strip() or "slack",
    )


def namespace_for(filename: str) -> str:
    """Un namespace mémoire par *type* de dataset.

    'sales_dirty.csv', 'sales_2026-10.csv' et 'sales_v2.csv' partagent 'dataclinic-sales' :
    les règles apprises sur un fichier servent aux suivants du même type.
    """
    stem = Path(filename).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    stem = re.sub(r"-(dirty|clean|raw|final|v\d+|\d{4}(-\d{2}){0,2}|\d+)$", "", stem)
    return f"dataclinic-{stem or 'dataset'}"
