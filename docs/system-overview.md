# System Overview

## One-sentence explanation

**Agents Should Survive Failure is a reference backend for running important AI-assisted business processes without losing progress, bypassing human authority, or repeating actions when workers crash or requests are retried.**

## Who is this for?

The direct users are engineers building AI systems:

- AI and agent engineers;
- backend engineers;
- platform and reliability engineers;
- teams moving an internal AI prototype toward a dependable business workflow.

A normal employee would usually use a simpler product interface built on top of this backend. For example, an operations manager might see a page saying:

> The AI investigation is complete. Review the evidence and approve or reject the action.

Behind that page, this project would manage the workflow history, tool permissions, human approval, retries, evidence, and duplicate prevention.

## What problem does it solve?

A simple agent demo often assumes everything works on the first attempt. Real systems cannot make that assumption.

A worker might crash after an email or database write succeeds but before it reports success. A network timeout may leave the caller unsure whether an action happened. An approval message may be delivered twice. A model may request an action it is not allowed to authorize.

This project demonstrates the surrounding control plane needed to handle those situations honestly.

## Why is the example about vendor onboarding?

Vendor onboarding is a test vehicle for the platform, not the entire product idea.

A company considering a new supplier must collect information, check policy, calculate risk, wait for an authorized decision, and record the outcome. That creates a useful reference workflow because it includes:

- AI-assisted evidence interpretation;
- deterministic business rules;
- a human approval boundary;
- an important final write;
- a notification;
- several opportunities for retries and duplicate actions.

The repository implements and proves that one scenario deeply instead of pretending to implement every possible industry workflow.

## How could the same pattern apply elsewhere?

The repository includes a second reference implementation: an AI-assisted high-value refund
workflow.

1. An agent gathers the order, payment, and support history.
2. Deterministic rules calculate whether the request is low or high risk.
3. The model explains the evidence.
4. Large refunds wait for an authorized human.
5. A governed payment tool performs the refund.
6. If the worker crashes, the workflow resumes.
7. A stable idempotency key prevents the payment from happening twice.

It retrieves synthetic order and policy evidence through governed tools, calculates deterministic
risk, waits for an authenticated decision, and writes an idempotent refund projection and
notification. It demonstrates that the platform contracts extend beyond vendor onboarding; it is
implemented on `main` and is awaiting the next release evidence bundle.

Other possible adaptations include incident response, insurance case review, account changes,
procurement, and internal IT operations.

## What does the built-in workflow do?

The mature vendor-onboarding workflow:

1. stores a synthetic vendor request;
2. starts a durable Temporal workflow;
3. retrieves vendor and policy evidence through governed tools;
4. calculates a deterministic jurisdiction-based risk score;
5. asks a model for a bounded explanation;
6. creates an approval request;
7. waits durably for an authorized decision;
8. records approval or rejection;
9. creates an approved-vendor projection when approved;
10. creates a synthetic email;
11. stores ordered events, tool calls, model-call metadata, approvals, and audit evidence.

The model does not approve the vendor.

## What does “durable” mean?

A normal program often keeps its progress in memory. If the process dies, that progress may disappear.

Temporal records workflow history. A replacement worker can reconstruct what has already happened and continue the case.

Think of Temporal as a case manager that keeps the official checklist even when the employee currently handling the case goes offline.

## How are duplicate actions prevented?

The project does not claim that code executes exactly once. In distributed systems, an activity may be delivered again after an uncertain failure.

Instead, each important intended action has a stable idempotency key—similar to a receipt number. Transactions and database constraints recognize the repeated request and converge on the result already committed.

> Execution may occur more than once, while the business effect is committed once.

## What is the SDK?

The SDK is the developer contract for writing a compatible external Python agent.

The platform is the secure workplace. The SDK defines the plug shape or access badge an agent must use to work inside it.

A compatible agent receives a constrained context for actions such as:

- calling an approved tool;
- saving a checkpoint;
- creating an artifact;
- requesting approval;
- checking cancellation;
- emitting progress events.

The SDK is not an automatic connector for every existing agent. Agent code must be adapted, packaged, installed by the operator, registered, and started through the platform API.

The SDK and managed-agent runtime are preview quality. The vendor-onboarding workflow is the mature reference implementation.

## What would a user see while it is running?

This repository does not include a polished customer dashboard. A technical demonstration uses:

- FastAPI/OpenAPI to create and approve requests;
- Temporal UI to show the durable workflow history and approval wait;
- Grafana to show operational health;
- the evidence API or PostgreSQL to show persisted results;
- the terminal to run the worker-crash proof.

A real product built from these patterns would normally place a simpler business interface in front of the control plane.

## What is proven, and what is not?

### Release-proven in `v0.2.0`

- the vendor-onboarding workflow;
- durable approval waiting;
- recoverable workflow starts;
- governed tool execution;
- 24 reviewed production-workflow evaluation cases;
- OS-level worker-kill and replacement-worker recovery;
- exactly one approval, approved-vendor projection, and synthetic email in the tested crash scenario;
- migration, CI, secret-scanning, dependency-audit, and SBOM checks.

### Preview or experimental

- the high-value refund workflow until its next release evidence ships;
- the generic external-agent SDK/runtime;
- long-running generic-agent approval behavior;
- agent delegation;
- broader production isolation and deployment concerns.

## The most accurate public description

> A production-style durable AI workflow control plane that combines governed tool use, human approval, crash recovery, audit evidence, and duplicate-safe business effects, proven through a real vendor-onboarding reference workflow.
