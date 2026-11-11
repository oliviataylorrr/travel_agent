# Negotiation ideas (copy selectively into harness/instructions.md)

These are illustrative instructions, not application code. The public market history API provides actual accepted service fees for comparable historical transactions. The Marketplace registry provides reputation scores.

- **First price:** Accept the first posted qualifying offer without bargaining.
- **Historical average:** Inspect completed service prices for your service type; accept offers at or above the historical average; otherwise counter at the average.
- **Reputation premium:** If your public reputation is above the 90th percentile of your specialty, require a price near the top 10% of comparable accepted fees.
- **Low-price entrant:** Offer a discount below the service median when eligible to grow market share.

Always return the requested structured negotiation decision (`accept`, `counter`, `reject`) and a USD-equivalent price. Do not invent reputation or historical prices when the registry has no examples.
