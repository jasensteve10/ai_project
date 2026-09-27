# Hypothetical enterprise adaptation: a bank IT knowledge assistant

Prepared 26 September 2026 for interview discussion. This is a proposed adaptation of the local election project, not a bank implementation, client architecture or compliance assessment.
The academic priority remains the approved election dataset and retrieval evaluation: 58 hours, one student, a Mac M5, and no expected training phase.
This scenario is an interview narrative and design exercise; it does not add a second implementation to that deadline.

## Problem and scope

An IT engineer needs to find the responsible team, understand a known incident, and locate an applicable recovery procedure across fragmented internal information.
A proposed assistant combines a structured service catalog with approved runbooks and historical incident summaries.
Its initial scope is read-only assistance with cited evidence. An operator remains responsible for checking and carrying out a procedure.
Incident remediation, ticket updates, deployment and production shell access are outside this proposed first release.
All examples below are fictional. A later demonstration could use a small synthetic catalog and documents without importing bank information.

## How the existing project transfers

| Election project | Hypothetical IT equivalent | Transferable question |
|---|---|---|
| Constituency, region and party identifiers | Service, environment and team identifiers | Can entity aliases be resolved reliably? |
| Candidate/result records and analytical views | Service ownership, dependencies and incident records | Can the assistant select the right tables and joins? |
| Turnout definitions and aggregation rules | Incident count, duration and availability definitions | Does the semantic layer prevent incorrect metrics? |
| Source PDF and extraction checks | Catalog snapshots and approved document versions | Can answers be traced to the source version? |
| SQL generation, validation and repair | Restricted catalog lookup and bounded search retries | Do additional steps improve successful answers? |
| Exact-question cache | Permission-scoped result cache | When is reuse correct and economically useful? |

As established by the repository audit, PDF ingestion, CSV/Parquet artifacts, DuckDB schema code, SQL generation/repair, Streamlit UI and exact-question caching exist.
These are prototype components with identified defects; their presence does not establish end-to-end reliability or measured accuracy.
Document retrieval, adaptive retrieval decisions, an explicit ontology, benchmark scoring, experiment traces, identity integration and enterprise access enforcement were not implemented at that audit.
Any later claim should reference the actual experiment artifacts and tested code version; proposed capabilities in this document remain proposals.

## Three example questions

1. “Who owns the fictional Atlas Payments service in production, and which approved restart runbook applies to its current version?”
   Resolve aliases, retrieve catalog ownership and version, then find a compatible approved runbook; show source references and validity dates.
2. “For Atlas Payments in August, how many incidents exceeded 30 minutes, and what recurring cause is supported by their incident summaries?”
   Query structured records for the count, then retrieve permitted summaries for explanation; distinguish recorded causes from inference.
3. “I cannot access the settlement platform documentation. Can you show me the cached recovery procedure instead?”
   Enforce the same authorization on retrieval and cached answers; deny access without revealing restricted content or sensitive metadata.

## Proposed architecture

```text
Authenticated user -> API / policy enforcement -> bounded question router
                                                 |-> read-only catalog query
                                                 |-> permitted document retrieval
                                                 |-> versioned semantic catalog
                                      evidence -> answer with source references
Each step -> redacted trace + usage/cost event -> evaluation and monitoring
Cache reads/writes -> authorization + dependency/version checks
```

The router may select a tool, reformulate a search, or request clarification, subject to an explicit call and time budget.
The semantic catalog defines identifiers, aliases, relationships and approved metric formulas; it is not a substitute for trustworthy source data.
Use deterministic database/policy controls outside the model. A prompt telling an agent to respect access rules is insufficient enforcement.
The existing SQL repair loop demonstrates bounded iteration; it should not be presented as a completed autonomous retrieval platform.

## Evaluation and decision evidence

Compare a full-catalog baseline with lexical retrieval, neural retrieval and a bounded adaptive strategy only where those variants are actually built.
Hold the data snapshot, model, question set and generation settings fixed, and disclose the additional tool/token budget used by adaptive methods.
Score retrieval against labeled evidence; separately score SQL result correctness, evidence-supported answers, abstention and failures by query type.
For an enterprise adaptation, add synthetic role-based tests, expired-document cases and conflicting service aliases, with expected outcomes defined in advance.
Record latency, tokens, tool calls, repair attempts, cache hits and estimated request cost; retain the model/pricing date and accounting assumptions.
Report cost per successful answer alongside total workload cost and success rate, so cheap failures do not look like an improvement.
Separate cold-cache quality comparisons from a repeated-query workload measuring cache benefits, stale reuse and erroneous semantic matches.
Trace run ID, data/index/schema/prompt/model versions, policy version, retrieved source IDs, tool decisions and final status; redact secrets and unnecessary personal data.
An interview “cockpit” can summarize experiment results and failure cases. It must not imply real users, production adoption, bank savings or an achieved service-level agreement.

## Enterprise constraints and sovereignty choices

Identity and authorization must constrain both SQL results and document evidence before they reach the model; the exact implementation depends on the bank's access model.
Cache entries need an effective access scope plus data, document, index, semantic-layer, prompt and model versions where these affect validity.
Recheck authorization when serving a hit and invalidate on relevant permission/source changes. A matching question or shared role name alone does not prove identical access.
Logs, embeddings, cached results and backups can expose information too; decide their access controls, retention and permitted processing locations.
Three deployment options to assess are an external managed API, a privately controlled cloud deployment, or infrastructure operated by the organization.
Compare permitted data flows, administrator access, jurisdiction, model/license terms, network boundaries, observability, capacity, latency and total operating cost.
Local inference on a Mac can support a prototype feasibility test; it does not establish bank-scale capacity, isolation or a sovereign production deployment.
No option automatically establishes security, sovereignty or regulatory compliance. The project can document trade-offs and questions for architecture, security and compliance owners.

## Two-minute interview pitch

“My current project starts from a concrete data problem: answering questions about election results using a pretrained model and a structured database. The prototype includes ingestion, SQL generation, validation, repair and an interface. My academic work is to evaluate retrieval strategies systematically, with no training phase required.

The question is whether a more adaptive approach finds better evidence often enough to justify its extra calls, latency and cost. I want to compare controlled baselines, inspect failures and separate retrieval quality from answer correctness. I would present improvements only when the experiment results support them.

For a bank IT use case, I would adapt the same pattern to a service catalog, incident records and approved runbooks. An engineer could identify a service owner, find an applicable procedure and see the supporting sources. That is a hypothetical adaptation, not a bank system I have deployed.

The enterprise requirements would change the design: authorization must apply before evidence reaches the model, cached answers must respect current permissions and document versions, and each tool call needs a useful trace. Hosting choices would depend on data flows, control requirements and operating cost.

What this project can demonstrate is my ability to connect implementation, measurement and a deployment decision. It gives me a concrete way to explain where an agent adds value, where a simpler workflow is sufficient, and what still needs validation before a production pilot.”

## Five likely interview questions

- **Why use an agent?** Only if bounded tool selection or another retrieval step improves the measured task enough to justify its cost; keep a simple baseline and explicit stopping rules.
- **How would you prevent a data leak?** Enforce access outside the model on queries, evidence and cache hits; test denied-access scenarios and control logs. Those enterprise controls remain proposed here.
- **Why not deploy everything on-premise?** Compare data/control requirements with operating capability, capacity and total cost. Local hosting alone does not settle the security or sovereignty questions.
- **How would you measure business value?** Begin with answer success, evidence quality, response time and cost. A later user pilot could measure task completion and time saved; benchmark scores alone do not prove productivity.
- **What have you personally demonstrated?** State the implemented prototype and verified experiment results, then identify remaining defects and proposed extensions. Do not claim bank deployment, senior programme ownership or savings without evidence.
