# Pilot charter guide (step 1 of the KonMari-Kit)

This guide is for the people who run a pilot: the business lead, the data protection officer (DPO), IT and, optionally, a volunteer or consultant as facilitator. You need no data team. The result is two files, `charter.yaml` and `metrics.yaml`, plus a golden question set. Together they decide whether later steps may touch any data.

A complete fictional example is in `examples/verein-faq-assistant/`.

## 1. Pick one pilot use case

```
konmari charter init --template minimal --lang en   # or --template full for comments on every field
```

Pick **one** use case. A second one gets its own charter. A good first pilot:

- has a named business owner who wants it,
- replaces a process you can measure today (how long does answering a question take now?),
- uses a small number of sources (our assumption: 3–10),
- starts with low-risk data such as FAQs, manuals and SOPs, not personnel or health data,
- is not a high-risk use under the EU AI Act, unless you accept the extra obligations.

Write down what is in scope, at least one thing that is explicitly **out** of scope, and at least one way the agent could be misused (for example "looking up members' private details").

## 2. Fill the stakeholder register and the RACI

Every person gets an `id`, name, contact, unit and one or more roles: `sponsor`, `use_case_owner`, `data_owner`, `data_steward`, `data_custodian`, `compliance`, `agent_user`, `facilitator`.

For each of the seven activities, the RACI names who is **A**ccountable (exactly one person), **R**esponsible (at least one), **C**onsulted and **I**nformed. Rules the tool checks:

- Exactly one Accountable per activity.
- The DPIA decision is owned by someone with the `compliance` role.
- A facilitator is never Accountable. Decisions stay with your organization.
- Each in-scope data category has one `data_owner` as approver and at least one reviewer.

## 3. Do the DPIA screening with your DPO

The charter does not replace a data protection impact assessment (DPIA). It records the **screening decision** and who made it:

- `dpia_screening.result`: `required`, `not_required` or `pending`, with a reason of at least 20 characters and the ID of the compliance person who decided it. GDPR Art. 35(2) says to seek the DPO's advice. In Germany a DPO is mandatory from 20 people regularly processing personal data (BDSG §38).
- `ai_act.classification`: `minimal`, `limited`, `high`, `prohibited` or `pending`, with a reason.
- `personal_data_categories`: a list, or `none`.
- `ai_literacy_briefing`: the date the agent users are briefed (EU AI Act Art. 4). This is an onboarding item, not a legal certificate.

`pending` blocks approval. `high` needs an extra sign-off by compliance (`konmari charter approve --as <dpo-id> --compliance-signoff`). `prohibited` can never be approved. The kit gives no legal advice. Check the current text on [EUR-Lex](https://eur-lex.europa.eu/eli/reg/2016/679/oj).

Then:

```
konmari charter validate
konmari charter approve --as <id of the Accountable for charter_approval>
```

After approval, change the charter only with `konmari charter amend --reason "..." --as <id>`. To revert, use `--from-version N`. `konmari charter diff 1 2` shows what changed.

## 4. Define metrics and measure baselines

`metrics.yaml` needs at least one `must_have` metric each for answer quality, abstention ("I don't know" when the answer is not in the data) and business outcome, plus at least one data-readiness and one governance metric. The governance metric `human_approved_decisions` must target 100 %.

The golden question set needs at least 30 questions, and at least 5 of them must expect `abstain` or `flag_conflict`. The questions come from agent users or domain experts, not from the facilitator. Paraphrase real tickets so they contain no personal data.

Measure the **as-is** process without the agent before any data work:

```
konmari metrics validate
konmari metrics approve --as <id of the Accountable for metric_approval>
konmari metrics baseline time_to_answer --value 12 --method "50 sampled questions, March" --as ben
konmari metrics baseline answer_accuracy --not-yet-measured --plan-date 2026-03-01 --as clara
```

A planned baseline lets only the inventory step run. Discarding and every later step wait for real values.

### About vendor numbers

Vendors of support AI quote ranges such as 20–40 % deflection in the first 3–6 months, 80–90 % answer accuracy and a hallucination ceiling of about 2–5 % ([twig.so](https://www.twig.so/blog/kpis-measure-ai-customer-support-success), [twig.so pilot length](https://www.twig.so/blog/how-long-ai-support-pilot-before-evaluating-results)). **These are vendor claims, not standards.** Use them as discussion anchors at most, and set your own targets from your baseline. Deflection overstates success, because a customer who gives up counts as "resolved". Pair it with resolution rate or satisfaction.

## 5. Keep the rhythm

Log every steering meeting:

```
konmari charter log --meeting steering --date 2026-02-10 --attendees anna,eva --decision "start inventory" --action "dirk: read-only access" --as gina
konmari charter status    # warns when steering is overdue (interval + 7 days)
```

At each phase gate, record measurements (`konmari metrics measure <id> --kind gate --step <step> ...`). At pilot close, record them with `--kind final`. `konmari metrics report` shows the verdict.
