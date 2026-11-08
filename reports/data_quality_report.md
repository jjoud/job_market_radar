# Data Quality Report

- **Generated (UTC):** 2026-10-31T21:07:27+00:00
- **Source file:** `data-job-postings.csv`
- **Source SHA-256:** `ad3b6f36c13c17da0967e52556d4cba6e1b9a93849c16c7315c936f106141b3e`
- **Overall status:** PASSED

> Results describe this dataset only; it may be synthetic and does not
> necessarily represent the real job market.

## Row counts

| Metric | Count |
|---|---:|
| raw_rows | 3,500 |
| cleaned_rows | 3,500 |
| rejected_rows | 0 |
| distinct_titles | 40 |
| distinct_role_families | 8 |

## Rejected records

| Reason | Count |
|---|---:|
| missing_posting_id | 0 |
| missing_title | 0 |
| exact_duplicate | 0 |
| duplicate_posting_id_conflicting_content | 0 |

Rejected rows (original values + reason) are saved to `data/processed/rejected_records.csv`.

## Duplicates

| Check | Count |
|---|---:|
| exact_duplicate_rows | 0 |
| duplicate_posting_ids_conflicting | 0 |
| duplicate_skills_within_posting | 0 |

## Missing values

Raw = empty cells in the source file. Processed = NULLs in `jobs.csv`.

| Column | Raw missing | Raw % |
|---|---:|---:|
| posting_id | 0 | 0.0% |
| posted_on | 0 | 0.0% |
| title | 0 | 0.0% |
| seniority | 0 | 0.0% |
| city | 0 | 0.0% |
| country | 0 | 0.0% |
| remote | 0 | 0.0% |
| salary_min_usd | 762 | 21.8% |
| salary_max_usd | 762 | 21.8% |
| company_size | 0 | 0.0% |
| skills | 0 | 0.0% |

| Processed column | NULLs |
|---|---:|
| posting_id | 0 |
| posted_on | 0 |
| title_raw | 0 |
| title | 0 |
| role_family | 0 |
| seniority | 0 |
| city | 0 |
| country | 0 |
| work_arrangement | 0 |
| company_size | 0 |
| salary_min_usd | 762 |
| salary_max_usd | 762 |
| salary_currency | 0 |
| salary_quality_flag | 0 |

## Salary quality (USD, NULL = not provided)

| salary_quality_flag | Postings |
|---|---:|
| ok | 2,738 |
| missing | 762 |
| partial | 0 |
| invalid_value | 0 |
| invalid_range | 0 |

## Dates

- **posted_on_min:** 2023-01-01
- **posted_on_max:** 2025-09-27
- **posted_on_missing:** 0
- **posted_on_invalid_set_null:** 0

## Skills extraction

| Metric | Value |
|---|---:|
| skill_tokens_total | 15,550 |
| skill_tokens_empty | 0 |
| skill_tokens_duplicate_in_posting | 0 |
| skill_tokens_resolved_by_alias | 0 |
| job_skill_pairs | 15,550 |
| unique_skills | 25 |
| postings_without_skills | 0 |

## Role families

| role_family | Postings |
|---|---:|
| Data Analyst | 924 |
| Data Engineer | 707 |
| Data Scientist | 547 |
| Analytics Engineer | 443 |
| Machine Learning Engineer | 304 |
| BI Developer | 267 |
| Data Platform Engineer | 211 |
| Research Scientist | 97 |

## Title mapping (40 distinct titles, all preserved)

| title | role_family | Postings |
|---|---|---:|
| Senior Analytics Engineer | Analytics Engineer | 136 |
| Analytics Engineer | Analytics Engineer | 131 |
| Junior Analytics Engineer | Analytics Engineer | 99 |
| Staff Analytics Engineer | Analytics Engineer | 54 |
| Lead Analytics Engineer | Analytics Engineer | 23 |
| BI Developer | BI Developer | 85 |
| Senior BI Developer | BI Developer | 75 |
| Junior BI Developer | BI Developer | 56 |
| Staff BI Developer | BI Developer | 33 |
| Lead BI Developer | BI Developer | 18 |
| Data Analyst | Data Analyst | 307 |
| Senior Data Analyst | Data Analyst | 270 |
| Junior Data Analyst | Data Analyst | 206 |
| Staff Data Analyst | Data Analyst | 84 |
| Lead Data Analyst | Data Analyst | 57 |
| Data Engineer | Data Engineer | 252 |
| Senior Data Engineer | Data Engineer | 198 |
| Junior Data Engineer | Data Engineer | 144 |
| Staff Data Engineer | Data Engineer | 61 |
| Lead Data Engineer | Data Engineer | 52 |
| Data Platform Engineer | Data Platform Engineer | 72 |
| Junior Data Platform Engineer | Data Platform Engineer | 49 |
| Senior Data Platform Engineer | Data Platform Engineer | 49 |
| Staff Data Platform Engineer | Data Platform Engineer | 25 |
| Lead Data Platform Engineer | Data Platform Engineer | 16 |
| Data Scientist | Data Scientist | 182 |
| Senior Data Scientist | Data Scientist | 169 |
| Junior Data Scientist | Data Scientist | 118 |
| Staff Data Scientist | Data Scientist | 42 |
| Lead Data Scientist | Data Scientist | 36 |
| Machine Learning Engineer | Machine Learning Engineer | 107 |
| Junior Machine Learning Engineer | Machine Learning Engineer | 68 |
| Senior Machine Learning Engineer | Machine Learning Engineer | 68 |
| Staff Machine Learning Engineer | Machine Learning Engineer | 31 |
| Lead Machine Learning Engineer | Machine Learning Engineer | 30 |
| Research Scientist | Research Scientist | 34 |
| Junior Research Scientist | Research Scientist | 24 |
| Senior Research Scientist | Research Scientist | 19 |
| Lead Research Scientist | Research Scientist | 11 |
| Staff Research Scientist | Research Scientist | 9 |

## Validation results

### Source validation: PASSED (0 errors, 0 warnings)

| Severity | Check | Message | Sample IDs |
|---|---|---|---|
| info | missing_values | salary_min_usd is empty (762 rows) | J72672, J71263, J72365, J72692, J73313 |
| info | missing_values | salary_max_usd is empty (762 rows) | J72672, J71263, J72365, J72692, J73313 |

### Transformed validation: PASSED (0 errors, 0 warnings)

| Severity | Check | Message | Sample IDs |
|---|---|---|---|
| info | salary_missing | Postings without salary (kept as NULL, not 0) (762 rows) | J72672, J71263, J72365, J72692, J73313 |
