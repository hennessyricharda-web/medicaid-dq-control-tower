-- Demo estate: cleaned claim staging row 3.
select * from {{ ref('demo_claim_intake_3') }}
