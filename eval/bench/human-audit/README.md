# Human label audit operator boundary

The audit tool generates deterministic blinded packets and validates submitted file structure. It
cannot verify that an annotator is human and never supplies, predicts, or fills a judgment. Packet
generation ends with `HUMAN JUDGMENT REQUIRED`.

Keep packets, keys, mappings, annotations, and code/documentation outside the repository with
owner-only permissions. Do not provide annotators with existing labels, LLM votes, Evergreen
outcomes, another annotator's work, or coordinator mappings.

The historical TypeScript, Rust, and Go derived source pools are missing. Their discarded-candidate
selection remains unverified; a regenerated lookalike is not the historical pool. A passing sample
is `human-audited`. Only full independently reviewed source-pool coverage is `human-validated`.

Only the root integrator updates checked-in benchmark and release claims after reviewing generated
hashes, coverage, agreement, error intervals, and every unverified boundary.
