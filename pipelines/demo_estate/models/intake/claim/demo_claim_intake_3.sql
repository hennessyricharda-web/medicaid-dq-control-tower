-- Demo estate (K4, PFP P2-4): raw claim intake row 3, discovered not evaluated (no TASK_GATE governs this manifest).
select * from {{ source('demo_estate', 'claim_3') }}
