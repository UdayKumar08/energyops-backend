# Agents

EnergyOps AI will have at most five agents. Five is a ceiling, not a target, and an agent is only added when a job cannot be done well by plain Python or by one of the agents already planned.

## Rules every agent follows

Every agent has typed input and output, a versioned prompt file, a named list of allowed tools, a timeout and a small set of evaluation cases. Every number in an answer comes from a Python function and is checked against the computed facts before the answer is returned, so a model can explain a figure but never invent one. Who the user is and which organisation they belong to always comes from the authenticated session, never from model output or tool output. No agent controls equipment, and no agent approves its own proposals: anything that changes the world, such as a maintenance ticket, is prepared by an agent and approved by a person.

## The five agents

The energy analyst explains how a site used energy over a period, such as which days were highest, when the peak happened and how weekdays compare with weekends. It is built first and is backlog item 7.

The report writer turns the same computed facts into a weekly or monthly summary written for a manager. It reuses the fact sheet from the analyst, so it adds little new calculation.

The anomaly investigator takes an anomaly that a Python detector has already found, gathers the surrounding context and explains possible causes as hypotheses, each with a stated confidence and a suggested check. It cannot be built until an anomaly detector exists in this repository, and it never decides on its own that something is a fault.

The savings advisor suggests actions that could reduce cost and gives an estimated range calculated in Python from the tariff. The range is always labelled as an estimate, is never promised as a saving, and is never described as verified until a real before and after comparison exists.

The supervisor receives a question, picks the right agent, passes on only the session context it was given and declines questions outside the product. It is built last as a LangGraph workflow, in Phase 3.

## What is not an agent

Forecasting, searching a knowledge base, voice and ticket preparation are capabilities, not agents. Forecasting and knowledge search become tools that the agents above can call, voice becomes a way of talking to the supervisor, and tickets are drafts that a person approves.

## Build order

The energy analyst, then the report writer, then the anomaly investigator, then the savings advisor, then the supervisor. Nothing on this list except the analyst's design has started, and no agent is implemented yet.
