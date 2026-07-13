# Evergreen benchmark report

Publication status: **PASS**.

Required completion coverage: **99.0%**.
Required languages: **Java, Python, go, rust, typescript**.

### Provenance

| Input | Identity |
|---|---|
| Skill SHA-256 | `8f52c8d03991b9a0f8111223046e6d87ee18b856494abd66ffef4ab632990951` |
| Judge SHA-256 | `5a72345bb69817449425cb11c4425d78aeca6ef4581585b93750f800055a369e` |
| Git commit | `cb24647f7c62b9704d10c97e615005d924c005f2` |
| Git tree | `e96142263d9a7d2623218d25b06685d60af5f12f` |
| Git dirty | `false` |
| Git status SHA-256 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Git diff SHA-256 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Git untracked SHA-256 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Provider | codex |
| CLI version | codex-cli 0.144.1 |
| Settings SHA-256 | `78c54f9f4a07899a55b461bbcf63b72183c2f040ccbb7ce0f7cb76515ccfa3b5` |
| Dataset eval/bench/cascade-java.jsonl | `1c322acf6bc02ae304c062f0d53306e6e9ebb0334bd133afd57940922892ae0b` |
| Dataset eval/bench/codocbench-go-validated.jsonl | `75154db6f2bb59e9ba3da0909f8d62ba566b62e8f96ad5b853350256b3a27eca` |
| Dataset eval/bench/codocbench-rust-validated.jsonl | `53c0e9ba23544fe1035a24cf437e51ed91d46df6064f544819c148e3f16914ec` |
| Dataset eval/bench/codocbench-ts-validated.jsonl | `47f2b98cc3599d2e9a9848dd32a5e0f17dc34665dc548939a49b3b39dea961e5` |
| Dataset eval/bench/codocbench-validated.jsonl | `af817541f91c00c5ee2731e3688377f1b437059021ddedd5da469d0aaaa6778e` |

## Java

Coverage: **100.0%** — **PASS**.

| Coverage | Count |
|---|---:|
| Attempted | 885 |
| Completed | 885 |
| Abstained | 0 |

| Core result | Value |
|---|---:|
| TP | 24 |
| FP | 95 |
| FN | 46 |
| TN | 720 |
| Precision | 0.202 |
| Recall | 0.343 |
| F1 | 0.254 |
| Specificity | 0.883 |
| Accuracy | 0.841 |

| Under-promise (informational) | Count |
|---|---:|
| Attempted | 0 |
| Completed | 0 |
| Abstained | 0 |
| Flagged | 0 |

## Python

Coverage: **100.0%** — **PASS**.

| Coverage | Count |
|---|---:|
| Attempted | 332 |
| Completed | 332 |
| Abstained | 0 |

| Core result | Value |
|---|---:|
| TP | 9 |
| FP | 61 |
| FN | 0 |
| TN | 262 |
| Precision | 0.129 |
| Recall | 1.000 |
| F1 | 0.228 |
| Specificity | 0.811 |
| Accuracy | 0.816 |

| Under-promise (informational) | Count |
|---|---:|
| Attempted | 0 |
| Completed | 0 |
| Abstained | 0 |
| Flagged | 0 |

## go

Coverage: **100.0%** — **PASS**.

| Coverage | Count |
|---|---:|
| Attempted | 299 |
| Completed | 299 |
| Abstained | 0 |

| Core result | Value |
|---|---:|
| TP | 12 |
| FP | 34 |
| FN | 4 |
| TN | 249 |
| Precision | 0.261 |
| Recall | 0.750 |
| F1 | 0.387 |
| Specificity | 0.880 |
| Accuracy | 0.873 |

| Under-promise (informational) | Count |
|---|---:|
| Attempted | 0 |
| Completed | 0 |
| Abstained | 0 |
| Flagged | 0 |

## rust

Coverage: **99.7%** — **PASS**.

| Coverage | Count |
|---|---:|
| Attempted | 304 |
| Completed | 303 |
| Abstained | 1 |

| Core result | Value |
|---|---:|
| TP | 13 |
| FP | 15 |
| FN | 6 |
| TN | 269 |
| Precision | 0.464 |
| Recall | 0.684 |
| F1 | 0.553 |
| Specificity | 0.947 |
| Accuracy | 0.931 |

| Under-promise (informational) | Count |
|---|---:|
| Attempted | 0 |
| Completed | 0 |
| Abstained | 0 |
| Flagged | 0 |

## typescript

Coverage: **100.0%** — **PASS**.

| Coverage | Count |
|---|---:|
| Attempted | 284 |
| Completed | 284 |
| Abstained | 0 |

| Core result | Value |
|---|---:|
| TP | 16 |
| FP | 59 |
| FN | 0 |
| TN | 209 |
| Precision | 0.213 |
| Recall | 1.000 |
| F1 | 0.352 |
| Specificity | 0.780 |
| Accuracy | 0.792 |

| Under-promise (informational) | Count |
|---|---:|
| Attempted | 0 |
| Completed | 0 |
| Abstained | 0 |
| Flagged | 0 |
