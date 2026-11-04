-- Demo estate: curated encounter conformance 3 (fans in two staging rows).
select a.*, b.* from {{ ref('demo_encounter_staging_3') }} a join {{ ref('demo_encounter_staging_4') }} b using (id)
