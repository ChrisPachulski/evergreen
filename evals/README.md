# Plugin evals

Cases for `claude plugin eval`. Each run spends provider tokens and needs a Claude credential,
so neither `tests/all.sh` nor CI runs them.

    claude plugin eval . --scaffold --allow-tools Bash --threshold 0

`--scaffold` is required: five of the six cases build their workspace with `scaffold.sh`, which
runs as you. Results land in `evals/results*`, which is gitignored.
