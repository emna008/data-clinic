# 🩺 Data Clinic

**A data-quality inbox run by Cominty agents.** Drop a dataset, the agents examine it, you approve the
treatment in one click, they execute it, and they remember the rules for the next file.

Built for the Cominty AI Engineer internship challenge (November 2026).

## The problem

Data scientists spend most of their time cleaning data, and the rules they discover ("dates are day-first",
"999 means missing", "Deutschland means Germany") live in notebooks nobody reads again. Every new monthly
file starts from zero.

## What Data Clinic does

| Step | Agent (Cominty custom agent) | What it does | Cominty features used |
|---|---|---|---|
| 1 | **Profiler** | Loads the file in the sandbox, returns a structured JSON profile | file upload, Python sandbox, strict JSON output |
| 2 | **Auditor** | Lists every quality issue with one concrete fix, flags the safe ones, **checks the data contract learned previously** | sandbox, **memory namespace**, streaming events |
| 3 | *You* | Tick the fixes you approve (safe ones are pre-ticked) | human in the loop |
| 4 | **Remediator** | Applies **only** the approved fixes, asks a question if a fix is ambiguous, returns the clean file + change log | sandbox, produced files, agent **questions** |
| 5 | **Reporter** | Writes a one-page report for a non-technical reader with a health score, **writes the data contract to memory**, and posts the summary in Slack | memory write, produced files, **MCP Slack integration** |

The second time a file of the same family arrives, the Auditor reads the contract and reports
**contract violations** (new country, renamed column, negative price) before anyone opens the file,
and the Reporter **alerts the team in Slack** through Cominty's MCP integration. The agents come to you;
you do not have to open a dashboard to find out something is wrong.

Two more behaviours build on that contract:

* **Schema changes get a plan, not a guess.** When a violation concerns the structure of the file (a renamed
  or missing column), you can ask Cominty's built-in **Planner** agent for a migration plan: column mapping,
  validations, contract update or rejection, rollback. You approve it, and the Remediator applies its data
  steps before the other fixes.
* **The contract is versioned.** Each Reporter run bumps `Version: N`, keeps a dated `## History` section,
  and the app shows a diff of what changed since the previous version.

## Architecture

```
Streamlit UI (app.py)  ──orchestrates──►  clinic/engine.py  ──Cominty Python SDK──►  4 custom agents on platform.cominty.ai
        │                                      │                                        each in its own thread + sandbox
        │  live activity feed  ◄── ToolCall / LlmStep / Result events                   memory namespace per dataset family
        └── approval table, downloads, memory viewer
```

* Agents are created on the Cominty **Agents** page (instructions in [`agents/`](agents/)); the app only needs their ids.
* Each agent call starts a fresh **thread** with locked capabilities: web `never`, memory `always` only for Auditor
  and Reporter, MCP `always` restricted to the `slack` connection only on Reporter threads, `never` elsewhere,
  `max_steps` capped. The thread policy cannot be widened later, which keeps cost and behaviour predictable.
* Slack is optional: set `SLACK_CHANNEL` in `.env` after connecting Slack on the platform (see
  [`docs/SLACK_SETUP.md`](docs/SLACK_SETUP.md)). Without it, everything else works unchanged.
* Outputs are passed from one agent to the next by the Python code, so the human approval step sits naturally
  between diagnosis and treatment.
* If an agent answers in prose instead of JSON, the engine sends one follow-up in the same thread asking for the
  JSON only (cheap: the analysis is already done).

## Run it locally

```bash
pip install -r requirements.txt
cp .env.example .env          # add COMINTY_API_KEY, COMINTY_USER_ID and the 4 agent ids
python scripts/make_dirty_dataset.py   # sample data (312 messy e-commerce orders)
python scripts/make_second_batch.py    # next month's batch, violates the learned contract
streamlit run app.py
```

Headless end-to-end test: `python scripts/run_pipeline.py [file.csv]`.

## Deploy (Streamlit Community Cloud)

1. Push this repository to GitHub.
2. On share.streamlit.io, create an app from the repo with `app.py` as the main file and Python 3.12 or 3.13.
3. In **Advanced settings → Secrets**, paste the same keys as `.env.example` in TOML form:

```toml
COMINTY_API_KEY = "sk-cmt-..."
COMINTY_USER_ID = "user_..."
PROFILER_AGENT_ID = "..."
AUDITOR_AGENT_ID = "..."
REMEDIATOR_AGENT_ID = "..."
REPORTER_AGENT_ID = "..."
SLACK_CHANNEL = "#data-clinic"
SLACK_CONNECTION = "slack"
```

`clinic/config.py` reads `st.secrets` and exposes them as environment variables, so the same code runs locally and in the cloud.

## Cost

A full case (4 agents) costs about **0.015 €** of Cominty credits on the sample file. A Slack alert adds
about 0.003 €. The optional Planner step is the most expensive one (0.02 to 0.07 € depending on the
contract length), which is why it runs only on request.

## Limits (proof of concept)

* Files up to a few hundred thousand rows; the agent sandbox does the heavy lifting.
* The Auditor is deliberately conservative: only deterministic, reversible fixes are pre-approved.
* No authentication; one memory namespace per dataset family is derived from the file name and can be edited.

## Author

Emna Bouharb, AI & Data Science student. MIT License.
