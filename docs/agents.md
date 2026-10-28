# Agents

EnergyOps AI will have at most five agents. Five is a ceiling, not a target, and an agent is only added when a job cannot be done well by plain Python or by one of the agents already planned.

## Rules every agent follows

Every agent has typed input and output, a versioned prompt file, a named list of allowed tools, a timeout and a small set of evaluation cases. Every number in an answer comes from a Python function and is checked against the computed facts before the answer is returned, so a model can explain a figure but never invent one. Who the user is and which organisation they belong to always comes from the authenticated session, never from model output or tool output. No agent controls equipment, and no agent approves its own proposals: anything that changes the world, such as a maintenance ticket, is prepared by an agent and approved by a person.

## The five agents

The energy analyst explains how a site used energy over a period, such as which days were highest, when the peak happened and how weekdays compare with weekends. It is built first and is backlog item 7.

The report writer turns the same computed facts into a short written report for a manager or a technician. It reuses the fact sheet from the analyst, so it adds little new calculation. It has no free-text input, only a site and an audience, so there is nothing for a person to inject through the request. Python enforces its structure as well as its figures: the sections must use known headings in a fixed order (Consumption, Demand, Weekdays and weekends, Data quality), each section must cite fact ids, and a Data quality section is required exactly when the fact sheet carries a warning about missing readings and is refused when it does not. It is built and sits in draft, with a versioned prompt, a registry entry, scripted evaluation cases and a live evaluation (`--agent report-writer`), but it has not yet been run against a real model.

The anomaly investigator takes an event that the Python detector has already found (see [anomalies.md](anomalies.md)), gets the facts about it, and suggests what a person might check, as hypotheses. It never decides that something is a fault. A run takes a site and an event id such as A1. Python turns the event into a small fact sheet of its own (how big, how long, how it compares with the site's usual demand and with an average day), and the model sees only that. Beyond the figure check, Python enforces what makes a hypothesis a hypothesis: confidence can only be low or medium because one meter cannot strongly support any cause, wording that states a cause as certain is rejected, and a suggested check may not tell anyone to switch equipment off or call a contractor. Python cannot judge whether a suggested check is sensible, so a person has to read them. It is built and sits in draft with a versioned prompt, nine scripted evaluation cases and a live evaluation, and it has not yet been run against a real model. It runs against the invented `demo-clinic` site, which has two events written into its readings so there is something to find.

The savings advisor suggests actions that could reduce cost and gives an estimated range calculated in Python from the tariff. The range is always labelled as an estimate, is never promised as a saving, and is never described as verified until a real before and after comparison exists.

The supervisor receives a question, picks the right agent, passes on only the session context it was given and declines questions outside the product. It is built last as a LangGraph workflow, in Phase 3.

## What is not an agent

Forecasting, searching a knowledge base, voice and ticket preparation are capabilities, not agents. Forecasting and knowledge search become tools that the agents above can call, voice becomes a way of talking to the supervisor, and tickets are drafts that a person approves.

## Build order

The energy analyst, then the report writer, then the anomaly investigator, then the savings advisor, then the supervisor. The energy analyst, the report writer and the anomaly investigator are built and share one pipeline (`energyops/agents/pipeline.py`) that fetches the fact sheet, makes one model call, checks the shape and every figure in Python, and sends a rejected answer back once with the reasons. A new agent supplies only its prompt message, its output shape and its own checks, and is added as one entry in `energyops/agents/registry.py`, which refuses to hold more than five. All three are still drafts. The savings advisor and the supervisor have not started.
