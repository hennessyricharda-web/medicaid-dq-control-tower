-- Demo estate: curated member conformance 2 (fans in two staging rows).
select a.*, b.* from {{ ref('demo_member_staging_2') }} a join {{ ref('demo_member_staging_3') }} b using (id)
