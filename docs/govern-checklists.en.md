# Govern checklists: DPIA screening (DSGVO Art. 35) and EU AI Act

Templates for the pilot, filled in by the facilitator with compliance. They record a screening, not a legal determination. Check the current wording of the AI Act (Digital Omnibus changes) and of your supervisory authority's DPIA list before relying on them.

## DPIA screening (Art. 35)

Tick every criterion that applies to the pilot (EDPB/WP248 criteria). Two or more ticks: a DPIA is usually required. Also check your authority's list of processing that always needs a DPIA (for Germany: the DSK "Muss-Liste").

- [ ] Evaluation or scoring of persons (profiling, prediction)
- [ ] Automated decisions with legal or similarly significant effect
- [ ] Systematic monitoring of persons
- [ ] Special categories (Art. 9) or data of a highly personal nature (`special-category` sources in the pilot)
- [ ] Large-scale processing (number of persons, volume, duration, geographic reach)
- [ ] Matching or combining datasets from different sources (c05 entity resolution across systems)
- [ ] Data of vulnerable persons (children, employees, patients)
- [ ] Innovative use of technology (an LLM agent usually counts)
- [ ] Processing that prevents persons from exercising a right or using a service

Result: `required` / `not required`, reason, decided by (compliance), date. Record it in the charter (`compliance.dpia_screening`).

## EU AI Act

- [ ] **Art. 4 AI literacy**: everyone who operates or uses the pilot agent has been briefed (charter `ai_literacy_briefing`); date and participants recorded.
- [ ] **Art. 50 transparency**: users are told they are interacting with an AI system; generated text that is published is marked where Art. 50 requires it.
- [ ] **Classification**: the use is not listed in Annex III (charter `compliance.ai_act.classification`). If it is (`high`), set `audit.ai_act_high_risk: true` in `governance.yaml` and complete the next block.
- [ ] Art. 26 (only for high-risk use): used according to the provider's instructions; human oversight assigned to named, trained persons; input data relevant and representative; operation monitored and incidents reported; logs kept at least six months (`konmari audit` enforces the minimum); workers and their representatives informed before use at the workplace (Art. 26(7)); DPIA uses the provider's information (Art. 26(9)).

## Art. 30 record

`konmari govern ropa -o ropa-entry.yaml` prefills the entry for the pilot agent from the catalog (purpose, data categories from the inventory, agents and scopes as recipients, retention, measures). Fill every `<FILL>` field and add it to your record of processing activities.
