-- Demo estate: curated provider conformance 4 (fans in two staging rows).
select a.*, b.* from {{ ref('demo_provider_staging_4') }} a join {{ ref('demo_provider_staging_1') }} b using (id)
