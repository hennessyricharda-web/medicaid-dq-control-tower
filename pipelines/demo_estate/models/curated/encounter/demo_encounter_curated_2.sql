-- Demo estate: curated encounter conformance 2 (fans in two staging rows).
select a.*, b.* from {{ ref('demo_encounter_staging_2') }} a join {{ ref('demo_encounter_staging_3') }} b using (id)
