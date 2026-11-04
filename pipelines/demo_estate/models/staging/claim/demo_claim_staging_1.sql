-- Demo estate: cleaned claim staging row 1.
select * from {{ ref('demo_claim_intake_1') }}
