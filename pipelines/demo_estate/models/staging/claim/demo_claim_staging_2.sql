-- Demo estate: cleaned claim staging row 2.
select * from {{ ref('demo_claim_intake_2') }}
