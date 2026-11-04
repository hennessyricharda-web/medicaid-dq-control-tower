-- Demo estate: curated claim conformance 2 (fans in two staging rows).
select a.*, b.* from {{ ref('demo_claim_staging_2') }} a join {{ ref('demo_claim_staging_3') }} b using (id)
