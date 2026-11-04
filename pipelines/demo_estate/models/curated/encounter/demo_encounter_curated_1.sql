-- Demo estate: curated encounter conformance 1 (fans in two staging rows).
select a.*, b.* from {{ ref('demo_encounter_staging_1') }} a join {{ ref('demo_encounter_staging_2') }} b using (id)
