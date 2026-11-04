-- Demo estate: curated provider conformance 1 (fans in two staging rows).
select a.*, b.* from {{ ref('demo_provider_staging_1') }} a join {{ ref('demo_provider_staging_2') }} b using (id)
