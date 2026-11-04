-- Demo estate: curated provider conformance 2 (fans in two staging rows).
select a.*, b.* from {{ ref('demo_provider_staging_2') }} a join {{ ref('demo_provider_staging_3') }} b using (id)
