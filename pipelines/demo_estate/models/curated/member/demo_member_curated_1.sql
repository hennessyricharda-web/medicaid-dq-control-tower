-- Demo estate: curated member conformance 1 (fans in two staging rows).
select a.*, b.* from {{ ref('demo_member_staging_1') }} a join {{ ref('demo_member_staging_2') }} b using (id)
