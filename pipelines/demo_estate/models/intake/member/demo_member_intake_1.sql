-- Demo estate (K4, PFP P2-4): raw member intake row 1, discovered not evaluated (no TASK_GATE governs this manifest).
select * from {{ source('demo_estate', 'member_1') }}
