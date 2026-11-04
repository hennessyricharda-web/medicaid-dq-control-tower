-- Demo estate: curated claim conformance 4 (fans in two staging rows).
select a.*, b.* from {{ ref('demo_claim_staging_4') }} a join {{ ref('demo_claim_staging_1') }} b using (id)
