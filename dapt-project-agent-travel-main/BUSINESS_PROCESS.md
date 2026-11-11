# Agent Marketplace — How the business works

## Purpose

A client preparing for an interview in the critical-mineral sector requests four types of advice: (1) flights and itinerary, (2) lodging, (3) role-specific interview preparation, and (4) chemical and commercial facts about a mineral. **These are recommendations, not purchases of flights or hotels.** The instructor controls the Marketplace. Each student controls one independent service agent on Render. All currency figures in this first version represent **USD-equivalent service fees**, not actual token transfers.

## Parties and responsibilities

- **Instructor Marketplace:** Accepts jobs from a CSV, keeps the agent directory and market transaction history, enforces one ensemble commitment per agent per job, accepts complete bids, applies the client-selected guardrail policy, awards one bid, accepts the deliverable, simulates initial client funding, records resulting payments and client evaluations, and publishes dashboards.
- **Travel agent (lead):** On each job posting, attempts to assemble an ensemble. Finds suitable Lodging, Interview and Mineral agents by advertised prices and reputation; negotiates through A2A; reserves members; submits an ensemble bid. If selected, researches flights and asks members for their outputs.
- **Lodging agent:** Negotiates participation and provides lodging options for the client's location and dates.
- **Interview Advice agent:** Negotiates participation, prepares interview advice, collects all four outputs into a standard JSON package, posts it to the Marketplace, and distributes the agreed service fees after client funding.
- **Mineral Expert agent:** Negotiates participation and researches chemical and commercial facts with supporting sources.

## End-to-end event sequence

1. **Client jobs posted.** The instructor uploads a UTF-8 CSV, which may have 1–100 rows. The Marketplace assigns `batch_id` to the upload and a distinct `job_id` to each row. A job selects a named Markdown guardrail policy stored by the Marketplace. The complete policy text is snapshotted when the job is created.
2. **Trigger.** The Marketplace's background worker notices a queued job. It sends an A2A `start_bid` task to every registered Travel agent. The worker handles jobs sequentially for reliability and gives each Travel agent a chance to submit a bid before deciding.
3. **Discovery and negotiation.** A Travel lead reads the instructor's public registry of agents and completed historical service prices, sorts potential partners by posted price and reputation, and sends A2A invitations. Specialists use their editable `instructions.md` and public market history to accept, reject, or counter. There are at most three rounds per specialist invitation. The Marketplace atomically ensures a specialist is reserved for **only one ensemble on a given job**, though it can participate in another job's ensemble.
4. **Ensemble and bid.** A complete ensemble has one of each service type. The final agreed prices are recorded as integer USD cents. The Travel lead submits the ensemble and its complete bid to the Marketplace board. Every complete ensemble bids; an incomplete one submits no bid. Exclusivity means there may be fewer complete ensembles than Travel agents.
5. **Client bid selection.** The Marketplace checks that a bid is complete and selects exactly one valid bid. `lowest_price.md` uses the lowest complete bid, breaking ties by average reputation. `reputation_and_price.md` delegates the price/reputation tradeoff to the LLM while Python enforces that the selected bid exists and is eligible. **No complete bid means the job remains `awaiting_bids`, not that a fictitious winner is created.**
6. **Fulfillment.** The winning Travel agent sends `fulfill` tasks by A2A to the Lodging, Interview and Mineral agents, and researches travel itself. Each produces its portion in structured JSON. The Interview agent checks for all four portions and posts one combined package to the Marketplace.
7. **Client funding and payouts.** Once a complete package is submitted, the Marketplace records simulated $ funding to the Interview agent for the total bid. It then sends that agent an A2A `settle` task. Interview records the four exact negotiated USD-equivalent payout obligations through the Marketplace's payment API, including the portion it retains. Every payment has a unique ID and an explicit `simulated` status. **No wallet balance or blockchain state changes in this version.**
8. **Reputation and closure.** The Marketplace client model evaluates the four delivered services (0–100) and posts public reputation feedback. If that evaluation fails, the job can still complete, with feedback marked pending rather than fabricated. Winning historical prices and reputations become public market data for future negotiations. The ensemble closes; specialists can compete again on new jobs.

## What the configurable files control

- Each **student** replaces `harness/instructions.md` with its negotiation strategy and service-research practices. The student also edits `config/agent.json` for identity, service role, posted price, token and wallet metadata. Python always validates service roles and monetary amounts.
- Each **client job** selects a Marketplace Markdown policy by its `guardrail` CSV field. Students cannot supply arbitrary filesystem paths. Policy text influences selection, but Python always enforces one winner, one member per service, exclusive per-job membership, and exact payout amounts. A completely new type of enforceable rule may require new Python validation code.

## Identifiers and records

`batch_id` identifies a CSV upload; `job_id` identifies a specific client request; `ensemble_id` identifies a temporary team; `bid_id` identifies its price offer; A2A task IDs identify messages; payment IDs identify obligations. They are deliberately different. SQLite stores registrations, reservations, bids, delivered JSON, payment events, model token usage and historical prices.

## Settlement phases

The first release uses **simulated settlement**: it records USD obligations in SQLite, with `SIM-...` references, and does not maintain a fictional point balance. A separate Base Sepolia settlement adapter is defined but **not yet implemented**; merely changing `PAYMENT_MODE` to `base_sepolia` will reject transfers rather than falsely report a successful payment. A later implementation must quote swaps, check pool liquidity/slippage and gas, decrypt the pre-existing wallet using a secret, sign and broadcast ERC-20 swaps/transfers on Base Sepolia, verify receipts, and ensure idempotent payouts. Expected pools may be seeded with ~10,000 tokens, but swap feasibility depends on actual pool reserves and price, not nominal token counts.

## Limitations and operating assumptions

- There are no actual airline or hotel reservations. Research responses are recommendations and should cite evidence, not claim real-time bookability.
- The single shared bearer secret is classroom-grade authentication, not identity-specific cryptographic authority. Students can potentially impersonate other agents that know the secret. Do not expose public wallets' private keys to LLMs.
- The A2A SDK handles the communication protocol and task states; the Marketplace database controls commercial state. A Render restart during an unfinished job marks it `needs_review` instead of blindly sending duplicate payments. An operator must inspect and reconcile it.
- Multiple Travel agents can bid only when enough exclusive specialist capacity exists. If none can complete a team, a job remains awaiting bids and the instructor can retry after adding agents.
- Reputation in this version is the mean of Marketplace client evaluations; new agents start at 50/100. Agents cannot set their own reputation in the registry.
