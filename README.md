# Where Should Domain Safety Policies Live?

A small-scale trustworthy AI research project on policy placement in safety pipelines for financial and medical assistants.

**Research question:** When the same domain policy is supplied to the answering model, the input guard, or the output guard, which placement gives users more compliant, useful answers while limiting harmful assistance?

**Status:** Pilot stage. The policy-placement harness is implemented and self-checked; a 15-pair pipeline pilot and a 25-sample targeted guard check have been run. The financial data, judge script and full experiment are pending. No final results are claimed.

## Motivation

A safety pipeline can reject a benign request at several stages. An input guard may block the question, the answering model may refuse, or an output guard may block the answer. Improving one stage may have little effect on what the user ultimately receives.

For example, explaining how to identify fraudulent financial statements is allowed under our intended deployment policy, while providing operational help to fabricate them is prohibited. We study whether making this boundary explicit at different stages improves the complete pipeline.

```text
User request -> Input guard -> Answering model -> Output guard -> Delivered answer
```

## Planned experiment

We use one frozen policy per domain, with allow, deny, and exception clauses. The substantive policy is shared across stages; task instructions and the guard-template rendering are recorded separately.

| Switch | Default setting (0) | Policy setting (1) |
| --- | --- | --- |
| M: answering model | Baseline prompt | Domain policy in the system prompt |
| I: input guard | Default safety policy | Domain-adapted safety policy |
| O: output guard | Default safety policy | Domain-adapted safety policy |

The three switches produce eight configurations. **Both guards remain present in all eight configurations.** Two additional references use the answering model alone, with and without the domain policy. Self-reminders are disabled in this experiment.

A secondary compact guard setting rewrites the same categories but leaves out the full policy text. Crossed with the input and output guards it gives 18 guarded configurations; the eight above stay the main design. The compact setting checks whether the form of the guard prompt matters, but it changes both length and content, so it cannot isolate either one.

Planned models:

- Answering models: `Qwen/Qwen2.5-7B-Instruct` and `meta-llama/Llama-3.1-8B-Instruct`.
- Input and output guard: `meta-llama/Llama-Guard-3-8B`.

Under a stateless pipeline in which guards only block or pass, we can reuse two model responses and nine guard decisions per request to compose the configurations. This reuse does not apply to pipelines that rewrite requests, retry generation, or otherwise alter subsequent stages.

## Data and evaluation

Medical data supports the initial pilot. The main financial evaluation will use benign/harmful request pairs with similar topics and wording but different intent. Each benign request will include a reason it is allowed and a minimum acceptable answer.

Planned data sources include:

- [Health-ORSC-Bench](https://github.com/ZhihaoZhang97/Health-ORSC-Bench): medical boundary requests and harmful seeds.
- [FinSafetyBench](https://github.com/sustech-nlp/FinSafetyBench): financial harmful requests in English and Chinese.
- [FinSafeGuard](https://huggingface.co/datasets/domyn/FinSafeGuard): candidate financial conversations for supplementary component evaluation.

Source labels will be reviewed against our deployment policy. Development and test examples will be separated by source/template family, keeping related variants together. Policies and test annotations will be frozen before the final comparison. Upstream datasets are not bundled here; their licenses and attribution requirements apply separately.

Primary outcomes, reported for every configuration:

1. **Benign usefulness:** the proportion of benign requests that receive a compliant answer meeting the minimum requirements.
2. **End-to-end harmful assistance:** the proportion of harmful requests that receive harmful help after all pipeline stages.

A fixed MT-bench subset is a secondary, exploratory check on general capability: the first two question IDs in each category, both turns, scored once per model by one frozen LLM judge, after the domain evaluation is stable. It is not a full measure of general capability, and questions will not be swapped after seeing results.

Additional analysis will attribute refusals and blocks to stages, examine paired boundary behavior, and report inference cost. A safe explanation in response to a harmful request can count as safe handling. Blocking a harmful answer to a benign request is a correct block.

Final judgments will distinguish refusal, policy compliance, and answer adequacy, using a frozen LLM-judge rubric with human auditing. Keyword refusal counts are preliminary mechanism measurements. Comparisons will use paired statistics and confidence intervals; absence of a significant safety difference will not be treated as proof of unchanged safety.

## Expected contribution

The intended contribution is a controlled comparison of policy placement, an annotated financial evaluation set, and an explanation of how individual stages affect final outcomes. Custom-policy guards, opposite-intent financial pairs, and per-layer attribution have prior work; we do not claim to introduce those ideas. Conclusions will be limited to the tested models, policies, and datasets.

Relevant prior work includes [FinGuard](https://arxiv.org/abs/2605.29427), [The Llama 3 Herd of Models](https://arxiv.org/abs/2407.21783), and prior work on [defense stacking and refusal attribution](https://arxiv.org/abs/2608.28327). The precise overlap with related policy-placement studies remains under review.

## Pilot findings so far (preliminary)

**Implementation findings** (checked directly; they affect how the experiment must be run):

- The Hugging Face chat template of `meta-llama/Llama-Guard-3-8B` (revision `7327bd9f`) hardcodes the S1–S14 category names and silently ignores a `categories=` argument. `placement_harness.py` therefore builds guard prompts itself; its default arm reproduces the official template exactly (string and token ids).
- Re-tokenizing a rendered chat template with default special tokens added a duplicate start-of-text token for `Llama-3.1-8B-Instruct` (not for Qwen or Llama Guard). Earlier Llama results from `combo_harness.py` used that input and need to be re-checked.
- In our pilot runs, every inspected Llama Guard output began with a blank line before `safe`/`unsafe`. The harness therefore locates the verdict token before reading its score.

**Behavioural observations** (small, selected samples; no final judging; treat as hypotheses, not results). All numbers come from runs with policy sha256 `34cb026c…` and Llama Guard revision `7327bd9f`; raw pipeline outputs are kept off this public repository, apart from the selected development cases in `review/`.

- *15-pair medical pilot, both answering models, 200-token cap.* Adding the policy to the Llama Guard prompt changed few verdicts, and under the policy arm the guard often reported implausible categories. For two Llama responses to requests from the dataset's harmful-seed set, the default output guard blocked and the policy output guard passed. Both responses used educational or simulation framing. One of them was cut off by the token cap before showing whether it gave harmful help. Neither has been judged by an annotator yet.
- *Targeted check on 25 boundary samples previously flagged S6,* compared with one human annotator's (A's) **input** decisions. Input guard: the policy arm unblocked none of the 11 requests A allowed and the default blocked (agreement with A: 11/25 default, 9/25 policy). Output guard, run on older truncated Qwen answers: the policy arm passed 4 answers to requests A allowed, and passed 1 answer to a request A blocked. A labelled requests, not these answers. These are component-level verdict changes, not demonstrated gains in end-to-end usefulness or losses in safety.
- Many pilot answers hit the 200-token cap (Qwen 42/60, Llama 27/60). The harness now uses an 800-token cap for every condition and records answer length and stop reason; the cap will be frozen after an observation batch. A cut-off answer can still show a violation, but the absence of one, or its overall usefulness, is treated with caution.

**Next steps:** re-review existing labels against the revised policies; obtain independent second-annotator labels; run the template check for the financial policy; write the judge script for refusal / compliance / minimum answer; rerun with a higher cap and more samples; expand the financial data.

## Repository guide

| File | Purpose |
| --- | --- |
| `combo_harness.py` | Existing pilot harness for input guard / self-reminder / output guard combinations; these switches differ from the planned M/I/O experiment |
| `summarize.py` | Reconstructs and summarizes saved pilot results without rerunning inference |
| `summarizer_usage.md` | Usage and interpretation of the existing summarizer |
| `test_summarize.py` | Synthetic-data tests for result reconstruction and summarization |
| `test_placement_harness.py` | No-GPU tests with fake models for domain selection, resume isolation, interrupted runs, rendered prompts and label versions |
| `medical_policy_review_B_blank.csv` | Blank table for the second annotator, without earlier labels, suggestions or model answers |
| `review/joint_review_2026-11-03.md` | Joint human review: open decisions, selected cases with full answers, both label tables and the boundary examples. The second annotator should finish the blank table first |
| `s6_rubric.md` | Legacy medical annotation rubric for the earlier stacking experiment; the placement experiment uses a separate rubric covering refusal, compliance and minimum-answer adequacy |
| `policies/medical.md`, `policies/financial.md` | Draft deployment policies, guard-category mappings, template-check notes and worked boundary examples |
| `placement_harness.py` | Policy-placement harness (M/I/O switches, 18 guarded configurations of which 8 are the main design, + 2 no-guard references) for `DOMAIN=medical` or `financial`; `--selfcheck` runs without a GPU |
| `s6_targeted.py` | Targeted default-vs-policy guard check on the 25 review samples |
| `medical_policy_review_guide.md`, `medical_policy_review_policy_1af549a4.md` | Annotation instructions and the current policy snapshot; `medical_policy_review_policy_34cb026c.md` is the earlier snapshot that annotator A's labels and the pilot used; `financial_policy_review_policy_*.md` are the financial equivalents |
| `health_orsc.py`, `guard_classify.py`, `spike_orr.py`, `extract_s6.py` | Earlier pilot and inspection scripts |
| `project_plan.md` | Earlier planning document; some sections describe previous scope and should be read with that distinction |

The judge script and final evaluation set are pending. The review tables `medical_policy_review_25.csv` and `data/financial_policy_review_first25_only.csv` are published with their prompts; the medical table also includes older model answers. Annotation is still in progress, so these labels are not final. `review/joint_review_2026-11-03.md` publishes nine selected development cases with their requests and model answers, some of which are harmful, for human review. Other annotation files and raw pipeline outputs stay out of this repository.

## Using the existing pilot tools

The inference scripts require access to the relevant model weights, a suitable PyTorch/Transformers environment, and sufficient GPU memory. Some scripts use the local path `~/orr_spike`; review dataset and output paths before running. A pinned inference environment will be recorded for the main experiment.

To summarize existing result files:

```powershell
python summarize.py Qwen2.5-7B-Instruct Llama-3.1-8B-Instruct --data-dir "PATH_TO_RESULTS" --json-out summary.json --csv-out summary.csv
```

To run the existing summarizer checks:

```powershell
python -m unittest test_summarize.py
```

See [summarizer_usage.md](summarizer_usage.md) for expected files and field definitions. No final placement scores or deployment recommendations are claimed at this stage.
