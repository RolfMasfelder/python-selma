
# Agent Selma 👩🏻

### A "Toy" Implementation of OpenClaw with Python and Ollama

<p align="center">
  <img src="images/selma_portrait.png" width="300" alt="Agent Selma interacting with futuristic holographic data displays." />
</p>

## 🌟 Overview

**Agent Selma** is a simplified, educational version of the [OpenClaw](https://github.com/openclaw/openclaw) project.

The primary goal of this project is to deconstruct and understand the underlying architecture of autonomous agents by rebuilding them from scratch using **Python** and **Ollama**. By stripping away complexity, Selma serves as a clean baseline for learning how agentic components interact.


## 🛠 Tech Stack

  * **Language:** [Python](https://www.python.org/)
  * **Free LLM:** [Ollama](https://ollama.com/)
  * **Inspiration:** [OpenClaw](https://github.com/openclaw/openclaw)/[PI-Agent](hhttps://github.com/badlogic/pi-mono)


## 📅 Roadmap & Current Status

### Phase 1: Foundations (Current)

  * **Build "MY-Agent":** Currently developing a simplified version of the **PI-Agent** (the core engine behind OpenClaw).
  * **Architecture Deep-Dive:** Researching and documenting OpenClaw components to ensure Selma captures the essential logic of the original project.
  * Work on a simple version of the **runtime** that calls the MY_Agent. An important component is the **system prompt**.


### Phase 2: First Release

  * **Deadline:** Early June 2026.
  * Targeting a functional prototype that demonstrates basic autonomous task execution.

## ⚠️ Disclaimer

> [\!IMPORTANT]
> **No Contributions Yet:** At this stage, I am not accepting Pull Requests or changes. I am focusing on the initial build to establish the core learning path.

**Communication:**
Feel free to reach out with questions or thoughts\! However, please understand that due to time constraints, I may not be able to respond to every message personally.

## 🧪 Test Scripts

To explore the framework's capabilities step-by-step, the following test scripts are provided:

* **`test_agent.py`**: Tests basic `My_Agent` interaction without session persistence.
* **`test_agent_session.py`**: Demonstrates the `My_AgentSession` abstraction, specifically focusing on subscribing to events like message streaming.
* **`test_agent_session_manage.py`**: Showcases `My_AgentSession` persistence and management, including creating, resuming, and listing historical sessions stored as `.jsonl` files.
* **`test_agent_session_chat.py`**: A complete interactive CLI chat client with `My_AgentSession` featuring a "thinking" spinner, session commands (`/info`, `/reset_session`), and tool selection.

## 🚀 How to use (Coming Soon)

*Instructions for installation and setup will be provided once the first version is released in June.*

```bash
# Placeholder for future installation
git clone https://github.com/YOUR_USERNAME/agent-selma.git
cd agent-selma
pip install -r requirements.txt
```