-- Demo estate: curated encounter conformance 4 (fans in two staging rows).
select a.*, b.* from {{ ref('demo_encounter_staging_4') }} a join {{ ref('demo_encounter_staging_1') }} b using (id)
