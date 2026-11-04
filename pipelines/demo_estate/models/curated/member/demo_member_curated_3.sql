-- Demo estate: curated member conformance 3 (fans in two staging rows).
select a.*, b.* from {{ ref('demo_member_staging_3') }} a join {{ ref('demo_member_staging_4') }} b using (id)
