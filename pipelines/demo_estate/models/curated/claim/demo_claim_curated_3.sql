-- Demo estate: curated claim conformance 3 (fans in two staging rows).
select a.*, b.* from {{ ref('demo_claim_staging_3') }} a join {{ ref('demo_claim_staging_4') }} b using (id)
