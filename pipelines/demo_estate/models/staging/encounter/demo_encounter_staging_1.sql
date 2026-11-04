-- Demo estate: cleaned encounter staging row 1.
select * from {{ ref('demo_encounter_intake_1') }}
