"""Moteur : appels au SDK Cominty, streaming des événements, parsing des sorties.

Chaque agent tourne dans son propre thread Cominty. Le code Python est l'orchestrateur :
il enchaîne Profiler -> Auditor -> (approbation humaine) -> Remediator -> Reporter
et passe les sorties de l'un en entrée du suivant.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cominty_sdk import (
    AgentCapabilities,
    AsyncCominty,
    McpCapability,
    MemoryCapability,
    WebCapability,
    events,
)

from .config import OUTPUT_DIR, AgentSpec

EventCallback = Callable[[dict[str, Any]], None]


@dataclass
class AgentResult:
    agent: str
    thread_id: str
    text: str
    files: list[dict[str, Any]] = field(default_factory=list)      # {name, bytes}
    questions: list[dict[str, Any]] = field(default_factory=list)  # {prompt, options}
    cost: float = 0.0
    steps: int = 0


# --------------------------------------------------------------------------- utilitaires

def parse_json(text: str) -> Any:
    """Extrait le premier objet/tableau JSON d'une réponse d'agent, même entouré de ``` ou de prose."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0]
    if not starts:
        raise ValueError("Aucun JSON trouvé dans la réponse de l'agent")
    start = min(starts)
    end = max(cleaned.rfind("}"), cleaned.rfind("]"))
    return json.loads(cleaned[start:end + 1])


def _headline(description: str | None) -> str:
    """Première phrase utile d'une étape de réflexion (le flux brut commence souvent par 'Reasoning')."""
    for line in (description or "").splitlines():
        line = line.strip().strip("*#").strip()
        if line and line.lower() not in {"reasoning", "thinking"}:
            return line[:150]
    return "Réflexion"


def _capabilities(spec: AgentSpec, namespace: str | None, connections: list[str] | None) -> AgentCapabilities:
    """Verrouille le thread (politique figée au démarrage, un message ne peut pas l'élargir) :

    - web : jamais ;
    - mémoire : seulement pour les agents qui en ont besoin, sur le namespace du dataset ;
    - intégrations MCP : uniquement les connexions listées (ex. ["slack"]), sinon désinstallées.
    """
    kwargs: dict[str, Any] = {"web": WebCapability(activation="never")}
    if spec.uses_memory and namespace:
        kwargs["memory"] = MemoryCapability(activation="always", namespace=namespace)
    elif not spec.uses_memory:
        kwargs["memory"] = MemoryCapability(activation="never")
    if connections:
        kwargs["mcp"] = McpCapability(activation="always", connections=list(connections))
    else:
        kwargs["mcp"] = McpCapability(activation="never")
    return AgentCapabilities(**kwargs)


async def _consume(client: AsyncCominty, run, spec: AgentSpec, on_event: EventCallback | None,
                   thread_id: str | None = None) -> AgentResult:
    """Lit le flux d'événements d'un run, relaie chaque étape à l'interface, puis collecte le résultat.

    `thread_id` est fourni pour une relance (chat.send) : sur ce type de run, `run.thread` peut être None.
    """
    cost, steps = 0.0, 0
    async for ev in run:
        payload: dict[str, Any] | None = None
        if isinstance(ev, events.SettingUpSandbox):
            payload = {"kind": "sandbox", "text": "Préparation du sandbox d'exécution"}
        elif isinstance(ev, events.ToolCall):
            status = str(getattr(ev, "status", "") or "")
            if any(s in status for s in ("success", "error", "fail")):
                steps += 1
            payload = {"kind": "tool", "text": f"Outil {ev.data.name} : {status}", "status": status}
        elif isinstance(ev, events.LlmStep):
            payload = {"kind": "llm", "text": _headline(ev.data.description)}
        elif isinstance(ev, events.Result):
            cost = float(ev.data.cost.total)
            payload = {"kind": "result", "text": f"Terminé. Coût : {cost:.4f} €", "cost": cost}
        if payload and on_event:
            payload["agent"] = spec.label
            on_event(payload)

    reply = await run.result()
    text = await run.text()
    files: list[dict[str, Any]] = []
    for f in reply.files or []:
        name = Path(f.name).name  # on ne fait pas confiance au chemin renvoyé
        files.append({"name": name, "bytes": await client.chat.download_file(f.id)})
    questions = [{"prompt": q.prompt, "options": list(q.options or [])} for q in (reply.questions or [])]
    thread = getattr(run, "thread", None)
    resolved_thread = thread_id or (str(thread.id) if thread is not None else str(getattr(reply, "thread_id", "")))
    return AgentResult(spec.label, resolved_thread, text, files, questions, cost, steps)


# --------------------------------------------------------------------------- appels agents

async def run_agent(
    spec: AgentSpec,
    message: str,
    *,
    file_bytes: bytes | None = None,
    filename: str | None = None,
    extra_files: list[dict[str, Any]] | None = None,   # [{name, bytes}] : 5 fichiers max par message
    namespace: str | None = None,
    connections: list[str] | None = None,              # intégrations MCP autorisées sur ce thread
    max_steps: int = 8,
    on_event: EventCallback | None = None,
) -> AgentResult:
    """Démarre un thread avec un agent, envoie (optionnellement) des fichiers, stream les événements."""
    async with AsyncCominty() as client:
        to_send = ([{"name": filename or "data.csv", "bytes": file_bytes}] if file_bytes is not None else [])
        to_send += (extra_files or [])[:4]
        file_ids = []
        for f in to_send:
            if on_event:
                on_event({"agent": spec.label, "kind": "upload", "text": f"Envoi de {f['name']}"})
            uploaded = await client.chat.upload_file(f["bytes"], filename=f["name"])
            file_ids.append(uploaded.id)
        file_ids = file_ids or None
        run = await client.chat.start(
            agent_id=spec.agent_id,
            message=message,
            file_ids=file_ids,
            max_steps=max_steps,
            thread_capabilities=_capabilities(spec, namespace, connections),
        )
        return await _consume(client, run, spec, on_event)


async def answer_agent(
    spec: AgentSpec,
    thread_id: str,
    message: str,
    *,
    max_steps: int = 8,
    on_event: EventCallback | None = None,
) -> AgentResult:
    """Répond à une question posée par l'agent, dans le même thread (il garde tout son contexte)."""
    async with AsyncCominty() as client:
        run = await client.chat.send(thread_id, agent_id=spec.agent_id, message=message, max_steps=max_steps)
        return await _consume(client, run, spec, on_event, thread_id=str(thread_id))


def extract_json(result: AgentResult) -> Any:
    """Cherche le JSON dans la réponse, puis dans les fichiers .json joints."""
    try:
        return parse_json(result.text)
    except (ValueError, json.JSONDecodeError):
        pass
    for f in result.files:
        if f["name"].lower().endswith(".json"):
            return json.loads(f["bytes"].decode("utf-8", "replace"))
    raise ValueError("Aucun JSON dans la réponse ni dans les fichiers joints")


async def run_agent_json(
    spec: AgentSpec,
    message: str,
    schema: str,
    **kwargs: Any,
) -> tuple[AgentResult, Any]:
    """run_agent + parsing JSON, avec UNE relance dans le même thread si l'agent a répondu en prose.

    La relance est peu coûteuse : le thread garde tout le contexte (fichier déjà analysé),
    l'agent n'a plus qu'à reformater.
    """
    from .prompts import json_retry_message  # import local pour éviter un cycle

    result = await run_agent(spec, message, **kwargs)
    try:
        return result, extract_json(result)
    except (ValueError, json.JSONDecodeError):
        on_event = kwargs.get("on_event")
        if on_event:
            on_event({"agent": spec.label, "kind": "llm", "text": "Réponse en prose : demande du JSON structuré"})
        retry = await answer_agent(spec, result.thread_id, json_retry_message(schema),
                                   max_steps=kwargs.get("max_steps", 8), on_event=on_event)
        merged = AgentResult(spec.label, result.thread_id, retry.text, result.files + retry.files,
                             retry.questions, result.cost + retry.cost, result.steps + retry.steps)
        return merged, extract_json(merged)


# --------------------------------------------------------------------------- mémoire

async def read_memory(namespace: str) -> list[dict[str, str]]:
    """Lit tous les fichiers mémoire d'un namespace : ce que les agents ont appris sur ce dataset."""
    async with AsyncCominty() as client:
        try:
            summaries = await client.memory.list(namespace=namespace)
        except Exception:
            return []
        out = []
        for s in summaries or []:
            try:
                f = await client.memory.get(s.path, namespace=namespace)
                out.append({"path": s.path, "purpose": getattr(f, "purpose", "") or "", "content": f.content})
            except Exception:
                continue
        return out


async def list_namespaces() -> list[str]:
    async with AsyncCominty() as client:
        try:
            return [n for n in await client.memory.list_namespaces() if str(n).startswith("dataclinic-")]
        except Exception:
            return []


async def forget_namespace(namespace: str) -> int:
    """Supprime les fichiers mémoire d'un namespace (utile pour rejouer la démo à zéro)."""
    async with AsyncCominty() as client:
        summaries = await client.memory.list(namespace=namespace)
        for s in summaries or []:
            await client.memory.delete(s.path, namespace=namespace)
        return len(summaries or [])


def save_outputs(files: list[dict[str, Any]]) -> list[Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in files:
        p = OUTPUT_DIR / f["name"]
        p.write_bytes(f["bytes"])
        paths.append(p)
    return paths


def run_sync(coro):
    """Streamlit est synchrone ; chaque appel agent tourne dans sa propre boucle asyncio."""
    return asyncio.run(coro)
