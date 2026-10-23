# 04 Discard pass: research notes

KonMari rule 3, "finish discarding first": before anything is normalized, chunked or exposed to an agent, remove duplicates, superseded versions, off-scope data and ungovernable sensitive data from the pilot corpus. Research date: 2026-10-23.

## 1. Findings

### 1.1 Exact duplicates
- Hashing normalized content (e.g. SHA-256 over a canonical text/byte form) is the standard first stage. Practitioner RAG guides recommend hash-based exact dedup at ingestion, then periodic semantic dedup, and deleting old versions on update while tracking document ID and version metadata [S9, S10].
- Exact dedup is cheap and has no false positives (barring a normalization bug), so it should run before any approximate method and shrink the candidate set.

### 1.2 Near-duplicates (lexical)
- **Shingling + Jaccard (Broder).** Resemblance r(A,B) = |S(A) ∩ S(B)| / |S(A) ∪ S(B)| over w-word shingles; containment C(A,B) = |S(A) ∩ S(B)| / |S(A)| detects a document embedded in another (e.g. an SOP pasted into a larger manual) [S5].
- **MinHash LSH (datasketch).** The threshold is a Jaccard cutoff fixed at index creation; the library picks bands/rows to put the S-curve near it. At `num_perm=128` documented examples are (b=37,r=3) for 0.3, (25,5) for 0.5, (14,9) for 0.7, (5,25) for 0.9. `weights=(fp,fn)` tilts the optimizer; results are approximate around the threshold. Backends: in-memory, Redis, Cassandra [S1].
- LSH returns *candidates*; they must be verified with exact Jaccard before flagging [S2]. A practitioner analysis argues datasketch's threshold optimizer is imperfect and recommends tuning bands/rows to the target similarity [S2].
- **SimHash.** Manku et al. (WWW 2007) found 64-bit fingerprints with Hamming distance k=3 reasonable for 8B web pages [S6]. A DocEng 2013 comparison reported both methods effective, with shingling finding a wider variety of duplicate types [S6b].

### 1.3 Near-duplicates (semantic)
- **SemDeDup (NVIDIA NeMo Curator).** Embed → k-means cluster → pairwise cosine within cluster → flag pairs with cosine similarity ≥ 1 − eps → keep one representative. Default `eps=0.01` (similarity 0.99); quick-start uses `eps=0.07` (0.93). The docs give no universal value: run once with `eps=None`, inspect the similarity distribution, then pick. Use FP32 at near-exact thresholds [S3, S4].
- The "up to 50% removed with minimal performance loss" figure applies to web-scale pretraining data, not RAG corpora [S4]. For an enterprise pilot corpus we do not assume any removal rate.
- Semantic similarity is not equivalence: two policy versions that differ in one number (a price, a deadline) embed almost identically. Semantic near-duplicates therefore need a human or a version rule to decide, never automatic deletion (our design conclusion, consistent with [S10]).

### 1.4 Superseded versions
- The main RAG failure: an older chunk ranks as high as the new one, and the generator sees conflicting statements [S9, S10]. Recommended: the source of truth governs, not the vector index; reconcile hashes, deletion markers and current-version flags against the source; challenge queries that check whether superseded chunks still show up in top-k [S9].
- Signals available in typical sources: explicit version metadata (SharePoint version history, document control numbers), filename patterns (`_v2`, `final_final`, dates), "replaced by" text, effective/valid-until dates, and near-duplicate clusters where one member is clearly newer. Ranking these is a heuristic (our assumption); the human reviewer confirms.

### 1.5 Relevance to the use case
- Storage limitation is "the temporal dimension of data minimisation": data should go once its purpose is met; controllers may not wait for an Art. 17 request [S7, S8]. For the pilot this means: data not needed for the chartered use case is not fed to the agent.
- EU AI Act Art. 10(2)–(3) (high-risk systems): data governance practices covering origin, preparation (incl. cleaning), and suitability; datasets "relevant, sufficiently representative, and to the best extent possible free of errors and complete in view of the intended purpose" [S11]. Even where the pilot is not high-risk, this is a good yardstick for documenting why data was kept or excluded.
- Relevance is judged against the pilot charter's purpose and the category map (dependencies 01, 02), not against a generic notion of "useful".

### 1.6 Ungovernable sensitive data
- Pseudonymised data remains personal data (EDPB Guidelines 01/2025, draft for consultation in early 2025; status of the final text and the CJEU EDPS v SRB appeal should be checked before relying on third-party-recipient arguments) [S12]. So masking reduces risk but does not take data out of DSGVO scope; only true anonymisation does.
- Microsoft Presidio (Apache 2.0) separates detection (analyzer, spaCy/Stanza/transformers NER, custom recognizers) from transformation (redact, replace, hash, encrypt). German needs a German NLP model and custom recognizers for German identifiers (e.g. Steuer-ID, IBAN patterns) [S13].
- Within this change the discard pass only *routes* such items (mask, exclude or escalate). The masking engine and policies belong to `sensitive-data-masking` (change 07).

### 1.7 Retention and deletion duties (DSGVO / German law)
- Art. 5(1)(e) storage limitation; Art. 5(2) accountability; Art. 17(1) erasure applies automatically when grounds exist, so controllers must check at regular intervals and document storage periods or criteria [S7, S8].
- An EDPB coordinated-action report on erasure (2025 topic) found a frequent failure: not defining retention periods per data category, or applying one period to everything [S8].
- Art. 17(3)(b): erasure does not apply where a legal obligation requires retention. BDSG §35(3): where statutory or contractual retention periods prevent erasure, restriction of processing (Art. 18) replaces erasure [S14].
- German commercial/tax retention: since BEG IV (2025) booking vouchers (Buchungsbelege) are kept 8 years (§147(3) AO, §257(4) HGB); annual accounts, inventories and balance sheets 10 years; commercial and business letters 6 years. Periods start at the end of the calendar year of the last entry. For banks, insurers and investment firms the reduction was reversed (10 years; Bundestag decision reported 13.11.2025) [S15, S16]. These are inputs for a consultant + compliance review, not legal advice; the tool must store the rule source per category rather than hard-code periods.
- DIN 66398 (May 2016) is the common German method for a Löschkonzept: data types → deletion classes (Löschklassen) → standard deletion periods (Regellöschfristen), plus deletion rules, responsibilities and documentation. Its DIN status is listed inconsistently (withdrawn vs. current) [S17, S18].
- ISO 15489-1:2016: disposition (retain, destroy, transfer) decisions are documented in disposition authorities [S19].

### 1.8 Archive to cold storage
- Self-hostable: MinIO ILM tiers move object data to a remote tier and keep only metadata locally; MinIO needs exclusive access to the tier, and metadata must be backed up [S20]. Simpler for a pilot: a separate, access-restricted bucket/prefix or filesystem path plus a manifest in the catalog (our assumption).
- Archiving is still processing under DSGVO: archived personal data keeps its retention deadline, and the archive must be covered by deletion runs (derived from [S7, S14]).

## 2. Recommended thresholds (starting points, all to be calibrated)

| Stage | Parameter | Starting value | Basis |
|---|---|---|---|
| Exact | SHA-256 over normalized text | equal hash | standard practice [S9, S10] |
| Lexical near-dup | MinHash `num_perm` | 128 | datasketch examples [S1] |
| Lexical near-dup | Jaccard threshold (5-word shingles) | 0.8 | our assumption; verify candidates with exact Jaccard [S2] |
| Containment | C(A,B) | ≥ 0.9 | our assumption, based on Broder's measure [S5] |
| SimHash (optional) | 64-bit, Hamming ≤ 3 | 3 | Manku et al., web scale [S6] |
| Semantic | cosine similarity | ≥ 0.93 (eps 0.07), calibrated from the distribution | NeMo Curator [S3, S4] |
| Calibration | labeled pair sample before first run | ≥ 50 pairs reviewed by a domain expert | our assumption |

## 3. Recommended OSS tools
- **hashlib (stdlib)**: exact dedup, no dependency.
- **datasketch** (MIT): MinHash, MinHashLSH, LSH Ensemble for containment; optional Redis backend [S1].
- **sentence-transformers** + a multilingual model (e.g. a multilingual MiniLM/E5) + numpy/FAISS: semantic near-dup at pilot scale; NeMo Curator's GPU pipeline is overkill for a pilot but documents the method [S3].
- **Microsoft Presidio** (Apache 2.0): detection routing for sensitive items, consumed via change 07 [S13].
- **MinIO** (AGPL) or plain filesystem/S3-compatible bucket: cold storage target [S20].
- **Postgres** (already in the default stack): decision log, approvals, retention rules, archive manifest.

## 4. Pitfalls
- Deleting on semantic similarity alone loses the one version with the correct number.
- Picking the "newest" by file mtime: copies and migrations reset mtimes; prefer source version metadata.
- One retention period for all data (explicitly called out by the EDPB report) [S8].
- Treating masked/pseudonymised data as out of DSGVO scope [S12].
- "Reversible" vs. erasure: real erasure cannot be reversible. Reversibility applies to archive/exclude; erasure needs a stronger approval and a waiting period.
- Archive that is never revisited: archived personal data silently exceeds its retention deadline.
- Vector index drift: the index keeps chunks of discarded items unless deletions are propagated [S9].
- Hard-coding statutory periods: they change (BEG IV, then the reversal for financial firms) [S15, S16].

## 5. Sources
- [S1] datasketch MinHash LSH docs: https://ekzhu.com/datasketch/lsh.html
- [S2] Skeptric, "Searching for Near Duplicates with Minhash": https://skeptric.com/minhash-lsh
- [S3] NVIDIA NeMo Curator, Semantic Deduplication: https://docs.nvidia.com/nemo/curator/latest/curate-text/process-data/deduplication/semdedup.html
- [S4] NVIDIA NeMo Framework, SemDeDup: https://docs.nvidia.com/nemo-framework/user-guide/latest/datacuration/semdedup.html
- [S5] Princeton COS 435 notes on duplicate documents (Broder shingling): https://www.cs.princeton.edu/courses/archive/spring16/cos435/Notes/duplicateDocs_topost.pdf
- [S6] Manku, Jain, Das Sarma, "Detecting Near-Duplicates for Web Crawling", WWW 2007: https://www.www2007.cpsc.ucalgary.ca/papers/paper215.pdf
- [S6b] DocEng 2013 near-duplicate detection study: https://clgiles.ist.psu.edu/pubs/DOCENG2013-near-duplicate-detection.pdf
- [S7] Helbing, Storage Limitation (Art. 5(1)(e) GDPR): https://www.thomashelbing.com/en/wissen/dsgvo-hub/einzelthemen/grundsaetze-der-verarbeitung/1.3.3.7-speicherbegrenzung
- [S8] CMS, Article 17 GDPR in the EDPB's coordinated action: https://cms.law/en/deu/legal-updates/article-17-gdpr-in-the-edpb-s-next-coordinated-action ; Cottgroup summary of EDPB report: https://cottgroup.com/en/blog/kvkk-gdpr/item/what-is-the-right-to-erasure-gdpr-turkish-data-protection-law-kvkk-and-back-up-compliance-in-light-of-the-edpb-report
- [S9] Oracle Developers, "How to detect RAG index drift": https://blogs.oracle.com/developers/how-to-detect-rag-index-drift-deleted-docs-stale-chunks-and-duplicate-embeddings
- [S10] Twig, RAG scenarios: duplicate content: https://www.twig.so/dev/rag-scenarios-and-solutions/data-quality/duplicate-content
- [S11] EU AI Act Service Desk, Article 10: https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-10
- [S12] EDPB Guidelines 01/2025 on Pseudonymisation (summary): https://ppc.land/european-data-regulators-release-updated-pseudonymisation-guidelines-for-2025/ ; Bitkom consultation reply: https://edpb.europa.eu/sites/default/files/webform/public_consultation_reply/20250313_bitkom-views-on-edpb-guidelines-01-2025-on-pseudonymisation.pdf
- [S13] Microsoft Presidio: https://github.com/microsoft/presidio
- [S14] BDSG §35 (English): https://www.activemind.legal/legislation/fdpa/paragraph-35 ; Bird & Bird guide, erasure and restriction: https://www.twobirds.com/-/media/pdfs/gdpr-pdfs/34--guide-to-the-gdpr--right-to-erasure-and-right-to-restriction-of-processing.pdf
- [S15] Lexware, Bürokratieentlastungsgesetz: https://www.lexware.de/wissen/buchhaltung-finanzen/buerokratieentlastungsgesetz/
- [S16] EY, Längere Aufbewahrungsfristen bei Banken, Versicherungen und Wertpapierinstituten: https://www.ey.com/de_de/technical/steuernachrichten/laengere-aufbewahrungsfristen-bei-banken-versicherungen-und-wertpapierinstituten
- [S17] Dr. Datenschutz, DIN 66398: https://www.dr-datenschutz.de/din-norm-66398-die-entwicklung-eines-loeschkonzepts/
- [S18] reuschlaw whitepaper Löschkonzepte: https://www.reuschlaw.de/wp-content/uploads/2023/07/230403_reuschlaw_Whitepaper_Loeschkonzepte_CK.pdf
- [S19] ISO TC46/SC11 disposition: https://committee.iso.org/sites/tc46sc11/home/projects/published/disposition.html
- [S20] MinIO lifecycle/tiering design: https://github.com/minio/minio/blob/master/docs/bucket/lifecycle/DESIGN.md
