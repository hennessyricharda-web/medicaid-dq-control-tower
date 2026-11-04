-- Demo estate: curated provider conformance 3 (fans in two staging rows).
select a.*, b.* from {{ ref('demo_provider_staging_3') }} a join {{ ref('demo_provider_staging_4') }} b using (id)
